"""Isolated budget policy tests; no runtime, processes, providers or filesystem."""
import copy
import json
import unittest

from autocode_budget_recovery import HARD_CEILINGS, PLANNING_KIND, RUNNER_DEFAULTS, recover


NOW = "2026-09-27T10:00:00+00:00"


def fixture(kind="iteration_ceiling"):
    stage = {"stage": "builder", "task_id": "T1", "output": "attempt/report.json", "accounted": True,
             "finished_at": "2026-09-27T09:59:00+00:00", "duration_seconds": 30,
             "exit_code": 0, "changed_files": ["src/main.py"],
             "metrics": {"provider_tokens": {"input_tokens": 10, "output_tokens": 5}}}
    state = {"settings": {"budget_origins": {name: "runner_default" for name in HARD_CEILINGS},
                          "limits": {"iteration_ceiling": 15, "stage_timeout_seconds": 300,
                                     "max_seconds": 600},
                          "milestone_checkpoints": {"max_seconds": 5400}},
             "iteration": 16, "active_seconds": 600, "no_progress_batches": 0,
             "stages": [stage], "history": [copy.deepcopy(stage)],
             "failure_history": {"previous": {"count": 1, "attempts": ["old"]}},
             "resolver": {"actions": [{"kind": "previous-action"}]},
             "goal_contract": {"hash": "contract", "body": {}},
             "current_task": {"id": "T1", "milestone_id": "M1", "contract_hash": "contract"},
             "milestone_progress": {"contract:M1": {"id": "M1", "contract_hash": "contract",
                                                       "seconds": 5400, "reviews_without_progress": 0}}}
    if kind == "stage_timeout_seconds":
        failed = copy.deepcopy(stage)
        failed.update(output="timeout/report.json", duration_seconds=300, exit_code=1,
                      timed_out=True, timeout_kind="stage", rejected=True, abandoned=True,
                      changed_files=[], activity={"activity": "provider_active"})
        state["stages"].append(failed)
        state["history"].append(copy.deepcopy(failed))
    return state


class BudgetRecoveryTests(unittest.TestCase):
    def test_progressive_aggregate_increase_requires_explicit_authority(self):
        for origin in ("runner_default", "resolver_delegated"):
            with self.subTest(origin=origin):
                state = fixture("max_seconds")
                state["settings"]["limits"]["max_seconds"] = 43200
                state["settings"]["budget_origins"]["max_seconds"] = origin
                state["active_seconds"] = 43200
                state["progressive"] = {"version": 1, "delegation": {"contract_token": "r1:goal"}}
                self.denied(state, "max_seconds")

    def test_progressive_candidate_does_not_change_ordinary_budget_policy(self):
        state = fixture("max_seconds")
        state["progressive"] = {"version": 1, "candidate": {"plan_hash": "unapproved"}}
        self.assertTrue(recover(state, kind="max_seconds", now=NOW))

    def test_suspended_progressive_budget_still_requires_explicit_aggregate_increase(self):
        state = fixture("max_seconds")
        state["progressive"] = {"version": 1, "budget": {"run_seconds": 600}, "delegation": None}
        self.denied(state, "max_seconds")

    def denied(self, state, kind="iteration_ceiling", now=NOW):
        before = json.dumps(state, sort_keys=True)
        self.assertFalse(recover(state, kind=kind, now=now))
        self.assertEqual(before, json.dumps(state, sort_keys=True))

    def test_each_kind_has_only_declared_delta(self):
        for kind in HARD_CEILINGS:
            with self.subTest(kind=kind):
                state = fixture(kind)
                before = copy.deepcopy(state)
                self.assertTrue(recover(state, kind=kind, now=NOW))
                ledger = state["resolver"].pop("budget_extensions")
                entry = ledger[0]
                self.assertEqual(kind, entry["kind"])
                self.assertEqual(NOW, entry["at"])
                self.assertEqual("accepted_changed_files", entry["evidence"]["source"])
                self.assertEqual(entry["from"] * 2, entry["to"])
                self.assertEqual("budget-extension:v1:" + kind, entry["idempotency_key"])
                container = state["settings"]["milestone_checkpoints" if kind == "milestone_max_seconds" else "limits"]
                container["max_seconds" if kind == "milestone_max_seconds" else kind] = entry["from"]
                self.assertEqual(json.dumps(before, sort_keys=True), json.dumps(state, sort_keys=True))

    def test_new_run_defaults_extend_once_to_their_ceiling(self):
        for kind, default in RUNNER_DEFAULTS.items():
            with self.subTest(kind=kind):
                state = fixture(kind)
                state["settings"]["limits"][kind] = default
                if kind == "max_seconds":
                    state["active_seconds"] = default
                else:
                    state["stages"][-1]["duration_seconds"] = state["history"][-1]["duration_seconds"] = default
                self.assertTrue(recover(state, kind=kind, now=NOW))
                self.assertEqual(HARD_CEILINGS[kind], state["settings"]["limits"][kind])
                self.denied(state, kind)

    def test_inherited_and_explicit_caps_are_protected(self):
        for kind in HARD_CEILINGS:
            for origin in (None, "user_explicit", "persisted", "unknown", True, {}, []):
                with self.subTest(kind=kind, origin=origin):
                    state = fixture(kind)
                    state["settings"]["budget_origins"][kind] = origin
                    self.denied(state, kind)
            state = fixture(kind)
            del state["settings"]["budget_origins"]
            self.denied(state, kind)

    def test_unknown_token_usage_is_not_accounted_as_zero(self):
        state = fixture()
        self.assertTrue(recover(state, kind='iteration_ceiling', now=NOW))
        state = fixture()
        state['stages'][0]['metrics']['provider_tokens']['input_tokens'] = None
        self.denied(state)

    def test_resolver_delegated_limit_uses_same_finite_one_time_policy(self):
        state = fixture()
        state['settings']['budget_origins']['iteration_ceiling'] = 'resolver_delegated'
        self.assertTrue(recover(state, kind='iteration_ceiling', now=NOW))
        self.assertEqual(30, state['settings']['limits']['iteration_ceiling'])
        before = copy.deepcopy(state)
        self.assertFalse(recover(state, kind='iteration_ceiling', now=NOW))
        self.assertEqual(before, state)

    def test_recent_bound_independent_validation_qualifies_without_source_churn(self):
        state = fixture()
        row = state['stages'][0]
        row.update(stage='sol', changed_files=[], output='/tmp/sol.json',
                   source_revision='candidate-revision')
        state['validation'] = {'verdict': 'PASS', 'output': '/tmp/sol.json',
                               'source_revision': 'candidate-revision'}
        state['settings']['budget_origins']['max_seconds'] = 'resolver_delegated'
        state['active_seconds'] = 700
        self.assertTrue(recover(state, kind='max_seconds', now=NOW))
        self.assertEqual(1200, state['settings']['limits']['max_seconds'])
        self.assertEqual('accepted_independent_validation',
                         state['resolver']['budget_extensions'][-1]['evidence']['source'])
        state = fixture()
        state['stages'][0].update(stage='sol', changed_files=[], output='/tmp/sol.json',
                                  source_revision='candidate-revision')
        state['validation'] = {'verdict': 'PASS', 'output': '/tmp/sol.json',
                               'source_revision': 'candidate-revision'}
        state['settings']['budget_origins']['max_seconds'] = 'resolver_delegated'
        state['active_seconds'] = 700
        state['no_progress_batches'] = 2
        self.assertTrue(recover(state, kind='max_seconds', now=NOW))
        self.assertEqual(2, state['no_progress_batches'])

    def test_validation_progress_cannot_override_actual_no_progress_limit(self):
        state = fixture()
        state['stages'][0].update(stage='sol', changed_files=[], output='/tmp/sol.json',
                                  source_revision='candidate-revision')
        state['validation'] = {'verdict': 'PASS', 'output': '/tmp/sol.json',
                               'source_revision': 'candidate-revision'}
        state['settings']['budget_origins']['max_seconds'] = 'resolver_delegated'
        state['active_seconds'] = 700
        state['settings']['limits']['no_progress_batches'] = 3
        state['no_progress_batches'] = 3
        self.denied(state, 'max_seconds')

    def test_delegated_time_extension_preserves_unknown_usage_after_current_validation(self):
        state = fixture()
        state['stages'][0].update(stage='sol', changed_files=[], output='/tmp/sol.json',
                                  source_revision='candidate-revision')
        state['validation'] = {'verdict': 'PASS', 'output': '/tmp/sol.json',
                               'source_revision': 'candidate-revision'}
        state['settings']['budget_origins']['max_seconds'] = 'resolver_delegated'
        state['stages'][0]['metrics']['provider_tokens'].update(input_tokens=None, output_tokens=None)
        state['active_seconds'] = 700
        self.assertTrue(recover(state, kind='max_seconds', now=NOW))
        evidence = state['resolver']['budget_extensions'][-1]['evidence']
        self.assertTrue(evidence['unknown_usage_preserved'])
        self.assertEqual(0, evidence['known_reported_tokens'])

    def test_unknown_usage_never_extends_user_cap_or_without_current_validation(self):
        for delegated, verdict in ((False, 'PASS'), (True, 'FAIL')):
            with self.subTest(delegated=delegated, verdict=verdict):
                state = fixture()
                state['stages'][0].update(stage='sol', changed_files=[], output='/tmp/sol.json',
                                          source_revision='candidate-revision')
                state['validation'] = {'verdict': verdict, 'output': '/tmp/sol.json',
                                       'source_revision': 'candidate-revision'}
                state['settings']['budget_origins']['max_seconds'] = (
                    'resolver_delegated' if delegated else 'user_explicit')
                state['stages'][0]['metrics']['provider_tokens'].update(input_tokens=None, output_tokens=None)
                state['active_seconds'] = 700
                self.denied(state, 'max_seconds')

    def test_validation_claim_without_matching_accepted_validator_row_is_not_progress(self):
        state = fixture()
        state['stages'][0].update(changed_files=[], output='/tmp/other.json', source_revision='candidate-revision')
        state['validation'] = {'verdict': 'PASS', 'output': '/tmp/sol.json',
                               'source_revision': 'candidate-revision'}
        state['settings']['budget_origins']['max_seconds'] = 'resolver_delegated'
        state['active_seconds'] = 700
        self.denied(state, 'max_seconds')

    def test_no_sentinel_or_invalid_numeric_grant(self):
        for kind in HARD_CEILINGS:
            for value in (0, -1, None, True, "15", [], {}, float("nan"), float("inf"), 10**400):
                state = fixture(kind)
                container = state["settings"]["milestone_checkpoints" if kind == "milestone_max_seconds" else "limits"]
                container["max_seconds" if kind == "milestone_max_seconds" else kind] = value
                self.denied(state, kind)
        state = fixture()
        state["settings"]["limits"]["iteration_ceiling"] = 15.0
        self.denied(state)

    def test_must_be_exhausted_and_have_remaining_extension(self):
        for usage in (None, True, "15", -1, 14, 15, 31, 50, float("nan")):
            state = fixture()
            state["iteration"] = usage
            self.denied(state)
        state = fixture()
        state["settings"]["limits"]["iteration_ceiling"] = 20
        state["iteration"] = 21
        self.assertTrue(recover(state, kind="iteration_ceiling", now=NOW))
        self.assertEqual(30, state["settings"]["limits"]["iteration_ceiling"])

    def test_restart_and_replay_never_repeat_even_if_limit_reset(self):
        for kind in HARD_CEILINGS:
            state = fixture(kind)
            self.assertTrue(recover(state, kind=kind, now=NOW))
            state = json.loads(json.dumps(state))
            self.denied(state, kind)
            original_settings = fixture(kind)["settings"]
            state["settings"] = original_settings
            self.denied(state, kind)

    def test_other_kind_appends_without_rewriting_prior_grant(self):
        state = fixture()
        self.assertTrue(recover(state, kind="iteration_ceiling", now=NOW))
        first = json.dumps(state["resolver"]["budget_extensions"][0])
        self.assertTrue(recover(state, kind="max_seconds", now=NOW))
        self.assertEqual(first, json.dumps(state["resolver"]["budget_extensions"][0]))
        self.assertEqual(2, len(state["resolver"]["budget_extensions"]))

    def test_missing_resolver_created_only_on_success(self):
        state = fixture()
        del state["resolver"]
        self.assertTrue(recover(state, kind="iteration_ceiling", now=NOW))
        self.assertEqual(["budget_extensions"], list(state["resolver"]))
        state = fixture()
        del state["resolver"]
        state["no_progress_batches"] = 1
        self.denied(state)

    def test_rejected_noop_and_synthetic_records_are_not_progress(self):
        for change in ({"rejected": True}, {"abandoned": True}, {"report_only": True},
                       {"runner_owned": True}, {"dry_run": True}, {"timed_out": True},
                       {"changed_files": []}, {"changed_files": "src/main.py"},
                       {"changed_files": [None]}, {"exit_code": False}, {"exit_code": 1},
                       {"finished_at": "2026-09-26T10:00:00+00:00"},
                       {"finished_at": "2026-09-27T10:01:00+00:00"}, {"output": ""}):
            state = fixture()
            state["stages"][0].update(change)
            self.denied(state)

    def test_unknown_usage_in_any_attempt_is_protected(self):
        for change in ({"metrics": {}}, {"metrics": None}, {"accounted": False},
                       {"duration_seconds": None}, {"duration_seconds": True},
                       {"metrics": {"provider_tokens": {"input_tokens": None, "output_tokens": 5}}},
                       {"metrics": {"provider_tokens": {"input_tokens": True, "output_tokens": 5}}}):
            state = fixture()
            old = copy.deepcopy(state["stages"][0])
            old.update(rejected=True, abandoned=True)
            old.update(change)
            state["stages"].insert(0, old)
            self.denied(state)

    def test_retired_token_settings_do_not_restrict_recovery(self):
        for cap in (1, 15, None, True, "100", -1):
            with self.subTest(cap=cap):
                state = fixture()
                state["settings"]["limits"]["max_reported_tokens"] = cap
                self.assertTrue(recover(state, kind="iteration_ceiling", now=NOW))
        self.denied(fixture(), "max_reported_tokens")
        self.denied(fixture(), "provider_quota")

    def test_human_uncertainty_and_failure_boundaries(self):
        for field, value in (
                ("active_stage", {"pid": 10}), ("pending_questions", ["Q1"]),
                ("pending_report_repair", {"attempts": 1}), ("uncertain_artifacts", ["log"]),
                ("active_uncertainty", True), ("pause_requested", True),
                ("billing_restriction", True), ("access_restriction", True),
                ("provider_quota_exhausted", True), ("usage_unknown", True),
                ("failure_loop", True), ("user_request", {"kind": "permission"}),
                ("agent_request", {"request": {"kind": "clarification"}}),
                ("status", "PAUSED_AUTH"), ("no_progress_batches", 1),
                ("no_progress_batches", False), ("consecutive_timeout_recoveries", 2),
                ("failure_history", {"failure": {"count": 3}})):
            with self.subTest(field=field):
                state = fixture()
                state[field] = value
                self.denied(state)

    def test_explicit_active_tool_snapshot_is_stage_only_evidence(self):
        state = fixture("stage_timeout_seconds")
        state["stages"][0]["changed_files"] = []
        activity = {"activity": "running_tool", "process_fallback": False,
                    "active_tool_count": 1, "tool_elapsed_seconds": 20, "tool_limit_seconds": 1800,
                    "stage_limit_seconds": 300, "observed_at": NOW}
        state["stages"][-1]["activity"] = activity
        original = copy.deepcopy(state)
        self.assertTrue(recover(state, kind="stage_timeout_seconds", now=NOW))
        self.assertEqual("activity_monitor_explicit_tool", state["resolver"]["budget_extensions"][0]["evidence"]["source"])
        for kind in ("iteration_ceiling", "max_seconds", "milestone_max_seconds"):
            self.denied(copy.deepcopy(original), kind)
        for change in ({"activity": "provider_active"}, {"activity": "stalled"},
                       {"process_fallback": True}, {"active_tool_count": 0},
                       {"tool_elapsed_seconds": 1800}, {"tool_limit_seconds": 0},
                       {"observed_at": "2026-09-27T09:58:00+00:00"},
                       {"stage_limit_seconds": 200}):
            candidate = copy.deepcopy(original)
            candidate["stages"][-1]["activity"].update(change)
            self.denied(candidate, "stage_timeout_seconds")

    def test_timeout_loop_is_not_rescued_by_older_progress(self):
        state = fixture("stage_timeout_seconds")
        state["stages"].append(copy.deepcopy(state["stages"][-1]))
        self.denied(state, "stage_timeout_seconds")
        self.denied(state, "iteration_ceiling")

    def test_milestone_usage_must_match_current_contract_and_scope(self):
        for change in ({"seconds": None}, {"contract_hash": "old"}, {"id": "M2"},
                       {"reviews_without_progress": 1}):
            state = fixture()
            state["milestone_progress"]["contract:M1"].update(change)
            self.denied(state, "milestone_max_seconds")
        state = fixture()
        state["current_task"]["contract_hash"] = "old"
        self.denied(state, "milestone_max_seconds")
        state = fixture()
        state["stages"][0]["task_id"] = "other-task"
        self.denied(state, "milestone_max_seconds")

    def test_malformed_structures_fail_without_mutation(self):
        for field in ("settings", "resolver", "stages", "failure_history", "goal_contract"):
            for value in (None, "unknown", 3, []):
                state = fixture()
                state[field] = value
                self.denied(state)
        for value in (None, {}, "unknown", [None], [{}], [{"kind": "unknown"}]):
            state = fixture()
            state["resolver"]["budget_extensions"] = value
            self.denied(state)
        for now in (None, 1, "unknown", "2026-09-27T10:00:00"):
            self.denied(fixture(), now=now)


def planning_fixture():
    state = fixture()
    state["settings"]["joint_planning"] = True
    state["failure_history"] = {}
    state["next_stage"] = "astra_finalize"
    state["status"] = "PAUSED_PLANNING_BUDGET"
    body = {"intended_outcome": "Implement retry policy", "open_blocking_questions": [],
            "required_behaviors": ["Retry temporary errors"],
            "acceptance_criteria": [{"id": "C1", "criterion": "Retry a temporary error"}],
            "technical_approach": ["Use bounded retries"],
            "milestones": [{"id": "M1", "objective": "Retry transient errors", "depends_on": []}]}
    revised = copy.deepcopy(body)
    revised["required_behaviors"].append("Preserve the original failure after retry exhaustion")
    reports = {
        "astra_discovery": {"contract": body, "summary": "Initial draft"},
        "astra_challenge": {"concerns": [{"id": "C1", "concern": "Original failure could be lost"}]},
        "glm_revise": {"contract": revised, "summary": "Preserve failure evidence", "responses": [
            {"concern_id": "C1", "change": "Retain original failure", "evidence_refs": ["retry.py:12"]}]},
    }
    template = state["stages"][0]
    state["stages"] = []
    state["planning"] = {"astra_calls": 2, "reports": {}, "final_token": None}
    for name, report in reports.items():
        row = copy.deepcopy(template)
        row.update(stage=name, output=name + "/report.json", changed_files=[], source_revision="source")
        state["stages"].append(row)
        state["planning"]["reports"][name] = {"report": report, "output": row["output"]}
    state["history"] = copy.deepcopy(state["stages"])
    state["goal_contract"]["body"] = copy.deepcopy(revised)
    return state


class PlanningBudgetRecoveryTests(unittest.TestCase):
    def denied(self, state):
        before = json.dumps(state, sort_keys=True)
        self.assertFalse(recover(state, kind=PLANNING_KIND, now=NOW))
        self.assertEqual(before, json.dumps(state, sort_keys=True))

    def test_default_two_extends_once_with_only_declared_delta(self):
        state = planning_fixture()
        before = json.dumps(state, sort_keys=True)
        self.assertTrue(recover(state, kind=PLANNING_KIND, now=NOW))
        self.assertEqual(4, state["planning"].pop("review_call_limit"))
        entry, = state["resolver"].pop("budget_extensions")
        self.assertEqual((PLANNING_KIND, 2, 4, 2), (entry["kind"], entry["from"], entry["to"], entry["used"]))
        self.assertEqual("accepted_revised_plan", entry["evidence"]["source"])
        self.assertNotEqual(entry["evidence"]["body_from"], entry["evidence"]["body_to"])
        self.assertEqual(before, json.dumps(state, sort_keys=True))

    def test_present_unmarked_explicit_and_malformed_limits_protected(self):
        for origin in (None, "user_explicit", "persisted", "unknown", True, {}, []):
            for present in (False, True):
                state = planning_fixture()
                if present:
                    state["planning"]["review_call_limit"] = 2
                state["planning"]["review_call_limit_origin"] = origin
                self.denied(state)
        state = planning_fixture()
        state["planning"]["review_call_limit"] = 2
        self.denied(state)
        for limit in (None, 0, 1, 3, 4, True, 2.0, "2", {}, []):
            state = planning_fixture()
            state["planning"].update(review_call_limit=limit, review_call_limit_origin="runner_default")
            self.denied(state)
        state = planning_fixture()
        state["planning"].update(review_call_limit=2, review_call_limit_origin="runner_default")
        self.assertTrue(recover(state, kind=PLANNING_KIND, now=NOW))

    def test_narrative_reordering_and_self_reported_resolution_not_progress(self):
        for mode in ("summary", "reorder", "whitespace", "audience", "resolved_claim"):
            state = planning_fixture()
            reports = state["planning"]["reports"]
            if mode == "reorder":
                reports["astra_discovery"]["report"]["contract"]["required_behaviors"].append("Keep diagnostics")
            revised = copy.deepcopy(reports["astra_discovery"]["report"]["contract"])
            reports["glm_revise"]["report"]["contract"] = revised
            if mode == "reorder":
                revised["required_behaviors"] = list(reversed(revised["required_behaviors"]))
            elif mode == "whitespace":
                revised["technical_approach"] = [" Use  bounded retries \n"]
            elif mode == "audience":
                revised["intended_outcome"] = "A more elegantly described retry policy"
            elif mode == "resolved_claim":
                reports["glm_revise"]["report"]["responses"][0]["resolved"] = True
            reports["glm_revise"]["report"]["summary"] = "Major progress: all concerns resolved"
            state["goal_contract"]["body"] = copy.deepcopy(revised)
            state["goal_contract"].update(hash="new-revision-only", revision=99)
            self.denied(state)

    def test_stale_reports_cycles_sources_and_frontiers_fail_closed(self):
        for mode in ("old_output", "old_body", "old_time", "old_cycle", "wrong_order", "new_attempt",
                     "wrong_stage", "wrong_source", "finalized", "unbound_report"):
            state = planning_fixture()
            if mode == "old_output":
                state["planning"]["reports"]["glm_revise"]["output"] = "old/report.json"
            elif mode == "old_body":
                state["goal_contract"]["body"]["required_behaviors"] = ["Other task"]
            elif mode == "old_time":
                state["stages"][-1]["finished_at"] = "2026-09-26T10:00:00+00:00"
            elif mode == "old_cycle":
                state["planning_history"] = [copy.deepcopy(state["planning"])]
            elif mode == "wrong_order":
                state["stages"][0], state["stages"][1] = state["stages"][1], state["stages"][0]
            elif mode == "new_attempt":
                state["stages"].append(copy.deepcopy(state["stages"][0]))
            elif mode == "wrong_stage":
                state["next_stage"] = "astra_challenge"
            elif mode == "wrong_source":
                state["stages"][-1]["source_revision"] = "new-source"
            elif mode == "finalized":
                state["planning"]["final_token"] = "already-finalized"
            elif mode == "unbound_report":
                del state["planning"]["reports"]["astra_challenge"]
            self.denied(state)

    def test_timeout_failure_repair_and_grant_history_never_fund_extension(self):
        for flag in ("timed_out", "rejected", "abandoned", "report_only", "dry_run", "automatic_recovery",
                     "planning_recovery_grant", "failure_key", "cleanup_error"):
            state = planning_fixture()
            state["stages"][0][flag] = True
            self.denied(state)
        for key, value in (("recovery_review_grants", [{"consumed": True}]),
                           ("recovery_review_grants", None), ("recovery_review_calls_used", 1),
                           ("recovery_review_calls_used", False)):
            state = planning_fixture()
            state["planning"][key] = value
            self.denied(state)
        for key, value in (("no_progress_batches", 1), ("failure_history", {"old": {"count": 1}}),
                           ("consecutive_timeout_recoveries", 1), ("pending_questions", [{"id": "Q"}])):
            state = planning_fixture()
            state[key] = value
            self.denied(state)

    def test_material_questions_or_missing_concern_evidence_block(self):
        for mode in ("question", "missing_response", "duplicate", "unknown_id", "no_evidence"):
            state = planning_fixture()
            report = state["planning"]["reports"]["glm_revise"]["report"]
            if mode == "question":
                report["contract"]["open_blocking_questions"] = [{"id": "Q1"}]
            elif mode == "missing_response":
                report["responses"] = []
            elif mode == "duplicate":
                report["responses"] *= 2
            elif mode == "unknown_id":
                report["responses"][0]["concern_id"] = "other"
            elif mode == "no_evidence":
                report["responses"][0]["evidence_refs"] = []
            self.denied(state)

    def test_usage_and_existing_protection_gates_still_apply(self):
        for usage in (None, True, "2", 0, 1, 3, 4):
            state = planning_fixture()
            state["planning"]["astra_calls"] = usage
            self.denied(state)
        state = planning_fixture()
        state["stages"][0]["metrics"]["provider_tokens"]["input_tokens"] = None
        self.denied(state)
        state = planning_fixture()
        state["active_stage"] = copy.deepcopy(state["stages"][-1])
        self.denied(state)

    def test_malformed_planning_evidence_fails_closed(self):
        for field in ("reports",):
            for value in (None, [], "unknown", {}):
                state = planning_fixture()
                state["planning"][field] = value
                self.denied(state)
        for value in (None, [], "unknown"):
            state = planning_fixture()
            state["planning"]["reports"]["glm_revise"]["report"] = value
            self.denied(state)
        for field, value in (("concerns", [None]), ("concerns", [{"id": []}]),
                             ("responses", [{"concern_id": "C1", "evidence_refs": None}])):
            state = planning_fixture()
            stage = "astra_challenge" if field == "concerns" else "glm_revise"
            state["planning"]["reports"][stage]["report"][field] = value
            self.denied(state)
        state = planning_fixture()
        del state["goal_contract"]["body"]
        self.denied(state)

    def test_restart_new_cycle_and_limit_reset_do_not_repeat(self):
        state = planning_fixture()
        self.assertTrue(recover(state, kind=PLANNING_KIND, now=NOW))
        state = json.loads(json.dumps(state))
        self.denied(state)
        state["planning"] = planning_fixture()["planning"]
        self.denied(state)

    def test_existing_four_kind_api_accepts_planning_ledger(self):
        state = planning_fixture()
        self.assertTrue(recover(state, kind=PLANNING_KIND, now=NOW))
        first = json.dumps(state["resolver"]["budget_extensions"][0])
        state["status"] = "RUNNING"
        row = copy.deepcopy(fixture()["stages"][0])
        state["stages"].append(row)
        self.assertTrue(recover(state, kind="iteration_ceiling", now=NOW))
        self.assertEqual(first, json.dumps(state["resolver"]["budget_extensions"][0]))
        self.assertEqual(2, state["planning"]["astra_calls"])


if __name__ == "__main__":
    unittest.main()
