# Proof gates status

The branch contains the work recorded in commit `458b1ae4a` for #3814 and #3926.
The original handoff file was absent from this checkout, so this status records
what was independently observed during continuation.

- #3944 remains unimplemented. Protected-test replay still treats failures
  already present in the base suite as candidate failures.
- Review found a potential false proof in the #3926 work. For a new feature,
  `autocode_test_setup.not_evidence` discards even a reported missing
  third-party dependency. The module-level path also classifies an unloaded
  test from its file name without checking the import failure. Resolve this
  before merging and verify the original requested public-import example.
- Unit and end-to-end acceptance-reference regressions now capture the
  in-test dependency case. The candidate test passes, while the original
  run raises a missing unrelated dependency before its assertion. Both new
  regressions fail on this branch until classification is corrected.
- Ruff check and format check passed locally. Mypy passed on 965 source files.
- The three new `SharedSetupAnswerTests` pass on this branch and error on
  current master, which does not have the new shared setup-classification API.
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
