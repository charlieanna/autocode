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
from `validate_body`, which runs on every draft install and again at approval,
so the issue's draft is refused before it is installed, with the reason
("AC1 declares test_ac1_fixed_returns_two but refers to TestFixedReturnsTwo
(write "test: TestFixedReturnsTwo")"), and a saved draft like r3 cannot be
approved. The report goes back for repair, whatever a review accepted. Each
planning and review stage gets a `NATIVE TEST NAMES` rule listing the names, and
the Builder note says to write a declared native name exactly. The matcher is
unchanged: prose still binds nothing.

A name counts as requested only when all of these hold. Each condition prevents
a refusal the planner could not satisfy:

- It has the form of a Go test function (`Test` then an upper-case letter, digit
  or underscore); `TestMain` is Go's test-binary hook, not a test.
- The user wrote it (task, brief feedback, own answers; not a delegated default
  or model text), introduced as a test: within eight words after "test",
  "tests", "testing" or "func", or after another such name in the same list. A
  word such as `TestNet` used otherwise asks for no test.
- The regression proof will run Go tests (an explicit `go test` command, else
  the framework `autocode_verify` detects, as `autocode_regression.prove`
  chooses). In Python or Java, `TestParser` is a class.
- The project's Go files do not already contain it. A bug report's failing test,
  "keep TestX passing", "remove the test TestOld" or a `TestServer` helper names
  existing code that the suite comparison already protects.
- The job is not design-only, and no reproduced diagnosis drives the proof.

A requested name is bound when a `test:` or `guard:` criterion declares an
identifier that `match_cases` (framework go) binds to it, so `test:
TestFixedReturnsTwo`, its subtests, and the proof's own snake spelling
`test_fixed_returns_two` all pass. A marked criterion whose verification method
mentions a requested name must declare one it binds. Criteria without a
requested name keep the criterion-ID convention.

## Limits

- Names that do not follow one of those cue words ("TestA must check X") are
  not detected; the previous behavior applies to them.
- A greenfield Go project with no `go.mod` yet is not detected as Go at
  planning time.
- After the Builder writes the tests they exist in the workspace, so a later
  revision is no longer checked against them.
- A bug fix proven by its Investigator's cases names tests after the case IDs
  (`test_t1_...`); a brief asking for native names there is not covered.
- Python `test_` names asked for in a brief get no equivalent check.
- No fresh live run is claimed here. The issue asks for one that reaches matched
  fail-to-pass and preservation proof.
