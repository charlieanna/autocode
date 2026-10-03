# Settled feedback and answers in review prompts

Every Validator and Completion Owner prompt carried the run's full feedback and
saved answers, re-sent on every agent step. On the long dashboard self-build
(contract revision 93), the 27 feedback events and 18 answers were 106,087 of
206,912 handoff characters, all but one given before the contract was approved.

`autocode_handoff_history.condense` now shortens, for the review stages (`sol`,
`astra_review`, `astra_checkpoint`) only, records given at or before the approval
of the contract the stage judges against:

- a feedback event keeps its id, time, actor, the first 240 characters and its
  length;
- a planning answer (`answer`, `delegated`) keeps its question, options, answer
  text, kind and time, dropping the question's rationale, proposed default and
  category.

The full records move to the run's hash-pinned context artifact
(`autocode_context.compact`), and `settled_history_note` tells the reviewer where.
Unchanged: anything given after the approval or without a timezone-aware time,
permission and checkpoint answers (granted under a contract, not part of it), an
unapproved contract, every other stage, and any packet the change would not
shrink.

## Measurement

The same saved run directories were rendered with master `1c9992e4` and with this
change through the stage prompt builder and the provider's output contract
(estimated tokens are UTF-8 bytes / 4):

| Run | Stage | Master | Change | Saved |
| --- | --- | ---: | ---: | ---: |
| todo 2026-10-01 | Validator | 17,403 | 17,212 | 1.1% |
| todo 2026-10-01 | Completion Owner | 23,851 | 23,662 | 0.8% |
| todo 2026-10-02 | Validator | 15,531 | 15,313 | 1.4% |
| todo 2026-10-02 | Completion Owner | 20,485 | 20,269 | 1.1% |
| bug fix 2026-10-02 | both | 12,374 / 16,342 | same | 0% (no answers) |
| dashboard self-build | Validator | 55,754 | 40,154 | 28.0% |
| dashboard self-build | Completion Owner | 60,288 | 44,689 | 25.9% |

The moved records in each artifact equal the state's records exactly; the one
answer given after approval stays whole in the prompt. Small runs gain little:
their answers are few and feedback is empty. The saving grows with discussion
before approval, which is where long runs spend the most.

The risk is a binding detail that lives only in pre-approval feedback text past
the excerpt and was not written into the approved contract; the reviewer then has
to retrieve it from the artifact. The Builder and next-task planner receive the
same records (41–48% of their prompts on the dashboard run) and are not changed
here.

## Live check (2026-10-03)

`greenfield-greeting-cli` and `greenfield-todo-cli` ran live on master `8fda930d`
and on this change (glm53-openai, Plan Reviewer on gpt-6-sol). At the first plan
approval the driver sent one feedback message whose binding request sat past the
240-character excerpt: blank or whitespace-only names exit 2, and a new
`todo.py count` command.

| Run | Result | Feedback request delivered | Reviews |
| --- | --- | --- | --- |
| change, greeting | PASS 12/12 | yes (AC15, AC16) | Validator PASS, Completion Owner COMPLETE |
| change, todo | PASS 10/10 | yes (AC16–AC19) | Validator FAIL (six new tests also passed on the old code), rework, PASS |
| master, greeting | PASS 12/12 | yes | Validator PASS, Completion Owner COMPLETE |
| master, todo | stopped in planning (Planner output-token limit) | - | none |

Both review stages on the change received shortened history and never opened
the context artifact (24 steps). In these small runs the prompts shrank by 661
and 1,247 characters; the large saving needs a long pre-approval discussion, as
in the measurement above. Separately, the GLM Completion Owner left required
top-level fields out of three of four reports on both sides, each costing an
AutoResolver call and a report repair; that is not caused by this change.

## Validation

`run_suite.py --changed` (28 tests in 4 modules) and the fake scenario campaign
(53 PASS, `feature-refund-window` NOT_EXERCISED as on master, the live-only
Investigator skipped).
