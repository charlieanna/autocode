# Proof gates status

The branch contains the work recorded in commit `458b1ae4a` for #3814 and #3926.
The original handoff file was absent from this checkout, so this status records
what was independently observed during continuation.

- #3944 is in progress. A first provenance-based replay implementation now
  accepts identical named failures from authenticated base and candidate proof
  captures for directly recognized Python pytest and unittest commands. It
  fails closed for other commands and for old pytest receipts without a pinned
  result file. Those routes still need a shared recovery and comparison before
  the issue is fixed or this branch can be merged.
- A second #3944 regression now runs real pytest and retains separately pinned
  base and candidate JUnit captures for an original test failing on a missing
  dependency. It failed on master and now passes on this branch. Pytest's failure
  text includes volatile object addresses, so a sound comparison must remove
  those display-only details without dropping the exception or assertion.
- A protected-replay regression now captures identical baseline failures
  with source-bound, hashed baseline evidence. It fails on master and passes
  on this branch. Companion checks require same-test different failures, tampered
  baseline or candidate proof output, stale revisions and a mismatched protected
  test binding to stay blocked. The fixtures carry the current test-role policy
  and protected binding, as real current regression proofs do.
- Separate controls reject a different project's same-named test, a skipped
  test or an unproven passing half. For multiple projects, an authenticated
  known failure plus a timed-out part remains NOT_VERIFIED rather than passing
  or directing the Builder to repair an existing failure.
- The existing suite-preservation judgment compares failure IDs alone. A
  changed assertion can fail under the same ID, so that judgment cannot
  establish the identical-failure condition for #3944. Preserve and compare
  authenticated failure provenance before exempting either replay half.
  The existing unittest signature parser distinguishes assertion details.
  Pytest records JUnit results, while other runners have their own reporters.
  Their per-test failure details need a shared representation tied to the
  runner-owned capture. Missing or incomplete details must not count as equal.
- The reported #3944 replay uses pytest. Previously its runner retained JUnit
  XML without a hash; `results_file_written` said only that a file was
  written. The runner now pins ordinary per-test result files in the receipt
  and the shared integrity check rejects tampering, removal or symlinks. A
  new real-pytest regression fails on master `f1b67a7b0` and passes here.
  An unreadable capture also fails closed; its regression fails on the first
  capture revision and passes with the follow-up. The receipt and scheduling
  suites passed 72 tests, and ruff and mypy passed for the capture revision.
  A further real-pytest regression found that shell quoting hid a result path
  containing an apostrophe and spaces; the shared command-word check now
  recognizes it. Existing Vitest, Node and Cargo suites ran 71 tests with six
  Cargo-dependent tests skipped because Cargo is unavailable on this host.
  This is a prerequisite only: replay still needs a shared per-test provenance
  comparison, and old receipts without a trusted capture must not be accepted
  as identical-failure evidence. Explicit JUnit mode has separate capture.
- CI's unittest signature deliberately discards appended assertion messages;
  the same assertion with `database down` versus `authorization failed`
  therefore yields the same CI signature. Those are not demonstrated identical
  failures. A replay safety regression now keeps them distinct; do not use the
  CI attribution signature alone for this gate.
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
- Ruff check and format check passed locally. Mypy passed on 966 source files
  after the traceback-root normalization.
- A broader 137-test replay/architecture/scheduler run failed its old
  architecture size limit (the capture code has since moved into the
  lower-level scheduler and the targeted architecture check passes) and timed
  out in `VerificationRestartCLI.test_restart_reuses_only_completed_supplemental_check_and_keeps_canonical_execution`.
  The 180-second driver budget had 41.4 seconds left for the second Resume;
  the Mac's load average was 40.5 on 10 CPUs when investigated. Contention is
  plausible, not proven. Rerun this test alone and inspect its outcome before
  treating the broader check as passing.
- The three new `SharedSetupAnswerTests` pass on this branch and error on
  current master, which does not have the new shared setup-classification API.
- The original #3926 public-export acceptance tests fail on master and pass
  here for both an import inside the test and an import at module level.
- For #3814, the two new assertion-identity tests passed on the branch. On
  current master their seven volatile-message subcases failed while the
  different-assertion guard passed.
- The focused command below passed 119 tests with normal process access
  before adding the intentionally red dependency regressions described above.
  A sandboxed attempt produced process-access errors.
- The branch has not been reviewed for a PR or merged.

Focused command in this checkout:

```sh
python -m unittest tests.test_acceptance_reference tests.test_attribute_failure tests.test_ci_failure_signatures tests.test_focused_suite tests.test_superseded_tests tests.test_test_setup
```
