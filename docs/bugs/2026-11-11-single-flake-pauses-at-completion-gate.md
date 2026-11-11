# One flaky test failure stops a run at the completion gate

**Seen:** 2026-11-11, while adding the completion writer for project notes (#2943), with the
offline fake provider. **Open:** belongs to the flake policy in #2942.

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

## Why it is not fixed here

The two rules disagree. #2065 replays a proof so that one flake does not drive REWORK. #2475
keeps a finding open on every source it failed on, so that a later pass cannot hide a failure
that comes and goes. Deciding between them, for example by routing the open finding to the
Builder or the Investigator, or by settling it on a recognized flake with its receipts, is the
flake policy #2942 asks for. Either way the run must not stop at the gate.

The completion writer in #2943 does not depend on that choice. It reads the replay pair from the
proof history whichever later source the run delivers, so a run that is sent back after the
replay still keeps the flaky test as a project note.
