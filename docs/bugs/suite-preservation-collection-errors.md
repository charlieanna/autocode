# Suite preservation must have executable evidence (#479)

The verifier compared candidate failures with baseline failures and accepted a
suite whose only result was the same import error on both sides. unittest's
synthetic `_FailedTest` counted as one reported result even though no test
passed. A separate valid arithmetic regression then made the combined proof
say PASS, incorrectly treating the existing suite as preserved.

The comparison now records UNVERIFIED when either suite has collection errors
or the baseline has no passing tests. It still compares named failures, so an
observed regression remains FAIL. Passing baseline tests alongside an ordinary
pre-existing assertion failure remain usable preservation evidence.

Seven real-process controls cover collection-only, mixed passing/collection,
all-failing and all-skipped baselines, a healthy suite, a passing suite with a
pre-existing assertion failure, and a named regression alongside a collection
error. Four controls failed before the change and all seven passed afterward.
Two older proof-seam fixtures had empty baselines. They now include meaningful
existing behavior; their PASS and fail-to-pass assertions remain, with added
pass-to-pass assertions. The corrected focused run passed 20 tests. The first
337-test gate retained the two fixture failures. The corrected 337-test gate
passed those tests but hit three unchanged 60-second CLI deadlines in
`test_check_replay` and `test_verification_config`. A targeted rerun of all three
passed in 196.699 seconds with the original deadlines and retained diagnostic
artifacts. The earlier timeout failures remain recorded; heavy host contention
was observed, but its causal role is unproven. The supplemental fake catalog
finished with 60 PASS, one existing NOT_EXERCISED and one live-only SKIPPED.

## Live evidence and limits

Fresh OpenCode 1.18.33 run
`20261005-101136-fix-calc-add-a-b-to-return-the-mathematical-sum--efb623ca`
used GLM 5.3 for requirements, planning and implementation, and GPT-6 Sol for
review, testing and completion. Three runner proofs reported PASS despite
collection errors and zero passing suite tests on both sides. Session exports,
source/runtime/provider pins and unmodified native streams confirmed the
reproduction. The protected suite was unchanged; 361 independent arithmetic
checks passed, and no owned worker remained at final audit.

That campaign did not complete successfully. It retained two 360-second Tester
timeouts and a corrected invalid evidence path. An outer 1900-second driver
timeout interrupted a verification check; after confirmed worker exit and
inspection through public status, the same run continued without changing its
limits or history. It then paused at the original active-time boundary after an
in-flight stage, with 1860.389 seconds recorded against the saved 1800-second
limit. The bug is reproduced; the campaign is not a successful qualification.

The fresh after-fix run
`20261005-115926-fix-calc-add-a-b-to-return-the-mathematical-sum--e6b07d10`
qualified the safety rejection on runtime commit `471d66c3`. It used the same
brief, protected suite, model profile and limits. The runner reported UNVERIFIED
with both collection-error reasons and the zero-passing-baseline reason, while
four arithmetic regressions genuinely failed on the original code and passed
on the fix. Both guards passed on both versions. All criterion-to-test bindings
were present. The run stopped WAITING_FOR_USER without claiming completion; its
request to change how the unavailable suite is treated remains unanswered.

The final audit passed: 361 independent arithmetic checks and keep()==9, only
the two authorized source paths changed, protected files unchanged, ten native
sessions exported (GLM 5.3, GPT-6 Sol and GPT-6 Astra), complete unmodified native
streams, unchanged runtime/provider/driver pins and limits, and no owned workers
remaining. Active time was 1534.234 seconds within the original 1800-second cap.
The setup's pre-existing untracked .venv symlink was separately checked against
its exact original target and was not counted as a model edit.

This was not frictionless: Homebrew git and shasum hit sandbox library denials;
the Builder used system git, and the shasum tool timed out. The Builder still
finished within its 360-second stage limit. A completion report requested a user
decision with an inconsistent verdict, was rejected, and was corrected in one
report-repair attempt. Host contention was recorded. None of these events was
erased or repaired manually. This proves the scoped refusal to certify an
uncollectible suite, not successful completion of that deliberately blocked
task or reliability across all providers. Run output remains under ignored
`.scenario-runs/verification-479/`, not committed.
