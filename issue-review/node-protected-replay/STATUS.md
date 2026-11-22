# Protected Node replay (#3959)

On master bf75a2c9 with Node 22.23.3, the original-test replay did run the
package's own build, and a normal superseded assertion was correctly excused.
When the replaced test left a Promise pending, Node named it and the next test
as cancelled. The gate treated both as failed. The failing comparison is
retained at /private/tmp/autocode-3959-node22-master.log.

The single-command Node recheck keeps the original receipt, filters only the
original failed tests the approved plan replaced, and runs from a fresh
retained overlay using the rest of the original check's allowance. Completion
requires the same supported launch, a pinned Node report, and fresh passes from
every cancelled original test. Genuine failures and changed report files stay
refused. Three actual local Node 22 comparisons passed, including build reuse,
recovery after cancellation and a real failure found on recheck. The adjacent
reporter, architecture and editor qualification still need to finish. The
shared source-link boundary is outside this change.

The full public CLI preflight found a difference from direct replay. The
Tester scheduler puts captures in an obligation's exact published attempt
directory, outside the pair's report directory. The new case fails on the
previous candidate and passes once the scheduler's immutable receipt binds
the capture, command and per-test rows. Completion rechecks both attempts
and their captured reports. A modified scheduler receipt or report holds.
The retry's remaining time and execution are included in its scheduling
record and summary. All 32 focused Node, reuse and architecture tests pass,
along with full Ruff and mypy.

The updated offline fixture reaches the real protected replay and passes
its named regression proof. It subsequently holds at changed-line evidence
because the fixture's npm build chain cannot supply a proving-test mutation
selection. That fixture will need its exact scripted Tester judgment or a
demonstrated mutation selection before the editor can verify completion.
The actual VS Code outcome and required CI are still outstanding.

The actual macOS Extension Host exposed one more cause. Node's parent
test invocation marks its child environment, and a fresh runner inherited
that marker, exited zero, and wrote no report. The old protected gate
recorded PASS even though the full task stayed held. The runner now
removes the invocation-only marker at the shared test boundary, and the
outcome reader requires the expected report regardless of exit code.
Completion rechecks the same outcome for previously retained pairs. The
two focused controls failed before the fix. The full affected selection
passes all 81 tests and mypy passes.

The actual VS Code rerun used the reviewed fixture and fixed Node 22
toolchain. It clicked Approve, verified the genuine named cancellation in
the first original receipt, verified the untouched original test passed
under a different scheduler attempt, and reached the verified completion
card. The host test and fixture cleanup passed in 35.6 seconds. An earlier
editor retry inherited Node 25 and was stopped through the named action
after its subprocess stayed in the pending-promise original test. That
attempt is not Node 25 qualification. The candidate still needs its
exact-head required CI.
