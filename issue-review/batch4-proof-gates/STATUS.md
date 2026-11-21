# Proof gates status

The branch contains the work recorded in commit `458b1ae4a` for #3814 and #3926.
The original handoff file was absent from this checkout, so this status records
what was independently observed during continuation.

**Current blocker:** the separate import probe's `__spec__.origin` comes from
the imported module or its finder, so it can be forged. A real external module
pointed its claimed origin at an existing project file and the prior revision
incorrectly granted proof authority. The new negative control reproduced this
at `bb3232381`; it passes after making every such origin diagnostic only.
Legitimate project-import positives now hold as well. Independently establish
the actual loaded source without trusting module/finder self-report before
restoring authority. This issue is separate from the unbound editable below.

The existing runner-owned fail-to-pass observation alone does not establish
the missing import's source. An added module can give the same base missing-
module error and candidate pass whether a project file or an external
resolver provides it. A re-export can give the same missing-name error and
candidate pass without proving where the exported object came from. The
missing-external-dependency end-to-end negative confirms that an identical
test can fail on base and pass on candidate for an unrelated setup failure.
For a module-level collection error no individual test ran on base at all.
Do not infer source from matching names, changed-file words, or an untrusted
finder; these cases remain held until there is independent evidence.
An owned-runner control also shows why a separate standard `PathFinder`
probe is insufficient: it finds the project's `release_api.py`, while the
actual candidate test first installs a custom finder and loads an external
module. The project file returns 0 and the external one returns 42, so the
test passes only with the external import. Authenticating the resolver and
module state at the exact import in the actual test process is still needed;
the existing runner records no such events.

The branch was rebased onto master `6ae029754` and additionally incorporates
the capture ownership fixture correction from `251841736` (cherry-picked as
`493baaae5`). Focused provenance, protected-oracle, capture fixture,
incomplete-outcome and architecture suites passed 101 tests on that rebased
head. Full ruff and mypy passed (971 files). The three #3926 unsafe-dependency
regressions were red at that head; the later acceptance-side change makes them
pass while the strict-binding positive cases and final judgment remain open.

- #3944 is in progress. A first provenance-based replay implementation now
  accepts identical named failures from authenticated base and candidate proof
  captures for directly recognized Python pytest and unittest commands and Go
  test events. Real Go, pytest and unittest controls run in separate scratch
  roots. It fails closed for other commands and for old pytest receipts without a pinned
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
- An in-progress shared import-observation helper now reads named errors only
  from authenticated runner captures, reruns the selected candidate tests in an
  owned scratch tree, and probes imports in a separate owned subprocess. It
  admits an origin only when the actual file is project source and the test and
  probe share the same execution-environment digest and unchanged source
  snapshot. Tests cover a re-export through an unchanged package file, an added
  module, `src/`, test helpers, external modules and an environment change.
  The acceptance-reference path now consumes this observation and records both
  test and probe receipts; final regression judgment reads the same answer.
  Its result retains the observation and receipts. All reported origins are
  currently diagnostic only, because they can be forged (see blocker above). The
  external-dependency safety controls now pass for acceptance, but the four
  positive import cases are currently blocked because this host's selected
  interpreter has an unbound editable `autocode_supervisor` install. The
  existing execution-identity contract marks that dependency inventory
  incomplete; do not ignore it or substitute a different interpreter. A
  general safe binding of actual editable import resolvers and their exposed
  source trees, or a semantics-preserving same-execution observation, is still
  required. A real acceptance case using a fresh isolated standard-library
  interpreter passes; it does not justify changing the selected interpreter in
  an actual run. Rejected observations retain their reason and evidence file.
- The owned runner now records a digest of its actual scrubbed execution
  environment. The observation uses the interpreter and package directory
  from the recognized test command, rejects shell environment prefixes and
  requires matching test/probe environment digests and unchanged source.
  Six focused import-observation tests, 58 receipt/scheduler/replay tests and
  the architecture limits pass; full mypy passes on 972 files. The final
  final regression-judgment integration also has a real owned-scratch positive
  and missing-external-dependency negative using a fully bound isolated
  interpreter. Both passed at `bb3232381`, before the forged-origin finding;
  the positive must now hold. On master the missing dependency incorrectly
  grants PASS and the recorded observation is absent. The existing first
  Node project control also passes. These checks do not resolve the unbound
  editable or unsupported/legacy runner gaps above.
- The 32-test focused setup, import-observation, first Node project and
  architecture run passed with normal process access; full ruff and mypy (972
  files) passed. An initial sandboxed run failed four subprocess tests because
  the supervisor could not authenticate keeper cleanup; the successful run
  used the process-inspection access those tests require.
- Unit and end-to-end acceptance-reference regressions now capture in-test
  and module-level dependency cases. The candidate test passes, while the
  original run raises a missing unrelated dependency before its assertion.
  Both end-to-end regressions pass on master and failed on the original branch;
  they now pass with acceptance-side fail-closed classification. The in-test
  case mentions that dependency in an added source comment, guarding against
  treating a matching diff word alone as proof that the project provides it.
- Ruff check and format check passed locally. Mypy passed on 972 source files
  with the import-observation foundation.
- A broader 137-test replay/architecture/scheduler run failed its old
  architecture size limit (the capture code has since moved into the
  lower-level scheduler and the targeted architecture check passes) and timed
  out in `VerificationRestartCLI.test_restart_reuses_only_completed_supplemental_check_and_keeps_canonical_execution`.
  The 180-second driver budget had 41.4 seconds left for the second Resume;
  the Mac's load average was 40.5 on 10 CPUs when investigated. Contention is
  plausible, not proven. The same test subsequently passed alone on master in
  98 seconds and on this branch in 87 seconds; the original combined-run
  failure remains evidence, not a passing result. Logs are retained at
  `/private/tmp/autocode-proof-restart-master.log` and
  `/private/tmp/autocode-proof-restart-branch.log`.
- The three new `SharedSetupAnswerTests` pass on this branch and error on
  current master, which does not have the new shared setup-classification API.
- The original #3926 public-export acceptance tests failed on master and
  passed here before complete dependency identity was required; they are now
  blocked by the local unbound editable described above.
- For #3814, the two new assertion-identity tests passed on the branch. On
  current master their seven volatile-message subcases failed while the
  different-assertion guard passed.
- The focused command below passed 119 tests with normal process access
  before adding the intentionally red dependency regressions described above.
  A sandboxed attempt produced process-access errors.
- Draft PR: https://github.com/charlieanna/autocode/pull/3981. This branch is
  not ready to merge.

Focused command in this checkout:

```sh
python -m unittest tests.test_acceptance_reference tests.test_attribute_failure tests.test_ci_failure_signatures tests.test_focused_suite tests.test_superseded_tests tests.test_test_setup
```
