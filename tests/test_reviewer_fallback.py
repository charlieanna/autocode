"""Offline canonical tests for bounded planning-review route fallback."""
from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_reviewer_fallback as fallback
import autocode_support as support

GLM = "zai-coding-plan/glm-5.3"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"


class ReviewerFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        self.run = self.root / ".autocode" / "runs" / "fixture"
        self.run.mkdir(parents=True)
        (self.root / "source.txt").write_text("source\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "source.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "fixture"], cwd=self.root, check=True)
        discovery = self.run / "discovery.json"
        support.atomic_json(discovery, {"summary": "draft"})
        self.state = {
            "iteration": 1, "next_stage": "astra_challenge", "task_id": "task-1",
            "goal_contract": {"task_id": "task-1", "hash": "contract", "revision": 1},
            "settings": {"joint_planning": True, "roles": {
                "plan_reviewer": {"engine": "opencode", "provider": None, "model": GLM,
                                  "reasoning_effort": "high"},
                "glm": {"engine": "opencode", "provider": None, "model": "qwen-plan/qwen3-coder",
                        "reasoning_effort": "medium"},
                "sol": {"engine": "opencode", "provider": None, "model": MIMO,
                        "reasoning_effort": "max"},
            }},
            "planning": {"astra_calls": 2, "review_call_limit": 2, "reports": {
                "astra_discovery": {"output": str(discovery), "report": {"summary": "draft"}}}},
            "stages": [], "history": [], "automatic_timeout_recoveries": [{}, {}],
            "failure_history": {"same": {"count": 2}},
        }
        self.add_attempt(1)
        self.add_attempt(2, planning_recovery_grant="focused-credit")

    def add_attempt(self, number, **extra):
        directory = self.run / f"archived-{number}"
        directory.mkdir()
        snapshot = support.snapshot(self.root)
        for name in ("before", "after"):
            support.atomic_json(directory / f"{name}.json", snapshot)
        events = directory / "events.jsonl"
        events.write_text(json.dumps({"type": "step_start"}) + "\n")
        record = {
            "stage": "astra_challenge", "role": "plan_reviewer", "route_role": "plan_reviewer",
            "output": str(directory / "report.json"), "events": str(events),
            "before_ref": str(directory / "before.json"), "after_ref": str(directory / "after.json"),
            "source_revision": snapshot["revision"], "changed_files": [], "accounted": True,
            "timed_out": True, "timeout_kind": "idle", "automatic_recovery": True,
            "abandoned": True, "rejected": True, "exit_code": -15,
            "launch_route": {"engine": "opencode", "provider": None, "model": GLM,
                             "reasoning_effort": "high"},
        }
        record.update(extra)
        self.state["stages"].append(record)
        return record

    def reserve(self):
        return fallback.reserve(self.state, self.run, self.root)

    def test_eligible_receipt_is_bound_and_admission_preserves_accounting(self):
        accounting = copy.deepcopy({key: self.state[key] for key in (
            "automatic_timeout_recoveries", "failure_history")})
        calls = self.state["planning"]["astra_calls"]
        grant = self.reserve()
        self.assertEqual(MIMO, grant["binding"]["selected_route"]["model"])
        self.assertEqual("max", grant["binding"]["selected_route"]["reasoning_effort"])
        self.assertIn("source_revision", grant["binding"])
        self.assertIn("contract_digest", grant["binding"])
        self.assertEqual("repeated nonterminal planning-review provider-stream silence",
                         grant["binding"]["evidence"]["observation"])
        selected = fallback.admit(self.state, self.run, self.root)
        self.assertEqual("sol", selected["role"])
        self.assertTrue(grant["consumed"])
        self.assertEqual(calls, self.state["planning"]["astra_calls"])
        self.assertEqual(accounting, {key: self.state[key] for key in accounting})

    def test_fallback_accepts_any_already_configured_independent_model(self):
        for model in ('mimo-token-plan/mimo-v2.6-pro', 'opencode/mimo-v2.6-flash-free', 'new-plan/future-model'):
            with self.subTest(model=model):
                state = copy.deepcopy(self.state)
                state['settings']['roles']['sol']['model'] = model
                current = 'openai/gpt-6-sol'
                state['settings']['roles']['plan_reviewer']['model'] = current
                for record in state['stages']:
                    record['launch_route']['model'] = current
                grant = fallback.reserve(state, self.run, self.root)
                self.assertEqual(model, grant['binding']['selected_route']['model'])

    def test_explicit_pin_denies_without_mutation(self):
        self.state["settings"]["roles"]["plan_reviewer"]["model_pinned"] = True
        before = copy.deepcopy(self.state)
        self.assertIsNone(self.reserve())
        self.assertEqual(before, self.state)

    def test_same_family_independence_denies(self):
        self.state["settings"]["roles"]["glm"]["model"] = MIMO
        before = copy.deepcopy(self.state)
        self.assertIsNone(self.reserve())
        self.assertEqual(before, self.state)

    def test_unavailable_alternate_denies(self):
        self.state["settings"]["roles"]["sol"]["available"] = False
        self.assertIsNone(self.reserve())

    def test_route_provider_or_engine_mismatch_denies(self):
        for key, value in (("provider", "different-billing-route"), ("engine", "codex")):
            with self.subTest(key=key):
                state = copy.deepcopy(self.state)
                state["settings"]["roles"]["sol"][key] = value
                self.assertIsNone(fallback.reserve(state, self.run, self.root))

    def test_changed_evidence_or_settings_fail_closed(self):
        pristine = copy.deepcopy(self.state)
        for name, mutate in (
            ("evidence", lambda: Path(self.state["stages"][0]["events"]).write_text('{"type":"other"}\n')),
            ("settings", lambda: self.state["settings"]["roles"]["sol"].update(reasoning_effort="low")),
            ("pin", lambda: self.state["settings"]["roles"]["plan_reviewer"].update(model_pinned=True)),
        ):
            with self.subTest(name=name):
                self.state = copy.deepcopy(pristine)
                grant = self.reserve()
                mutate()
                with self.assertRaises(support.Paused) as caught:
                    fallback.admit(self.state, self.run, self.root)
                self.assertEqual("PAUSED_REVIEWER_FALLBACK", caught.exception.status)
                self.assertFalse(grant["consumed"])
                Path(self.state["stages"][0]["events"]).write_text(json.dumps({"type": "step_start"}) + "\n")

    def test_changed_source_contract_or_planning_input_fail_closed(self):
        pristine = copy.deepcopy(self.state)
        discovery = Path(self.state["planning"]["reports"]["astra_discovery"]["output"])
        for name, mutate in (
            ("source", lambda: (self.root / "changed.txt").write_text("changed\n")),
            ("contract", lambda: self.state["goal_contract"].update(hash="changed-contract")),
            ("input", lambda: discovery.write_text('{"summary":"changed"}\n')),
        ):
            with self.subTest(name=name):
                self.state = copy.deepcopy(pristine)
                grant = self.reserve()
                mutate()
                with self.assertRaises(support.Paused):
                    fallback.admit(self.state, self.run, self.root)
                self.assertFalse(grant["consumed"])
                (self.root / "changed.txt").unlink(missing_ok=True)
                support.atomic_json(discovery, {"summary": "draft"})

    def test_restart_is_idempotent_and_consumed_grant_cannot_relaunch(self):
        grant = self.reserve()
        self.assertEqual(MIMO, fallback.pending_route(self.state)['model'])
        stage_count = len(self.state["stages"])
        self.assertEqual(grant["id"], self.reserve()["id"])
        self.assertEqual(stage_count, len(self.state["stages"]))
        support.atomic_json(self.run / "state.json", self.state)
        self.state = support.read(self.run / "state.json")
        selected = fallback.admit(self.state, self.run, self.root)
        self.assertEqual(MIMO, selected["model"])
        self.assertIsNone(fallback.pending_route(self.state))
        with self.assertRaises(support.Paused):
            fallback.admit(self.state, self.run, self.root)
        self.assertIsNone(self.reserve())

    def test_failed_fallback_is_exhausted_without_second_switch(self):
        grant = self.reserve()
        fallback.admit(self.state, self.run, self.root)
        counts = (self.state["planning"]["astra_calls"], len(self.state["automatic_timeout_recoveries"]))
        fallback.record_failed(self.state, grant["id"], {"timed_out": True, "timeout_kind": "idle"})
        first = copy.deepcopy(grant)
        fallback.record_failed(self.state, grant["id"], {"different": "ignored after first record"})
        self.assertEqual(first, grant)
        self.assertIsNone(self.reserve())
        self.assertEqual(1, len(self.state["planning"]["reviewer_route_fallbacks"]))
        self.assertEqual(counts, (self.state["planning"]["astra_calls"],
                                  len(self.state["automatic_timeout_recoveries"])))

    def test_consumed_fallback_cannot_switch_again_at_finalize_in_same_cycle(self):
        grant = self.reserve()
        self.assertIsNotNone(grant)
        fallback.admit(self.state, self.run, self.root)
        self.state["next_stage"] = "astra_finalize"
        self.state["stages"] = [row for row in self.state["stages"] if row.get("runner_owned")]
        self.add_attempt(3)
        self.state["stages"][-1]["stage"] = "astra_finalize"
        self.add_attempt(4, planning_recovery_grant="final-focused")
        self.state["stages"][-1]["stage"] = "astra_finalize"
        self.assertIsNone(self.reserve())
        self.assertEqual(1, len(self.state["planning"]["reviewer_route_fallbacks"]))

    def test_terminal_or_non_idle_stream_is_not_misdiagnosed(self):
        for change in ({"timeout_kind": "stage"}, {"timed_out": False}):
            with self.subTest(change=change):
                state = copy.deepcopy(self.state)
                state["stages"][-1].update(change)
                self.assertIsNone(fallback.reserve(state, self.run, self.root))
        Path(self.state["stages"][-1]["events"]).write_text('{"type":"turn.completed"}\n')
        self.assertIsNone(self.reserve())

    def test_two_model_independence_leaves_no_safe_fallback(self):
        # The user's supported live pairing is GLM Planner + MiMo Reviewer.
        # Switching review to the only alternate (GLM) would be self-review.
        self.state['settings']['roles']['glm']['model'] = GLM
        self.state['settings']['roles']['sol']['model'] = GLM
        before = copy.deepcopy(self.state)
        self.assertIsNone(self.reserve())
        self.assertEqual(before, self.state)


if __name__ == "__main__":
    unittest.main()
