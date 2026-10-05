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

Of the three ways forward this note listed, the owner chose the first: move
these findings without being asked when the revision that moves the criterion
is approved. `tools/autocode_finding_rescope.py` holds the policy; the approval
step (`autocode_goal_lifecycle._approve`) calls it inside the approval
transaction, after the user's `goal_approval` event is saved. A draft, a
proposal that is never approved, or a contract marked approved without that
event moves nothing.

- Each open finding whose saved scope fitted the previously approved contract
  and no longer fits the new one is attributed to the milestones that now own
  its criteria. The defect stays open: status, severity, blocking, text,
  evidence and repeat count are kept, and nothing is resolved or closed.
- A scope names one milestone, and `relevant_blockers` and `_covers` accept
  nothing else, so a finding whose criteria now belong to several milestones is
  split. In the example the row keeps its ID as `{M2: [AC10]}` and a copy with a
  new ID and `split_from` takes `{M5: [AC15]}`. M1 is no longer blocked; M2 is
  blocked by the AC10 part, M5 by both (M2 is its prerequisite). M2's reviewer
  closes the AC10 part and M5's reviewer the AC15 part; neither may close the
  other's. Keeping one row would let one milestone's reviewer close the defect
  for a criterion it never reviewed, or let a milestone be accepted while the
  defect may concern its criterion.
- When all its criteria move to one milestone, the row keeps its ID and moves
  there. A criterion several milestones list goes to the first of them in
  contract order; `relevant_blockers` holds the finding at the others too.
- A finding that cites a criterion no milestone of the approved contract lists
  (removed or unassigned) is left as it was and still fails closed, for a
  person to settle with `--close-finding` or a permission answer. So is a
  finding whose scope did not fit the previously approved contract either
  (unscoped, a batch, or stale since an approval made before this fix): the
  revision being approved did not move it.
- Each move is recorded once, on the row it started from, as a `scope_history`
  entry (`from`, `to` with every resulting row's ID and scope, the approved
  `contract_token`, `at`). The status view lists them in
  `evidence.finding_scope_moves` (only once there is one).
- A moved finding fits the new contract, so a restart, a resume or a later
  approval that does not move its criteria again changes nothing.

`tests/test_finding_rescope.py` reproduces the deadlock and covers the cases
above, including approval through `--approve-goal` and the status view. The
operator command sketched in `77933a8` (`--reconcile-finding-scopes`,
`finding_scope_reconciliations`) was not built; the draft test in #447 that
expected that field described the abandoned sketch, not supported behavior.
