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
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HERE))

import live_profiles as profiles  # noqa: E402
import live_scenarios as scenarios  # noqa: E402
import live_trial  # noqa: E402
from autopilot_testkit import Bundle, artifacts_root  # noqa: E402


def git_workspace(path):
    """What make_workspace really returns: a Git project with a committed baseline.

    The driver snapshots it (oracle fingerprint), which needs a repository.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.test",
                        "commit", "--allow-empty", "-qm", "baseline"], check=True)
    return path


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

    def test_glm53_openai_uses_subscription_models_only(self):
        profile = profiles.resolve("glm53-openai")
        flags = profiles.cli_overrides(profile)
        joined = " ".join(flags)
        self.assertIn("--glm-model", flags)
        self.assertIn("zai-coding-plan/glm-5.3", flags)
        self.assertIn("--terra-model", flags)
        self.assertIn("openai/gpt-6-sol", flags)
        self.assertNotIn("mimo", joined)
        self.assertNotIn("-free", joined)
        self.assertNotIn("flash", joined)
        self.assertNotIn("mimo-token-plan/", joined)

    def test_glm53_openai_verifier_never_equals_producer(self):
        profile = profiles.resolve("glm53-openai")
        models = profile["role_models"]
        self.assertNotEqual(models["planner"].split("/")[0], models["reviewer"].split("/")[0])
        self.assertNotEqual(models["builder"].split("/")[0], models["validator"].split("/")[0])
        self.assertNotEqual(models["builder"].split("/")[0], models["completion"].split("/")[0])

    def test_glm53_openai_ladder_efforts_match_docs(self):
        profile = profiles.resolve("glm53-openai")
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
    def test_harness_timeout_preserves_both_reports_and_oracle_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            oracle = scenarios.OracleResult(scenarios.FAIL, "not delivered", [{"name": "artifact", "ok": False}])
            spec = {"title": "Fixture", "task": "fixture", "oracle_name": "FX01",
                    "oracle": mock.Mock(return_value=oracle)}
            with (mock.patch.dict(os.environ, {"AUTOCODE_TEST_ARTIFACTS": str(root / "evidence")}),
                  mock.patch.object(live_trial, "make_workspace", return_value=git_workspace(root / "project")),
                  mock.patch.object(scenarios, "scenario", return_value=spec),
                  mock.patch.object(live_trial, "drive", side_effect=live_trial.TrialError("deadline exhausted"))):
                self.assertEqual(1, live_trial.main(["LIVE-01", "--workspace", str(root)]))
            evidence = root / "evidence/LIVE-01/01"
            self.assertEqual("ERROR", json.loads((evidence / "result.json").read_text())["status"])
            report = json.loads((evidence / "live-trial.json").read_text())
            self.assertEqual("ERROR", report["verdict"])
            self.assertEqual(oracle.checks, report["checks"])

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
                      mock.patch.object(live_trial, "make_workspace", return_value=git_workspace(root / "project")),
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
        for flags in ([], ["--authorize-deployment"]):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory(prefix="live-trial-auth-") as temp:
                code = live_trial.main([
                    "LIVE-01", "--profile", "glm53", "--workspace", temp, *flags,
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


class DrivingBoundsTest(unittest.TestCase):
    def test_deadline_is_shared_across_cli_steps(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            states = [{"status": "WAITING_FOR_USER", "pending_questions": [{"id": "Q1", "options": ["yes"]}]},
                      {"status": "TASK_COMPLETE"}, {"status": "TASK_COMPLETE"}]
            with (mock.patch.object(live_trial.time, "monotonic", side_effect=[0, 2, 5]),
                  mock.patch.object(live_trial, "_discover_run_dir", return_value=root),
                  mock.patch.object(live_trial, "load_state", side_effect=states),
                  mock.patch.object(live_trial, "invoke", return_value=subprocess.CompletedProcess([], 0, "", "")) as invoke):
                live_trial.drive(root, root, profiles.resolve("fixture"), "task", 8, 20, mock.Mock())
            self.assertEqual([18, 15], [call.args[-1] for call in invoke.call_args_list])

    def test_unhandled_pause_stops_without_blind_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (mock.patch.object(live_trial, "_discover_run_dir", return_value=root),
                  mock.patch.object(live_trial, "load_state", return_value={"status": "PAUSED_USAGE_UNKNOWN"}),
                  mock.patch.object(live_trial, "invoke", return_value=subprocess.CompletedProcess([], 2, "", "")) as invoke):
                result = live_trial.drive(root, root, profiles.resolve("fixture"), "task", 8, 20, mock.Mock())
            self.assertEqual("PAUSED_USAGE_UNKNOWN", result["state"]["status"])
            self.assertEqual(1, invoke.call_count)

    def test_review_uses_public_cli_and_artifact_token(self):
        state = {"status": "WAITING_FOR_USER", "user_request": {"kind": "human_review", "criteria": ["C1"]},
                 "displayed_review": "artifact-token"}
        step = mock.Mock()
        self.assertTrue(live_trial._serve_gate(state, HERE, HERE, profiles.resolve("fixture"), step))
        self.assertEqual(["--approve-review", "C1", "--review-token", "artifact-token"], step.call_args.args[1][-4:])
        del state["displayed_review"]
        with self.assertRaises(live_trial.TrialError):
            live_trial._serve_gate(state, HERE, HERE, profiles.resolve("fixture"), step)

    def test_timeout_stops_provider_in_separate_process_group(self):
        import psutil
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "child.pid"
            script = ("import subprocess,sys,time; from pathlib import Path; "
                      "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
                      f"Path({str(marker)!r}).write_text(str(child.pid)); time.sleep(60)")
            # invoke raises TimeoutExpired carrying the stopped processes; the driver's step()
            # turns it into a TrialError recording timed_out (the budget stop).
            with self.assertRaises(subprocess.TimeoutExpired) as stopped:
                live_trial.invoke([sys.executable, "-c", script], dict(os.environ), root, 1)
            self.assertIsNotNone(getattr(stopped.exception, "processes", None))
            pid = int(marker.read_text())
            try:
                started = psutil.Process(pid).create_time()
            except psutil.NoSuchProcess:
                started = None
            def cleanup_child():
                if started is None:
                    return
                try:
                    child = psutil.Process(pid)
                    if child.create_time() != started or child.status() == psutil.STATUS_ZOMBIE:
                        return
                    child.terminate()
                    try:
                        child.wait(timeout=1)
                    except psutil.TimeoutExpired:
                        if child.create_time() == started:
                            child.kill()
                            child.wait(timeout=1)
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    pass
            self.addCleanup(cleanup_child)
            deadline = time.monotonic() + 2
            while True:
                try:  # a zombie awaiting a reaper (containers whose PID 1 never reaps) is stopped too
                    running = psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
                except psutil.NoSuchProcess:
                    running = False
                if not running or time.monotonic() >= deadline:
                    break
                time.sleep(.01)
            self.assertFalse(running)


class ProgramModeTest(unittest.TestCase):
    """`--mode program` drives `autocode program run` and serves each child run's gates."""

    TOKEN = "a1:" + "0" * 64

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="program-mode-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = mock.patch.dict(os.environ, {"AUTOCODE_TEST_ARTIFACTS": str(self.root / "artifacts")})
        env.start()
        self.addCleanup(env.stop)
        self.project = self.root / "project"
        self.project.mkdir()
        self.integration = self.root / "integration"
        self.integration.mkdir()
        self.child_run = self.root / "child-run"
        self.child_run.mkdir()
        self.calls: list[list[str]] = []
        self.agreement_approved = False
        # What `program run` reports once the agreement is approved, one entry per invocation.
        self.program_status = ["WAITING", "COMPLETE"]

    def fake_invoke(self, cmd, env, cwd, timeout):
        self.calls.append(cmd)
        if cmd[2:4] == ["program", "approve"]:
            if cmd[cmd.index("--token") + 1] != self.TOKEN:  # the real command refuses any other token
                return subprocess.CompletedProcess(cmd, 2, "", "approve only the exact token")
            self.agreement_approved = True
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"approved": self.TOKEN, "affected": ["contracts"]}), "")
        if cmd[2:4] == ["program", "run"] and not self.agreement_approved:
            # The real controller starts nothing, not even the integration branch, before approval.
            summary = {"status": "WAITING_AGREEMENT_APPROVAL", "state_file": str(self.root / "state.json"),
                       "integration_workspace": None,
                       "agreement": {"revision": 0, "approved": False, "token": None,
                                     "pending": {"revision": 1, "token": self.TOKEN}},
                       "workstreams": [{"id": "contracts", "status": "PENDING", "skeleton": True}]}
            return subprocess.CompletedProcess(cmd, 2, json.dumps(summary), "")
        if cmd[2:4] == ["program", "run"]:
            status = self.program_status.pop(0)
            child_status = "AWAITING_GOAL_APPROVAL" if status == "WAITING" else "TASK_COMPLETE"
            (self.child_run / "state.json").write_text(json.dumps(
                {"status": child_status, "displayed_goal": "r1:abc"}))
            summary = {"status": status, "state_file": str(self.root / "state.json"),
                       "integration_workspace": str(self.integration),
                       "workstreams": [{"id": "contracts", "status": "WAITING" if status == "WAITING" else "MERGED",
                                        "run_dir": str(self.child_run), "workspace": str(self.root / "wt")}]}
            return subprocess.CompletedProcess(cmd, 2 if status != "COMPLETE" else 0, json.dumps(summary), "")
        if "--approve-goal" in cmd:
            (self.child_run / "state.json").write_text(json.dumps({"status": "RUNNING"}))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command {cmd}")

    def kinds(self):
        return [" ".join(cmd[2:4]) if cmd[2] == "program" else "approve-goal" if "--approve-goal" in cmd else str(cmd)
                for cmd in self.calls]

    def test_program_command_passes_profile_flags_without_authorizing_deployment(self):
        cmd = live_trial.program_command(self.project, profiles.resolve("fixture"), self.root / "program.json")
        self.assertEqual(["program", "run"], cmd[2:4])
        self.assertNotIn("--authorize-deployment", cmd)
        self.assertIn("--joint-planning", cmd)
        self.assertNotIn("--in-place", cmd)
        self.assertNotIn("--no-chat", cmd)

    def test_program_command_authorizes_deployment_only_when_explicit(self):
        cmd = live_trial.program_command(self.project, profiles.resolve("fixture"), self.root / "program.json",
                                         authorize_deployment=True)
        self.assertEqual(1, cmd.count("--authorize-deployment"))

    def test_program_deadline_is_shared_across_gate_steps(self):
        bundle = mock.Mock()
        self.program_status = ["WAITING", "COMPLETE"]
        with (mock.patch.object(live_trial.time, "monotonic", side_effect=[0, 2, 5, 8, 11, 14]),
              mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke) as invoke):
            live_trial.drive_program(self.project, self.root, profiles.resolve("fixture"),
                                     {"version": 1}, 8, 20, bundle)
        # run, approve the agreement, run, approve the child plan, run: one shared deadline.
        self.assertEqual([18, 15, 12, 9, 6], [call.args[-1] for call in invoke.call_args_list])

    def test_cli_deployment_opt_in_is_independent_of_live_spend(self):
        spec = {"title": "Fixture", "task": "fixture program", "program_manifest": {"version": 1, "name": "x"},
                "oracle_name": "T", "oracle": lambda project: scenarios.OracleResult(scenarios.PASS, "ok", [])}
        for profile in ("fixture", "glm53"):
            for authorize in (False, True):
                with self.subTest(profile=profile, authorize=authorize):
                    self.calls.clear()
                    self.agreement_approved = False
                    self.program_status = ["WAITING", "COMPLETE"]
                    flags = ["--i-authorize-live-model-spend"] if profile != "fixture" else []
                    if authorize:
                        flags.append("--authorize-deployment")
                    with (mock.patch.object(live_trial, "make_workspace", return_value=git_workspace(self.project)),
                          mock.patch.object(scenarios, "scenario", return_value=spec),
                          mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke)):
                        code = live_trial.main(["PROGRAM-01", "--mode", "program", "--profile", profile,
                                                "--workspace", str(self.root), *flags])
                    if profile == "fixture":
                        self.assertEqual(0, code)
                        self.assertEqual(["program run", "program approve", "program run", "approve-goal",
                                          "program run"], self.kinds())
                    else:
                        # A live profile stops at the program agreement, an honest pause: the harness
                        # never approves for a human.
                        self.assertEqual(2, code)
                        self.assertEqual([["program", "run"]], [cmd[2:4] for cmd in self.calls])
                    for cmd in self.calls:
                        is_program = cmd[2:4] == ["program", "run"]
                        self.assertEqual(authorize and is_program, "--authorize-deployment" in cmd)
                        self.assertNotIn("--i-authorize-live-model-spend", cmd)

    def test_a_program_pass_reruns_while_a_child_waits_only_for_its_next_invocation(self):
        advances = live_trial._program_advances
        self.assertTrue(advances({"status": "RUNNING", "workstreams": []}))
        # e.g. after the program's own feedback on a plan that dropped an inherited requirement
        self.assertTrue(advances({"status": "WAITING", "workstreams": [{"status": "WAITING", "run_status": "RUNNING"}]}))
        self.assertFalse(advances({"status": "WAITING", "workstreams": [
            {"status": "WAITING", "run_status": "AWAITING_GOAL_APPROVAL"}]}))
        self.assertFalse(advances({"status": "WAITING_CHANGE_REQUEST", "workstreams": [
            {"status": "WAITING", "run_status": "RUNNING", "blocked_reason": "change request CR-1 is open"}]}))

    def test_gates_are_served_per_child_and_the_product_is_the_integration_worktree(self):
        bundle = Bundle("PROGRAM-TEST")
        with mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke):
            run = live_trial.drive_program(self.project, self.root, profiles.resolve("fixture"),
                                           {"version": 1, "name": "x"}, 20, 60, bundle)
        self.assertEqual("COMPLETE", run["state"]["status"])
        self.assertEqual(self.integration, run["product"])
        self.assertEqual(["program run", "program approve", "program run", "approve-goal", "program run"],
                         self.kinds())
        self.assertTrue(all("--authorize-deployment" not in cmd for cmd in self.calls))
        approve = self.calls[3]
        self.assertIn("--approve-goal", approve)
        self.assertEqual(str(self.root / "wt"), approve[approve.index("--workspace") + 1])
        self.assertEqual(str(self.child_run), approve[approve.index("--run-dir") + 1])

    def test_only_the_fixture_profile_approves_the_program_agreement(self):
        # Fixture: the exact displayed token is approved for the manifest the driver wrote, then the program runs.
        with mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke):
            run = live_trial.drive_program(self.project, self.root, profiles.resolve("fixture"),
                                           {"version": 1, "name": "x"}, 20, 60, Bundle("PROGRAM-TEST"))
        self.assertEqual("COMPLETE", run["state"]["status"])
        approve = self.calls[1]
        self.assertEqual(["program", "approve", str(self.root / "program.json")], approve[2:5])
        self.assertEqual(str(self.project), approve[approve.index("--workspace") + 1])
        self.assertEqual(self.TOKEN, approve[approve.index("--token") + 1])
        self.assertEqual(["program run", "program approve", "program run"], self.kinds()[:3])

        # A live profile stops at the agreement without approving it; nothing ran, so the product is the project.
        self.calls.clear()
        self.agreement_approved = False
        self.program_status = ["WAITING", "COMPLETE"]
        bundle = Bundle("PROGRAM-TEST")
        with mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke):
            run = live_trial.drive_program(self.project, self.root, profiles.resolve("glm53"),
                                           {"version": 1, "name": "x"}, 20, 60, bundle)
        self.assertEqual(["program run"], self.kinds())
        self.assertFalse(self.agreement_approved)
        self.assertEqual("WAITING_AGREEMENT_APPROVAL", run["state"]["status"])
        self.assertEqual(self.project, run["product"])
        spec = {"oracle": lambda project: scenarios.OracleResult(scenarios.PASS, "ok", []), "oracle_name": "T"}
        self.assertEqual(scenarios.HONEST_BLOCKER, live_trial.judge(run, spec, run["product"], bundle).status)

        # A person approved the agreement: the live profile still never approves a child plan.
        self.calls.clear()
        self.agreement_approved = True
        with (mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke),
              self.assertRaises(live_trial.HumanReviewRequired)):
            live_trial.drive_program(self.project, self.root, profiles.resolve("glm53"),
                                     {"version": 1, "name": "x"}, 20, 60, Bundle("PROGRAM-TEST"))
        self.assertEqual(["program run"], self.kinds())

    def test_an_agreement_approval_that_does_not_take_is_not_repeated(self):
        def ignored(cmd, env, cwd, timeout):
            if cmd[2:4] == ["program", "approve"]:
                self.calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "{}", "")
            return self.fake_invoke(cmd, env, cwd, timeout)

        with (mock.patch.object(live_trial, "invoke", side_effect=ignored),
              self.assertRaisesRegex(live_trial.TrialError, "still pending after it was approved")):
            live_trial.drive_program(self.project, self.root, profiles.resolve("fixture"),
                                     {"version": 1, "name": "x"}, 20, 60, Bundle("PROGRAM-TEST"))
        self.assertEqual(["program run", "program approve", "program run"], self.kinds())

    def test_a_harness_error_before_any_integration_branch_scores_the_project(self):
        spec = {"title": "Fixture", "task": "fixture program", "program_manifest": {"version": 1, "name": "x"},
                "oracle_name": "T", "oracle": lambda project: scenarios.OracleResult(scenarios.PASS, "ok", [])}

        def no_summary(cmd, env, cwd, timeout):
            # The controller saved its state (no integration branch before approval), then printed no JSON.
            saved = self.project / ".autocode/programs/x-000000/state.json"
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_text(json.dumps({"status": "WAITING_AGREEMENT_APPROVAL", "integration": None}))
            return subprocess.CompletedProcess(cmd, 2, "not json", "")

        with (mock.patch.object(live_trial, "make_workspace", return_value=git_workspace(self.project)),
              mock.patch.object(scenarios, "scenario", return_value=spec),
              mock.patch.object(live_trial, "invoke", side_effect=no_summary)):
            code = live_trial.main(["PROGRAM-01", "--mode", "program", "--workspace", str(self.root)])
        self.assertEqual(1, code)
        report = json.loads(next((self.root / "artifacts").glob("PROGRAM-01/*/live-trial.json")).read_text())
        self.assertEqual(("ERROR", str(self.project), "WAITING_AGREEMENT_APPROVAL"),
                         (report["verdict"], report["product"], report["runner_status"]))

    def test_a_program_stop_without_a_servable_gate_is_never_promoted(self):
        self.program_status = ["BLOCKED"]
        bundle = Bundle("PROGRAM-TEST")

        def blocked(cmd, env, cwd, timeout):
            self.calls.append(cmd)
            summary = {"status": "BLOCKED", "state_file": str(self.root / "s.json"),
                       "integration_workspace": str(self.integration),
                       "workstreams": [{"id": "contracts", "status": "FAILED"}]}
            return subprocess.CompletedProcess(cmd, 2, json.dumps(summary), "")

        with mock.patch.object(live_trial, "invoke", side_effect=blocked):
            run = live_trial.drive_program(self.project, self.root, profiles.resolve("fixture"),
                                           {"version": 1, "name": "x"}, 20, 60, bundle)
        self.assertEqual(1, len(self.calls))
        spec = {"oracle": lambda project: scenarios.OracleResult(scenarios.PASS, "ok", []), "oracle_name": "T"}
        verdict = live_trial.judge(run, spec, self.integration, bundle)
        self.assertEqual(scenarios.ERROR, verdict.status)
        self.assertEqual("paused", live_trial.classify_program_status("AUTHORIZATION_REQUIRED"))
        self.assertEqual("paused", live_trial.classify_program_status("PAUSED_MERGE_CONFLICT"))
        self.assertEqual("complete", live_trial.classify_program_status("COMPLETE"))

    def test_a_program_level_pause_is_neither_rerun_nor_served(self):
        # The controller pauses before it advances any child, so a child that would otherwise wait only for
        # the next pass (or show a gate) moves nothing: the driver stops at the pause for a person.
        for child_status in ("RUNNING", "AWAITING_GOAL_APPROVAL"):
            with self.subTest(child_status=child_status):
                self.calls.clear()
                (self.child_run / "state.json").write_text(json.dumps(
                    {"status": child_status, "displayed_goal": "r1:abc"}))

                def paused(cmd, env, cwd, timeout):
                    self.calls.append(cmd)
                    summary = {"status": "PAUSED_INTEGRATION_CHECK", "state_file": str(self.root / "s.json"),
                               "integration_workspace": str(self.integration),
                               "workstreams": [{"id": "search", "status": "WAITING", "run_status": "RUNNING",
                                                "run_dir": str(self.child_run), "workspace": str(self.root / "wt")}]}
                    return subprocess.CompletedProcess(cmd, 2, json.dumps(summary), "")

                with mock.patch.object(live_trial, "invoke", side_effect=paused):
                    run = live_trial.drive_program(self.project, self.root, profiles.resolve("fixture"),
                                                   {"version": 1, "name": "x"}, 20, 60, Bundle("PROGRAM-TEST"))
                self.assertEqual(["program run"], self.kinds())
                self.assertEqual("PAUSED_INTEGRATION_CHECK", run["state"]["status"])

    def test_a_merge_conflict_still_lets_the_other_workstreams_gates_be_served(self):
        # A conflict holds only its own workstream; the controller goes on merging and starting the others.
        self.agreement_approved = True
        statuses = ["PAUSED_MERGE_CONFLICT", "COMPLETE"]

        def conflict(cmd, env, cwd, timeout):
            if cmd[2:4] != ["program", "run"]:
                return self.fake_invoke(cmd, env, cwd, timeout)
            self.calls.append(cmd)
            status = statuses.pop(0)
            if status != "COMPLETE":
                (self.child_run / "state.json").write_text(json.dumps(
                    {"status": "AWAITING_GOAL_APPROVAL", "displayed_goal": "r1:abc"}))
            summary = {"status": status, "state_file": str(self.root / "s.json"),
                       "integration_workspace": str(self.integration),
                       "workstreams": [{"id": "a", "status": "CONFLICT", "run_status": "TASK_COMPLETE"},
                                       {"id": "search", "status": "WAITING", "run_status": "AWAITING_GOAL_APPROVAL",
                                        "run_dir": str(self.child_run), "workspace": str(self.root / "wt")}]}
            return subprocess.CompletedProcess(cmd, 0 if status == "COMPLETE" else 2, json.dumps(summary), "")

        with mock.patch.object(live_trial, "invoke", side_effect=conflict):
            run = live_trial.drive_program(self.project, self.root, profiles.resolve("fixture"),
                                           {"version": 1, "name": "x"}, 20, 60, Bundle("PROGRAM-TEST"))
        self.assertEqual(["program run", "approve-goal", "program run"], self.kinds())
        self.assertEqual("COMPLETE", run["state"]["status"])

    def test_mode_program_needs_a_manifest_and_score_only_scores_without_driving(self):
        with mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke):
            self.assertEqual(2, live_trial.main(["LIVE-01", "--mode", "program", "--workspace", str(self.root)]))
        self.assertEqual([], self.calls)
        import scenario_references as references
        product = self.root / "delivered"
        references.write(references.BUGFIX_REFERENCE, product)
        with mock.patch.object(live_trial, "invoke", side_effect=self.fake_invoke):
            self.assertEqual(0, live_trial.main(["BUGFIX-01", "--score-only", str(product)]))
        self.assertEqual([], self.calls)
        report = json.loads(next((self.root / "artifacts").glob("BUGFIX-01/*/live-trial.json")).read_text())
        self.assertEqual(("PASS", "score-only", "bugfix"), (report["verdict"], report["mode"], report["task_type"]))


if __name__ == "__main__":
    unittest.main()
