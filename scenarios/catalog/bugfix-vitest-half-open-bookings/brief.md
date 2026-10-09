The TypeScript booking module incorrectly refuses an adjacent booking: a saved interval from 9 to 10 conflicts with a requested interval from 10 to 11. Fix findConflicts in src/bookings.ts so intervals are half open: start is included, end is excluded, touching endpoints do not overlap, and zero-length saved or requested intervals are empty. Keep validation of finite ordered endpoints, real overlap detection, returned IDs in the original booking order, and the existing public interfaces. Do not mutate the bookings or requested interval. Add meaningful Vitest regressions for touching and empty intervals and keep existing tests passing. Dependencies are pinned and installed before the run; use node node_modules/vitest/vitest.mjs run, with no npx download.

Name the added regression cases `test_c1_touching_bookings_do_not_conflict`
and `test_c2_empty_intervals_do_not_conflict_or_mutate_inputs`. Each must
fail against the original implementation and pass after the fix.

Record the touching-booking regression as investigation case C1 and the
empty-interval regression as case C2, matching the required test_c1 and test_c2
names. Keep those case IDs throughout planning and verification. For additional
investigation cases, use C3, C4, and so on, with matching test_c3_, test_c4_
test-name prefixes.
