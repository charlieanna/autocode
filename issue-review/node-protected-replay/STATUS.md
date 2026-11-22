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
