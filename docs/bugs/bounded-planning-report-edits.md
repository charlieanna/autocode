# Bounded planning report edits (#2421)

The completed paging trial `20261028-090745-chat-dff300` exposed a costly
representation problem: removing one unexpected root `acceptance_test` field
required a 3,694-output-token replacement report. That repair took 47.469 active
seconds (32.936 seconds between native request/finish timestamps). A separate
missing Requirements assumption category repair took 33.824 active seconds.
The retained bundle manifest is
`5427ff918462616b900b5d27f9812c8d4eccf05c24e0dc900635ba5f7effb0b7`.
These are observed old-path durations, not claimed savings.

For complete, non-truncated JSON objects, Requirements and planning revision
report-only attempts now offer an optional `repair_report` object: the exact
rejected artifact SHA-256 and at most 32 disjoint add/remove/replace operations.
The core applies these edits to the pinned artifact and then performs the same
complete validation. It preserves the provider's original edit response beside
the reconstructed report. There is no root replacement, array insertion/removal,
or implicit correction of semantic content. A stale artifact, conflicting edit,
wrong stage or mixed full-report/edit response is rejected.

Existing full reports still work. Malformed/truncated sources and other stages
keep their existing repair protocol. No saved state keys, routes, budgets,
retry allowances, human gates or verification stages change. The optional
wire schema is saved with the attempt; the original required fields are restored
for complete validation, including on later loads.

The public CLI regression replays the observed unexpected-field failure and
reaches exact plan approval with one short response. Negative controls refuse a
stale source hash and weakened protected requirements. Separate loader tests
cover schema failures, conflicting paths, array operations and raw-response
retention. This establishes correctness of reconstruction, not live latency or
that a model always chooses the shorter representation.
