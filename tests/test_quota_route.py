"""A quota stop asks the person for a model; the answer is validated and recorded (#184). Pure functions."""
import json
import tempfile
import unittest
from pathlib import Path

import autocode_builder_policy as builder_policy
import autocode_dispatch as dispatch
import autocode_quota_route as quota_route
import autocode_resolver_recovery as resolver_recovery
import autocode_run_view as run_view
import autocode_support as support

QUOTA = {"type": "error", "error": {"message": "subscription usage limit reached"}}
CAPACITY = {"type": "error", "error": {"message": "Selected model is at capacity"}}
RATE = {"type": "turn.failed", "error": {"message": "rate limit reached (429)"}}


class QuotaRouteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def state(self, error=QUOTA, *, stage="sol", role="sol", sol="gpt-5.6-sol", engine="codex"):
        events = self.root / "validator-01.jsonl"
        events.write_text(json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n" + json.dumps(error) + "\n")
        return {"status": "WAITING_FOR_USER",
                "settings": {"engine": engine, "roles": {
                    "terra": {"model": "zai-coding-plan/glm-5.3" if engine == "opencode" else "gpt-5.6-terra"},
                    "sol": {"model": sol}, "completion": {"model": "gpt-6-astra"}}},
                "sessions": {"sol": "old-session", "terra": "builder-session"},
                "active_stage": {"stage": stage, "role": role, "iteration": 1,
                                 "output": str(self.root / "validator-01.json"), "events": str(events)}}

    def stopped(self, state):
        return quota_route.stopped_attempt(state, failure_status=support.failure_status)

    def validate(self, state, model, role="sol", configured_tool=False):
        quota_route.validate(state, role, model, configured_tool=configured_tool,
                             cross_check=dispatch.enforce_cross_model_verification)

    def test_quota_question_names_the_job_and_has_no_default(self):
        state = self.state()
        attempt = self.stopped(state)
        self.assertEqual({"role": "sol", "stage": "sol", "attempt_id": "001/validator-01", "active": True},
                         {key: attempt[key] for key in ("role", "stage", "attempt_id", "active")})
        asked = quota_route.question(state, attempt)
        self.assertEqual("route-sol", asked["id"])
        self.assertTrue(asked["question"].startswith("Tester's quota is exhausted"))
        self.assertEqual(("", [], False, "quota"),
                         (asked["proposed_default"], asked["options"], asked["delegable"], asked["category"]))
        self.assertEqual("gpt-5.6-sol", asked["current_model"])
        advice = quota_route.advice(asked, attempt["attempt_id"])
        self.assertIn("--answer route-sol=MODEL --resolver-token TOKEN", advice)
        self.assertIn("--abandon-stage 001/validator-01, then --resume-paused --sol-model MODEL", advice)

    def test_completion_stop_asks_for_the_completion_route(self):
        state = self.state(stage="astra_review", role="astra")
        state["active_stage"]["route_role"] = "completion"
        asked = quota_route.question(state, self.stopped(state))
        self.assertEqual("route-completion", asked["id"])
        self.assertTrue(asked["question"].startswith("Completion Reviewer's quota"))
        self.assertIn("--completion-model MODEL", quota_route.advice(asked, "001/x"))

    def test_capacity_rate_limit_and_unknown_failures_ask_nothing(self):
        for error in (CAPACITY, RATE, {"type": "error", "error": {"message": "401 unauthorized"}}):
            with self.subTest(error=error["error"]["message"]):
                self.assertIsNone(self.stopped(self.state(error)))

    def test_a_worker_run_or_an_unrouted_role_asks_nothing(self):
        worker = {**self.state(), "parent_run": "/parent"}
        self.assertIsNone(self.stopped(worker))
        self.assertIsNone(self.stopped(self.state(stage="orchestrator", role="orchestrator")))

    def test_an_abandoned_quota_attempt_stays_the_stop_until_a_stage_runs(self):
        state = self.state()
        record = dict(state.pop("active_stage"), abandoned=True)
        state["stages"] = [record, {"stage": "resolver", "runner_owned": True}]
        self.assertFalse(self.stopped(state)["active"])
        state["stages"].append({"stage": "sol", "role": "sol", "events": record["events"]})
        self.assertIsNone(self.stopped(state))

    def test_validate_accepts_another_model_and_refuses_launch_violations(self):
        state = self.state()
        self.validate(state, "gpt-6-luna")
        for model, message in (("gpt-5.6-terra", "identical model"), ("openai/gpt-6-luna", "bare Codex"),
                               ("gpt-5.6-sol", "already uses"), ("gpt 6", "without spaces")):
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, message):
                self.validate(state, model)

    def test_validate_keeps_the_engine_and_the_model_family_rule(self):
        state = self.state(engine="opencode", sol="openai/gpt-6-sol")
        self.validate(state, "xiaomi-token-plan-sgp/mimo-v2")
        with self.assertRaisesRegex(ValueError, "same family glm"):
            self.validate(state, "zai-coding-plan/glm-5.3-flash")
        with self.assertRaisesRegex(ValueError, "provider/model identifier"):
            self.validate(state, "gpt-6-luna")
        self.validate(state, "local-model", configured_tool=True)

    def test_assign_applies_the_model_drops_the_session_and_records_the_change(self):
        state = self.state()
        attempt = self.stopped(state)
        record = quota_route.assign(state, "sol", "gpt-6-luna", at="2026-10-05T00:00:00+00:00", via="answer",
                                    attempt=attempt, request_id="r1")
        self.assertEqual("gpt-6-luna", state["settings"]["roles"]["sol"]["model"])
        self.assertNotIn("sol", state["sessions"])
        self.assertEqual("builder-session", state["sessions"]["terra"])
        self.assertEqual({"kind": "route_assignment", "actor": "user_cli", "role": "sol", "job": "Tester",
                          "from": "gpt-5.6-sol", "to": "gpt-6-luna", "stage": "sol", "via": "answer",
                          "pause_status": "PAUSED_BUDGET", "at": "2026-10-05T00:00:00+00:00",
                          "attempt_id": "001/validator-01", "engine": "codex", "request_id": "r1",
                          "events": attempt["events"]}, record)
        self.assertEqual([record], quota_route.assignments(state))

    def test_a_named_model_is_kept_when_the_next_milestone_starts(self):
        state = self.state(stage="terra", role="terra")
        state.update(goal_contract={"hash": "h"}, current_task={"milestone_id": "M1"})
        builder_policy.lane(state)
        quota_route.assign(state, "terra", "gpt-6-luna", at="t", via="answer", attempt=self.stopped(state))
        state["current_task"] = {"milestone_id": "M2"}
        builder_policy.lane(state)
        self.assertEqual("gpt-6-luna", state["settings"]["roles"]["terra"]["model"])
        # A checker moved off the Builder's model keeps the named model the same way.
        lane = state["builder_retries"][state["builder_retry_key"]]
        lane["checker_routes"] = {"sol": {"model": "gpt-5.6-sol"}}
        quota_route.assign(state, "sol", "gpt-6-nova", at="t", via="answer")
        state["current_task"] = {"milestone_id": "M3"}
        builder_policy.lane(state)
        self.assertEqual("gpt-6-nova", state["settings"]["roles"]["sol"]["model"])

    def test_a_named_model_keeps_a_recovery_packet_binding(self):
        state = self.state()
        bound = resolver_recovery._binding(state, "rev")
        quota_route.assign(state, "sol", "gpt-6-luna", at="t", via="answer", attempt=self.stopped(state))
        self.assertEqual(bound, resolver_recovery._binding(state, "rev"))
        state["settings"]["roles"]["sol"]["model"] = "gpt-6-nova"  # not named at a quota stop
        self.assertNotEqual(bound, resolver_recovery._binding(state, "rev"))

    def test_resume_change_is_recorded_only_for_the_quota_stopped_role(self):
        state = self.state()
        previous = state["settings"]
        changed = json.loads(json.dumps(previous))
        changed["roles"]["terra"]["model"] = "gpt-6-sol"
        self.assertEqual([], quota_route.record_resume_change(state, previous, changed,
                                                              failure_status=support.failure_status, at="t"))
        changed["roles"]["sol"]["model"] = "gpt-6-luna"
        [record] = quota_route.record_resume_change(state, previous, changed,
                                                    failure_status=support.failure_status, at="t")
        self.assertEqual(("sol", "gpt-5.6-sol", "gpt-6-luna", "resume_flag"),
                         (record["role"], record["from"], record["to"], record["via"]))
        capacity = self.state(CAPACITY)
        self.assertEqual([], quota_route.record_resume_change(capacity, capacity["settings"], changed,
                                                              failure_status=support.failure_status, at="t"))

    def test_parse_answer_accepts_only_the_published_quota_question(self):
        asked = quota_route.question(self.state(), self.stopped(self.state()))
        origin = {"pause_status": "PAUSED_BUDGET"}
        self.assertEqual((asked, "gpt-6-luna"), quota_route.parse_answer(["route-sol=gpt-6-luna"], [asked], origin))
        for answers, where, message in (
                (["route-sol=gpt-6-luna"], {"pause_status": "PAUSED_TIME_LIMIT"}, "Only a quota stop"),
                (["route-terra=gpt-6-luna"], origin, "not the quota question"),
                (["route-sol="], origin, "Name the model"),
                (["route-sol=a", "Q1=b"], origin, "on its own")):
            with self.subTest(answers=answers), self.assertRaisesRegex(ValueError, message):
                quota_route.parse_answer(answers, [asked], where)
        disguised = {**asked, "category": "requested_outcome"}
        with self.assertRaisesRegex(ValueError, "not the quota question"):
            quota_route.parse_answer(["route-sol=gpt-6-luna"], [disguised], origin)

    def test_status_view_adds_routes_assignments_and_the_route_need(self):
        state = self.state()
        asked = quota_route.question(state, self.stopped(state))
        state["pending_questions"] = [asked]
        view = run_view.view(state)
        self.assertEqual({"model": "gpt-5.6-sol", "engine": "codex"}, view["routes"]["sol"])
        self.assertEqual([], view["route_assignments"])
        self.assertEqual({"question_id": "route-sol", "role": "sol", "job": "Tester",
                          "current_model": "gpt-5.6-sol", "engine": "codex"}, view["needs"]["route"])
        self.assertEqual("", view["needs"]["questions"][0]["proposed_default"])
        quota_route.assign(state, "sol", "gpt-6-luna", at="t", via="answer")
        self.assertEqual("gpt-6-luna", run_view.view(state)["route_assignments"][0]["to"])


if __name__ == "__main__":
    unittest.main()
