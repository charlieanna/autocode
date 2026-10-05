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
refund, no grant is reserved. Grants saved before this change are not reconciled
or rewritten. Their bindings and evidence hashes still fail closed. Adaptive and
explicitly capped runs reach the boundary too: they are refunded there exactly as
admission would refund them, and still never reserve.

Leaving runner timeout events and refund markers out of the grant hashes would
also have avoided the stop, but it would hide a grant that no longer has a call
to fund. The issue asked not to rewrite grant hashes, so the order changed instead.

A second, separate rule: a refunded attempt no longer counts as an ordinary
attempt that can fund a grant, so one failed review earns a refund or a grant,
never both. The #453 fix does not need it. Without it, a refunded timeout
followed by reviews that reported and spent the allowance still funded one more
review, which admission consumed at once. That extends the allowance, which the
refund policy (`docs/workflow.md`: "Bounded recovery does not otherwise extend
the allowance") rules out, so such a run now stops at `PAUSED_PLANNING_BUDGET`.
Maintainers can drop the rule by removing the `planning_review_refunded`
condition in `operational_boundary`.

## Consequence for planning recovery

Under the refund policy every ordinary review call carries a live charge, so the
boundary refunds a failed one before computing eligibility, and a refunded call
cannot fund a grant. Grants therefore come only from review calls admitted before
charge IDs existed, which stay charged. For runs started under the refund policy, the
planning recovery reservation, grant consumption, `MAX_PLANNING_RECOVERY_GRANTS`
and the reviewer route fallback (`autocode_reviewer_fallback`, which needs a
grant-funded second silent attempt) are unreachable. A throwaway fuzz of 240
random challenge/finalize sequences over the real boundary and admission minted
no grant without legacy rows and stranded none; before the fix the same
sequences stranded grants even without legacy rows. Repeated silent reviews in
new runs stop at the ceiling of three automatic recoveries
(`PAUSED_TIMEOUT_RECOVERY`) and need `--grant-recovery N`. `docs/execution.md`
says so.

## Regression coverage

`tests.test_finalizer_timeouts` drives the real CLI, admission, refunds, archive
and recovery with an offline provider and a simulated idle deadline. Two silent
final reviews followed by a report reach `AWAITING_GOAL_APPROVAL` with two
review calls used and no grant. The run refuses the stale plan token and builds
nothing before explicit approval. Continued silence stops after three final
reviews and launches no fourth. `tests.test_operational_recovery` covers the
boundary order, the refund-or-grant rule, and sealed grants that still fail
closed when source, evidence or inputs change.
