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

`adopt_manual_merge` still checks the person's resolution first, as before:

- **The plan.** It must be the run's checked plan.
- **The cumulative checks.** They run through `land(manual=True)`. A failure keeps the
  workstream `CONFLICT` and says the person's merge is still on the integration branch.

Then it looks in the workstream's worktree for work the branch does not have, using
`_uncommitted`, which lists the changes `_commit_all` would commit. If there is any:

- **The first check publishes nothing.** It runs as `land(final=False)`: no interfaces,
  no skeleton and no verification record yet.
- **The later work merges on top.** The workstream goes back to `COMPLETE`. Unless
  something holds it, `integrate()` commits that work and merges it in the same step.
  It is an ordinary merge with every check a merge has: ownership, interfaces, the plan
  and the cumulative checks. That merge is the one that publishes the workstream's
  interfaces and verifies the skeleton.
- **A failure undoes only the program's merge.** The message says so, and a second
  conflict pauses again.
- **The resolution stays guarded meanwhile.** The record carries `manual_merge`, which
  `_owned_checks` reads. Until the later work lands, the workstream's checks run on
  every other merge, as a merged workstream's would. This covers a change request
  holding the later work, and a failed merge of it. `land` drops the key when the later
  work lands, and `abandon` drops it with the run.

**Review rounds.** A first version reopened the workstream as `COMPLETE` without
checking the resolution. A review found that:

- another workstream could merge on top of the unchecked resolution, and take the blame
  for its failure;
- a failure said "The merge was undone" while the person's merge stayed on the branch.

A second version checked the resolution first through a full `land`. A re-review found
two more problems:

- That `land` published the workstream's interfaces. The later work's merge was then
  refused as a change to an interface already delivered, so a producer such as the
  skeleton stalled at `PAUSED_INTERFACE_CHANGE` on every rerun.
- While the later work waited, other merges skipped the workstream's checks, because
  only `MERGED` workstreams counted.

**Tests.** `tests/test_program_agreement_runs.py`:

- `test_a_conflict_resolved_by_hand_still_merges_what_the_run_delivered_after_it` fails
  on master: the follow-up's file never reaches the integration branch.
- `test_a_failing_manual_merge_is_named_before_the_runs_later_work_merges_on_it`: a
  resolution that fails its checks is named before the later work merges on it.
- `test_a_skeleton_resolved_by_hand_is_delivered_once_its_later_work_lands`: the
  producer case.
- `test_while_a_resolutions_later_work_waits_its_checks_guard_every_other_merge`: the
  guard while a change request holds the later work.
- `test_later_work_that_fails_its_merge_is_undone_and_the_hand_merge_stays`.

**Limits.**

- **Uncommitted work only.** A commit made on the workstream branch after the hand merge
  is not an ancestor of the integration head. The workstream stays `CONFLICT` until the
  person merges the branch again (docs/program.md says so).
- **Checks of the later work.** If the run's replayed checks test the later work itself,
  the resolution fails them on its own. The workstream stays `CONFLICT` and never merges
  that work. Master does the same. The way out is the one above: commit the work on the
  workstream branch and merge it again by hand.
