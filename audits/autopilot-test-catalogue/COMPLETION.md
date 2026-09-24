# T04 — Review aggregation and completion (REV-01..REV-14)

Executed 2026-09-24. 14/14 executed, 14 PASS, 0 environment blocks.
Command: `env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t04`
Bundles: `.tmp-autopilot-testkit/artifacts/REV-*/NN/`.

| ID | Status | Before | Notes |
|---|---|---|---|
| REV-01 | PASS | full | valid candidate completes once, no extra writer |
| REV-02 | PASS | partial | late PASS archived "during an open correction"; REWORK routing kept — explicit case new |
| REV-03 | PASS | partial | failed executed check refuses COMPLETE — explicit case new |
| REV-04 | PASS | partial | missing review and crashed reviewer both stay pending; next action review — variants new |
| REV-05 | PASS | full | unverified criterion named and blocking |
| REV-06 | PASS | full | ledger F1 blocks despite empty latest findings |
| REV-07 | PASS | full | advisory low/nonblocking finding completes with note preserved |
| REV-08 | PASS | full | human acceptance requested then honored |
| REV-09 | PASS | full | C1 acceptance cannot cover edited C2 |
| REV-10 | PASS | full | source edit invalidates acceptance; C1 history retained |
| REV-11 | PASS | partial | end-to-end correction → disposition → completion — explicit case new |
| REV-12 | PASS | full | terminal on plain re-invocation; acceptance never replaced. Scoped note: a *direct* `apply_result` call with a stale report re-points `next_stage` without touching the acceptance record; the supported runner surface applies nothing after completion |
| REV-13 | PASS | gap | validate-kind task completes with empty diff, no no-progress escalation — new |
| REV-14 | PASS | gap | mixed-candidate reports cannot complete; current review named — new |

No product defects. Two scoped notes recorded in bundles (DAG-10-style direct-
API observations; see also the REV-12 note above).
