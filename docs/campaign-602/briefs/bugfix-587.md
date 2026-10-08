# bugfix-587

Workflow: `bugfix`
Upstream: charlieanna/autocode#587

## Acceptance brief

Fix npm suite narrowing so the regression proof no longer false-PASSes. Root cause + fix; every listed narrowing path fails the proof.

## Oracle

`git log --oneline origin/master..HEAD | wc -l; # oracle: each narrowing path exits 1`
