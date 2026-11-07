# Whole-contract revision churn in the paging implementation trial

The completed TUI run `20261028-064449-chat-6f5c81` used Codex Medium and GLM Low.
Bundle manifest: `83241cb23f2306927029014d07d892f1929389a659220be85c61f031f308c81c`.
It delivered the correct paging fix: 10 delivered and seven independent tests pass;
both suites fail against the original source. All failed attempts remain included.

It took 2,137.768 seconds of stage-active work, versus the retained direct Kilo
implementation's 29.219 seconds elapsed on the same original source, brief and
independent oracle. AutoCode's 4,521.588 seconds elapsed also includes human gates,
patch-installation and restart gaps; it is not all model time. This is one sample,
with different planning/review workflows, not a statistical benchmark.

Before test writing/Builder, AutoCode spent 1,791.411 active seconds (83.8%). Three
full revisions totaled 839.229 seconds; five revision repairs 304.421 seconds.
One full revision omitted acceptance criteria; another returned an empty trace
and needed a 64.936-second repair. The interface required full-contract rewriting
although only localized engineering changes were needed. Existing criteria deltas
and final-review references did not retain the rest of a Planner revision.

## Scoped correction (#2374, follow-up to #2113)

The default Planner revision can now use `contract.revise_contract`, bound to the
exact current revision/hash, and a `changes` object containing changed engineering
fields only. Criteria use the existing criteria delta. Explicit
`retain_requirement_trace: true` reuses only a validated matching predecessor trace.
The core expands these references before normal schema, source coverage, proof,
change-authority and independent-review checks. Changed criteria still undergo
trace coverage; references do not authorize weaker proofs or missing criteria.

Protected behavior/permission changes use the existing full report and authority
receipts. The full-report form remains compatible; pinned old repair schemas are
never instructed to use the new protocol. No role, model, effort, budget, approval
or verification-stage policy changes.

Deterministic Linux CLI tests cover sparse revision to the exact human approval
gate without a repair, one unrelated repair preserving the reference, and refusal
of stale references, protected edits and removed criteria. The same positive test
on pre-fix master pauses after repeated missing-field failures. These tests prove
protocol behavior, not that a live model will converge faster. Live timing remains
unproven until a new matched TUI run reaches the path.
