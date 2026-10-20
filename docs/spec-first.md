# Spec-first runs

`--spec-first` (off by default; new runs with joint planning on the default flow) changes two points of a
build, so that what you approve is what gets built (#1200).

## 1. You confirm the requirements before any plan exists

Every time Requirements produces a handoff, the run stops with one question (`needs.kind` `answer`, id
`requirements-review-…`) beside the handoff's own open questions. It shows what Requirements understood:
the outcome, the behaviors, the acceptance examples, the constraints, what it left out and its
assumptions.

- Answer `plan` to start the Planner.
- Answer anything else to correct it: Requirements runs again with your answer and asks again.
- `--feedback` works as usual.

Adaptive planning never skips Requirements on such a run. Planning, plan review and plan approval are
unchanged.

Without the setting, a complete handoff goes straight to planning. A handoff that has only questions so
far, with no behaviors or acceptance tests yet, still stops the run for your answers on any flow, and
Requirements runs again with them; only the confirmation of a complete handoff is spec-first's own.

## 2. The Tester writes the acceptance tests before the Builder

After you approve the plan, and before the first Builder call, the run goes to `write_acceptance_tests`.
The Tester (the Validator's model, on a session of its own) writes one test per approved `test:`/`guard:`
case, named as the case says, changing only test files. The runner then runs those files on the code as
it is:

| Case | Required on today's code |
| --- | --- |
| `test:` (new behavior) | the test fails, or its file cannot load because the product lacks what it imports |
| `guard:` (must keep working) | the test passes |

A report that breaks this is rejected, and the Tester's retry gets the runner's result.

When the code already does what a `test:` case says, the Tester keeps the test and reports the case as
already working; once the runner sees that test pass, it sends the plan back: it relabels that case
`guard:` (must keep working), the Plan Reviewer reviews the corrected plan, and you approve it as any
plan. Then the Tester runs again. A case sent back once that disagrees again, a case no test can show,
and a bug's reproduction that already passes stop the run with `PAUSED_ACCEPTANCE_TESTS`; `--feedback`
revises the plan.

A bug fix's cases are its Investigator's, plus any `test:`/`guard:` cases its plan adds: the Tester writes
the reproduction tests, which must run and fail before the fix (a file that cannot load is no
reproduction), and the preserve tests, which must pass.

In a plan with milestones, the Tester writes the cases due when a milestone is reached; a parallel batch
gets the cases of every milestone in it at once. Tests for milestones not yet built are written later, so
they never fail an earlier milestone's checks.

The Builder then works with the Tester's lines fixed. Its prompt names the files. It may add tests to them
(a plan can name further tests in the same file), but every line the Tester wrote must stay, in order. When
an attempt changes or deletes one, the runner puts the file back and sends the Builder straight back,
naming the file (twice per plan; after that the attempt is rejected). A parallel batch whose patch removes
or changes a Tester line is not integrated (`PAUSED_ACCEPTANCE_TESTS`; `--retry-builder` rebuilds that
member). A test weakened by an added line (a skip, an early return) still fails the final regression
proof, which needs each case's test to fail without the change.
The status view's `acceptance_tests` field shows the files, the cases and how each test behaved on the
code before building. The final regression proof (fails without the change, passes with it) is
unchanged.

## Blind tests (`--blind-tests`)

`--blind-tests` (off by default; implies `--spec-first`) runs the Tester before the Builder starts,
but isolates the test code from the Builder entirely (#702):

- The Tester writes the tests and the runner verifies them on base code (`test:` criteria must fail;
  `guard:` must pass).
- The runner archives the tests in evidence and removes them from the workspace.
- The Builder gets no prompt note naming the test files, and workspace protection is not applied because the
  files are not in the workspace. The Builder never sees the test implementation.
- Before independent validation (`sol`, `astra_checkpoint`) and completion review (`astra_review`),
  the runner executes the archived tests in a candidate scratch worktree.
- Any test failure in the candidate scratch tree opens a blocking runner finding (`source="runner"`),
  preventing completion and appearing in open findings for review. When tests pass on candidate, the finding
  resolves.
- The status view's `acceptance_tests` field marks `blind: true`.

