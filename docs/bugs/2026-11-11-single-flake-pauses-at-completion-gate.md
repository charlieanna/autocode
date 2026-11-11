# One flaky test failure stops a run at the completion gate

**Seen:** 2026-11-11, while adding the completion writer for project notes (#2943), with the
offline fake provider. **Fix:** #3197, then the flake settlement (#2942).

## What happens

A bug fix whose repository already has a flaky test:

1. The Builder writes the fix. The runner's regression proof of that source fails because the
   flaky test fails once on the candidate ("Tests that pass on base fail on the candidate").
2. The proof failure opens a blocking runner finding with that source in `failed_revisions`
   (`autocode_proof_findings`, #2475).
3. Before the Completion Owner reviews, the failed proof is replayed once on the same source
   (#2065). The replay passes.
4. The Completion Reviewer sees a passing proof and asks to complete. `_send_back_unproven`
   does not send it back, because the proof now passes. But the finding stays open: it closes only
   when the proof passes on a source it has not failed on.
5. The completion gate refuses because of the open finding, and the run stops at
   `PAUSED_COMPLETION_GATE` with `--resume-paused` as its next action. That is a stop about
   AutoCode's own machinery, which AGENTS.md treats as a defect.

The run reaches this stop with the stages `builder, regression_proof, tester, regression_proof,
review_completion`. A reviewer that returns REWORK instead gets past it, but only by making the
Builder change the source.

## Fix

#3197 keeps the finding open, because the test was not seen failing on the original code, so it is
not a recognized flake. A completion request on that source is now sent back once, with a note
naming the intermittent failure, so the Builder makes the change or the test reliable. A second
request goes to the Investigator through the existing `PAUSED_COMPLETION_REVIEW` route. The run
no longer stops at the gate.

A live Kilo run on master with that fix then showed the send-back was not enough for a test the change
does not touch. The Builder may not edit a protected test, so the Investigator advised disarming it,
and the run paused at `PAUSED_BUILDER_OPERATIONAL` until its wall-time bound. The runner now settles
the finding itself (`autocode_proof_findings.flake_settled`, #2942). The failing proof's only failure must
be suite tests that the flaky-test recheck saw fail once and then pass on every rerun. The runner's later
proof of the same source must pass. The settlement names both receipts, and the send-back remains for
every other failure.

The completion writer in #2943 does not depend on that fix. It reads the replay pair from the
proof history whichever later source the run delivers, so a run that is sent back after the
replay still keeps the flaky test as a project note.
