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

## 2. The Tester writes the acceptance tests before the Builder

After you approve the plan, and before the first Builder call, the run goes to `write_acceptance_tests`.
The Tester (the Validator's model, on a session of its own) writes one test per approved `test:`/`guard:`
case, named as the case says, changing only test files. The runner then runs those files on the code as
it is:

| Case | Required on today's code |
| --- | --- |
| `test:` (new behavior) | the test fails, or its file cannot load because the product lacks what it imports |
| `guard:` (must keep working) | the test passes |

A report that breaks this is rejected, and the Tester's retry gets the runner's result. When the Tester
finds that a `test:` case already works, or that no test can show it, the run stops with
`PAUSED_ACCEPTANCE_TESTS` before any building: the plan and the code disagree, and `--feedback` revises
the plan.

The Builder then works with those files fixed. Its prompt names them; an attempt that changes or deletes
one is rejected and the file restored from the runner's copy. It may add further tests in other files.
The status view's `acceptance_tests` field shows the files, the cases and how each test behaved on the
code before building. The final regression proof (fails without the change, passes with it) is
unchanged.

Not yet covered: bug fixes (their cases come from the Investigator), later batches of a plan whose
milestones run in parallel, and sending a disagreeing plan straight back to the Planner.
