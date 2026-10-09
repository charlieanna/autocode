# Test failures inherited from the master merge (2026-09-27)

Found while adding workflow recognition (issue #38). These fail identically on
`efd4652` (the merge of master into `restructure/scenarios`) before any of that
work, so they are not caused by it. Verified by running each in a detached
worktree at that commit.

| Module | Test | Failure |
| --- | --- | --- |
| `tools/test_activity_runtime.py` | `test_legacy_recent_failures_seed_the_aggregate_recovery_ceiling` | `Paused not raised` |
| `tools/test_activity_runtime.py` | `test_zero_no_progress_budget_does_not_disable_aggregate_recovery_ceiling` | `Paused not raised` |
| `tools/test_report_repair.py` | `test_invalid_or_unbounded_repair_configuration_is_rejected` (value=3) | `ValueError not raised` |
| `tools/test_report_repair.py` | `test_explicit_retry_replaces_legacy_rejected_planning_attempt` | archive `attempts` is 0, test expects 2: `autocode.py` now resets `pending_report_repair["attempts"]` to 0 on `--resume-paused` ("Reset report repair attempts on explicit resume"), which the older test contradicts |
| `tools/dashboard/tests/test_joint_planning_contract.py` | `test_default_browser_creation_routes_discovery_to_glm_and_review_to_astra`, `test_explicit_opencode_model_ids_are_preserved_without_double_prefixes` | default model ids differ from the test's expectations (`openai/gpt-5.6-*` vs `xiaomi-token-plan-sgp/mimo-v2.6-pro`) |

The main checkout had uncommitted edits to `tools/test_activity_runtime.py`
and `tools/test_report_repair.py` on 2026-09-26; the fix or the updated
expectations probably live there. Whoever merges master next should check
whether these clear.
