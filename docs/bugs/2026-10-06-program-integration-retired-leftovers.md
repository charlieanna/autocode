# A retired final check leaves its edits in the integration worktree

Open; found in the adversarial review of the #22 and #23 branch
([Programs](../program.md#revisions-and-stale)).

The integration workstream (the final check) has no worktree of its own: its run works
in the program's integration worktree, on the integration branch itself. Retiring that
run (`STALE`) forgets the run but leaves the files alone, as for every workstream. That
happens when:

- an approved revision changes the final check's scope before it merged, such as its
  brief, a journey, or an interface it is bound to;
- its approved plan drops an inherited journey or requirement.

The retired run's uncommitted edits stay in the shared integration worktree:

- the next merge of a code workstream pauses at `PAUSED_INTEGRATION_DIRTY` ("The
  integration worktree ... has uncommitted tracked changes; commit or restore them");
- the fresh final check does not start either: the program pauses at
  `PAUSED_INTEGRATION_DIRTY`, and the message names the worktree and says to commit or
  discard (`git restore`) the changes, which may be a retired integration run's.

Nothing merges and the final check does not start again until a person decides what
the leftovers are. A code workstream retired in place has no such stop: its fresh run
plans again in its own worktree, over whatever the retired run left there.

Untracked files the retired run created are not part of that check
(`--untracked-files=no`). They stay in the worktree for the fresh final check, whose
delivery commits them with its own changes.

## Evidence

Scratch runs (not kept in the repository) with the scripted children of
`tests/test_program.py` (`ProgramHarness`):

- The final check's run edited `README.md`, created `notes/left.txt`, and completed
  without verifying `J1` (`PAUSED_JOURNEY_UNVERIFIED`). A revision that changed only its
  brief retired it (`STALE`). The next `program run` paused at
  `PAUSED_INTEGRATION_DIRTY` before starting the fresh final check; `git status` in the
  integration worktree still showed ` M README.md`, `?? notes/` and `?? tests/`. After
  `git restore README.md`, the fresh final check ran and the program completed with
  `notes/left.txt` committed on the integration branch.
- With the same revision also changing workstream `a`'s brief, `a`'s re-check
  completed and its merge paused at `PAUSED_INTEGRATION_DIRTY` with the merge message
  above.
- A final check whose approved plan dropped `J1` was retired in the pass that saw the
  plan; the next pass paused at `PAUSED_INTEGRATION_DIRTY` before the fresh final check
  started.

## Options

- When retiring the final check's run, save its diff (tracked and untracked) as a
  patch under `.autocode/programs/<key>/<workstream>/` and restore the integration
  worktree to the integration head, so the fresh run starts clean and a person can
  still read what the retired run did.
- Let a fresh final check run continue over the leftovers, as a code workstream
  retired in place does, and skip the dirty check for that start.
