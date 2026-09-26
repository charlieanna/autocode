"""OpenCode defaults and safe migration of existing mixed-CLI checkpoints."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_opencode as oc
import autocode_support as support
import test_planning


class OpenCodeRoutingTests(unittest.TestCase):
    def setUp(self):
        self.transport_patch = patch.object(runner, "opencode", oc)
        self.transport_patch.start()
        self.addCleanup(self.transport_patch.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run = Path(temp.name)
        self.identity = {"engine": "opencode", "identity_version": 2, "version": "1.18.31"}
        self.state = {
            "status": "PAUSED_REQUESTED", "next_stage": "sol", "iteration": 12,
            "goal_contract": {"hash": "approved-contract", "approval_status": "approved"},
            "implementation": {"evidence_refs": ["saved-report.json"], "source_revision": "unchanged"},
            "planning": {"astra_calls": 2}, "milestone_progress": {"M1": {"accepted": True}},
            "stages": [{"engine": "codex", "role": "astra", "session_id": "old-astra"}],
            "sessions": {"astra": "old-astra", "sol": "old-sol", "terra": "ses_terra", "glm": "ses_glm"},
            "settings": {"engine": "opencode", "joint_planning": True,
                "transport_identity": self.identity,
                "transport_identities": {"opencode": self.identity, "codex": {"auth_mode": "ChatGPT"}},
                "limits": {"iteration_ceiling": None, "stage_timeout_seconds": 0},
                "roles": {
                    "astra": {"engine": "codex", "provider": "openai", "model": "gpt-6-astra", "reasoning_effort": "high"},
                    "sol": {"engine": "codex", "provider": "openai", "model": "gpt-5.6-sol", "reasoning_effort": "high"},
                    "terra": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3"},
                    "glm": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3"}}}}
        support.atomic_json(self.run / "state.json", self.state)
        for name in ("check_models", "check_subscription_routes"):
            p = patch.object(oc, name)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(oc, "local_settings", return_value=self.identity)
        p.start()
        self.addCleanup(p.stop)

    def test_new_defaults_and_bare_aliases_never_read_codex_login(self):
        for overrides, expected_sol in (
            ({}, "zai-coding-plan/glm-5.3"),
            ({"astra_model": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
              "sol_model": "xiaomi-token-plan-sgp/mimo-v2.6-pro"},
             "xiaomi-token-plan-sgp/mimo-v2.6-pro"),
        ):
            args = test_planning.PlanningTests.configure_args(self, **overrides)
            with patch.object(support, "local_settings", side_effect=AssertionError("Codex must not be used")):
                settings = runner.configure(args, {"workspace": str(self.run), "iteration": 0})
            self.assertEqual({"opencode"}, {c["engine"] for c in settings["roles"].values()})
            self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", settings["roles"]["astra"]["model"])
            self.assertEqual(expected_sol, settings["roles"]["sol"]["model"])
            self.assertEqual({"opencode"}, set(settings["transport_identities"]))

    def test_migration_preserves_approved_work_and_archives_only_codex_sessions(self):
        before = copy.deepcopy(self.state)
        self.assertTrue(runner.migrate_opencode_roles(self.state, self.run, self.run))
        for key in ("status", "next_stage", "iteration", "goal_contract", "implementation", "planning", "milestone_progress", "stages"):
            self.assertEqual(before[key], self.state[key], key)
        self.assertEqual(before["settings"]["limits"], self.state["settings"]["limits"])
        self.assertEqual({"terra": "ses_terra", "glm": "ses_glm"}, self.state["sessions"])
        self.assertEqual({"old-astra", "old-sol"}, {r["old_session"] for r in self.state["session_rotations"]})
        self.assertEqual("openai/gpt-5.6-sol", self.state["settings"]["roles"]["sol"]["model"])
        self.assertEqual({"opencode"}, set(self.state["settings"]["transport_identities"]))
        backup = Path(self.state["configuration_changes"][-1]["backup"])
        self.assertEqual(before, json.loads(backup.read_text()))
        self.assertEqual(self.state, json.loads((self.run / "state.json").read_text()))
        saved = (self.run / "state.json").read_bytes()
        self.assertFalse(runner.migrate_opencode_roles(self.state, self.run, self.run))
        self.assertEqual(saved, (self.run / "state.json").read_bytes())

    def test_unresolved_stages_cannot_change_routes_or_sessions(self):
        for key in ("active_stage", "pending_report_repair", "uncertain_artifacts"):
            state = copy.deepcopy(self.state)
            state[key] = {"saved": True}
            before = copy.deepcopy(state)
            with self.subTest(key=key), self.assertRaises(support.Paused):
                runner.migrate_opencode_roles(state, self.run, self.run)
            self.assertEqual(before, state)
        self.assertEqual([], list(self.run.glob("state.pre-opencode-*")))

    def test_unavailable_models_or_oauth_cannot_change_saved_account_route(self):
        before = copy.deepcopy(self.state)
        original = (self.run / "state.json").read_bytes()
        for name in ("check_models", "check_subscription_routes"):
            with patch.object(oc, name, side_effect=RuntimeError("unavailable")), self.assertRaises(RuntimeError):
                runner.migrate_opencode_roles(self.state, self.run, self.run)
            self.assertEqual(before, self.state)
            self.assertEqual(original, (self.run / "state.json").read_bytes())
        self.assertEqual([], list(self.run.glob("state.pre-opencode-*")))

    def test_custom_provider_is_not_guessed_and_configuration_drift_is_preserved(self):
        self.state["settings"]["roles"]["astra"]["provider"] = "custom"
        with self.assertRaises(support.Paused):
            runner.migrate_opencode_roles(self.state, self.run, self.run)
        self.state["settings"]["roles"]["astra"]["provider"] = "openai"
        before = copy.deepcopy(self.state)
        with patch.object(oc, "local_settings", return_value={**self.identity, "version": "changed"}), self.assertRaises(support.Paused):
            runner.migrate_opencode_roles(self.state, self.run, self.run)
        self.assertEqual(before, self.state)

    def test_explicit_transport_acceptance_uses_validated_current_identity_only_at_clean_pause(self):
        current = {**self.identity, "version": "1.18.32", "config_hashes": {"mimo-token-plan": "current"}}
        self.state["status"] = "PAUSED_TRANSPORT_CHANGED"
        self.state["workspace"] = str(self.run)
        args = test_planning.PlanningTests.configure_args(
            self, resume_paused=True, accept_transport_change=True)
        with patch.object(oc, "local_settings", return_value=current):
            settings = runner.configure(args, self.state)
        self.assertEqual(current, settings["transport_identity"])
        self.assertEqual(current, settings["transport_identities"]["opencode"])
        self.state["status"] = "RUNNING"
        with self.assertRaisesRegex(ValueError, "paused for a transport change"):
            runner.configure(args, self.state)


class OpenCodeMigrationFlow(unittest.TestCase):
    setUp = test_planning.JointFlow.setUp
    launch = test_planning.JointFlow.launch
    saved = test_planning.JointFlow.saved
    prepare = test_planning.JointFlow.prepare
    draft = test_planning.JointFlow.draft
    new_run_engine_args = ()

    def test_resumed_mixed_run_launches_only_opencode_with_fresh_reviewer_sessions(self):
        run, state = self.draft()
        args = ["--run-dir", str(run), "--no-chat"]
        self.launch([*args, "--approve-goal", state["displayed_goal"]], 0)
        state = self.saved()[1]
        for role in ("astra", "sol"):
            cfg = state["settings"]["roles"][role]
            cfg.update(engine="codex", provider="openai", model="gpt-5.6-sol")
            state["sessions"][role] = "legacy-codex-" + role
        state["settings"]["transport_identities"]["codex"] = {"auth_mode": "ChatGPT"}
        (run / "state.json").write_text(json.dumps(state))
        self.launch([*args, "--pause-after-stage"], 2)
        migrated = self.saved()[1]
        self.assertEqual(state["goal_contract"], migrated["goal_contract"])
        self.assertEqual(state["stages"], migrated["stages"][:-1])
        self.assertNotIn("astra", migrated["sessions"])
        self.assertNotIn("sol", migrated["sessions"])
        self.assertIn(migrated["stages"][-1]["engine"], ("runner", "opencode"))
        if migrated["stages"][-1]["engine"] == "runner":
            self.assertTrue(migrated["stages"][-1]["runner_owned"])
        self.assertTrue(Path(migrated["configuration_changes"][-1]["backup"]).is_file())
        for _ in range(3):
            checked = self.saved()[1]
            if any(row["stage"] == "sol" for row in checked["stages"][len(state["stages"]):]):
                break
            self.launch([*args, "--resume-paused", "--pause-after-stage"], 2)
        checked = self.saved()[1]
        validation = next(row for row in reversed(checked["stages"]) if row["stage"] == "sol")
        self.assertEqual("sol", validation["role"])
        self.assertEqual("opencode", validation["command"][0])
        self.assertNotIn("--session", validation["command"])
        self.assertNotEqual(checked["sessions"]["terra"], checked["sessions"]["sol"])


if __name__ == "__main__":
    unittest.main()
