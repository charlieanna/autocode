# A flow that ends in human approval never reached review (#195)

A live mixed-model run on `6d640cca` approved an end-to-end flow whose last step
was the user's approval. The Validator correctly reported every technical
criterion PASS, the human criterion NOT_VERIFIED and the flow NOT_VERIFIED. The
human-only gap (`autocode_goals.human_only_pending_validation`) required the
flow to PASS, so the runner never replayed the checks, never offered the
structured artifact review, and the Completion Owner sent the unchanged artifact
back through four Validator attempts before a model-written permission question
appeared.

The gap also accepts an overall verdict of PASS as well as BLOCKED: in the
original run the GLM 5.3 Validator reported PASS three times and BLOCKED once with
the same pending rows, and a live rerun of that task on this fix's first version
(BLOCKED only) reported PASS and looped again.

The initial #312 implementation accepted NOT_VERIFIED flows when their summary
named every human criterion. Review reproduced a false completion: the approved
flow included installing a CLI and invoking it from another directory, and the
Validator explicitly reported those steps unexecuted alongside pending C1
acceptance. The summary's C1 mention still offered review and allowed completion.

For a NOT_VERIFIED flow, `flow_awaits_only` now requires separate structured proof:

- `end_to_end_result.technical_result`: PASS, a nonempty summary, and evidence_refs
  for **all technical steps** of the approved flow. An unfinished technical step
  is NOT_VERIFIED; a verified technical defect is FAIL.
- `end_to_end_result.pending_human_criteria`: exactly the outstanding human
  criterion IDs, each once. Summary text does not establish this set.

The Validator writes these report fields; Autopilot resolves their check references,
authenticates executed events and pins their evidence. The existing presentation,
approval, completion and milestone gates read the proof through
`human_only_pending_validation`. There are no new top-level run-state keys.
Ordinary flows use `technical_result=null` and `pending_human_criteria=[]`.
Saved PASS reports keep working. Older NOT_VERIFIED reports without this proof
must be validated again; prose alone cannot be carried forward as technical proof.
New reports cannot override incomplete technical proof with an overall flow PASS,
and an explicit technical FAIL requires the flow itself to FAIL.

The remaining guards are unchanged:

- A FAIL flow, a flow without evidence, an incomplete technical result, or an
  inexact pending human set (`C10` is not `C1`) is not a review gap.
- Technical criteria, executed checks, the check replay, findings and source,
  contract and evidence freshness are checked through the same gate as before.
- Completion still needs the user's approval bound to that exact validation. The
  flow's human step is satisfied by that approval, not by the Validator, and the
  completion report says so.

`tests/test_artifact_review.py` drives it through the CLI (one Builder, one
Validator, review, approval, completion) and checks that unexplained and mixed
technical/human gaps are not offered as reviews. Proof references use the same
event authentication and evidence-freshness checks as other validation results.
The structured-proof change has fake-provider regression coverage; the earlier
live run did not exercise these new fields.
