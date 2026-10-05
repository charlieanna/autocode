# A refunded plan review left a stale recovery grant (#453)

A live fixed-pipeline trial stopped after two final plan review (`astra_finalize`)
idle timeouts with `Planning recovery scope or inputs changed; no provider will
launch`, although contract, settings, source and pinned evidence were unchanged.

## Cause

`autocode_resolver_runtime.operational_boundary` reserved a planning recovery
grant for the first silent review while the review still held its ordinary call.
Admission (`units/autoplanner.charge`) then gave that call back
(`refund_unreported`), so the retry ran as an ordinary call and the grant stayed
unused. The refund marked the grant's origin record, and the second timeout added
a runner event to the planning inputs, so the old grant no longer matched what it
had bound. The next boundary refused to launch anything.

The offline CLI reproduces this on master before the fix, but only for runs that
plan with the fixed pipeline (`--no-adaptive-planning`, and runs started before
adaptive planning became the default). Adaptive planning saves its sized review
allowance without `review_call_limit_origin`, and the boundary treats an unmarked
limit as protected, so it never reserves planning recovery for those runs.

## Fix

The boundary now gives back unreported ordinary calls before deciding whether
credit is exhausted, as admission would. When nothing is exhausted after the
refund, no grant is reserved. A refunded attempt also no longer counts as an
ordinary attempt that can fund a grant: one failed review earns a refund or a
grant, never both. Grants saved before this change are not reconciled or
rewritten. Their bindings and evidence hashes still fail closed. Repeated silent
reviews stop at the automatic recovery limit, three attempts in a row.

## Regression coverage

`tests.test_finalizer_timeouts` drives the real CLI, admission, refunds, archive
and recovery with an offline provider and a simulated idle deadline. Two silent
final reviews followed by a report reach `AWAITING_GOAL_APPROVAL` with two
review calls used and no grant. The run refuses the stale plan token and builds
nothing before explicit approval. Continued silence stops after three final
reviews and launches no fourth. `tests.test_operational_recovery` covers the
boundary order, the refund-or-grant rule, and sealed grants that still fail
closed when source, evidence or inputs change.
