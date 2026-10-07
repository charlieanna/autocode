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
