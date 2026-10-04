# A flow that ends in human approval never reached review (#195)

A live mixed-model run on `6d640cca` approved an end-to-end flow whose last step
was the user's approval. The Validator correctly reported every technical
criterion PASS, the human criterion NOT_VERIFIED and the flow NOT_VERIFIED. The
human-only gap (`autocode_goals.human_only_pending_validation`) required the
flow to PASS, so the runner never replayed the checks, never offered the
structured artifact review, and the Completion Owner sent the unchanged artifact
back through four Validator attempts before a model-written permission question
appeared.

The gap now also holds when the flow is NOT_VERIFIED, cites evidence for its
executed steps, and names every pending human-review criterion as a whole token
in its summary (`flow_awaits_only`). The Validator instructions ask for exactly
that. The gap also accepts an overall verdict of PASS as well as BLOCKED: in the
original run the GLM 5.3 Validator reported PASS three times and BLOCKED once with
the same pending rows, and a live rerun of that task on this fix's first version
(BLOCKED only) reported PASS and looped again. Everything else is unchanged:

- A FAIL flow, a flow without evidence, or a summary that does not name every
  pending human criterion (`C10` does not name `C1`) is not a review gap.
- Technical criteria, executed checks, the check replay, findings and source,
  contract and evidence freshness are checked through the same gate as before.
- Completion still needs the user's approval bound to that exact validation. The
  flow's human step is satisfied by that approval, not by the Validator, and the
  completion report says so.

`tests/test_artifact_review.py` drives it through the CLI (one Builder, one
Validator, review, approval, completion) and checks that an unexplained flow gap
is not offered as a review. Before the fix the same CLI case validated twice and
stopped at the iteration ceiling.
