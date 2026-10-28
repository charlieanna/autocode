# Acceptance proof needs execution, not report-only repair

A live joint task's planned test class did not match the individual test results,
and its guard binding did not name the function actually collected. The runner
correctly refused the proof. It then incorrectly launched report-only repairs,
which cannot edit test files or rerun a corrected proof.

Missing collected cases, nonpassing guards, runner errors and missing per-test
results now use the existing execution-evidence failure classification. They
retain the attempt and stop at the existing acceptance-proof hold, offering
plan feedback or explicit fresh Writer execution.
Saved invalid acceptance-Writer reports also expose named plan feedback, under
the same pause-authority guard used for planning corrections. An explicit resume can rerun the acceptance Writer; this grants no plan approval,
selector change, proof waiver or replay of a Builder. Metadata-only errors still
use bounded report repair, including an omitted already-passing declaration when
the runner already observed the claimed behavior.

The Linux public CLI regression deliberately renames an acceptance function so
it is not collected. Master dispatches two ineffective report repairs; the fix
retains a single Writer attempt at the hold. Explicit recovery writes the proper
test, preserves the approved contract token, establishes fail-before/pass-after
and completes through the ordinary independent checks. This is deterministic
runtime evidence, not a claim that the live multi-repository task has completed.
