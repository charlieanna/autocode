# Proof gates status

The branch contains the work recorded in commit `458b1ae4a` for #3814 and #3926.
The original handoff file was absent from this checkout, so this status records
what was independently observed during continuation.

- #3944 remains unimplemented. Protected-test replay still treats failures
  already present in the base suite as candidate failures.
- Ruff check and format check passed locally. Mypy passed on 965 source files.
- Focused tests are running with normal process access. A sandboxed attempt
  produced process-access errors, so its failures need the unrestricted result.
- The branch has not been reviewed for a PR or merged.

Focused command in this checkout:

```sh
python -m unittest tests.test_acceptance_reference tests.test_attribute_failure tests.test_ci_failure_signatures tests.test_focused_suite tests.test_superseded_tests tests.test_test_setup
```
