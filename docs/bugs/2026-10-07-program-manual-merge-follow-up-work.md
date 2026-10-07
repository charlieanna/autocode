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

## Why it stays open

What should land here is not settled. Two options:

- commit the worktree and ask for a fresh merge, as `integrate()` does;
- refuse to adopt a branch whose worktree has changes since the conflict.

Until one is chosen, merge a follow-up's work by rerunning the program before resolving
the conflict by hand.
