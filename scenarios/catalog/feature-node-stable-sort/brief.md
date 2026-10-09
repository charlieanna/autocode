Extend the existing dependency-free CommonJS module `src/records.js` with
`sortByScore(records, direction = 'asc')`. Preserve the existing `byId` export
and all its tests.

Each record has a string `id` and finite numeric `score`. Return a new array
ordered numerically by score, ascending by default or descending when
`direction` is `'desc'`. Equal scores retain their original input order in
both directions. Keep the original record objects and do not mutate the
caller's array or records. Empty input returns a new empty array.

Reject a non-array input, a non-object record, a non-string id or a nonfinite
numeric score with `TypeError`. Reject any direction other than `'asc'` or
`'desc'` with `RangeError`. Keep the package dependency-free; do not add a
different runner or change the CommonJS API.

Add native `node:test` cases under `tests/`, using
`test_c1_numeric_score_order_in_both_directions` for numeric ordering and
`test_c2_stable_ties_and_caller_data_preserved` for stable ties and preservation.
These cases must fail on the original module, where `sortByScore` is absent,
and pass with the feature. Run `node --test tests/*.test.js`.
