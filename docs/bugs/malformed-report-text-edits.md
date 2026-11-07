# Malformed planning JSON and bounded correction (#2428)

Live TUI run `20261028-165106-chat-887793` completed the paging task, but
preapproval work took 720.624 active seconds. Two repairs returned identical
malformed 10,611-byte reports: the contract body lacked its outer wrapper and
closed before the remaining report fields. They took 37.551s and 36.585s. A
91.680s Investigator identified the missing wrapper; a 72.683s revision followed.
The saved bundle manifest is
`341f2724ea0e71594c64377b1427685131938f0b0a00212ce3f485032def383d`.

The existing bounded object-edit format required parsed JSON, so this syntax
failure still required copying the whole report. Requirements and revision
report-only repairs now also offer `repair_report.text_edits` for complete
malformed JSON beginning with an object. The model names the source SHA-256 and
supplies exact `find`/`replace` strings. Each match must occur exactly once in the
original text; overlapping matches are rejected. At most 32 replacements and
4096 UTF-8 bytes across their strings are allowed. The reconstructed report stays
under 128 KiB and must parse as one object. Unedited text is preserved exactly.

This is an optional output encoding, not automatic syntax guessing. Full reports
and existing object edits remain compatible. Empty, truncated, prose-only or
other-stage inputs do not get this offer. The raw response and original source
are retained. Normal schema, source/provenance, evidence, protected requirements
and approval validation still run. No retry counts, routing, stage selection,
user gates or limits change.

A manually authored 306-byte patch reconstructs the retained 10,611-byte response
and passes its saved wire schema. A shorter ambiguous match was correctly
refused; the patch must include enough context to be unique. This measures
representation size, not model speed or automatic task acceptance. The public
CLI regression uses the same missing-wrapper shape and reaches exact-plan
approval with one repair and no Builder. The same test on unmodified master
fails because text edits were not offered. Negative controls prove changed hash,
ambiguous text and protected-requirement weakening cannot reach approval.

The live model's adoption of the encoding and end-to-end latency remain separate
qualification work. This fix does not claim that the full #2113 latency gap is
closed or justify omitting Tester or Completion Reviewer.
