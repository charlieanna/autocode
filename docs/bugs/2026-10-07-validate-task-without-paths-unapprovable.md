# A milestone plan whose first task names no paths cannot be approved

Found by live run 15 of `program-notes-cli` (2026-10-07, profile `claude-tiers`). The
run stopped at the final check, which never got past plan approval. The failing code
is in single-run planning and is the same on master.

## What happened

- **The plan.** The final check's planner proposed one milestone (M5). Its
  `initial_task` was `kind: validate` with `affected_paths: []`, and M5 named no
  affected paths either. The program's brief asks for this kind of plan: the merged
  product is re-verified, and only integration defects are repaired.
- **Review passed it.** The Plan Reviewer passed the plan, and the run showed it for
  approval (`AWAITING_GOAL_APPROVAL`).
- **Approval failed.** Approving it failed with `Input rejected: Milestone tasks require
  a named milestone and explicit affected paths`, from
  `autocode_milestones.before_assignment`. That rule applies whenever milestone
  checkpoints are on.
- **The plan could not change.** Every retry showed the same plan, so the person could
  not approve it, and the run had no way to repair it.

## Why

`autocode_goal_lifecycle.validate_body` checks a draft by assigning its first task to a
probe state. That probe carries no settings, so the milestone-checkpoint rule never
runs on a draft. Only the real assignment after approval applies it.

The Planner's own instructions (`autocode_goals`, the milestone `affected_paths` note)
also allow `[]` when the paths "cannot be established".

## Open question

What a validate task's affected paths should be is not settled. Three options:

- the milestone's own paths;
- the files its checks read;
- none, since a validate task has no writer.

Until that is decided, either:

- apply the checkpoint rule when a draft is validated, so the Planner repairs the plan
  before anyone is asked to approve it; or
- let a validate task with no paths through `before_assignment`.

Both change single-run planning, so they belong in their own pull request with a live
run.
