# Investigator omits cited evidence outside the current iteration

## Fix (2026-09-30)

`apply` now uses the authoritative saved `run_dir` when resolving cited run
artifacts, including `state.json` and sibling regression receipts. Existence,
workspace boundaries and duplicate-basename checks remain enforced. Both valid
citation controls recover to completion; missing and uncited probe inputs are
still refused in the adversarial recovery suite.

Historical reproduction on the unfixed revision follows.

Verified on `628b257df5ed6c07d00d3d59a94ee44fc8a078db`. A valid Investigator
probe can fail with `FileNotFoundError: run/verification.json` even though its
report cites an existing regression proof. The run then pauses instead of
receiving the Investigator's recovery guidance. This describes the original unfixed revision.

In `tools/autocode_stuck_job.py:355`, `apply()` passes
`Path(record["output"]).parent` to `cited_files()` as the run directory. For an
ordinary stage output this is `iterations/001`, not the enclosing run root.
At line 332, only cited files under that directory become `run/<basename>`
scratch copies. A proof under `regression/proof-01/verification.json` is
instead treated as a repository file. The scratch workspace excludes the
runner's `.autocode/` tree, so that proof is unavailable to the probe.

The expected behavior is to resolve citations against the authoritative run
root and stage every explicitly cited run artifact under the documented
`run/<basename>` location, including artifacts outside `iterations/`. Existing
file-existence, workspace-boundary, and basename-collision checks must remain.
The current behavior fails closed: a nonzero probe is rejected, the original
pause is restored, and no retry or completion is approved on missing evidence.

## Deterministic reproduction

From the repository root, this uses only temporary files and no model calls,
network, or existing run state. It exits successfully when the defect is present:

```sh
PYTHONPATH=tools python3 - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from autocode_stuck_job import cited_files

with TemporaryDirectory() as temporary:
    workspace = Path(temporary) / "project"
    run_root = workspace / ".autocode/runs/example"
    output = run_root / "iterations/001/investigate_stuck-01.json"
    review = output.with_name("astra_review-01.json")
    proof = run_root / "regression/proof-01/verification.json"
    for path in (output, review, proof):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    report = {"evidence_refs": [str(p.relative_to(workspace))
                                for p in (review, proof)]}
    actual = cited_files(report, workspace, output.parent)
    expected = cited_files(report, workspace, run_root)
    assert set(actual) == {"run/astra_review-01.json"}
    assert set(expected) == {"run/astra_review-01.json", "run/verification.json"}
    print("Current mapping:", sorted(actual))
    print("Run-root mapping:", sorted(expected))
PY
```

Both mappings resolve the same existing citations; only the directory passed
by `apply()` differs. Copying the current mapping into an empty scratch tree
also reproduces the missing `run/verification.json` file.

## Campaign evidence and separate fixture issues

Local, ignored evidence is under
`.scenario-runs/20260929-codex-campaign/hybrid/20260929T224213Z-stuck-planner-citation-fake-3vbbxxae/`.
Its `result.json` records `HONEST_BLOCKER` / `PAUSED_INVALID_OUTPUT` and three
real Investigator/report-repair calls. Within the saved run directory:

- `regression/proof-01/verification.json` exists and is explicitly cited by
  the Investigator's archived report.
- `iterations/001/archived-investigate_stuck-01-0548c3/investigate_stuck-01.json`
  uses the documented `run/verification.json` probe path.
- `iterations/001/investigation-probe/scratch-command.log` records the
  `FileNotFoundError` for that path. The original rejection and final repair
  encounter the same missing proof; the intervening repair fails a separate
  source-text assertion.

Two fixture defects caused this campaign to reach a different failure from its
intended planner test. Its prose-suffixed citation is accepted deliberately by
`units/autoplanner.split_code_ref`, so the injected planner fault is obsolete.
Its count example, `count_words("one  two\nthree\n") == 3`, passes on both the
seed and fixed code, while the diagnosis incorrectly claims the seed returns 4
and the fake provider labels the case fail-before/pass-after. The completion
gate correctly rejects that mislabeled obligation. These fixture problems are
separate from the evidence-staging defect, which the synthetic reproduction
above demonstrates without either fixture.

## Independent full-live reproduction

The same defect occurred in
`.scenario-runs/20260929-codex-campaign/live/20260929T234506Z-ladder-11-optimistic-documents-codex-only-92nb51dg/`.
Planning stopped before any Builder ran: three finalizer outputs appended one
backtick to protected AC12 text. The Investigator correctly diagnosed that
exact change and cited the existing run-root `state.json` plus the archived
finalizer output, but its probe failed because `run/state.json` was not staged.

Replaying the exact probe on temporary copies independently confirmed the cause:
the current iteration-based mapping copies only the finalizer report and exits 1;
the enclosing-run-root mapping also copies `state.json` and the same probe exits 0.
Original evidence hashes were unchanged. The run remained `HONEST_BLOCKER` /
`WAITING_FOR_USER`, below its active-time cap, with the application still the seed.
This reproduction uses an ordinary full-live scenario and does not depend on the
hybrid fixture defects described above.

## Black-box regression pair

`scenarios.test_adversarial_recovery` now drives this failure through the public
CLI without importing runtime modules or modifying saved run state. Its provider
first emits a nonexistent planner citation until AutoCode asks the Investigator
to diagnose it. The positive control cites the actual archived planner output,
proves the invalid citation in a scratch command, receives one guided retry, and
completes. The paired `test_run_root_evidence_allows_a_verified_investigator_retry`
also cites the existing `state_file` explicitly supplied in the Investigator's
handoff, then reads both cited inputs at their documented `run/<basename>` paths.
That test fails: the planner artifact is present but `run/state.json` is missing,
and both report-only repairs repeat the same `FileNotFoundError` before pausing.
Separate controls prove that an actually missing citation and an uncited probe
input remain rejected. These are deterministic scripted-provider tests; they do
not spend tokens or rely on a live model choosing the desired diagnosis.
