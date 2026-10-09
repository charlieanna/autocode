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

## Fixed (#615)

- **Validate tasks.** A validate task writes nothing, so it no longer needs paths of its
  own. `assign_task` gives it its milestone's paths (in a progressive run, the active
  slice's, which `guard_assignment` requires), and when the milestone owns none
  (as a program's final check owns none) the task keeps `[]`, which
  `before_assignment` now accepts for a validate task. An implement task still needs
  explicit paths.
- **Draft checks.** `validate_body` checks the draft's first task under a copy of the
  run's settings, so the milestone-checkpoint rule runs on the draft. A first task that
  approval could not start is refused as a draft and goes back to the Planner as a
  report repair. The refusal tells it to list the files the implement task writes.
- **Instructions.** The Planner's instructions (`CONTRACT_REFERENCES`) and the
  milestone policy say the same.
- **Tests.** `tests/test_taskrun.py` plans both of live run 15's shapes through the CLI
  (`LIVE_FIXTURE_FIRST_TASK`):
  - a validate first task with no paths is approved;
  - an implement one is repaired before approval.

  Unit tests in `tests/test_planning.py` cover the draft check and the path fallback.

## Follow-up (#667)

- **Saved runs and later checkpoint activation.** At the locked CLI boundary, a recorded
  unapproved draft's first task is checked under the current settings before it is shown or
  approved. An unassignable authored draft goes to the existing bounded report repair path.
  Adaptive review cannot rewrite the Planner's task in its schema, so its Planner repairs
  the draft and an independent review follows. The spent review allowance is retained;
  automatic correction grants neither extra review capacity nor approval. A missing authored
  report keeps the existing refusal and requires explicit feedback or a user edit.
- **Progressive runs.** Draft checking uses the candidate first slice's ownership and the
  same pure assignment guard used by execution. A validate task naming no paths inherits
  the slice's paths. This structural check creates no delegation or active slice: approval
  still authenticates and seals the reviewed proposal, and execution rechecks it.
- **Coverage.** The CLI tests emulate saved drafts, both adaptive and fully finalized,
  already-repaired Planner reports, and checkpoints enabled after drafting. They require
  no provider launch when an invalid old token is refused or the plan is shown, a refreshed
  approval token after repair, and preservation of the spent review count. The progressive
  planning regression rejects a first task inside the product grant but outside the first
  slice. The separate execution-guard test deliberately bypasses preflight to retain its
  atomic rejection coverage for an older draft.
