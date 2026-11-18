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

The live resume also exposed a related display race: a checked live controller
can still hold its saved pause while applying the next action. Core correctly
suppresses further actions, but the editor treated that absence as a client gap
and asked the person to continue again. `progress.continuing` now carries the
core's fresh observation explicitly. The editor shows ongoing work and takes no
additional action at that boundary.

The same observation covers a closed attempt still saved as `RUNNING` while its
live controller archives or validates it. A missing accepted report previously
made that transition appear abandoned. Closed-attempt metadata plus the fresh
controller observation now suppress that stale claim; an unfinished attempt
without a closed record, an unknown controller and durable workers retain their
existing handling. This changes display and action suppression, never report
acceptance or retry authority.

Regression coverage exercises questions and plan approval, several staleness
causes, unpublished proposals, consistent status sampling and the inventory,
attention, wait, event and editor consumers. Existing exact-token rejection and
stop-precedence tests remain required.

The public TaskRun client also takes the exact named continuation when it has
no separate `needs.action` field. Previously that plain refresh fell back to
relaunching with every start option; a chat started with `--single-model` then
refused its refresh because that flag applies only to a new run. Foreign named
commands remain refused, and a refreshed question requires its own answer.

The corrected path was also exercised in the actual extension while a live
GLM Requirements confirmation waited. Installing the candidate core expired
the old request. VS Code took one named continuation and displayed the same
Requirements under a new authentic request; attempts, tokens, cost and planning
calls stayed unchanged. It submitted no confirmation. The operating agent then
reviewed and confirmed the fresh Requirements, and the editor showed the
subsequent planning work without an unavailable-action error.
