# Check the reply contract that the scheduled model actually receives

The live VS Code follow-up sent a correction through the saved-message boundary.
Requirements received the correct four-field conversation schema, but its
advertised report-check command required full Requirements coverage rows that
the schema forbids. The model observed the contradiction, emitted the short
reply, and the real conversation owner correctly accepted it. This is a false
pre-submit failure and wasted model work, not permission to weaken that owner.

The cause is selecting semantic rules and public evidence context only by the
scheduled stage. Requirements and plan-review roles also answer saved-message
conversations. The checker now recognizes that identity-bound wire contract
from the actual required message, reply, intent and gate/contract fields. It
still validates every schema restriction; full-stage evidence rules apply to
full-stage reports. A normal Requirements report's assistant_reply field alone
does not select the conversation protocol. No provider-specific annotations or
new model-facing fields are added. Existing specs carrying the same schema
also use the corrected interpretation.

Before changing product code, the generated-command regression reproduced the
coverage contradiction for Requirements, saved-plan and live replies, and
unrelated retained-plan context for plan-gate and coordinated replies. All five
variants pass after the shared correction. Invalid intents, field types,
extra full-stage fields and missing replies remain refused. Existing quote,
coverage, citation, retention, stale-binding and approval tests stay in the
focused validation. All 83 focused tests and the affected selection's 365 tests
in 19 modules pass. Full Mypy passes for all 961 source files. CI and live
verification evidence are maintained in the PR.
