# The Plan Reviewer's prompt and the milestone replan gate (#459)

In live run `8soi9a5s` (feature-stock-refusals, 2026-10-05) milestone M1 stalled
(`needs_replan`, replans 0 of 1). `autocode_milestones.before_assignment` then accepts a
task on M1 only as a `REWORK` with evidence and a changed approach. The Plan Reviewer's
prompt did not say so. It still had the general rule "Use kind=validate with CONTINUE when
existing work only needs Validator revalidation". The reviewer followed that rule each
time, the gate refused every answer, and the run paused `PAUSED_MILESTONE_REPLAN` at
$12.31 with one replan still allowed.

## What changed

- **#470 (merged)** added `tools/autocode_milestone_replan.py`, which holds the one
  definition of a pending replan. The gate and the prompt both read it. While a replan is
  required, the `astra_review` and `astra_plan` prompts include `MILESTONE REPLAN
  REQUIRED` with the milestone's counts, and the conflicting general rule is replaced.
  The Resolver's prompt is built from the review prompt, so it gets the same text.
- **This change** covers the state #470 left silent. A replan has been used, the
  milestone stalls again, and the replans are spent. The gate now refuses every task on
  that milestone, `REWORK` or `CONTINUE`, with `PAUSED_MILESTONE_STALLED`. But the prompt
  gave no warning, and the milestone policy still asked for "an evidence-backed REWORK
  with a materially different approach". A reviewer that followed the policy could spend
  a Resolver call planning a task the gate then refused. The prompt now includes
  `MILESTONE REPLANS SPENT` with the counts, for example "3 validations without progress
  (limit 3) and its replans are spent (1 made, limit 1)". It says that any further task
  on the milestone pauses the run, and the general validate rule is qualified for that
  milestone. The policy text also no longer claims "one such automatic replan". It points
  to `milestone_checkpoint.limits.max_replans`, because `--max-milestone-replans` and the
  continuous-v1 routes (unbounded) change that number. The gate's behavior is unchanged.
- **Which decisions still call the Resolver.** A `REWORK` is not the only spent-state
  decision that calls the Resolver before the pause. A `BLOCKED` review does too, unless
  its `user_request.kind` is `permission` or `goal_change`. `autopilot` queues the Resolver
  for `blocker` and `clarification`. The AutoResolver human gate defers `contradiction` and
  `infeasible` to it. Only a `CONTINUE` (or one of those two kinds) pauses without it. The
  spent statement now names these choices and leaves the usual rule for choosing the kind
  unchanged. `ASKS_USER_DIRECTLY` in the replan module holds the two kinds.
- **Integrated batches.** A batch's progress row has the id `batch:<digest>`, which no
  `next_task.milestone_id` can take. The gate refuses a task on any member. Both statements
  used to name the digest ("no further task on batch:caa8bd913910f702"). Members such as
  M2 then read as another milestone. Both statements now name the members ("a next task
  on M1 or M2"). The gate and the text read the same `members()`.

Tests: `tests/test_milestone_replan.py` covers the spent statement, the replacement
rules, the Resolver routes and the batch members. `PendingReplanThroughTheCLI` in
`tests/test_milestone_checkpoints.py` drives the second stall through the CLI with a
scripted provider. The review prompt states the spent gate, and a `CONTINUE` from the
review pauses the run (`view.recovery.cause` is `PAUSED_MILESTONE_STALLED`) before any
Resolver or writer runs. A `BLOCKED` of every `user_request.kind` in the schema calls the
Resolver first exactly when the prompt says it does. On a stalled `[M1, M2]` batch, the
prompt names M1 and M2, and a `CONTINUE` on M2 pauses the run. Another test checks that
`--max-milestone-replans 0` gives a prompt that no longer also promises a single replan.

## Still open

- **The report-repair churn before the pause (problem 2 of #459).** #504 names the
  closure row a report repair changed, and it tells repairs to copy kept rows byte for
  byte. No live run has exercised it. Repairs that cannot change the outcome are still
  not stopped early. The existing report-repair limit is what bounds them.
- **No live run** has reached either replan state since #470.
