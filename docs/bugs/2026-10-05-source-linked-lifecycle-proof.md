# Source-linked process-recovery proof (#451)

On pristine master `1192fa558faee0cbdcb8aa89daf787e22ac83498`, the existing
fake scenario harness completed both honest references (oracle 6/6 each), but
also completed `ladder-18-durable-lease-queue/broken/process-local-tokens` and
`ladder-19-transactional-outbox/broken/ack-with-exception-rollback` (external
FALSE_COMPLETE, 5/6 each). Both mutants passed their supplied tests and the
runtime's independent ordinary check replay. These are planted counterexamples,
not live-model failures or a measured failure rate.

The first bounded guard recognizes disclosed Python lease queue and
transactional outbox APIs with the required signatures and lifecycle promises.
It derives their class/method roles from authenticated human declarations and
lets the independent Plan Reviewer select an original public module and
criterion IDs. Original public imports and source are captured before the
Builder runs; subsequent candidate edits cannot rebind that inventory. The
runner-owned record is `goal_contract.body.risk_acceptance`, under the existing
approved contract hash, with pinned human sources, raw reviewer reports/events,
initial target inventory and the fixed protocol version. A recognized family
with missing facts remains unverified; it does not silently acquire assumed
methods, expected values or stronger guarantees.

The queue protocol kills its first worker after enqueue/claim returns, opens a
fresh interpreter at the exact injected lease deadline, verifies fresh tokens
and stale ack/nack rejection, then uses a third interpreter to finish both jobs
while preserving idempotency. The outbox protocol records and fsyncs actual
sink callbacks in the parent, kills the publisher at the middle callback before
it returns, and verifies the stable pending suffix and bounded at-least-once
retry in a fresh interpreter. It permits the interrupted event's duplicate
sink delivery; it makes no exactly-once claim.

The existing clean scratch replay executes these fixed commands. A shared cap
of 30 seconds is contained within the supplied replay allowance, covers all
selected cases and reserves worker cleanup. At most four workers are owned
(three sequential workers in each current protocol), with shared output and
IPC/transcript limits. Actual SIGKILL exits, distinct worker PIDs, phase order,
public return values, durable sink bytes and owned-worker reaping are retained
and revalidated. The supervisor uses isolated standard-library imports and
preflights candidate module paths before importing them. No holdout test,
database-internal assertion, real-time sleep, detached worker, new model route,
second controller or allowance replenishment is introduced.

Contribution-only progressive slices defer these cases; due full-verification
criteria run them. A whole-product technical PASS claim runs all protected
observations through the same replay, even when the current task lists fewer
criteria. Completion requires full current source/contract/manifest/target
identities and intact pinned transcripts; stale, omitted, forged or changed
proof cannot count as verified delivery. Failures retain their actual bounded
protocol reason and use existing report rejection/rework. Public status exposes
`evidence.check_replay.risk_acceptance`.

Separate fake and real-process controls pass both references and reject both
self-consistent mutants for token reuse or lost pending events. The independent
catalog holdouts remain separate and use different repeated failure schedules.
This guard covers the two disclosed API protocols, not arbitrary natural-language
risk, parallel contention, general security or performance guarantees. Live
model qualification is still owed in the draft PR. Diagnosis quality is
separate work tracked in #59.
