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
