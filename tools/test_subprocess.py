"""Full command-line flow with real processes and an explicitly fake provider."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class SubprocessFlow(unittest.TestCase):
    new_run_engine_args = ("--engine", "codex")

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / "unrelated-project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c", "user.email=f@example.test",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        bin_dir = self.root / "fixture-bin"
        bin_dir.mkdir()
        source = Path(__file__).resolve().parent
        for filename in ("fake_codex.py", "goal_fixtures.py"):
            shutil.copy2(source / filename, bin_dir / ("codex" if filename == "fake_codex.py" else filename))
        (bin_dir / "codex").chmod(0o755)
        self.env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                    "PYTHONDONTWRITEBYTECODE": "1"}
        self.entry = [os.environ["AUTOCODE_TEST_CLI"]] if os.environ.get("AUTOCODE_TEST_CLI") else [sys.executable, str(source / "autocode.py")]

    def launch(self, args, expected, *, answers=None):
        if "--run-dir" not in args and "--engine" not in args:
            args = [*self.new_run_engine_args, *args]
        result = subprocess.run([*self.entry, "--workspace", str(self.project), *args], cwd=self.root, env=self.env,
                                input=answers, capture_output=True, text=True, timeout=30)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return result

    def saved(self):
        run = next((self.project / ".autocode/runs").iterdir())
        return run, json.loads((run / "state.json").read_text())

    def test_chat_brief_feedback_approval_and_autonomous_rework(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "rework"
        result = self.launch(["Build a greeting tool", "--chat"], 0,
                             answers="CLI\nKeep Unicode support\nyes\n")
        run, state = self.saved()
        self.assertEqual("COMPLETE", state["phase"])
        self.assertEqual(["CONTINUE", "REWORK", "COMPLETE"], [d["report"]["status"] for d in state["decisions"]])
        self.assertEqual(2, sum(r["stage"] == "terra" for r in state["stages"]))
        self.assertEqual(2, sum(r["stage"] == "sol" for r in state["stages"]))
        self.assertEqual(["FAIL"], [row["validation"]["verdict"] for row in state["validation_archive"]])
        self.assertIn("Keep Unicode support", state["goal_contract"]["body"]["constraints"])
        self.assertEqual(1, sum(e["kind"] == "goal_approval" for e in state["user_events"]))
        self.assertIn("Acceptance evidence:", result.stdout)
        self.assertIn("End-to-end flow: PASS", result.stdout)
        executed = [row for row in state["stages"] if row["stage"] != "astra_discovery"]
        for record in executed:
            prompt = Path(record["prompt"]).read_text()
            data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
            self.assertEqual(state["goal_contract"], data["goal_contract"])
            self.assertEqual(str(self.project), data["workspace"])
            if record["stage"] == "sol":
                self.assertEqual(data["current_task"]["id"], data["implementation"]["task_id"])
                self.assertIn("commands_run", data["implementation"])
                self.assertEqual(data["source_revision"], data["implementation"]["source_revision"])
        before = (run / "state.json").read_bytes()
        self.launch(["--run-dir", str(run)], 0)
        self.assertEqual(before, (run / "state.json").read_bytes())

    def test_chat_pause_before_approval_resumes_same_brief(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch(["Build a greeting tool", "--chat"], 2, answers="CLI\nno\n")
        run, state = self.saved()
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["phase"])
        self.assertFalse((self.project / "greet.py").exists())
        token = state["displayed_goal"]
        self.launch(["--run-dir", str(run), "--chat"], 0, answers="yes\n")
        _, final = self.saved()
        self.assertEqual(token, final["goal_contract"]["approval_event"]["token"])
        self.assertEqual("COMPLETE", final["phase"])

    def test_completed_run_rechecks_changed_source_and_evidence(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch(["Build a greeting tool", "--chat"], 0, answers="CLI\nyes\n")
        run, state = self.saved()
        args = ["--run-dir", str(run)]
        stage_count = len(state["stages"])
        source = self.project / "greet.py"
        source.write_text(source.read_text() + "\n# external edit\n")
        unchanged = (run / "state.json").read_bytes()
        status = self.launch([*args, "--status"], 0)
        self.assertFalse(json.loads(status.stdout)["completion_current"])
        self.assertEqual(unchanged, (run / "state.json").read_bytes())
        self.launch(args, 2)
        _, paused = self.saved()
        self.assertEqual("PAUSED_STALE_VALIDATION", paused["status"])
        self.assertEqual(stage_count, len(paused["stages"]))
        self.launch([*args, "--resume-paused"], 0)
        _, final = self.saved()
        self.assertEqual(["sol", "astra_review"], [r["stage"] for r in final["stages"][stage_count:]])
        evidence = Path(next(iter(final["validation"]["evidence_hashes"])))
        evidence.write_text(evidence.read_text() + "\n")
        self.launch(args, 2)
        self.assertEqual("PAUSED_STALE_VALIDATION", self.saved()[1]["status"])

    def test_chat_completes_artifact_review_without_busy_loop(self):
        result = self.launch(["Build a greeting tool", "--chat"], 0, answers="CLI\nyes\nyes\n")
        _, state = self.saved()
        self.assertEqual("COMPLETE", state["phase"])
        self.assertEqual(1, len(state["human_reviews"]))
        self.assertIn("Approve artifact criterion C1", result.stdout)

    def test_unexpected_session_pauses_and_can_be_explicitly_abandoned(self):
        self.launch(["Build a greeting tool"], 2)
        run, initial = self.saved()
        args = ["--run-dir", str(run)]
        self.launch([*args, "--answer", "Q1=CLI"], 0)
        self.env["AUTOCODE_FIXTURE_SESSION_DRIFT"] = "1"
        self.launch(args, 2)
        _, paused = self.saved()
        self.assertEqual("PAUSED_UNCERTAIN_STAGE", paused["status"])
        self.assertEqual(initial["sessions"], paused["sessions"])
        self.assertEqual(1, len(paused["stages"]))
        status = json.loads(self.launch([*args, "--status"], 0).stdout)
        unchanged = (run / "state.json").read_bytes()
        self.launch([*args, "--abandon-stage", "001/wrong-01"], 2)
        self.assertEqual(unchanged, (run / "state.json").read_bytes())
        self.launch([*args, "--abandon-stage", status["attempt_id"]], 0)
        _, abandoned = self.saved()
        self.assertEqual("PAUSED_STAGE_ABANDONED", abandoned["status"])
        self.assertEqual(2, len(abandoned["stages"]))
        self.assertTrue(abandoned["stages"][-1]["abandoned"])
        self.assertNotIn("astra", abandoned["sessions"])
        self.assertFalse((self.project / "greet.py").exists())
        del self.env["AUTOCODE_FIXTURE_SESSION_DRIFT"]
        self.launch([*args, "--resume-paused"], 2)
        self.assertEqual("AWAITING_GOAL_APPROVAL", self.saved()[1]["phase"])

    def test_rework_pauses_at_saved_iteration_limit_and_can_resume(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "rework"
        self.launch(["Build a greeting tool", "--chat", "--max-iterations", "1"], 2, answers="CLI\nyes\n")
        run, state = self.saved()
        self.assertEqual("PAUSED_ITERATION_LIMIT", state["status"])
        self.assertEqual("PAUSED_OR_BLOCKED", state["phase"])
        self.assertEqual("REWORK", state["last_decision"]["report"]["status"])
        task_id = state["current_task"]["id"]
        self.assertEqual(1, sum(r["stage"] == "terra" for r in state["stages"]))
        self.launch(["--run-dir", str(run), "--resume-paused", "--max-iterations", "3"], 0)
        _, final = self.saved()
        self.assertEqual("COMPLETE", final["phase"])
        self.assertEqual(task_id, final["current_task"]["id"])
        self.assertEqual(2, sum(r["stage"] == "terra" for r in final["stages"]))

    def test_standalone_cli_full_interview_approval_review_and_completion(self):
        project, launch = self.project, self.launch
        launch(["Build a useful greeting tool", "--reasoning-effort", "high", "--terra-provider", "ZAI"], 2)
        expected_models = {"astra": "gpt-6-astra", "terra": "gpt-5.6-terra", "sol": "gpt-5.6-sol"}
        run = next((project / ".autocode/runs").iterdir())
        args = ["--run-dir", str(run)]
        def state(): return json.loads((run / "state.json").read_text())
        self.assertEqual("DISCOVERING", state()["phase"])
        self.assertEqual("WAITING_FOR_USER", state()["status"])
        self.assertEqual(expected_models, {role:settings["model"]
                                          for role,settings in state()["settings"]["roles"].items()})
        self.assertFalse((project / "greet.py").exists())
        launch([*args, "--answer", "Q1=CLI"], 0)
        self.assertEqual(1, len(state()["stages"]))
        launch(args, 2)
        self.assertEqual("AWAITING_GOAL_APPROVAL", state()["phase"])
        self.assertEqual("CLI", state()["answers"]["Q1"]["text"])
        launch([*args, "--approve-goal", "r1:stale"], 2)
        self.assertFalse((project / "greet.py").exists())
        launch([*args, "--approve-goal", state()["displayed_goal"]], 0)
        self.assertEqual("READY_TO_EXECUTE", state()["phase"])
        self.assertEqual(2, len(state()["stages"]))
        launch(args, 2)
        self.assertEqual("WAITING_FOR_USER", state()["phase"])
        self.assertEqual("human_review", state()["user_request"]["kind"])
        launch([*args, "--resume-paused"], 2)
        before = (project / "greet.py").read_bytes()
        launch([*args, "--approve-review", "C1", "--review-token", state()["displayed_review"]], 0)
        launch(args, 0)
        final = state()
        self.assertEqual("COMPLETE", final["phase"])
        self.assertEqual(expected_models, {role:settings["model"]
                                          for role,settings in final["settings"]["roles"].items()})
        self.assertEqual(before, (project / "greet.py").read_bytes())
        self.assertEqual(1, sum(r["stage"] == "terra" for r in final["stages"]))
        self.assertEqual(["Optional web UI"], final["deferred_backlog"])
        launch(args, 0)
        self.assertEqual(len(final["stages"]), len(state()["stages"]))
        for record in final["stages"]:
            command = record["command"]
            expected = "workspace-write" if record["role"] == "terra" else "read-only"
            self.assertEqual(expected, command[command.index("--sandbox") + 1])
            self.assertEqual(expected_models[record["role"]], command[command.index("--model") + 1])
            self.assertNotIn("--last", command)
            if record["role"] == "terra":
                self.assertIn('model_provider="ZAI"', command)
            else:
                self.assertFalse(any(str(item).startswith("model_provider=") for item in command))

if __name__ == "__main__":
    unittest.main()
