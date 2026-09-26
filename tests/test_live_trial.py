"""Harness tests for the live-trial driver (step 1).

These run offline with the fixture profile. They prove workspace setup, CLI
driving, gate serving, oracle independence and evidence bundles. They do not
evaluate model quality.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_profiles as profiles  # noqa: E402
import live_scenarios as scenarios  # noqa: E402
import live_trial  # noqa: E402
from autopilot_testkit import Bundle, artifacts_root  # noqa: E402


class ProfilesTest(unittest.TestCase):
    def test_resolve_rejects_unknown_profile(self):
        with self.assertRaisesRegex(ValueError, "unknown live profile"):
            profiles.resolve("nope")

    def test_fixture_profile_needs_no_spend_flag(self):
        profile = profiles.resolve("fixture")
        self.assertEqual("fixture", profile["provider"])
        self.assertEqual([], profiles.cli_overrides(profile))

    def test_live_profiles_pin_model_and_effort(self):
        profile = profiles.resolve("glm53")
        flags = profiles.cli_overrides(profile)
        self.assertIn("--terra-model", flags)
        self.assertIn("zai-coding-plan/glm-5.3", flags)
        self.assertIn("--glm-reasoning-effort", flags)
        self.assertIn("max", flags)

    def test_glm53_mimo_uses_subscription_models_only(self):
        profile = profiles.resolve("glm53-mimo")
        flags = profiles.cli_overrides(profile)
        joined = " ".join(flags)
        self.assertIn("--glm-model", flags)
        self.assertIn("zai-coding-plan/glm-5.3", flags)
        self.assertIn("--terra-model", flags)
        self.assertIn("xiaomi-token-plan-sgp/mimo-v2.6-pro", flags)
        self.assertNotIn("-free", joined)
        self.assertNotIn("flash", joined)
        self.assertNotIn("mimo-token-plan/", joined)

    def test_glm53_mimo_verifier_never_equals_producer(self):
        profile = profiles.resolve("glm53-mimo")
        models = profile["role_models"]
        self.assertNotEqual(models["planner"].split("/")[0], models["reviewer"].split("/")[0])
        self.assertNotEqual(models["builder"].split("/")[0], models["validator"].split("/")[0])
        self.assertNotEqual(models["builder"].split("/")[0], models["completion"].split("/")[0])

    def test_glm53_mimo_ladder_efforts_match_docs(self):
        profile = profiles.resolve("glm53-mimo")
        effort = profile["effort"]
        self.assertEqual("medium", effort["requirements"])
        self.assertEqual("high", effort["planner"])
        self.assertEqual("high", effort["reviewer"])
        self.assertEqual("medium", effort["builder"])
        self.assertEqual("high", effort["validator"])
        self.assertEqual("medium", effort["completion"])
        self.assertEqual("high", effort["resolver"])
        flags = profiles.cli_overrides(profile)
        i = flags.index("--requirements-reasoning-effort")
        self.assertEqual("medium", flags[i + 1])
        i = flags.index("--reasoning-effort")
        self.assertEqual("medium", flags[i + 1])
        i = flags.index("--sol-reasoning-effort")
        self.assertEqual("high", flags[i + 1])


class ScenarioTest(unittest.TestCase):
    def test_unknown_scenario_lists_known_ids(self):
        with self.assertRaisesRegex(ValueError, "LIVE-01"):
            scenarios.scenario("LIVE-99")

    def test_classifier_never_calls_a_pause_complete(self):
        self.assertEqual("complete", scenarios.classify_runner_status("TASK_COMPLETE"))
        self.assertEqual("paused", scenarios.classify_runner_status("PAUSED_REPORT_REPAIR_LIMIT"))
        self.assertEqual("paused", scenarios.classify_runner_status("AWAITING_GOAL_APPROVAL"))
        self.assertEqual("stopped", scenarios.classify_runner_status("RUNNING"))


class Fx01OracleTest(unittest.TestCase):
    """The oracle is independent: score known-good and known-bad deliveries."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="fx01-")
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)

    def _write(self, name: str, body: str):
        (self.project / name).write_text(body)

    def test_missing_delivery_fails(self):
        result = scenarios.fx01_greeting_oracle(self.project)
        self.assertEqual(scenarios.FAIL, result.status)
        self.assertTrue(result.failed)

    def test_reference_delivery_scores_12_of_12(self):
        # Same reference bytes the fixture provider ships.
        import live_fixture_provider as fixture
        self._write("greet.py", fixture.GREET_PY)
        self._write("test_greet.py", fixture.TEST_GREET_PY)
        self._write("README.md", fixture.README_MD)
        result = scenarios.fx01_greeting_oracle(self.project)
        self.assertEqual(scenarios.PASS, result.status, result.summary)
        self.assertEqual(12, len(result.checks))
        self.assertEqual([], result.failed)

    def test_wrong_exit_code_is_detected(self):
        import live_fixture_provider as fixture
        self._write("greet.py", fixture.GREET_PY.replace("return 2", "return 1"))
        self._write("test_greet.py", fixture.TEST_GREET_PY)
        self._write("README.md", fixture.README_MD)
        result = scenarios.fx01_greeting_oracle(self.project)
        self.assertEqual(scenarios.FAIL, result.status)
        names = {row["name"] for row in result.failed}
        self.assertIn("no-arg.exit", names)

    def test_non_stdlib_import_is_detected(self):
        import live_fixture_provider as fixture
        self._write("greet.py", fixture.GREET_PY + "\nimport requests\n")
        self._write("test_greet.py", fixture.TEST_GREET_PY)
        self._write("README.md", fixture.README_MD)
        result = scenarios.fx01_greeting_oracle(self.project)
        self.assertEqual(scenarios.FAIL, result.status)
        self.assertIn("stdlib_only", {row["name"] for row in result.failed})


class TrialVerdictTest(unittest.TestCase):
    def test_verdict_matrix(self):
        cases = [
            ("TASK_COMPLETE", scenarios.PASS, scenarios.PASS),
            ("COMPLETE", scenarios.FAIL, scenarios.FALSE_COMPLETE),
            ("TASK_COMPLETE", scenarios.FAIL, scenarios.FALSE_COMPLETE),
            ("PAUSED_USAGE_UNKNOWN", scenarios.FAIL, scenarios.HONEST_BLOCKER),
            ("AWAITING_GOAL_APPROVAL", scenarios.PASS, scenarios.HONEST_BLOCKER),
            ("RUNNING", scenarios.PASS, scenarios.ERROR),
            ("", scenarios.FAIL, scenarios.ERROR),
            ("TASK_COMPLETE", scenarios.ERROR, scenarios.ERROR),
            ("PAUSED_REQUESTED", scenarios.ERROR, scenarios.ERROR),
            ("TASK_COMPLETE", scenarios.DEFERRED, scenarios.DEFERRED),
        ]
        for status, oracle_status, expected in cases:
            with self.subTest(status=status, oracle=oracle_status):
                checks = [{"name": "acceptance", "ok": oracle_status == scenarios.PASS}]
                oracle = scenarios.OracleResult(oracle_status, "independent check", checks)
                spec = {"oracle": mock.Mock(return_value=oracle), "oracle_name": "FX01"}
                result = live_trial.judge({"state": {"status": status}}, spec, HERE, mock.Mock())
                self.assertEqual(expected, result.status)
                self.assertEqual(checks, result.checks)

    def test_failed_check_cannot_be_hidden_by_oracle_pass_label(self):
        oracle = scenarios.OracleResult(scenarios.PASS, "incorrect label", [{"ok": False}])
        spec = {"oracle": mock.Mock(return_value=oracle), "oracle_name": "FX01"}
        result = live_trial.judge({"state": {"status": "TASK_COMPLETE"}}, spec, HERE, mock.Mock())
        self.assertEqual(scenarios.FALSE_COMPLETE, result.status)

    def test_exit_code_and_both_reports_preserve_verdict(self):
        for status, oracle_status, expected, exit_code in [
            ("TASK_COMPLETE", scenarios.PASS, scenarios.PASS, 0),
            ("TASK_COMPLETE", scenarios.FAIL, scenarios.FALSE_COMPLETE, 1),
            ("PAUSED_USAGE_UNKNOWN", scenarios.FAIL, scenarios.HONEST_BLOCKER, 2),
            ("RUNNING", scenarios.PASS, scenarios.ERROR, 1),
        ]:
            with self.subTest(status=status, oracle=oracle_status), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                oracle = scenarios.OracleResult(oracle_status, "oracle result", [
                    {"name": "acceptance", "ok": oracle_status == scenarios.PASS}])
                spec = {"title": "Fixture", "task": "fixture", "oracle_name": "FX01",
                        "oracle": mock.Mock(return_value=oracle)}
                with (mock.patch.dict(os.environ, {"AUTOCODE_TEST_ARTIFACTS": str(root / "evidence")}),
                      mock.patch.object(live_trial, "make_workspace", return_value=root),
                      mock.patch.object(scenarios, "scenario", return_value=spec),
                      mock.patch.object(live_trial, "drive", return_value={"state": {"status": status}})):
                    code = live_trial.main(["LIVE-01", "--workspace", str(root)])
                self.assertEqual(exit_code, code)
                evidence = root / "evidence" / "LIVE-01" / "01"
                self.assertEqual(expected, json.loads((evidence / "result.json").read_text())["status"])
                self.assertEqual(expected, json.loads((evidence / "live-trial.json").read_text())["verdict"])


class LiveTrialSmokeTest(unittest.TestCase):
    """End-to-end offline smoke: the known-PASS scenario under the fixture profile."""

    def test_live01_fixture_profile_reaches_pass(self):
        with tempfile.TemporaryDirectory(prefix="live-trial-smoke-") as temp:
            code = live_trial.main([
                "LIVE-01", "--profile", "fixture", "--workspace", temp,
                "--budget-stages", "40", "--timeout", "120",
            ])
        self.assertEqual(0, code)

    def test_live_profile_requires_authorization(self):
        with tempfile.TemporaryDirectory(prefix="live-trial-auth-") as temp:
            code = live_trial.main([
                "LIVE-01", "--profile", "glm53", "--workspace", temp,
            ])
        self.assertEqual(2, code)

    def test_bundle_records_profile_and_oracle_checks(self):
        with tempfile.TemporaryDirectory(prefix="live-trial-bundle-") as temp:
            live_trial.main([
                "LIVE-01", "--profile", "fixture", "--workspace", temp,
                "--budget-stages", "40", "--timeout", "120",
            ])
        results = sorted(artifacts_root().glob("LIVE-01/*/live-trial.json"))
        self.assertTrue(results, "live-trial.json was not written")
        payload = json.loads(results[-1].read_text())
        self.assertEqual("LIVE-01", payload["scenario"])
        self.assertEqual("fixture", payload["profile"])
        self.assertEqual("FX01", payload["oracle"])
        self.assertEqual(scenarios.PASS, payload["verdict"])
        self.assertEqual("TASK_COMPLETE", payload["runner_status"])
        self.assertTrue(payload["checks"])
        self.assertTrue(all(check["ok"] for check in payload["checks"]))


if __name__ == "__main__":
    unittest.main()
