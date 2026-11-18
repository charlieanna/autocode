# Expired decisions still offered their old confirmation

A live VS Code check upgraded the core while a Requirements confirmation was
waiting. The runtime change correctly expired its exact request token. The next
status response reported `RESOLVER_PENDING` and no authorized human request at
the root, but its `view` still reported `WAITING_FOR_USER` and offered the old
confirmation. Clicking it was correctly refused by the writer. An unpublished
Resolver proposal could also appear as an answer without a published token.

The shared cause was that presentation read saved questions independently of
the Resolver's current authorization projection. The same mismatch affected run
inventory, attention, wait and event readers. Runtime, source, settings and task
changes all reproduce it without a model.

All readers now use the same read-only decision projection. An expired published
request or unpublished proposal names ordinary continuation so the writer can
re-evaluate it. Status samples that projection once for both the envelope and
editor view. The original checkpoint still binds controller observations and
tokens; readers never reissue, consume or approve a decision. Legacy questions
without a Resolver publication retain their existing contract.

VS Code takes this exact named refresh once per decision context, as it does
metadata reconciliation. A missing or foreign command cannot trigger it, and a
newly published question or plan still requires its own answer or approval.

Regression coverage exercises questions and plan approval, several staleness
causes, unpublished proposals, consistent status sampling and the inventory,
attention, wait, event and editor consumers. Existing exact-token rejection and
stop-precedence tests remain required.
