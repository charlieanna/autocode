# T10 — Parallel builds and integration (PAR-01..PAR-10)

Executed 2026-09-24. 10/10 executed, 10 PASS. The four subprocess-fixture
regressions are re-executed programmatically (clean subprocess, repo root) and
their results recorded in the bundles.
Command: `env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t10`

| ID | Status | Notes |
|---|---|---|
| PAR-01 | PASS | isolated worktrees inside the parent + existing isolation regression rerun |
| PAR-02 | PASS | existing partial-success fixture regression rerun green |
| PAR-03 | PASS | ownership violations rejected (existing + escape-path validator) |
| PAR-04 | PASS | overlap not parallelized (existing + disjointness unit check) |
| PAR-05 | PASS | parent index and user files intact through batch preparation |
| PAR-06 | PASS | parent drift detected against the pinned baseline; tampered worker pauses |
| PAR-07 | PASS | conflicting patch refused; neither output accepted (git-level) |
| PAR-08 | PASS | pre-integration validation cannot approve the integrated candidate |
| PAR-09 | PASS | focused repair keeps the combined acceptance scope |
| PAR-10 | PASS | superseded-plan results rejected by binding |

No product defects.
