# Recovery mistook a writing Tester for a read-only reviewer

The live VS Code follow-up's independent Tester wrote the approved regression
test and returned a valid report. Reconciliation compared its source snapshots
using only the role name: every role except Builder was treated as read-only.
It therefore refused the Tester's expected test-file edit as stale validation.

The launch and native snapshot guard already permit this test-writing stage.
Recovery and the snapshot guard now read one source-writer classification from
the stage-access policy. The Builder and test writer may reconcile their source
changes; ordinary reviewers and every report-only repair remain read-only.
The stage owner's existing path, report, approval and test-evidence checks still
run before any result is applied. This classification grants no new write or
completion authority.

A regression on clean master reproduces the exact stale-validation refusal for
the writing Tester. The Builder control passes there. After the fix both source
writers reconcile, while ordinary Tester/Planner results and both writer report
repairs still refuse source drift. Existing native snapshot, stage-access and
architecture checks pass with those regressions.
