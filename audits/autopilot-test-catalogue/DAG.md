# T02 — Plan validation and module handoffs (DAG-01..DAG-10)

Executed 2026-09-24. 10/10 executed, 10 PASS, 0 environment blocks. Independent
`ReadySetOracle` computes eligible milestones by plain topology (deps ⊆
accepted, ownership present, pairwise-disjoint paths/criteria).
Command: `env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t02`
Bundles: `.tmp-autopilot-testkit/artifacts/DAG-*/NN/`.

| ID | Status | Before | Notes |
|---|---|---|---|
| DAG-01 | PASS | full | diamond ordered A→(B,C)→D; D refused before B/C accepted; wave {B,C} matches oracle; existing `test_dependent_milestone_waits_for_accepted_prerequisites` |
| DAG-02 | PASS | partial | two-node/self/longer cycles all rejected; torture had one variant |
| DAG-03 | PASS | gap | unknown prerequisite named at scheduling, never assumed satisfied |
| DAG-04 | PASS | full | duplicate milestone/criterion/question ids rejected, draft retained |
| DAG-05 | PASS | full | B refused as initial task; recovery admits root A |
| DAG-06 | PASS | full | accepted A unlocks B/C; overall completion still blocked |
| DAG-07 | PASS | full | approved overlapping plan yields no parallel wave; absent ownership ineligible; existing `test_assignment_scenarios` |
| DAG-08 | PASS | full | planning/review/resolver applies dispatch no cross-unit writer; existing `test_units.*` |
| DAG-09 | PASS | full | forged handoff source revision → `PAUSED_STALE_HANDOFF`; existing resolver tests |
| DAG-10 | PASS | partial | supported path: resume launches no planner while Q1 open; answered question is the legitimate resolution. Scoped note: a *direct* `install_draft` call replacing an unapproved draft can drop open questions (`revision_guard` bypasses unapproved-draft swaps); unreachable through the CLI because the planner never launches while a blocking question is pending |

No product defects.
