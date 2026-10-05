# A finding's saved scope goes stale when a revision moves its criterion

Fixed (#447); found in desktop work kept at `77933a8` (issue #412). An approved
contract revision can move an acceptance criterion to another milestone, for
example AC15 from M2 to M5. Open findings kept the scope they were recorded
with, so a Validator finding saved as `{M2: [AC10, AC15]}` no longer fit M2's
approved criteria (`[AC10]`).

`autocode_finding_scope.relevant_blockers` fails closed on a saved scope that
does not match its owner, so that finding blocked every milestone, including an
unrelated earlier M1. No milestone's reviewer could close it either:
`autocode_findings._covers` needs a report covering both AC10 and AC15, and each
milestone reviews only its own. Checked on master 095474cf with M1 → M2 → M5:
`blocking_for_milestone` returned the finding for all three, and `_covers` was
false for all three.

Only closing the finding got the run past this, and closing drops the defect:
`--close-finding ID --close-reason TEXT` (#300), or a permission answer whose
payload names the finding (#427, `autocode_finding_cause.resolve_named`). #427's
same-cause inheritance only closes copies of a finding that was already resolved,
so it could not release this one.

## Fix

The note listed three ways forward: move these findings when the revision that
moves the criterion is approved; let `relevant_blockers` hold such a finding
back from milestones that neither own nor depend on its criteria; or finish the
operator command sketched in `77933a8`. The owner chose the first: move them
without being asked when the revision is approved.
`tools/autocode_finding_rescope.py` holds the policy; the approval step
(`autocode_goal_lifecycle._approve`) calls it inside the approval transaction,
after the user's `goal_approval` event is saved. A draft, a proposal that is
never approved, or a contract marked approved without that event moves nothing.

- Each open finding whose saved scope fitted the previously approved contract
  and no longer fits the new one is attributed to the milestones that now own
  its criteria. The defect stays open: status, severity, blocking, text,
  evidence and repeat count are kept, and nothing is resolved or closed.
- A scope names one milestone, and `relevant_blockers` and `_covers` accept
  nothing else, so a finding whose criteria now belong to several milestones is
  split. In the example the row keeps its ID as `{M2: [AC10]}` and a copy with a
  new ID and `split_from` (the original finding's ID) takes `{M5: [AC15]}`. M1
  is no longer blocked; M2 is blocked by the AC10 part, M5 by both (M2 is its
  prerequisite). M2's reviewer closes the AC10 part and M5's reviewer the AC15
  part; neither may close the other's. Keeping one row would let one
  milestone's reviewer close the defect for a criterion it never reviewed, or
  let a milestone be accepted while the defect may concern its criterion.
- The copy does not take the row's bookkeeping: a pending resolution attempt,
  the tasks assigned to fix the row and how its scope was restored stay with
  the row, whose pending attempt keeps only its own criteria.
- Parts of one split finding are not duplicates. A permission answer that names
  one part closes only that part, not the others by same-cause inheritance
  (`autocode_finding_cause.split_family`), and the validation-only stall
  question does not suggest closing a part because another part was resolved.
- When all its criteria move to one milestone, the row keeps its ID and moves
  there.
- A moved criterion that several milestones of the new contract list goes to
  one that did not list it before (where the revision moved it), else to one
  not accepted under the previous contract, else to the first in contract
  order. `relevant_blockers` holds the part at every milestone listing all its
  criteria, and any of their reviewers can close it.
- A milestone accepted before that receives a part is not carried forward as
  accepted (`autocode_carryforward.carry`: an accepted milestone with an open
  blocking finding recorded against it revalidates), so its reviewer checks
  the criterion again and can close the part. Without this, a part given to a
  carried prerequisite blocked the next milestone and nobody reviewed it.
- A finding is left exactly as it was when any criterion it cites left its
  milestone and no milestone of the approved contract lists it (removed or
  unassigned), or the revision changed that criterion's wording (a different
  behavior under the same ID). This holds even when its other criteria still
  exist, so such a finding, if blocking, still blocks every milestone and no
  reviewer can close it, as before the fix: a person settles it with
  `--close-finding` or a permission answer, which closes the whole finding,
  including the part on criteria that still exist. A finding whose scope did
  not fit the previously approved contract either (unscoped, a batch, or stale
  since an approval made before this fix) is also left as it was: the revision
  being approved did not move it.
- Each move is recorded once, on the row it started from, as a `scope_history`
  entry (`from`, `to` with every resulting row's ID and scope, the approved
  `contract_token`, `at`). The status view lists them in
  `evidence.finding_scope_moves` (only once there is one).
- A moved finding fits the new contract, so a restart, a resume or a later
  approval that does not move its criteria again changes nothing.

`tests/test_finding_rescope.py` reproduces the deadlock and covers the cases
above, including approval through `--approve-goal`, the status view, and a
carried prerequisite with the milestone checkpoint fixtures. The operator
command sketched in `77933a8` (`--reconcile-finding-scopes`,
`finding_scope_reconciliations`) was not built; the draft test in #447 that
expected that field described the abandoned sketch, not supported behavior.
