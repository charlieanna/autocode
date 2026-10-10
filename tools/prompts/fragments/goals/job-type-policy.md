
JOB TYPE. task_kind is "bugfix" when the request reports existing behavior that is wrong
(a crash, a wrong result, a missed validation, a regression) and asks for it to be corrected;
otherwise "build". The Requirements Gatherer proposes it and says why in its summary; the
Planner sets contract.task_kind; the Plan Reviewer confirms or challenges it. The user sees it
at approval. For a bugfix, keep the plan to the defect: reproduction, root cause, the smallest
correct fix, and a regression test in the project's own test suite that fails on the current
code and passes after the fix. One milestone is usually enough; do not add features or
unrelated refactors, and keep review concerns to whether the plan fixes the root cause and
proves it. Before completion the runner itself runs the new or changed tests against the
original code (they must fail) and the fixed code (they must pass), then the project suite.
A bugfix regression test must build and run on the original code and fail there because of the
bug: do not plan it around a hook, package variable or other seam the fix adds (on the original
code it cannot compile or import, which proves nothing). Plan it to drive the real failure path
through public APIs that exist before the fix and to assert the behavior (the returned error, the
result, the saved state); a log line or message alone does not prove the behavior.
