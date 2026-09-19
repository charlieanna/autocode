# Historical source documentation — superseded for this standalone tool

This file is retained only as extraction provenance. Its launch and activation
instructions describe the original project runner. Use [README.md](README.md)
for this standalone version; do not execute the historical project commands below.

Run a durable three-role Codex loop from a Git repository:

```sh
python3 tools/autocode.py "Build OAuth login" \
  --astra-model gpt-6-astra \
  --terra-model gpt-5.6-terra \
  --sol-model gpt-5.6-sol --reasoning-effort high --headroom off
```

`Astra` is read-only and is the only role allowed to declare `TASK_COMPLETE`.
`Terra` is the only writer and receives one bounded objective per iteration.
`Sol` is read-only and independently tests the repository. All roles have their
own resumable Codex thread; state and JSONL event logs live under
`.autocode/runs/<timestamp>-<task>/`.

Use a no-cost wiring check first:

```sh
python3 tools/autocode.py "Check wiring" --dry-run
```

Resume an interrupted run:

```sh
python3 tools/autocode.py --run-dir .autocode/runs/<run-directory>
```

Completion additionally requires Sol's executed passing checks, every frozen
criterion verified, and current source/criteria/evidence hashes. Invalid outputs,
uncertain processes, rate limits, budgets, no progress and blockers pause; they
never count as success. Terra retains `--approve-for-me`; use trusted repositories.
Do not put API keys in the repository or task prompt.

## Retrofit: safe activation for the existing adaptive DSA run

Run from `/Users/ankurkothari/Documents/workspace/idlecampus`. The legacy running
process does **not** adopt source edits and cannot observe the new pause marker.
Wait for its natural saved exit. Do not kill an active request/tool/write.
The new runner refuses launch if it detects the legacy run or Codex child.

Read-only status:

```sh
python3 tools/autocode.py --run-dir .autocode/runs/20260918-113408-complete-the-adaptive-dsa-mapping-and-validation --status
```

After the old process exits:

```sh
python3 tools/autocode.py \
  --run-dir .autocode/runs/20260918-113408-complete-the-adaptive-dsa-mapping-and-validation \
  --legacy-iteration-ceiling 18 \
  --astra-model gpt-6-astra --terra-model gpt-5.6-terra \
  --sol-model gpt-5.6-sol --reasoning-effort high --headroom off --migrate-only
python3 tools/autocode.py \
  --run-dir .autocode/runs/20260918-113408-complete-the-adaptive-dsa-mapping-and-validation \
  --resume-paused
```

Migration preserves the original task/sessions/results, backs up
`state.pre-v2.json`, and updates the same authoritative `state.json`. Completed
Terra advances to Sol without replay. Partial/uncertain invocations require
reconciliation; `--resume-paused` does not bypass that guard. Legacy validation
lacks source pins and cannot authorize completion. The original invocation began
at iteration 3 with 15 iterations, hence ceiling 18; no automatic extension.

## State, context and evidence

Atomic temp-file/fsync/rename checkpoints and a project writer lock preserve
state. Full prompts, JSONL, finals, diffs and source snapshots remain local.
Role packets omit transcript/history arrays but retain requirements, findings
and evidence paths. Code/diffs remain uncompressed. Schema-valid agent responses
propose transitions; the runner owns state and completion gates.

Noisy writer-role commands can use:

```sh
python3 tools/autocode.py capture \
  --output .autocode/runs/<run>/evidence/unique-check.json -- ruby path/to/test.rb
```

Full output goes to a companion `.log`. Child exit status is retained. Duplicate
lines/progress-only noise are reduced; all distinct output remains. JSON is kept
whole. `--no-compress` disables formatting. Formatting failures return complete
original content. This is opt-in, not interception of every Codex tool response.
Sol may reference actual captured command events using `event:<id>`; command and
exit status are verified against independent execution, not Terra's claims.

Soft context budget defaults to 10,000 estimated tokens (UTF-8 bytes/4, excluding
resumed history and tool output). Over-budget requirements are preserved. Roles
use explicit sessions, never `--last`. A role whose previous stage reports at
least 1,000,000 cumulative input tokens rotates at its next checkpoint; old IDs
remain archived. This is a heuristic, not a context-window measurement.

Optional initial time/token limits pause at boundaries, never mid-request.
Unknown usage cannot satisfy a token cap. Three unchanged implementation batches
pause by default; automatic retries are zero. Saved limits stay authoritative.
For v2, `--pause-after-stage` pauses after saving one stage. A `pause-requested`
file in the run directory also pauses at a boundary; remove it intentionally
before resuming. Neither mechanism affects an already-loaded legacy runner.

## Conservative rollback

At a safe saved boundary, disable optional transport/session optimizations while
preserving newer progress:

```sh
python3 tools/autocode.py \
  --run-dir .autocode/runs/20260918-113408-complete-the-adaptive-dsa-mapping-and-validation \
  --headroom off --rotate-after-input-tokens 0 --resume-paused
```

Use `capture --no-compress` for original tool output. Do not restore old state
over new results. `.autocode/retrofit-backup/autocode.py` holds the pre-retrofit
source for inspection; automatic downgrade after v2 progress is not supported.

## Headroom, measurements and tests

Headroom is **disabled/unverified**, absent in the inspected environment.
`--headroom on` fails closed before a model launch. No guessed proxy flags,
global config/MCP changes, alternate billing, memory, or effort routing are added.
This supplies a gated adapter seam, not a verified Headroom transport. Activation
still requires an isolated installed-version smoke of existing authentication,
streaming, tools, schema, evidence access, loopback binding and direct rollback.

Metrics retain runner calls/retries, durations, provider input/cache/output/
reasoning tokens when emitted, estimates and adapter state. Unknown values are
null, not zero. Token changes do not prove subscription-quota savings.

```sh
python3 -m unittest tools/test_autocode.py
```

Uses isolated Git fixtures/mocked model processes plus real subprocess capture.
See `docs/plans/autocode-retrofit-report-2026-09-18.md` for measured results.
