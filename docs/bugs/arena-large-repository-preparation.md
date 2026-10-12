# Large Arena repository preparation deadlines

Tracking: https://github.com/charlieanna/autocode/issues/619

The TypeScript dependent-destructuring case has about 61,000 tracked files.
Its first CLI preparation stopped before running either oracle because the
shared Git helper imposed a 60-second deadline on `git add .` while creating
its frozen baseline. The failed destination was retained and no case was admitted.

Bulk archive, staging, initial commit and candidate-patch operations need a
longer explicit bounded deadline than small revision queries. The preparation
process must allow those operations plus both independently bounded oracle
controls to finish. Increasing setup time does not change the oracle deadline,
the negative/reference requirements, or any behavioral score.

Use a fresh preparation destination after a setup failure. Retained failed
controls and attempts must not be overwritten or silently scored as failures
of the candidate's implementation.

The corrected TypeScript case also exposed a later preparation failure before
Completion Reviewer launch ([#991](https://github.com/charlieanna/autocode/issues/991)):
the verification copy's uncompressed Git source pack still had a 30-second
timeout. Public evidence inspection confirmed that the saved validation and
current source matched; this was preparation exhaustion, not stale validation.

Verification copying, source packing, staging and initial commit now share the
existing 900-second preparation allowance. Small Git setup operations retain
their 30-second ceiling. The outer supervisors and model/test budgets remain
unchanged. A collected preparation timeout pauses as
`PAUSED_VERIFICATION_PREPARATION` before model launch; actual source or input
drift still uses `PAUSED_STALE_VALIDATION`.
