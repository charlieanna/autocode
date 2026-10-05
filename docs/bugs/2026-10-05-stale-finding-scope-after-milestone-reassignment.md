# A finding's saved scope goes stale when a revision moves its criterion

Open; found in desktop work kept at `77933a8` (issue #412). An approved
contract revision can move an acceptance criterion to another milestone, for
example AC15 from M2 to M5. Open findings keep the scope they were recorded
with, so a Validator finding saved as `{M2: [AC10, AC15]}` no longer fits M2's
approved criteria (`[AC10]`).

`autocode_finding_scope.relevant_blockers` fails closed on a saved scope that
does not match its owner, so that finding blocks every milestone, including an
unrelated earlier M1. No milestone's reviewer can close it either:
`autocode_findings._covers` needs a report covering both AC10 and AC15, and each
milestone reviews only its own. Checked on master 095474cf with M1 → M2 → M5:
`blocking_for_milestone` returns the finding for all three, and `_covers` is
false for all three.

Only closing the finding gets the run past this, and closing drops the defect:
`--close-finding ID --close-reason TEXT` (#300), or a permission answer whose
payload names the finding (#427, `autocode_finding_cause.resolve_named`). #427's
same-cause inheritance only closes copies of a finding that was already resolved,
so it cannot release this one. Once closed, the defect is gone until a reviewer
of M5 reports it again.

`77933a8` sketched a fix that keeps the defect open. The operator gives the
finding ID, its exact saved scope, a new scope and a reason, and the finding is
moved to the milestone that now owns its criteria. The change is recorded in
`user_events`. Only the pure policy (`tools/autocode_finding_scope_repair.py`)
was written. The `--reconcile-finding-scopes` flag, run lock, request
authentication and status-view fields that its note describes were never
built. Its gate also relies on a permission request worded "Operational
ledger-metadata reconciliation only:", and nothing on master writes that.
Three ways forward:

- move these findings without being asked when the revision that moves the
  criterion is approved;
- let `relevant_blockers` hold a finding back from milestones that neither own
  nor depend on its criteria;
- finish the explicit operator command.
