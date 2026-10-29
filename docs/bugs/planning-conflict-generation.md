# Empty conflict-resolution generation (#2457)

In run `20261028-235553-chat-5162e6`, both Requirements handoffs recorded
`conflicts: []`. Discovery nevertheless emitted a `conflict_resolutions` row for
`R7`, treating reviewer concern C3 as a recorded requirement conflict. The existing
guard correctly rejected it: neither the current handoff nor eligible history
contained that conflict. This triggered an unnecessary 80.190-second report repair
before planning could continue. The repair removed the row. The clearer error
introduced by #1823 helped recovery, but generation still offered an unrestricted
resolution array.

Evidence is retained under bundle manifest
`bc50be9a2f360258b56478dac2c52031f83ae243257a480791a54b266032785d`.

The generation schema now sets `conflict_resolutions.maxItems = 0` when the
Requirements inventory and history are known, requirements are present, and no
eligible conflict exists. Generation and validation share the existing conflict
lookup: a historical resolution remains eligible only while its requirement IDs
retain the same nonempty source quotes. Real conflicts and unknown legacy contexts
keep their existing behavior.

Static ingestion schemas and saved schemas are unchanged. Existing checks still
require the exact recorded conflict, saved user authority, a verbatim event quote
and complete requirement coverage. The new wire constraint rejects impossible rows
earlier; it grants no authority and removes no planning, review or approval stage.

Verification passed 38 focused tests covering prepared wire schemas, current and
historical conflicts, changed quotes, legacy contexts, architecture and CI selection.
Two supported-Linux public TaskRun tests inspect the actual provider schemas: the
valid case reaches exact approval with zero repairs and no Builder launch; a
persistent invented resolution is refused before approval. The positive schema
assertion fails on the frozen baseline and passes with this change.

These are deterministic protocol results. Kilo receives the schema as instructions,
so a model can still violate it. Live model adoption and latency improvement have
not been established.
