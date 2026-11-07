# Orchestrator and Builder live verification, 2026-11-05

Two bounded Kilo runs completed the dependency graph `A -> (B, C) -> D` on
base `3f3b1f6f35c5bc61669b22b1ab3ddf8a4570f5f0`. A writes a JSON contract;
B and C implement independent server/client functions; D calls both. The
integration test must reject a hardcoded correct-looking output.

## Execution and evidence

The public CLI invocation was `autocode BRIEF --workspace PROJECT --in-place
--provider kilocode --joint-planning --milestone-checkpoints
--max-parallel-builders N --max-cost-usd 10 --max-seconds 7200
--max-stage-seconds 1800 --no-chat --evidence-provenance live`, with `N=1`
and `N=2`. Saved routes stayed unchanged: GLM-5.3 high for Requirements,
Planner, Tester and Completion Reviewer; MiMo-v2.6-pro high for Plan Reviewer,
Builder and Resolver. Requirements and exact plans were reviewed by the
operating agent under the repository's owner delegation.

| Case | Run directory | Result | Independent checks | Wall time |
| --- | --- | --- | --- | --- |
| Serial | `20261105-110324-build-a-four-milestone-dependency-graph-as-a-sma-c31af4be` | TASK_COMPLETE | 22/22 | about 114 minutes |
| Parallel | `20261105-110310-build-a-four-milestone-dependency-graph-as-a-sma-5071f9b8` | TASK_COMPLETE | 23/23 | about 162 minutes |

The parallel Builder calls overlapped for 46.391383 seconds in separate
worktrees. A's review finished at 21:20:01 UTC before either call; B/C's joint
review finished at 21:30:15 before D started at 21:30:21. A pause requested
while both Builders ran preserved their results: all ten checked file hashes
matched after integration, with one model attempt per worker. Resuming needed
two named CLI actions: one applied the queued pause, the next continued.

Independent checks exercised exact outputs, Unicode/empty/integer/None inputs,
both integration calls, package markers and delivered test suites. Those tests
rejected broken returns in all three functions and a hardcoded integration
result. Both final status views authenticated the delivered file hashes.

Redacted bundle manifest hashes:

- Serial: `ca04a5230fbe83e0bc8529bc56725067c4a0917d3240f1d864bd94e19df45396`.
- Parallel: `f35266bff2396445ac26fe314488c8d770ea265fa0b125faa7d1aeb4c2b23a6a`.

Raw runs and failed attempts remain in the ignored
`.scenario-runs/orchestrator-builders-live-20261105/` directory. Estimated
costs were $1.804847 and $2.380932; these are incomplete estimates, not billing
totals. Parallel active time was extended once to 10,800 seconds for recovery,
without changing its $10 cap or routes.

## Resolver retry defect and regression

Parallel A's direct tests passed, but discovery found zero tests because its
package marker was missing. Malformed Completion Reviewer/Resolver reports
and exhausted report repairs led to a repeated-failure pause. The CLI admitted
an exact `--resume-paused --retry-failed-stage` grant, then the Resolver's
handoff guard rejected it again before launch.

The fix passes the invocation-local consumed grant into request preparation.
Preparation still refuses absent, stale or spent grants; final launch admission
consumes the separate novelty credit. Report validation retains approval,
source and evidence checks without reapplying launch policy to a completed
authorized report. Failure history and budgets are preserved.

`ResolverFlow.test_explicit_resolver_retry_reaches_one_read_only_diagnosis`
fails on the original runtime at that exact refusal and passes on the fix in
supported Linux (27.438 seconds failing base; 23.198 seconds passing candidate).
It verifies one provider call, unchanged source/contract/history, and the
Builder handoff. Authorization, guidance and architecture checks passed 43
tests; seven existing Linux retry tests passed; seven deliberate authorization
mutations were detected. An initial removal-only fix was rejected by a negative
admission test and replaced. The live retry used that intermediate patch;
after a clean Tester boundary pause, the final guarded implementation ran the
remaining parallel graph. Its exact retry path is qualified by the Linux CLI
regression, not claimed as a second live retry.

## Limits and outstanding observations

- Live providers ran through Kilo's portable macOS path, without a kernel
  session boundary. Separately, 177 supported-Linux fake-provider checks passed
  for dispatch, Builder policy, recovery, retries and worker lifelines on the
  original base. They do not establish live Linux provider compatibility.
- Planning/review latency dominated, and malformed model reports required
  recovery. Two passing deliveries are not a general reliability guarantee.
- Serial replay export lacked immutable source snapshots; concurrent Builder
  recording is unsupported by replay v1. Bundles exported, but replay was not
  qualified.
- Import bindings have no mutation sites; independent Testers accepted exact
  source-bound reasons. Parallel stability repeats omitted the contract tests;
  a separate final contract-suite run passed.
- Direct `--feedback` at the repeated-failure pause was refused by the CLI.
  A later audit of all 49 saved snapshots found that the feedback card carried
  no executable arguments: it was a chat drafting hint. The driver incorrectly
  inferred a command from that hint; this is not evidence of an executable
  public-view action mismatch. Two-step pause recovery remains recorded friction.

## Final verification follow-up

The initial Linux `run_suite.py --changed --jobs 4 --module-timeout 900`
reported 1,609 tests across 80 modules in 2,111 seconds: 72 modules passed,
eight failed or timed out. The retained logs distinguish runtime edits that
invalidated approval identities, the controller line limit, a missing installed
package, Docker bind-mount ownership/I/O, and two fixture failures reproduced
on master. The empty-supersedes fixture is fixed by merged PR #2772; the
storage-floor fixture correction remains a separate local commit.

After correcting those conditions and rebasing onto `827a6987235e2f9377939d37645b83554d6bc999`,
all eight affected modules and `tests.test_units` passed on a frozen source
copy on the container's Linux filesystem: **215 tests in nine modules, 169
seconds**. The tested source commit was `9a8af2914bcffc838ab842c214fbea2750cb27ef`;
its archive SHA-256 was `806c850e9210a691b0088faa3c8b7c99234bf6e6758e17e5c81f8f4da080407b`.
Only this evidence note was updated afterward. Repository Ruff check and format
passed (3,244 files), and mypy passed (764 source files).

Exact commands, module outcomes, source identity and failed attempts are kept
in `finish-testing-report.json`, `final-native-command.json`, `final-native.log`
and `final-static-result.json` under the ignored evidence directory above.
The final rerun resolves the earlier failures; it is not a claim that the
initial 80-module invocation passed.

## Broader hard-case qualification

After the owner requested coverage beyond the two live diamonds, the exact
published runtime `3593e82358bf69ccbafd7cfa26b6a13c18311015` passed **317 tests
in 20 modules in 452 seconds** on the supported Linux container. This covers:

| Boundary | Cases exercised |
| --- | --- |
| Scheduling | Dependency readiness, ownership overlap, parallel limits, independent worktrees, reviewed prerequisites |
| Crashes | Controller SIGKILL/SIGTERM, before spawn, after spawn before PID save, after patch application, worktree retirement |
| Partial failure | Successful siblings retained, failed member retried alone, late failed result cannot replace integrated retry |
| Source safety | Parent edits and new files preserved, scope escapes refused, stale contracts rejected, merge conflicts retain both sides |
| Delivery evidence | Combined review required, incompatible client/server rejected, missing or deleted tests fail, prior passing criteria cannot silently regress |
| Recovery | Report repairs do not reimplement, duplicate results consumed once, queued pause/stop blocks subsequent calls |
| Bounds | Retry/escalation exhaustion, cost/time/milestone limits, worker quota, pinned routes and verifier independence |

`tests.test_build_graph_cli` adds a ten-milestone computation with five waves
of 1, 3, 3, 2 and 1 Builders. It passed in 89.706 seconds, with real public CLI
calls and worktrees and scripted providers. Assertions require exact reviewed
prerequisite bytes, overlapping isolated siblings, review before advancement,
exactly ten Builder launches, no Resolver launches and current final completion.
Breaking a shared prerequisite then fails the downstream test and invalidates
completion. Explicit CI mappings retain this test for dispatcher, worker,
milestone and fixture changes.

Another 149 pure tests passed for CI selection, dependency graph validation,
recovery views, pause authority and queued messages. Four new CI-selection
assertions fail against the original mapping and pass with the added mapping.
The earlier feedback finding was corrected using all 49 saved repeated-failure
status snapshots, not by weakening the operational pause.

Commands, source hashes, logs and the coverage inventory are under
`hard-cases/` in the ignored evidence directory. These are finite test results,
not proof of all possible schedules or of model effectiveness on every complex
project. The ten-milestone trial is scripted; the earlier live-host and replay
limits still apply. No additional live-model calls were made.
