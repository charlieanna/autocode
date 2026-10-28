# Generated audit decisions discarded by planning (#2440)

The final Plan Reviewer in paging TUI run `20261028-211621-chat-154a01`
(Codex Medium / GLM Low through Kilo; master `b1e1fb3ab`) took 79.191 seconds.
It answered real concerns C1/C2 but also emitted AUDIT-PROVENANCE,
AUDIT-EXAMPLES-PROOFS and AUDIT-HANDOFF. Those synthetic rows were 2,968 of
5,837 minified UTF-8 report bytes. The existing consumer discarded them before
saving decisions. Bundle manifest:
`c27a115df40e368c516d47088af44895bd2667b1fc603a02097544272998c079`.

The concern schema admitted arbitrary IDs, while the prompt required auditing
the full contract and returning independent decisions. The corrected generation
schema names the actual concerns and their count. The prompt separates complete
review/investigation from emitted answer rows. New blockers still use the
schema-defined question field; sparse revisions use the offered full-contract
form when needed. No planning stage or independent verification is omitted.

The pinned model schema also reaches the report loader. Tightening that schema
alone would reject legacy synthetic rows and cause the very repair call being
avoided. The existing validation projection therefore restores compatible concern
row acceptance for planning contracts only. Row content remains validated;
the original coverage check still rejects missing, duplicate or empty real
answers. The later decision guard rejects unresolved answers without blocking
questions; genuine blockers still reach the person. Raw provider evidence stays intact.

A saved-response-shaped regression passes strict model_output_schema output
through the actual report loader, retained-contract expansion, planning consumer
and public approval view. Original and concise reports preserve the substantive
plan and real decisions. Different raw-report hashes legitimately yield different
source-trace bindings and tokens; each displayed plan needs its own exact approval.
Neither an empty concern list nor resolved answers starts implementation. Separate
controls cover invented generation IDs, unchanged unrelated schemas, missing and
duplicate real answers, new blockers, stale references and architecture direction.

The generation regression fails on original master for all four v1/v2 revision
and final-review forms. This establishes reduced requested output and unchanged
authority, not measured model compliance or a proportional latency saving. The
four live planning/review calls took 413.900 seconds and corrected substantive
coverage gaps; #2113 remains open for that larger cost. No new paid comparison is
claimed for this protocol correction.
