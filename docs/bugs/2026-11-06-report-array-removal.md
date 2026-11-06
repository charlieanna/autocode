# Remove an extra report item without rewriting its array

A large Tester report contained every required per-line decision plus one entry
for a line that was already tested. Admission correctly rejected that extra
entry. The bounded edit format could remove object fields but not array items,
so correcting one item required retranscribing the entire decision array. The
report repair exhausted its response budget while doing that rewrite.

Report edits now permit removal of existing array items with a null value.
All paths address the original pinned report: replacements happen before array
removals, and removals within each array run from the highest index down. This
keeps later edits bound to the original entries regardless of edit order.
Overlapping paths, duplicate targets, insertion, non-null removals, wrong index
types and out-of-range indices remain refused. The input artifact and its hash
stay intact, and the reconstructed report passes the existing schema, evidence
and protected-contract checks before it can be accepted.

Regressions cover a single small edit to a large report, mixed nested edits in
every order, retained checks, invalid operations, and the public CLI completing
with an exact-source-bound array removal. The selected model, reasoning level,
output cap and report size limits are unchanged.
