# Retained candidates after reapproval

A joint task had already applied its component candidates when a corrected plan
received fresh approval. Its next Builder correctly left the source unchanged.
The runner found the original Builder snapshots but rejected their assignment
because reapproval changed the contract hash. Independent diagnosis stopped the
run rather than inventing a new source edit.

Equivalent scope in the same turn can now retain that candidate for fresh review.
Both approvals must authenticate against their sealed contracts and historical
events. Milestones, criterion IDs, owned paths, constraints and permission
boundaries must match; original snapshots and the exact current source identity
must still hold. Missing evidence, changed ownership, another turn and rolled-back
work remain refusals. No validation, completion or earlier approval is inherited.
Every current criterion and mandatory runner proof still requires fresh checking.

Historical approval authentication lives in the existing contract-identity
module, shared with acceptance-test provenance. The public fake-provider
regression reapproves an implemented task, performs a genuinely no-op second
Builder and requires fresh final proof without an artificial edit. The same
flow stops at Builder classification before this fix. The retained broken-source
control still requires fresh validation and cannot complete.
