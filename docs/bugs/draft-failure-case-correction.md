# Correcting an unapproved model failure-case inference (#2453)

The retained planning run `20261028-235553-chat-5162e6` stated that `page([], 1, size)`
raises `IndexError`, although the current implementation returns `[]`. Requirements
and the investigation had not asked to change that behavior. The first Plan Reviewer
challenged the draft's unsupported preservation statement. A direct guard replay of
only that exact replacement failed with “needs a saved user answer or feedback event”.
No person had approved an implementation plan. The exported run also had a separate
retained-reference/output failure; the revision repair must not be attributed solely to
this guard. Evidence is retained under bundle manifest
`d00a0029319fbac257bf29bba998704116980ff26144d024489a448885dbcd27`.

The first `astra_challenge` review can now supply an optional nullable
`draft_failure_correction` on a blocking concern. It contains an explicit independent
judgment that the line is an unapproved model inference, the exact before/after text,
evidence rationale, and a core-computed snapshot token. The token binds the sealed
draft, complete known draft history, human source ledger, answers, feedback,
Requirements handoff/history, diagnosis and saved design context. Existing workspace
freshness checks still apply. Feedback or changed requirements invalidate the receipt.

Only `glm_revise` can consume that current saved receipt, with the same declared
`agent_proposed` replacement. The failure-case list must otherwise be identical.
Other protected lists and existing criteria, proof methods and source citations must
remain; additive criteria and citations are permitted. Approved or user-edited
history, unknown provenance, recorded approval events, human-backed prior deltas,
exact human quotes and text owned by another protected field are excluded. These
checks do not prove natural-language ownership: absence of a quote is not evidence
that a requirement belongs to the model. The independent reviewer's semantic judgment
remains necessary and can be wrong.

The final review still audits the correction. Human approval binds the exact final
plan and shows the before/after, concern, rationale and draft/source snapshot. No
implementation authority is granted by a receipt. Legacy concerns without one grant
nothing, and repair prompts offer it only when the pinned schema supports it. An
already-paused run needs a fresh independent review; the core never synthesizes a
receipt from an older concern or a revision's own claim.

Deterministic tests in `tests/test_draft_failure_correction.py` exercise actual saved
wire-schema ingestion, planning admission, diagnosis metadata filling, final review,
exact approval, stale sources, malformed/protected lineage, human quotes, mismatched
pairs and attempted weakening. A mocked repair launch verifies both old and new
pinned schemas. The public TaskRun fake tests in
`tests/test_draft_failure_correction_cli.py` cover ordinary and adaptive review plus a
stale receipt refusal on supported Linux. These fixtures test the protocol and guards;
they do not establish model judgment quality, receipt adoption or a live latency gain.

## Composing an independently allowed assumption update

Live verification on merged `4f9064af4` exposed a second refusal. The fresh first
review emitted a valid receipt, and the revision applied the exact pair, but it
also updated an obsolete receipt-status statement in a model-written interpretation
of saved feedback. The existing feedback-interpretation rule independently permits
that update. Requiring the whole assumption list to be identical discarded the
valid receipt and misleadingly reported missing user authority. The following
repair calls added their own output-format errors; their whole duration is not
attributed solely to this composition check. Redacted evidence manifest:
`bc50be9a2f360258b56478dac2c52031f83ae243257a480791a54b266032785d`.

The revision guard now passes its already-computed interpretation corrections to
the receipt check. Only a recognized old interpretation may change text at the same
list position, with identical basis, source ID and all other fields. List length,
other assumptions, delegated decisions, protected lists and existing proofs remain
unchanged. The ordinary assumption guard still runs; the failure-case receipt
grants no assumption authority. A forbidden adjacent edit reports its actual
field instead of implying that the matching receipt lacks authority.

A public TaskRun regression reproduces the refusal on `4f9064af4` and reaches
disclosed exact-token approval on the fix, with zero repairs and no Builder.
Unit and wire-admission negatives retain rejection of changed sources, quoted or
human-backed assumptions, added/dropped/reordered rows, stale receipts and weakened
proofs. These checks do not yet establish a successful live continuation or a
measured overall speedup.
