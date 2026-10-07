# A hand-resolved program conflict leaves a later follow-up's work out

Found by a review of the program merge gate (2026-10-07). Reproduced on master
9f927d4. The change that holds every merge to a checked plan did not cause it.

## What happens

1. A workstream's merge onto the integration branch conflicts, and the program pauses at
   `PAUSED_MERGE_CONFLICT`.
2. While the program is not running, a person follows up the workstream's run. The
   follow-up completes under a new approved plan, and its edits sit uncommitted in the
   workstream's worktree.
3. The person resolves the conflict by merging the workstream's branch, as the pause
   asks.

`adopt_manual_merge` then accepts the branch tip that conflicted. That tip holds the
work from before the follow-up. The program never commits the worktree, so the
follow-up's edits are not merged. The record is `MERGED`, and its `approved_plan` is the
follow-up's plan.

## Fixed (#626)

When the person's merge brought the branch tip in, `adopt_manual_merge` now first
checks the workstream's worktree for work the branch does not have. It checks with
`_uncommitted`, the changes `_commit_all` would commit.

If it finds any, the program does not land the tip on its own. The workstream goes
back to `COMPLETE`, and the same pass's `integrate()` commits that work and merges it
on top, the hand-merged tip being already there. That is an ordinary merge, with
every check a merge has:

- ownership and interfaces;
- the run's checked plan;
- the cumulative checks.

A second conflict pauses again.

`tests/test_program_agreement_runs.py`
(`test_a_conflict_resolved_by_hand_still_merges_what_the_run_delivered_after_it`)
drives this scenario. On master the follow-up's file never reaches the integration
branch.
