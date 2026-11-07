# Builder commit reconciliation (#2571)

A Builder that committed its changes moved HEAD away from the run's launch
base. Atomic delivery refused that HEAD, and the only offered resume action
repeated the same completion pause. The controller now recognizes a clean,
linear chain recorded inside successful Builder attempts for the approved plan
and consolidates it into the requested atomic commit.

The source captures must belong to this run and match their recorded content
identities. Every commit must be covered by a Builder capture, and a named task
branch must still match. Missing evidence, intervening external commits, merges,
dirty or staged work, and changes outside approved paths remain refused. File
identities are checked directly, including changes hidden by Git index flags.
The existing index lock, hooks, durable intent and fresh independent verification
remain in force. A failed hook restores the original Builder HEAD only while
the ref still equals the expected delivery parent. Uncertain writes retain the
existing reconciliation stop.

## Deterministic evidence

The new Linux CLI regression failed before implementation with the reported
`PAUSED_COMPLETION_GATE` message, after a fixture Builder committed its change.
It now reaches `TASK_COMPLETE`, with one commit carrying the approved message,
a clean tree, and a current evidence report for the delivered revision.

```sh
.venv/bin/python -m unittest tests.test_git_delivery
.venv/bin/python -m unittest tests.test_completion_result tests.test_proof_infrastructure
.venv/bin/python -m unittest tests.test_architecture tests.test_run_state
.venv/bin/python scripts/run_linux_fake.py --unittest tests.test_git_delivery_cli.GitDeliveryCLI
```

All 27 delivery unit tests passed in 13.117 seconds; the seven direct completion
consumer tests also passed. Architecture and run-state checks passed all 32 tests
in 18.211 seconds. The initial failing Linux test took 17.024 seconds, excluding
container image setup. The final two Linux CLI cases passed in 43.811 seconds. Focused unit
coverage includes multi-commit delivery, unrelated commits, branch changes,
missing and stale captures, captures outside the run, symlinks, merges, dirty
source hidden by index flags, hooks, and repeated delivery without another commit.

A bounded scratch study enumerated all 32 eligible Python mutations on the
changed product lines. After strengthening the refusal tests, 31 mutations
failed assertions and one raised a wrong exception from a named test body;
none survived. The initial study took 384.594 seconds and the focused rechecks
took 136.286 seconds. The scratch baseline passed, the real checkout was never
mutated, and the JSON receipts retain initial survivors and their rechecks.
This supplements the tests; it is not an authenticated task-run coverage receipt.

## Live qualification, October 31, 2026

One isolated vowel-counting bugfix ran through public `TaskRun` operations with
the incident's existing GoCode routes. A qualification wrapper invoked the real
configured provider and, after the successful real Builder response, injected
a local commit before the runner captured the Builder's final source. It
changed no model response or acceptance result. This made the recovery trigger
deterministic while retaining real model planning, implementation and reviews.

Retained harness commands:

```sh
.venv/bin/python .scenario-runs/issue-2571-live/drive.py start --i-authorize-live-model-spend
.venv/bin/python .scenario-runs/issue-2571-live/drive.py approve --i-authorize-live-model-spend
.venv/bin/python .scenario-runs/issue-2571-live/drive.py advance --i-authorize-live-model-spend
```

The agent reviewed the exact revision 2 plan and approved it under the
October 29 delegation in `AGENTS.md`; the harness retained that review. No
owner-personal review was claimed. The run used a $6 configured cap, a
1,800-second active-time limit, and 600-second stage limits. No limits or routes
were changed during the run.

| Reached job | Model | Reasoning | Duration |
| --- | --- | --- | --- |
| Investigator | `claude-opus-5-5` | high | 33.890 s |
| Planner (`astra_discovery`) | `gpt-6.1-sol` | high | 145.752 s |
| Plan Reviewer | `claude-opus-5-5` | high | 50.167 s |
| Builder | `gpt-6.1-sol` | medium | 54.794 s |
| Tester | `claude-opus-5-5` | high | 124.732 s |
| Completion Reviewer | `claude-opus-5-5` | high | 34.435 s |

Public CLI action durations totaled 465.476 seconds. Wall time from first stage
start to completion review finish was 542.478 seconds, including plan review.
Reported spend was **at least $2.038415**: the two GPT stages did not report
usage, so total cost and compliance with the configured monetary cap cannot be
established from the saved ledger.

Run directory:
`20261031-144826-fix-count-vowels-in-counter-py-y-and-y-are-conso-df402484`
in `/private/tmp/autocode-2571-live/.autocode/runs/`.

The committed delivery receipt proves the changed path executed:

- Launch parent: `8a8b5e2f34df1a42008b1d8f269fedc7c3e49e14`.
- Injected Builder commit: `9e66bf67489274ed5d1b918f1c860f13b21533b6`.
- Controller delivery: `0ba49dfe88cf3f9785fe6217a79f86c1d9b76ead`.
- `reconciled_commits` contains the injected Builder commit; `phase` is `committed`.
- Exactly one commit remains between the launch parent and HEAD, with a clean tree.
- Regression proof ran again after delivery: five failing-on-base tests pass on
  the candidate, changed-code evidence is `PASS`, and both observed mutants of
  the fixture's changed product line are killed.
- Final status is `TASK_COMPLETE`, `done` is true, and the public evidence report
  is `current`. No operator Git reset or resume action was used.

Harness, public views, report, injection marker and mutation-study receipts are
retained under `.scenario-runs/issue-2571-live/` and the fixture's `.autocode/`.
The live result establishes this reconciliation path; it does not qualify
Linux live transport, other providers, or uncertain-write recovery.
