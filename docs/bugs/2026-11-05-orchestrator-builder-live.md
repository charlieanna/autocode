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
- Feedback offered at the repeated-failure pause was refused by the CLI. The
  public-view action mismatch and two-step pause recovery remain recorded gaps.

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
