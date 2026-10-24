# Diagnosis and criterion tests written twice (#1969)

The live TUI bug-fix bundle has manifest
`0c4b826a6a99af7880af8000f925f0d417b58e9ee02955c00293a241918f69df`.
The acceptance writer received diagnosis T1 with an ID-based naming placeholder,
and approved criterion AC1 with the explicit name `test_t1_mixed_values`.
Both obligations remained due, but their generation instructions diverged.
Five behaviors were consequently delivered under two names each.

The domain helper now finds an approved name only when the existing identifier
matcher binds it to exactly one diagnosis case, its restore/preserve kind agrees,
and no competing name or combined-case binding makes it ambiguous. It does not
compare English text. The routing step pins these names, with the contract hash,
for a newly dispatched acceptance job. Tester and Builder receive the same names.
The report still accounts for every diagnosis and criterion ID; the runner checks
each result. One exact function can prove both IDs without duplicating its body.

Old in-flight jobs have no pinned bindings and retain their old naming rules.
Bindings from a different contract are not reused. Existing receipts and protected
files are neither rewritten nor discarded. Independent plan cases remain due;
missing cases and passing reproductions still stop the run. Tester and Completion
Reviewer remain separate.

The focused packet regression fails on pre-change master. The public CLI positive
case delivers three new test functions for five ledger obligations, checks their
real fail-first/preserve results and completes. Negative cases prove that an
unmapped missing obligation and an already-passing reproduction cannot disappear
behind a shared binding. A fresh default-adaptive live trial is retained separately
from the earlier classic-flow trial; its final qualification is recorded on #1969.
