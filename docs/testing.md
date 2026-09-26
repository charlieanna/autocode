# Tests, evidence, and legacy migration

[← Back to README](../README.md)

## Progressive testing plan

See the [progressive testing plan](testing-plan.md) for the simple-to-complex
task ladder, independent pass criteria, failure-injection matrix, evidence format,
and promotion gates. It separates offline runner checks from authorized live-model
delivery trials and identifies current harness hazards. In particular, unrestricted
source test discovery is not currently an offline-only command.

## Tests and evidence

AutoReview generation pins contract/task identity and reviewer-owned finding IDs
in each attempt's schema. Report-only repairs receive the original executed
commands and exit codes; normal evidence validation still applies. Repairs cannot
close findings. Fresh reviews can resolve a finding only when all criteria in its
recorded scope have passing evidence; unsupported closure claims remain open with
`pending_resolution`. Because older findings carry milestone-wide scope, they
conservatively require that entire scope to pass. Explicit retractions remain a
separate disposition.

Reviewers stay read-only. Missing devices, credentials, browser access or compiler
scratch permissions are verification blockers, not permission to change source
or bypass restrictions. A shell wrapper exiting zero does not prove its nested
test command succeeded. Live audit limitations are recorded alongside results.

### Unit suite

```sh
python3 -m unittest tools/test_escalation.py tools/test_autocode.py tools/test_goals.py tools/test_subprocess.py tools/test_opencode.py tools/test_process.py
```

The unit suite uses isolated Git fixtures. The subprocess test drives the actual CLI,
runner, schemas, snapshots and approval commands with a deterministic **fake Codex**
provider. That provider writes a tiny greeting program, then executes both success and
failure cases in its validation stage. It never calls a real model or reads credentials.
Set `AUTOCODE_TEST_CLI=/path/to/installed/autocode` to test the installed package.
These tests prove runner behavior, not a model's interviewing quality or semantic
review accuracy. See [`VALIDATION.md`](../VALIDATION.md) for measured results and remaining limits.

The OpenCode subprocess tests use a fake OpenCode executable with native event shapes,
including separate resumed sessions, command evidence and usage. An optional tiny
live check is available via `python3 tools/opencode_smoke.py --run-live`; it makes one
request to each selected provider in a temporary workspace and saves raw evidence.
Process tests require local `ps` access and exercise detached-worker cleanup, timeouts,
interruption and checkpoint-write failure. The repair audit under
`audits/opencode-repair-2026-09-19/` also verifies the original defects with native
OpenCode metadata and a loopback provider fixture, without hosted model requests.

Codex launch compatibility was checked against installed exec/resume help and
[official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

### Dashboard tests

See [Dashboard](dashboard.md#dashboard-verification) for the dashboard test commands.

### Live-trial results

`tools/live_trial.py` uses the same verdict in `live-trial.json` and the bundle's
`result.json`. Its exit codes describe delivery, not merely whether the harness ran:

- `0`: `PASS`, runner completion with passing independent oracle checks.
- `2`: `HONEST_BLOCKER`, a recorded pause rather than delivered work. Missing live
  spending authorization also exits `2` without starting a trial.
- `1`: unsuccessful or unverified delivery, including `FALSE_COMPLETE` when the
  runner claims completion but an independent check fails, and `ERROR` for an
  unexpected stopped state or an oracle-reported infrastructure error.

An oracle-reported error or deferred check is not proof of a product defect and
cannot establish successful delivery. The fixture-profile tests in
`tools/test_live_trial.py` exercise these verdicts without hosted-model requests.
These scoring checks do not remove the other live-driver limitations listed in
the progressive testing plan.

## Legacy migration — opt-in only

Existing v3 approved contracts retain their exact content, hash and approval. New
discovery drafts include the expanded build-brief fields. Older `TASK_COMPLETE` and
`VALIDATE` stage results remain readable during recovery; newly requested Plan Reviewer
decisions use the four statuses in [Execution](execution.md).

This extraction does not modify or attach to any existing project/run. Keep a live
legacy process running until it reaches a natural saved exit; it cannot hot-load these
gates. Do not launch a second writer or restore old state over newer work.

At a confirmed idle boundary, an operator may deliberately use this standalone tool:

```sh
autocode --workspace /path/to/project --run-dir /path/to/existing/run --migrate-only
autocode --workspace /path/to/project --run-dir /path/to/existing/run
```

Migration retains the original task, sessions, completed artifacts, stage history,
criteria, plan, iteration and limits. It backs up `state.pre-v2.json` where applicable
and `state.pre-v3.json`, then reconstructs an **unapproved** draft. Old evidence is
archived for inspection and must be revalidated. The Requirements Gatherer uses known answers/artifacts
to ask only material unresolved questions. User decisions are never backdated.
Completed interrupted stages reconcile before migration; live/uncertain stages
refuse migration. No migration was applied to the original IdleCampus run.

See also: [Execution](execution.md) · [Reliability priorities](../RELIABILITY.md)
