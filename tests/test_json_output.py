"""--json on user actions: one JSON object on stdout, everything else on stderr (#714)."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode as runner
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_json_output as json_output
import autocode_support as support
from goal_fixtures import body, seed_greeting_workspace


class JsonOutputTests(unittest.TestCase):
    """The stream routing, with a stand-in status payload."""

    def setUp(self):
        self.addCleanup(json_output.restore)

    def args(self, **flags):
        return SimpleNamespace(json=True, status=False, explain=False, dry_run=False, **flags)

    def test_under_json_everything_printed_goes_to_stderr_and_the_streams_come_back(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            json_output.begin(self.args())
            print("Saved; no agent launched by this action.")
            print("Input rejected: stale token", file=sys.stderr)
            self.assertEqual(2, json_output.finish(2))
            json_output.restore()
            self.assertIs(out, sys.stdout)
            self.assertIs(err, sys.stderr)
        result = json.loads(out.getvalue())["result"]
        self.assertEqual(
            {
                "exit_code": 2,
                "saved": False,
                "message": "Saved; no agent launched by this action.",
                "error": {"code": "input_rejected", "message": "stale token"},
            },
            result,
        )
        lines = err.getvalue().strip().splitlines()
        self.assertEqual("Saved; no agent launched by this action.", lines[0])
        self.assertEqual({"error": result["error"]}, json.loads(lines[-1]))

    def test_one_object_only_and_the_read_commands_are_left_alone(self):
        out = io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(io.StringIO()),
            patch.object(json_output.status_command, "render", lambda *_: print('{"status": "TASK_COMPLETE"}')),
        ):
            json_output.begin(self.args())
            json_output.status(runner, {}, None, None, None)
            json_output.finish(0)  # the status payload was the one object
            json_output.restore()
        self.assertEqual({"status": "TASK_COMPLETE"}, json.loads(out.getvalue()))
        for read in ("status", "explain", "dry_run"):
            with self.subTest(read=read):
                json_output.begin(SimpleNamespace(**{**vars(self.args()), read: True}))
                self.assertNotIsInstance(sys.stdout, json_output._Copy)
        json_output.begin(SimpleNamespace(json=False))
        self.assertNotIsInstance(sys.stdout, json_output._Copy)


class JsonActionCliTests(unittest.TestCase):
    """The CLI on a saved run whose plan waits for approval, in process (autocode.main)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        registry = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry-home")})
        registry.start()
        self.addCleanup(registry.stop)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        seed_greeting_workspace(self.root)
        subprocess.run(["git", "-C", str(self.root), "add", "greet.py", "test_greeting.py"], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=F", "-c", "user.email=f@example.test"]
            + ["commit", "-qm", "fixture"],
            check=True,
        )
        with (self.root / ".git/info/exclude").open("a") as exclude:
            exclude.write("/registry-home/\n")
        self.run_dir = self.root / ".autocode/runs/fixture"
        self.run_dir.mkdir(parents=True)
        self.local = {"auth_mode": "fixture"}
        self.state = {
            "version": 2,
            "workspace": str(self.root),
            "task": "Make a greeting tool",
            "status": "RUNNING",
            "iteration": 1,
            "sessions": {},
            "stages": [],
            "history": [],
            "next_stage": "terra",
            "acceptance_criteria": [],
            "settings": {
                "roles": {r: {"model": r, "reasoning_effort": "high"} for r in ("astra", "terra", "sol")},
                "transport_identity": self.local,
                "headroom": {"enabled": False},
                "context_soft_tokens": 10000,
                "limits": {"iteration_ceiling": 5, "max_seconds": None, "no_progress_batches": 3},
            },
        }
        lifecycle.migrate(self.state)
        lifecycle.install_draft(self.state, body(), origin="test")
        support.atomic_json(self.run_dir / "state.json", self.state)
        self.invoke("--show-goal")  # publishes the plan request a person approves
        self.token = goals.token(self.state["goal_contract"])

    def invoke(self, *args):
        """autocode ARGS on the fixture run; (exit code, stdout, stderr)."""
        argv = ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run_dir), *args]
        with (
            patch.object(sys, "argv", argv),
            patch.object(support, "assert_no_legacy_process"),
            patch.object(support, "local_settings", return_value=self.local),
            patch.object(runner, "run_role", side_effect=AssertionError("No agent may launch")),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            code = runner.main()
        self.state = support.read(self.run_dir / "state.json")
        return code, stdout.getvalue(), stderr.getvalue()

    def test_an_action_prints_the_status_payload_with_its_result(self):
        code, stdout, stderr = self.invoke("--approve-goal", self.token, "--json")
        self.assertEqual(0, code, stderr)
        payload = json.loads(stdout)  # exactly one object: anything else would not parse
        self.assertEqual(self.state["status"], payload["status"])
        self.assertIn("needs", payload["view"])
        result = payload["result"]
        self.assertEqual((0, True, None), (result["exit_code"], result["saved"], result["error"]))
        self.assertIn("Saved; no agent launched by this action.", result["message"])
        self.assertIn("Saved; no agent launched by this action.", stderr)  # the prose a person reads

    def test_a_refused_action_reports_its_error_as_json_on_both_streams(self):
        code, stdout, stderr = self.invoke("--approve-goal", "r9:stale", "--json")
        self.assertEqual(2, code)
        error = json.loads(stdout)["result"]["error"]
        self.assertEqual("input_rejected", error["code"])
        self.assertTrue(error["message"])
        self.assertEqual({"error": error}, json.loads(stderr.strip().splitlines()[-1]))
        self.assertFalse(goals.approved(self.state))

    def test_a_refusal_before_the_run_loads_still_prints_one_result(self):
        code, stdout, _ = self.invoke("--feedback", "  ", "--json")
        self.assertEqual(2, code)
        self.assertEqual(
            {
                "result": {
                    "exit_code": 2,
                    "saved": False,
                    "message": "",
                    "error": {"code": "input_rejected", "message": "Feedback and follow-ups must be nonempty"},
                }
            },
            json.loads(stdout),
        )

    def test_without_json_an_action_prints_its_prose(self):
        code, stdout, _ = self.invoke("--approve-goal", self.token)
        self.assertEqual(0, code)
        self.assertIn("Saved; no agent launched by this action.", stdout)
        with self.assertRaises(ValueError):
            json.loads(stdout)


if __name__ == "__main__":
    unittest.main()
