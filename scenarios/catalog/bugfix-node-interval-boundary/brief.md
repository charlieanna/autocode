The dependency-free Node module `src/interval.js` exports `overlaps(left, right)`.
Each argument is a two-element array `[start, end]` of finite numbers with
`start <= end`. An interval is half-open: its start is included and its end is
excluded. Invalid intervals raise `TypeError`, as the existing tests require.

The bug report: `overlaps([1, 2], [2, 3])` currently returns `true`. These
intervals only touch, so the result must be `false` in either argument order.
An empty interval such as `[2, 2]` overlaps nothing, even when another interval
contains its point. Positive-length intersections must still return `true`,
including containment, negative endpoints and fractional endpoints. Do not
mutate either argument or weaken interval validation.

Fix the boundary handling and add native `node:test` regressions in `tests/`.
Preserve the existing tests. Use the named cases
`test_c1_touching_intervals_are_disjoint` for the touching boundary and
`test_c2_empty_interval_never_overlaps` for empty intervals. Each regression
must fail against the original implementation and pass after the fix. Keep
the CommonJS API and dependency-free package. Run `node --test tests/*.test.js`.

Record the touching-boundary regression as investigation case C1 and the
empty-interval regression as case C2, matching the required test_c1 and test_c2
names. Keep those case IDs throughout planning and verification. For additional
investigation cases, use C3, C4, and so on, with matching test_c3_, test_c4_
test-name prefixes.
