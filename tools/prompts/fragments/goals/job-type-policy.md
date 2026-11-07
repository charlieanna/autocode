
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

Before adding a blocking question or a new behavior to a bugfix, connect the decision to the requested behavior
using the task, saved human answers and relevant source. A nearby capability is not evidence that it must change:
for example, a single-record lookup defect does not by itself require a new batch aggregation policy. If the
capability is unnecessary to the fix, preserve its existing behavior and appropriate regression guards instead
of asking the person to redesign it. Reassess a model-proposed question when that connection is disproved;
record why it is no longer needed without inventing an answer. Never discard an explicit user requirement or
silently weaken the requested outcome to avoid a question. If a product choice is necessary, explain what
concrete part of the requested fix cannot proceed without it and ask. Missing incident evidence remains an
uncertainty, not permission to invent a reproduction or claim the whole defect fixed; do not repeatedly request
evidence the person has already said is unavailable.
