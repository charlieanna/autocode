# A rejected repair attempt's own work paused its retry as a stale handoff

A live feature-stock-refusals run (claude-tiers-hybrid profile, 2026-10-06) stopped
at `PAUSED_STALE_HANDOFF` with "Recovery packet: current source, task, settings,
contract or scope changed before admission". Nobody had changed anything.

## Sequence

1. The Completion Reviewer sent the vacuous refusal tests back (REWORK). The
   AutoResolver diagnosed them and planned a repair of `tests/test_stock.py`. Its
   recovery packet bound source revision `889fae6b`.
2. The repair Builder (`task-ffe82c17e4ad`) strengthened `tests/test_stock.py`.
   It also backed up `stock.py` as `stock_current.py` in the project root to run
   the tests against the original code, and never removed the backup. The runner
   rejected the attempt as outside its assignment (`PAUSED_INVALID_OUTPUT`) and
   removed `stock_current.py` (`autocode_assignment.undo_created`). It kept the
   test edit for the retry, as it keeps any in-scope work.
3. The Investigator diagnosed exactly that, verified its probe and recommended
   one more attempt.
4. Admission of that Builder attempt compared the packet's binding with the
   current tree and paused as stale.

The hypothesis was that the runner's cleanup changed the fingerprint. It did
not. Only `source_revision` differed in the binding: `8f918549` now, `889fae6b`
bound. The single changed file was `tests/test_stock.py`, holding exactly what
the rejected attempt's after-snapshot recorded. Without the cleanup, the tree
would have been at the attempt's own after-revision `914681d6`, which is not the
bound one either. The offline CLI reproduces this. With `SCENARIO_FAKE_SCOPE_SLIP`
the scripted repair Builder does the same, and master stops at
`PAUSED_STALE_HANDOFF`.

A temporary variant of that Builder, not kept, wrote only the backup. There the
cleanup restored the bound source and there was no stale handoff, but the run hit
a second wall. A rejected report counts for recovery novelty, so the same incident
was held as `PAUSED_NO_PROGRESS`. A second Investigator call followed, and the run
ended `WAITING_FOR_USER`. The Investigator's retry, documented to run the stage
once more, could not be admitted, so its paid diagnosis bought nothing (compare
#422). Fixing only the binding leaves the live case at the same wall.

## Fix

- `autocode_retained_work.own_repair_source` decides whether the current tree
  differs from the packet's bound source only by this packet's own Builder
  attempts. Every file must hold its bound content (the first such attempt's
  before-snapshot, at the bound revision) or what the latest one left (its
  after-snapshot), at either one's HEAD. `autocode_resolver_recovery` then binds a
  Builder dispatch, and an operator's `--retry-failed-stage` of its hold, at the
  packet's revision. A person's edit, a further change, a new commit or an
  unreadable snapshot still binds the current revision and pauses as stale.
- An Investigator's verified retry of a packet-bound Builder output the runner
  rejected (`PAUSED_INVALID_OUTPUT` or `PAUSED_REPEATED_FAILURE`, trigger
  `rejected_output`) is a one-use novelty grant (`grant_kind` `investigation`),
  like an accepted operational diagnosis's. It is bound to that investigation
  while its guidance is in force, and to the packet. An investigation of a novelty
  hold grants nothing, and a plain resume of the rejected attempt is still held.

`tests/test_rejected_repair_retry.py` drives both routes
through the CLI. The Investigator's retry completes from the retained edit. With
the Investigator pausing instead, a person's README edit pauses as stale, a plain
resume holds, and `--retry-failed-stage` completes. Pure tests cover the source
rule (`tests/test_retained_work.py`) and the grant's bounds.
