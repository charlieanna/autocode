# Proof gates status

The branch contains the work recorded in commit `458b1ae4a` for #3814 and #3926.
The original handoff file was absent from this checkout, so this status records
what was independently observed during continuation.

- #3944 remains unimplemented. Protected-test replay still treats failures
  already present in the base suite as candidate failures.
- A protected-replay regression now captures identical baseline failures
  with source-bound, hashed baseline evidence. It fails on master and this
  branch. Companion checks require same-test different failures, tampered
  baseline output, and stale proofs to stay blocked.
- The existing suite-preservation judgment compares failure IDs alone. A
  changed assertion can fail under the same ID, so that judgment cannot
  establish the identical-failure condition for #3944. Preserve and compare
  authenticated failure provenance before exempting either replay half.
  The existing unittest signature parser distinguishes assertion details.
  Pytest records JUnit results, while other runners have their own reporters.
  Their per-test failure details need a shared representation tied to the
  runner-owned capture. Missing or incomplete details must not count as equal.
- Review found a potential false proof in the #3926 work. For a new feature,
  `autocode_test_setup.not_evidence` discards even a reported missing
  third-party dependency. The module-level path also classifies an unloaded
  test from its file name without checking the import failure. Resolve this
  before merging and verify the original requested public-import example.
  The unittest parser currently records only the unloaded file ID for the
  module-level case. A common fix needs to retain the observed failure cause
  and establish that the missing import is provided by changed project code.
- Unit and end-to-end acceptance-reference regressions now capture in-test
  and module-level dependency cases. The candidate test passes, while the
  original run raises a missing unrelated dependency before its assertion.
  Both end-to-end regressions pass on master and fail on this branch. The
  regressions remain red here until classification is corrected. The in-test
  case mentions that dependency in an added source comment, guarding against
  treating a matching diff word alone as proof that the project provides it.
- Ruff check and format check passed locally. Mypy passed on 965 source files.
- The three new `SharedSetupAnswerTests` pass on this branch and error on
  current master, which does not have the new shared setup-classification API.
- The original #3926 public-export acceptance tests fail on master and pass
  here for both an import inside the test and an import at module level.
- For #3814, the two new assertion-identity tests passed on the branch. On
  current master their seven volatile-message subcases failed while the
  different-assertion guard passed.
- The focused command below passed 119 tests with normal process access.
  A sandboxed attempt produced process-access errors.
- The branch has not been reviewed for a PR or merged.

Focused command in this checkout:

```sh
python -m unittest tests.test_acceptance_reference tests.test_attribute_failure tests.test_ci_failure_signatures tests.test_focused_suite tests.test_superseded_tests tests.test_test_setup
```
