# T01 — Approval and requirement preservation (APP-01..APP-12)

Executed 2026-09-24 on the catalogue branch of work (see coverage-map.json for
the exact commit). 12/12 executed, 12 PASS, 0 environment blocks. Independent
`ApprovalOracle` recomputes seal/token/actor/event facts (canonical-json
SHA-256) without calling `autocode_goals` decision functions.

Command: `env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t01`
Bundles: `.tmp-autopilot-testkit/artifacts/APP-*/NN/`.

| ID | Status | Before | Executed by / existing |
|---|---|---|---|
| APP-01 | PASS | full | `test_app01...`; existing `test_goals.test_vague_task_starts_read_only_discovery_and_waits` |
| APP-02 | PASS | partial | `test_app02...` (explicit both-path refusal new); guard also in `test_goals` |
| APP-03 | PASS | full | `test_app03...`; existing `test_approval_requires_displayed_exact_revision` |
| APP-04 | PASS | full | `test_app04...` (stale branch of same) |
| APP-05 | PASS | gap | `test_app05...` new: duplicate delivery explicitly refused, one effective event, no second writer |
| APP-06 | PASS | full | `test_app06...`; existing `test_answer_is_never_approval...` |
| APP-07 | PASS | full | `test_app07...`; existing `test_in_place_tampering...` |
| APP-08 | PASS | full | `test_app08...`; existing forged answer-id / approval_status cases |
| APP-09 | PASS | full | `test_app09...`; existing `test_goal_change_answer_requires_new_revision_and_approval` |
| APP-10 | PASS | full | `test_app10...`; existing denial + scope-widening tests |
| APP-11 | PASS | full | `test_app11...`; existing `test_existing_answers_persist...` |
| APP-12 | PASS | full | `test_app12...`; existing `test_stale_role_result_and_criterion_weakening...` |

No product defects. One semantic note (APP-05): a re-delivered approval token
is an explicit `ValueError` ("show the goal again"), not a silent no-op —
state, event count and writer count are unchanged, which satisfies the
scenario's one-effective-authorization requirement with an equivalent trace.
