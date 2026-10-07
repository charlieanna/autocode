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
  placeholder; neither is a test to write. A subtest path
  (`TestCacheExpiry/expired`) asks for its test function, `TestCacheExpiry`.
- The user wrote it (task, brief feedback, own answers; not a delegated default
  or model text) as a test to add. It must come right after "test" or "tests"
  ("function", "func", "case", "named" or "called" may come between), as in
  "add the Go tests TestA, TestB and TestC" or "a test for Fixed named TestA",
  be declared with a test's parameter (`func TestA(t *testing.T)`; "implement
  func TestConnection() error in product.go" is a production API), or start an
  item of a list whose lead-in names tests ("Add these tests in
  x_test.go:" then "- TestA: what it checks", one name per item however long
  its description). An inline list may describe each name too ("TestA (empty
  input), TestB (one item)" or "TestA checks X, TestB checks Y; TestC ..."); it
  ends at the sentence's end, at a description's first comma or semicolon that
  no name follows ("the test TestA, which must not break TestB"), and at a
  name inside a description ("TestA for the parser and make sure TestServer,
  TestClient still pass"). A name
  further away ("the tests pass on TestNet", "a test
  for TestHelper misuse") asks for nothing; so does one in a clause that negates
  or gives an example ("do not name the test TestFixed", "like the test
  TestReadAll", "e.g. TestA") or one offered with an alternative ("a test TestA
  or similar"). A clause restarts after a comma, "and", "but" or "then".
- A later message of the user's that says "instead of", "rather than" or "not"
  right before a name withdraws it.
- The regression proof will run Go tests (an explicit `go test` command, else
  the framework `autocode_verify` detects, as `autocode_regression.prove`
  chooses). In Python or Java, `TestParser` is a class.
- The code of the project's Go files does not already contain it. A bug
  report's failing test, "keep the test TestX passing" or a `TestServer` helper
  names existing code that the suite comparison already protects. Comments and
  string and rune literals are not code: a `// TODO: TestFixedReturnsTwo` or a
  constant holding the names declares no test, and counting it let the issue's
  alias through.
- The job is not design-only, and no reproduced diagnosis drives the proof.
- The user has not settled it. The user's own `--edit-goal` is never refused by
  this check, and a name their latest edit leaves unaccounted for is no longer
  required of the planners' later drafts (joint planning reviews an edit again).

A requested name is accounted for when one `test:` or `guard:` criterion
declares exactly that identifier, in the user's spelling (a subtest
`TestA/case` counts), or when an ordinary criterion's verification method names
it and no other test, for the Validator: a test the runner cannot run to a pass,
such as one that skips without a database, could otherwise never be proven. A
respelling such as `test_fixed_returns_two` is refused: the Go matcher would
bind it to `Test_fixed_returns_two` and the proof would pass without the
requested identifier. A marked criterion that mentions a requested name no
marked criterion declares, in its verification method or in its own criterion
text, while declaring another identifier, is the issue's prose alias, even when
an ordinary criterion leaves that name to the Validator (the issue's draft plus
one Validator criterion would otherwise pass); mentioning a name another marked
criterion declares is not. A marked criterion whose name the runner cannot read
(`test: TestFixedReturnsTwo.`, `guard: TestA: what it checks`) would be proven
under its criterion ID, so it is refused the same way, and the reason says what
may follow a name (nothing, ` — ` or a parenthesis). Two criteria never declare
the same requested test. Criteria without a requested name keep the
criterion-ID convention.

A first version of this fix (review, 2026-10-06) counted any Test-prefixed word
within eight words of "test", so it missed the second and later names of a
described list, required placeholders, examples and negated names, could not be
withdrawn, refused the Validator route, accepted respellings and treated a
mention of a declared name as an alias. Each of those has a test in
`tests/test_native_test_names.py`. A second pass (2026-10-07) found that the
same list written inline with a description after each name still lost every
name after the first, that the alias passed when it sat in the criterion text
next to a Validator criterion, and that one Validator criterion naming all the
requested tests accounted for each of them, against the rule given to the
planner; those have tests too. A third reader of the branch (issue comment,
2026-10-07) found that a `.go` comment or string naming the requested tests
made them count as existing, so nothing was requested and the alias passed, and
that `func TestConnection() error`, a production API, was read as a requested
test because "func" alone was a cue; "func" now needs a `*testing.T` parameter.
It also asked for the scope of a subtest path, which had asked for nothing and
dropped the rest of its list; it now asks for its test function. Tests cover
all three.

## Limits

- Phrasings outside that grammar are not detected ("TestA must check X", "add
  a TestFoo test", a Markdown table of tests, a list whose description has a
  comma before the next name, "call it TestB instead"); the previous behavior
  applies to them.
- The Validator route is not checked against its stated reason. A plan may
  leave every requested test to the Validator, one ordinary criterion each,
  while differently named tests (`test_ac1_...`) prove the behavior, with no
  prose claiming they map. The runner's proof then binds those other tests,
  and the requested ones are checked only by the Validator. The planning rule
  reserves that route for a test the runner cannot run to a pass, and the plan
  shows both tests to its reviewers and to the user.
- Negation and examples are recognized by a few words ("not", "never",
  "without", "instead", "rather", "like", "such", "e.g.", "for example"), not
  by understanding the sentence; the user's own edit is the way out of a wrong
  reading.
- Once the user has edited the plan, only the names that edit accounts for are
  required. A name their later message asks for, again or for the first time,
  is not required until another edit of theirs declares it (messages carry no
  order relative to the edit here); the previous behavior applies to it.
- A greenfield Go project with no `go.mod` yet is not detected as Go at
  planning time.
- A requested subtest path is checked only through its test function: a plan
  declaring `TestCacheExpiry` or `TestCacheExpiry/other` accounts for
  `TestCacheExpiry/expired`. A subtest asked for under a test that already
  exists asks for nothing, since subtest names live in strings.
- `func TestA` with no parameter list asks for nothing, even when it is meant
  as a test.
- After the Builder writes the tests they exist in the workspace, so a later
  revision is no longer checked against them.
- A bug fix proven by its Investigator's cases names tests after the case IDs
  (`test_t1_...`); a brief asking for native names there is not covered.
- Python `test_` names asked for in a brief get no equivalent check.
- No fresh live run is claimed here. The issue asks for one that reaches matched
  fail-to-pass and preservation proof.
