"""A fresh approved-plan correction earns bounded review, never erased usage."""

import contextlib
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_budget_recovery as budgets
import autocode_goals as goals
import autocode_planning_cycle as cycle
import autocode_util as util
from units import autoplanner

NOW = "2026-11-08T00:04:00+00:00"


def contract(body, revision):
    value = {"task_id": "task", "revision": revision, "body": copy.deepcopy(body)}
    return {**value, "hash": util.digest(value), "approval_status": "draft", "approval_event": None}


def attempt(stage, output, *, finished="2026-11-08T00:00:00+00:00", **extra):
    return {
        "stage": stage,
        "output": output,
        "events": output + ".events",
        "source_revision": "source",
        "accounted": True,
        "exit_code": 0,
        "duration_seconds": 10,
        "finished_at": finished,
        "metrics": {"provider_tokens": {"input_tokens": 20, "output_tokens": 10}},
        "role": "planner" if "draft_plan" in stage else "task_planner",
        "iteration": 1,
        "schema": "planner.schema.json",
        "contract_hash": "owner-contract",
        "criteria_revision": 1,
        "supports_sessions": False,
        **extra,
    }


def fixture(*, repaired=False):
    body = {
        "required_behaviors": ["Keep the scoped behavior"],
        "technical_approach": ["Original implementation"],
        "acceptance_criteria": [{"id": "C1", "criterion": "Original behavior works"}],
        "milestones": [{"id": "M1", "objective": "Implement", "depends_on": []}],
        "open_blocking_questions": [],
    }
    old = contract(body, 1)
    token = goals.token(old)
    approval = {"kind": "goal_approval", "actor": "user_cli", "token": token, "at": "2026-11-08T00:01:00+00:00"}
    old.update(approval_status="approved", approval_event=approval)
    state = {
        "task_id": "task",
        "task": "Keep the scoped behavior",
        "status": "RUNNING",
        "next_stage": "review_plan",
        "goal_contract": old,
        "user_events": [approval],
        "turns": [{"event_id": "turn"}],
        "settings": {
            "joint_planning": True,
            "adaptive_planning": True,
            "limits": {"max_cost_usd": 0},
            "roles": {"plan_reviewer": {"model": "independent", "reasoning_effort": "high"}},
            "effort_origins": {"plan_reviewer": "user_explicit"},
        },
        "workflow": {"clarity": "clear", "brief_size": "small"},
        "stages": [],
        "history": [],
        "no_progress_batches": 0,
        "failure_history": {"historical-builder": {"count": 2}},
    }
    cycle.new_cycle(state)
    for stage in ("review_plan", "finalize_plan"):
        row = attempt(stage, stage)
        autoplanner.charge(state, stage, row)
        state["stages"].append(row)
    state["planning"]["final_token"] = token
    state["planning"]["adaptive"] = {"size": "small", "challenges": 1, "review_limit": 2}
    state["stages"].append(
        attempt("builder", "old-timeout", timed_out=True, exit_code=1, finished="2026-11-08T00:01:30+00:00")
    )
    state["status"] = "PAUSED_COMPLETION_GATE"
    with patch.object(goals.s, "now", return_value="2026-11-08T00:02:00+00:00"):
        goals.feedback(state, "Correct the scoped implementation before testing.", interpret=False)
    state["contract_history"] = [copy.deepcopy(state["goal_contract"])]
    body["technical_approach"] = ["Correct the implementation and preserve its original contract"]
    state["goal_contract"] = contract(body, 2)
    cycle.new_cycle(state)
    draft = attempt("draft_plan", "corrected-draft", finished="2026-11-08T00:03:00+00:00")
    state["stages"].append(draft)
    output = draft["output"]
    if repaired:
        draft["rejected"] = True
        repair = attempt(
            "draft_plan_report_repair",
            "accepted-repair",
            finished="2026-11-08T00:03:30+00:00",
            report_only=True,
            original_stage="draft_plan",
            applied_original_events=draft["events"],
        )
        state["stages"].append(repair)
        state["report_repair_history"] = [
            {
                "result": "accepted",
                "original_output": draft["output"],
                "repair": copy.deepcopy(repair),
                "output_hash": "a" * 64,
            }
        ]
        output = repair["output"]
    state["planning"]["reports"] = {"draft_plan": {"report": {"contract": copy.deepcopy(body)}, "output": output}}
    state.update(status="RUNNING", phase="PLANNING", next_stage="review_plan")
    state["history"] = copy.deepcopy(state["stages"])
    return state


class CorrectiveBudgetTests(unittest.TestCase):
    def denied(self, state):
        before = copy.deepcopy(state)
        self.assertFalse(budgets.recover(state, kind=budgets.PLANNING_KIND, now=NOW))
        self.assertEqual(before, state)

    def test_adaptive_correction_renews_once_and_preserves_accounted_timeout_and_repair(self):
        for repaired in (False, True):
            with self.subTest(repaired=repaired):
                state = fixture(repaired=repaired)
                before = copy.deepcopy(state)
                self.assertTrue(budgets.recover(state, kind=budgets.PLANNING_KIND, now=NOW))
                self.assertEqual(
                    (2, 4), (state["planning"]["plan_review_calls"], state["planning"]["review_call_limit"])
                )
                for key in ("stages", "history", "failure_history", "settings", "planning_history"):
                    self.assertEqual(before[key], state[key], key)
                self.assertEqual(
                    "accepted_corrective_plan", state["resolver"]["budget_extensions"][0]["evidence"]["source"]
                )
                self.assertFalse(budgets.recover(state, kind=budgets.PLANNING_KIND, now=NOW))
                another = fixture(repaired=repaired)
                another["resolver"] = copy.deepcopy(state["resolver"])
                self.denied(another)
                autoplanner.charge(state, "review_plan", attempt("review_plan", "new-challenge"))
                state["next_stage"] = "finalize_plan"
                autoplanner.charge(state, "finalize_plan", attempt("finalize_plan", "new-final"))
                self.assertEqual(4, state["planning"]["plan_review_calls"])
                self.assertFalse(goals.approved(state))

    def test_internal_restart_without_external_input_keeps_its_spent_total(self):
        state = fixture()
        state["brief_feedback"] = []
        before = copy.deepcopy(state)
        self.assertFalse(budgets.recover(state, kind=budgets.PLANNING_KIND, now=NOW))
        self.assertEqual(before, state)

    def test_explicit_configured_and_current_or_archived_unmarked_caps_are_protected(self):
        for target in ("settings", "planning", "archived"):
            for origin in (None, "user_explicit", "config", "unknown"):
                with self.subTest(target=target, origin=origin):
                    state = fixture()
                    record = (
                        state["settings"]
                        if target == "settings"
                        else state["planning"]
                        if target == "planning"
                        else state["planning_history"][0]
                    )
                    value_key = "planning_review_call_limit" if target == "settings" else "review_call_limit"
                    origin_key = value_key + "_origin"
                    record[value_key] = 2
                    record.pop(origin_key, None)
                    if origin is not None:
                        record[origin_key] = origin
                    self.denied(state)
        state = fixture()
        state["user_events"].append({"kind": "planning_budget_change", "actor": "user_cli", "limit": 2})
        self.denied(state)

    def test_core_external_feedback_must_join_the_preceding_exact_approval(self):
        for change in (
            "no_approval",
            "wrong_token",
            "model_approval",
            "approval_after",
            "stale_episode",
            "no_feedback_receipt",
        ):
            with self.subTest(change=change):
                state = fixture()
                if change == "no_approval":
                    state["user_events"].pop(0)
                elif change == "wrong_token":
                    state["user_events"][0]["token"] = "r0:foreign"
                elif change == "model_approval":
                    state["user_events"][0]["actor"] = "model"
                elif change == "approval_after":
                    state["user_events"].reverse()
                elif change == "stale_episode":
                    state["clarification_episode"]["started_by"] = "foreign"
                else:
                    state["user_events"].pop()
                self.denied(state)

    def test_old_feedback_cannot_fund_a_later_turn_or_machine_only_replan(self):
        state = fixture()
        state["turns"].append({"event_id": "later-turn"})
        state["planning"]["turn_event_id"] = "later-turn"
        self.denied(state)
        state = fixture()
        report = copy.deepcopy(state["planning"]["reports"]["draft_plan"])
        cycle.new_cycle(state)
        report["output"] = "later-machine-draft"
        state["planning"]["reports"]["draft_plan"] = report
        state["stages"].append(attempt("draft_plan", report["output"], finished="2026-11-08T00:03:30+00:00"))
        self.denied(state)

    def test_rewording_revision_numbers_old_drafts_and_foreign_tasks_do_not_fund_review(self):
        for change in ("same_body", "wrong_body", "old_output", "foreign_task", "stale_draft", "already_reviewed"):
            with self.subTest(change=change):
                state = fixture()
                if change == "same_body":
                    state["goal_contract"] = contract(state["contract_history"][0]["body"], 2)
                    state["planning"]["reports"]["draft_plan"]["report"]["contract"] = copy.deepcopy(
                        state["goal_contract"]["body"]
                    )
                elif change == "wrong_body":
                    state["planning"]["reports"]["draft_plan"]["report"]["contract"] = {}
                elif change == "old_output":
                    state["planning_history"][0]["reports"]["draft_plan"] = copy.deepcopy(
                        state["planning"]["reports"]["draft_plan"]
                    )
                elif change == "foreign_task":
                    state["task_id"] = "foreign"
                elif change == "stale_draft":
                    state["stages"][-1]["finished_at"] = "2026-11-07T23:00:00+00:00"
                else:
                    state["planning"]["reports"]["review_plan"] = {"output": "previous-review"}
                self.denied(state)

    def test_only_the_exact_previously_approved_source_trace_may_be_inherited(self):
        for supplied in ("absent", "none", "foreign"):
            for changed in (False, True):
                with self.subTest(supplied=supplied, changed=changed):
                    state = fixture(repaired=True)
                    previous = state["contract_history"][0]
                    previous["body"]["source_trace"] = {"version": 1, "review": {"output_sha256": "approved-seal"}}
                    previous["hash"] = util.digest({key: previous[key] for key in ("task_id", "revision", "body")})
                    token = goals.token(previous)
                    state["user_events"][0]["token"] = token
                    state["brief_feedback"][0]["contract_token"] = token
                    current_body = copy.deepcopy(state["goal_contract"]["body"])
                    current_body["source_trace"] = copy.deepcopy(previous["body"]["source_trace"])
                    report_body = state["planning"]["reports"]["draft_plan"]["report"]["contract"]
                    if supplied == "none":
                        report_body["source_trace"] = None
                    elif supplied == "foreign":
                        report_body["source_trace"] = {"version": 1, "review": {"output_sha256": "foreign-seal"}}
                    if changed:
                        current_body["source_trace"]["review"]["output_sha256"] = "another-seal"
                    state["goal_contract"] = contract(current_body, 2)
                    if changed or supplied == "foreign":
                        self.denied(state)
                    else:
                        self.assertTrue(budgets.recover(state, kind=budgets.PLANNING_KIND, now=NOW))

    def test_protected_contract_lists_are_material_but_whitespace_is_not(self):
        for key in ("constraints", "scope_exclusions", "permission_boundaries"):
            for changed in (False, True):
                with self.subTest(key=key, changed=changed):
                    state = fixture()
                    old = state["contract_history"][0]
                    old["body"][key] = ["Keep the original boundary"]
                    old["hash"] = util.digest({key: old[key] for key in ("task_id", "revision", "body")})
                    token = goals.token(old)
                    state["user_events"][0]["token"] = token
                    state["brief_feedback"][0]["contract_token"] = token
                    body = copy.deepcopy(old["body"])
                    body[key] = (
                        ["Keep the original boundary", "Preserve the exact correction"]
                        if changed
                        else [" Keep  the original boundary "]
                    )
                    state["goal_contract"] = contract(body, 2)
                    state["planning"]["reports"]["draft_plan"]["report"]["contract"] = copy.deepcopy(body)
                    if changed:
                        self.assertTrue(budgets.recover(state, kind=budgets.PLANNING_KIND, now=NOW))
                    else:
                        self.denied(state)

    def test_pending_custody_repairs_classification_and_failure_guards_still_refuse(self):
        for key in (
            "active_stage",
            "active_runner_check",
            "uncertain_artifacts",
            "pending_report_repair",
            "pending_builder_failure",
            "job_failure",
            "stop_intent",
            "pending_questions",
        ):
            with self.subTest(key=key):
                state = fixture()
                state[key] = {"pending": True}
                self.denied(state)
        for change in (
            {"consecutive_timeouts": 2},
            {"failure_history": {"current": {"count": 3}}},
            {"stuck_investigation": {"stage": "review_plan"}},
            {"applied_interventions": [{"kind": "stop"}]},
        ):
            self.denied({**fixture(), **change})
        state = fixture()
        state["stages"][-1]["metrics"]["provider_tokens"]["input_tokens"] = None
        self.denied(state)

    def test_historical_partial_or_unknown_usage_stays_unknown_only_with_explicit_zero_cost_cap(self):
        for partial, tokens in (
            (True, {"input_tokens": 17, "output_tokens": 3}),
            (False, {"input_tokens": None, "output_tokens": None}),
            (True, None),
        ):
            with self.subTest(partial=partial, tokens=tokens):
                state = fixture(repaired=True)
                state["stages"][2]["metrics"] = {"provider_tokens_partial": partial, "provider_tokens": tokens}
                before = copy.deepcopy(state)
                self.assertTrue(budgets.recover(state, kind=budgets.PLANNING_KIND, now=NOW))
                for key in ("stages", "history", "failure_history", "settings"):
                    self.assertEqual(before[key], state[key])
                evidence = state["resolver"]["budget_extensions"][0]["evidence"]
                self.assertTrue(evidence["unknown_usage_preserved"])
                (observation,) = evidence["historical_unknown_usage"]
                self.assertEqual(before["stages"][2]["metrics"]["provider_tokens"], observation["provider_tokens"])
                self.assertEqual("old-timeout", observation["output"])
                self.assertFalse(budgets.recover(state, kind=budgets.PLANNING_KIND, now=NOW))

    def test_unknown_usage_never_waives_a_finite_missing_or_malformed_cost_cap_or_current_usage(self):
        for cap in ("missing", 1, 0.01, None, "0", False, -1):
            state = fixture(repaired=True)
            state["stages"][2]["metrics"]["provider_tokens_partial"] = True
            if cap == "missing":
                state["settings"]["limits"].pop("max_cost_usd")
            else:
                state["settings"]["limits"]["max_cost_usd"] = cap
            self.denied(state)
        for change in (
            "current_repair",
            "current_original",
            "after_feedback",
            "at_feedback",
            "unknown_duration",
            "unscoped_hold",
        ):
            with self.subTest(change=change):
                state = fixture(repaired=True)
                target = (
                    state["stages"][-1]
                    if change == "current_repair"
                    else state["stages"][-2]
                    if change == "current_original"
                    else state["stages"][2]
                )
                target["metrics"]["provider_tokens_partial"] = True
                if change == "after_feedback":
                    target["finished_at"] = "2026-11-08T00:02:01+00:00"
                elif change == "at_feedback":
                    target["finished_at"] = state["brief_feedback"][-1]["at"]
                elif change == "unknown_duration":
                    target["duration_seconds"] = None
                elif change == "unscoped_hold":
                    state["usage_unknown"] = True
                self.denied(state)

    def test_every_historical_unknown_observation_is_retained_without_zero_fill(self):
        state = fixture(repaired=True)
        for row in state["stages"][:3]:
            row["metrics"] = {"provider_tokens": None, "provider_tokens_partial": True}
        state["history"] = copy.deepcopy(state["stages"])
        before = copy.deepcopy(state)
        self.assertTrue(budgets.recover(state, kind=budgets.PLANNING_KIND, now=NOW))
        self.assertEqual(before["stages"], state["stages"])
        self.assertEqual(before["history"], state["history"])
        evidence = state["resolver"]["budget_extensions"][0]["evidence"]
        self.assertTrue(evidence["unknown_usage_preserved"])
        self.assertEqual(
            [row["output"] for row in before["stages"][:3]],
            [row["output"] for row in evidence["historical_unknown_usage"]],
        )
        self.assertTrue(all(row["provider_tokens"] is None for row in evidence["historical_unknown_usage"]))
        self.assertEqual(
            60, evidence["known_reported_tokens"], "Only the complete current Planner and repair have known totals"
        )

    def test_only_the_bound_accepted_report_repair_can_supply_current_draft_evidence(self):
        for change in (
            "pending",
            "missing",
            "foreign_original",
            "foreign_source",
            "failed_original",
            "bad_hash",
            "rejected_repair",
        ):
            with self.subTest(change=change):
                state = fixture(repaired=True)
                if change == "pending":
                    state["pending_report_repair"] = {"original": state["stages"][-2]}
                elif change == "missing":
                    state["report_repair_history"] = []
                elif change == "foreign_original":
                    state["stages"][-1]["applied_original_events"] = "foreign"
                elif change == "foreign_source":
                    state["stages"][-2]["source_revision"] = "foreign"
                elif change == "failed_original":
                    state["stages"][-2]["timed_out"] = True
                elif change == "bad_hash":
                    state["report_repair_history"][0]["output_hash"] = "unbound"
                else:
                    state["stages"][-1]["rejected"] = True
                self.denied(state)

    def test_current_work_is_readonly_uninterrupted_and_retains_original_repair_pins(self):
        for index in (-1, -2):
            for flag in ("changed_files", "interrupted"):
                state = fixture(repaired=True)
                state["stages"][index][flag] = ["source.py"] if flag == "changed_files" else True
                self.denied(state)
        for key in ("role", "iteration", "contract_hash", "criteria_revision"):
            with self.subTest(pin=key):
                state = fixture(repaired=True)
                state["stages"][-2][key] = "foreign"
                self.denied(state)

    def test_original_fresh_default_is_marked_but_legacy_missing_origins_are_not_inferred(self):
        for settings in ({}, {"planning_review_call_limit": 2}, {"planning_review_call_limit_origin": "config"}):
            state = {"settings": settings}
            cycle.new_cycle(state)
            self.assertEqual(not settings, state["planning"].get("review_call_limit_origin") == "runner_default")
        state = {"settings": {}, "planning": {"plan_review_calls": 2, "reports": {}, "review_depth_reduced": True}}
        cycle.new_cycle(state)
        self.assertEqual(2, state["planning"]["plan_review_calls"])
        self.assertNotIn("review_call_limit_origin", state["planning"])

    def test_runtime_keeps_source_and_repaired_output_checks_before_committing(self):
        import autocode_default_budget_runtime as runtime

        for tampered in (None, "repair_bytes", "original_session", "schema_pin"):
            with self.subTest(tampered=tampered), tempfile.TemporaryDirectory() as directory:
                state = fixture(repaired=True)
                output = Path(directory) / "repair.json"
                output.write_text("accepted bytes")
                digest = util.file_hash(output)
                repair = state["stages"][-1]
                original = state["stages"][-2]
                original_output, original_events = (
                    Path(directory) / "original.json",
                    Path(directory) / "original.events",
                )
                original_output.write_text("original report")
                original_events.write_text("")
                original.update(output=str(original_output), events=str(original_events))
                repair["applied_original_events"] = str(original_events)
                repair["output"] = str(output)
                state["planning"]["reports"]["draft_plan"]["output"] = str(output)
                state["report_repair_history"][0].update(
                    repair=copy.deepcopy(repair), output_hash=digest, original_output=str(original_output)
                )
                if tampered == "repair_bytes":
                    output.write_text("changed bytes")
                elif tampered == "original_session":
                    original_events.write_text('{"type":"thread.started","thread_id":"foreign"}\n')
                elif tampered == "schema_pin":
                    original["schema"] = "foreign.schema.json"
                runner = SimpleNamespace(
                    interventions=SimpleNamespace(serialized=lambda _: contextlib.nullcontext(), pending=lambda _: []),
                    resolver_human=SimpleNamespace(PRIVATE="resolver_human_proposal", current=lambda _: None),
                    source_scope=SimpleNamespace(snapshot=lambda *_args, **_kwargs: {"revision": "source"}),
                    support=SimpleNamespace(snapshot=Mock(), file_hash=util.file_hash),
                    budget_recovery=budgets,
                    now=lambda: NOW,
                    resolver_runtime=SimpleNamespace(_operational_receipt=Mock(return_value="receipt")),
                    progressive_state=SimpleNamespace(apply_recovery_limit=Mock()),
                    write_json=Mock(),
                )
                before = copy.deepcopy(state)
                self.assertEqual(
                    not tampered, runtime.recover(runner, state, directory, directory, budgets.PLANNING_KIND)
                )
                self.assertEqual(0 if tampered else 1, runner.write_json.call_count)
                if tampered:
                    self.assertEqual(before, state)
