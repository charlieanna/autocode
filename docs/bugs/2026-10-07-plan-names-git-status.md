# A plan told the Validator to run the git status check the replay refuses

Found by live runs of `discuss-then-design-then-build` (#185) on 2026-10-07, on Claude models
(`claude-tiers`).

#589 made clean replay refuse a Validator check that runs `git status`
([bug note](2026-10-06-replay-uncommitted-git-state.md)). Plans could still require one. The design
turn's contract proved "no code is written" with it, in a criterion's `verification_method` and in
`initial_task.validation_plan`. The Completion Reviewer's and the Resolver's `next_task` repeated it; one
Completion Reviewer told the Validator to run it "exactly as written". A Validator that ran the planned
check had its report refused, and one that dropped it left the criterion unproven.

- `wgmlq3o7` and `pt6xzqan` paused at `PAUSED_INVALID_OUTPUT` on that refusal, after their report repairs.
- `wymv4k6n`'s first Validator report was refused the same way. Its repairs dropped the check, and the run
  then paused on a different defect: an approved method said "run `python3 -c` asserting ...", and the
  runner extracted and replayed a bare `python3 -c`.

## Fix

A new plan that names `git status` is refused when its author hands it in, and that author gets its
report back for repair. The refusal names every such row at once: each live plan named it in two or more,
and a report gets at most two repairs.

- `autocode_goal_lifecycle.install_draft` checks every criterion's `verification_method` and
  `initial_task`'s `validation_plan` and `requirements`. Human-review criteria are checked too, because
  `autocode_dispatch.task_for` puts their methods in other milestones' task plans. A user's
  `--edit-goal` is a new draft and is checked too. Two drafts no model writes at install are not
  (`UNAUTHORED_DRAFTS`): the Plan Reviewer's adaptive approval installs the Planner's draft again, and a
  bug job's small correction is built by the runner from the Investigator's report.
- `autopilot._apply_result` checks a Completion Reviewer's or Resolver's CONTINUE or REWORK `next_task`
  (`validation_plan` and `requirements`) before a REWORK is routed on, after the check that pauses a
  report for another contract or task. Under reviewer routing the final audit's decision goes the same way.
- `autocode_progressive_state` checks a new progressive proposal's slice checks and a slice revision's
  initial task.

`autocode_verification_plan.GIT_STATUS` is narrower than the replay's pattern, because it reads prose:
`git`, its global options, then `status` as the subcommand, or `'git','status'` in a Python argument
list. It is matched against the row and against the commands `commands()` extracts from it. It does not
match "git diff --stat lists only app/status.py" or "records the exit status". It cannot tell an
instruction from a prohibition, so `GIT_STATUS_RULE` says not to name `git status` at all. The Planner,
the Plan Reviewer, the Completion Reviewer (and the final audit under reviewer routing) and the Resolver
are each told this rule. The program workflow's brief for a code workstream no longer names it either. It also says that
scope needs no such check: the runner pauses a Builder that changes a file outside its task's
`affected_paths` (when the task names them), and it rejects a workflow job's change outside the paths
that job may write.

When this was fixed, 180 of about 2,200 distinct plan rows recorded on that machine matched, all from
live runs of this scenario. 7 of them only say that `git status` "cannot show edits" or forbid it, and
they are refused too.

## Limits

Nothing that runs at a stage launch or reads a saved plan refuses one. That covers approving a saved
draft, `assign_task`, `approved_commands`, `check_commands`, `validate_revision` of the saved plan and
`progressive_state.context`. A contract approved, or a progressive plan saved, before this rule keeps
working as before, and its Validator can still be told to run a check the replay refuses. A draft saved
before the rule is refused only if a planning stage writes it again.

Only the check plan is read: a criterion's statement and a task's objective are prose that can name
`git status` and are not checked (a live criterion said "git status shows only docs/design/... added"; that
run completed). Nor is a Builder's own continuation task in the final-audit-only workflow. The pattern
misses forms the replay still refuses, such as `git -C "$PWD" status` and a Python argument list built
from a variable; no live plan used them. A product that is itself about `git status` must word its plan
without naming it.
