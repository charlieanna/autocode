# Requested Go test names and prose aliases (#498)

## What happened

A live Go run (`20261005-131050-fix-fixed-in-product-go-to-return-2-instead-of-0-2387d753`,
on `7df90f7`) was told to add the native tests `TestFixedReturnsTwo`,
`TestFixedPreservesExisting` and `TestFixedPreservesCrash`. Discovery declared
`test: test_ac1_fixed_returns_two`, `guard: test_ac2_preserves_existing` and
`guard: test_ac3_preserves_crash`. The first Plan Reviewer blocked the mismatch;
the revision kept the identifiers and added prose saying each one "resolves to"
the native case; the final review accepted that and offered draft r3 for
approval. The runner proves a criterion only by the identifier right after the
mark (`autocode_test_cases.declared_test_name`, `match_cases`), so on that draft
`match_cases(..., framework='go')` against the three requested tests bound
nothing: `{'AC1': [], 'AC2': [], 'AC3': []}`. The draft was not approved.

Two causes: the planner prompt only ever taught the lowercase criterion-ID
convention (`test: test_<id>_...`), although `declared_test_name` has accepted
native Go names since #460; and no runner check compared the plan's declared
names with the names the user asked for, so a review could accept the prose.

## The fix

`tools/autocode_native_test_names.py` decides which Go test names the user
asked for and checks a contract body against them. The goal lifecycle calls it
from `validate_body`, with the draft's origin, on every draft install and again
at approval, so the issue's draft is refused before it is installed, with the
reason ("AC1 declares test_ac1_fixed_returns_two but refers to
TestFixedReturnsTwo (write "test: TestFixedReturnsTwo")"), and a saved draft
like r3 cannot be approved. The report goes back for repair, whatever a review
accepted. Each planning and review stage gets a `NATIVE TEST NAMES` rule listing
the names, and the Builder note and its test-style section say to write a
declared native name exactly. The matcher is unchanged: prose still binds
nothing.

A name counts as requested only when all of these hold. Each condition prevents
a refusal the planner could not satisfy:

- It has the form of a Go test function (`Test` then an upper-case letter, digit
  or underscore). `TestMain` is Go's test-binary hook and `TestXxx` Go's own
  placeholder; neither is a test to write.
- The user wrote it (task, brief feedback, own answers; not a delegated default
  or model text) as a test to add. It must come right after "test", "tests" or
  "func" ("function", "case", "named" or "called" may come between), as in "add
  the Go tests TestA, TestB and TestC" or "a test for Fixed named TestA", or
  start an item of a list whose lead-in names tests ("Add these tests in
  x_test.go:" then "- TestA: what it checks", one name per item however long
  its description). A name further away ("the tests pass on TestNet", "a test
  for TestHelper misuse") asks for nothing; so does one in a clause that negates
  or gives an example ("do not name the test TestFixed", "like the test
  TestReadAll", "e.g. TestA") or one offered with an alternative ("a test TestA
  or similar"). A clause restarts after a comma, "and", "but" or "then".
- A later message of the user's that says "instead of", "rather than" or "not"
  right before a name withdraws it.
- The regression proof will run Go tests (an explicit `go test` command, else
  the framework `autocode_verify` detects, as `autocode_regression.prove`
  chooses). In Python or Java, `TestParser` is a class.
- The project's Go files do not already contain it. A bug report's failing test,
  "keep the test TestX passing" or a `TestServer` helper names existing code that
  the suite comparison already protects.
- The job is not design-only, and no reproduced diagnosis drives the proof.
- The user has not settled it. The user's own `--edit-goal` is never refused by
  this check, and a name their latest edit leaves unaccounted for is no longer
  required of the planners' later drafts (joint planning reviews an edit again).

A requested name is accounted for when one `test:` or `guard:` criterion
declares exactly that identifier, in the user's spelling (a subtest
`TestA/case` counts), or when an ordinary criterion names it and no other test,
for the Validator: a test the runner cannot run to a pass, such as one that
skips without a database, could otherwise never be proven. A respelling such as
`test_fixed_returns_two` is refused: the Go matcher would bind it to
`Test_fixed_returns_two` and the proof would pass without the requested
identifier. A marked criterion that mentions an unaccounted requested name while
declaring another identifier is the issue's prose alias; mentioning a name
another criterion already declares is not. Two criteria never declare the same
requested test. Criteria without a requested name keep the criterion-ID
convention.

A first version of this fix (review, 2026-10-06) counted any Test-prefixed word
within eight words of "test", so it missed the second and later names of a
described list, required placeholders, examples and negated names, could not be
withdrawn, refused the Validator route, accepted respellings and treated a
mention of a declared name as an alias. Each of those has a test in
`tests/test_native_test_names.py`.

## Limits

- Phrasings outside that grammar are not detected ("TestA must check X", "add
  a TestFoo test"); the previous behavior applies to them.
- Negation and examples are recognized by a few words ("not", "never",
  "without", "instead", "rather", "like", "such", "e.g.", "for example"), not
  by understanding the sentence; the user's own edit is the way out of a wrong
  reading.
- A user's message that asks for a name again after their latest edit dropped
  it does not bring it back; the edit decides.
- A greenfield Go project with no `go.mod` yet is not detected as Go at
  planning time.
- After the Builder writes the tests they exist in the workspace, so a later
  revision is no longer checked against them.
- A bug fix proven by its Investigator's cases names tests after the case IDs
  (`test_t1_...`); a brief asking for native names there is not covered.
- Python `test_` names asked for in a brief get no equivalent check.
- No fresh live run is claimed here. The issue asks for one that reaches matched
  fail-to-pass and preservation proof.
