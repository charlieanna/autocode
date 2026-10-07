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

`adopt_manual_merge` checks the person's resolution on its own first, as before:

- the run's checked plan;
- the cumulative checks, through `land(manual=True)`. A failure keeps the workstream
  `CONFLICT` and says the person's merge is still on the integration branch.

Then it looks in the workstream's worktree for work the branch does not have. It uses
`_uncommitted`, which lists the changes `_commit_all` would commit. If it finds any, the
workstream goes back to `COMPLETE`. Unless something holds it, `integrate()` then
commits that work and merges it on top in the same step, before any other workstream
merges. That is an ordinary merge, with every check a merge has: ownership,
interfaces, the plan and the cumulative checks. Its failure undoes only the program's
own merge, and a second conflict pauses again.

A first version reopened the workstream as `COMPLETE` without checking the resolution.
A review found two problems with that:

- another workstream could merge on top of the unchecked resolution and take the blame
  for its failure;
- a failure said "The merge was undone" while the person's merge stayed on the branch.

**Tests.** `tests/test_program_agreement_runs.py` covers both ways the scenario can go:

- `test_a_conflict_resolved_by_hand_still_merges_what_the_run_delivered_after_it` fails
  on master: the follow-up's file never reaches the integration branch.
- `test_a_failing_manual_merge_is_named_before_the_runs_later_work_merges_on_it` covers
  the review's case.

**Limit.** The fix covers uncommitted work only. A commit made on the workstream branch
after the hand merge is not an ancestor of the integration head, so the workstream stays
`CONFLICT` until the person merges the branch again (docs/program.md says so).
