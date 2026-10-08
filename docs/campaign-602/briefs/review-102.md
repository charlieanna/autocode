# review-102

Workflow: `review`
Upstream: pytest-dev/pytest-reportlog#102

## Acceptance brief

Review the ANSI-strip PR against issue #83. Findings only in review/findings.json; the tree is not modified. Judge correctness of the PR.

## Oracle

`test -f review/findings.json && git status --porcelain | wc -l; # oracle: findings present, tree clean`
