"""The built-in claude provider end to end (#717): the real CLI, TaskRun and the offline `claude` fixture.

`tools/fake_claude.py` stands in for Claude Code: it runs the scripted Codex fixture for each stage and
answers in Claude Code's stream-json shapes. PATH is the fixture directory plus the system default, so a
`claude` installed on the host can never be reached. Assertions read the status view, the CLI's exit and
output, the files the view names, and the fixture's own log; never state.json.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from tests import test_subprocess

TOOLS = Path(__file__).resolve().parents[1] / "tools"
BRIEF = "Build a greeting tool"
OPTIONS = ("--engine", "claude")  # repeated on every advance; the rest only start the run
START = ("--workflow", "build", "--joint-planning", "--evidence-provenance", "fake")
CANARIES = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_SESSION_ID")
REPAIR_PROMPT = "Return exactly one JSON object matching the saved stage schema"  # the report repair stage


class ClaudeProviderCliTests(unittest.TestCase):
    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        self.bin = self.root / "fixture-bin"
        shutil.copy2(TOOLS / "fake_claude.py", self.bin / "claude")
        (self.bin / "claude").chmod(0o755)
        self.log = self.root / "claude-calls.jsonl"
        self.env.update(
            {
                "PATH": f"{self.bin}{os.pathsep}{os.defpath}",
                "AUTOCODE_FIXTURE_CLAUDE_LOG": str(self.log),
                "AUTOCODE_FIXTURE_CLAUDE_HOME": str(self.root / "claude-home"),
                "AUTOCODE_FIXTURE_MODE": "no-human",
                **dict.fromkeys(CANARIES, "CANARY"),
            }
        )
        for name in ("AUTOCODE_PASS_ENV", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
            self.env.pop(name, None)

    def start(self, *extra):
        return taskrun.TaskRun.start(
            self.project, BRIEF, options=OPTIONS, start_options=(*START, *extra), env=self.env, timeout=300
        )

    def finish(self, run, view):
        """Answer the planner's question and approve the plan, as a person would, until the run stops."""
        for _ in range(8):
            need = view["needs"] or {}
            if view["done"] or need.get("kind") not in ("answer", "approve_plan", "continue"):
                return view
            if need["kind"] == "answer":
                question = need["questions"][0]
                if "CLI" not in (question.get("options") or ()):
                    return (
                        view  # a stop for a person (a pause AutoResolver could not resolve), not the planner's question
                    )
                view = run.answer(question["id"], "CLI", resolver_token=need.get("resolver_token"))
            elif need["kind"] == "approve_plan":
                view = run.approve_plan(need["token"])
            view = run.advance_until_input()
        return view

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.is_file() else []

    def prints(self):
        """The `claude -p` calls, in order."""
        return [call for call in self.calls() if call["argv"][:1] == ["-p"]]

    @staticmethod
    def paused(view):
        """The pause a stopped run reports: AutoResolver turns one it cannot resolve into a request for a person."""
        return (view["needs"] or {}).get("pause_origin") or view["status"]

    @staticmethod
    def attempts(view, stage=None):
        rows = view["usage"]["accounting"]["attempts"]
        return [row for row in rows if stage is None or row.get("stage") == stage]

    @staticmethod
    def events(attempt):
        return [json.loads(line) for line in Path(attempt["events"]).read_text().splitlines()]

    def test_missing_claude_refuses_a_new_run_before_any_request(self):
        (self.bin / "claude").unlink()
        with self.assertRaises(taskrun.TaskRunError) as raised:
            self.start()
        process = raised.exception.process
        self.assertEqual(2, process.returncode, process.stdout + process.stderr)
        self.assertIn("claude is not on PATH; no provider request was launched", process.stdout + process.stderr)
        self.assertFalse(self.log.exists())

    def test_logged_out_claude_pauses_before_any_stage(self):
        self.env["AUTOCODE_FIXTURE_CLAUDE_AUTH"] = "logged_out"
        view = self.start().status()
        self.assertFalse(view["done"])
        self.assertEqual("PAUSED_BILLING_ROUTE", self.paused(view), view)
        self.assertIn("claude auth login", view["stop_reason"])
        self.assertIn(["auth", "status", "--json"], [call["argv"] for call in self.calls()])
        self.assertEqual([], self.prints())

    def test_startup_failure_pauses_once_without_retry(self):
        """Acceptance (provider startup failure): one launch, one terminal row, one pause."""
        self.env["AUTOCODE_FIXTURE_CLAUDE_START_ERROR"] = "requirements_gather"
        view = self.start().status()
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", self.paused(view), view)
        self.assertIn("exited 1", view["stop_reason"])
        self.assertIn("no automatic replay", view["stop_reason"])
        self.assertEqual(1, len(self.prints()))
        (attempt,) = self.attempts(view, "requirements_gather")
        lines = Path(attempt["events"]).read_text().splitlines()
        rows = [json.loads(line) for line in lines]  # every line is JSON: nothing else reached the log
        self.assertEqual("turn.failed", rows[-1]["type"])
        self.assertIn("Error: --json-schema must be a JSON object", rows[-1]["error"]["message"])
        self.assertNotIn("Traceback", "\n".join(lines))

    def test_a_truncated_builder_stops_at_the_output_cap(self):
        """Acceptance (output truncation): a Builder cut at Claude's output limit stops for a person."""
        self.env["AUTOCODE_FIXTURE_TRUNCATE_STAGE"] = "terra"
        run = self.start()
        view = self.finish(run, run.status())
        self.assertEqual("PAUSED_OUTPUT_CAP", self.paused(view), view)
        self.assertIn("output token limit", view["stop_reason"])
        self.assertIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS", view["stop_reason"])
        builder = [call for call in self.prints() if call["prompt_head"].startswith("You are the Builder")]
        self.assertEqual(1, len(builder))

    def test_a_truncated_validator_report_is_repaired_without_replaying_checks(self):
        """Acceptance (output truncation): the Tester's partial report is repaired, its checks never rerun."""
        self.env["AUTOCODE_FIXTURE_TRUNCATE_STAGE"] = "sol"
        run = self.start()
        view = self.finish(run, run.status())
        self.assertTrue(view["done"], view)
        self.assertEqual("TASK_COMPLETE", view["status"])
        self.assertEqual(1, len(self.attempts(view, "sol")))
        (repair,) = self.attempts(view, "sol_report_repair")
        self.assertEqual(
            [], [row for row in self.events(repair) if row.get("item", {}).get("type") == "command_execution"]
        )
        repairs = [call for call in self.prints() if call["prompt_head"].startswith(REPAIR_PROMPT)]
        self.assertTrue(repairs, [call["prompt_head"] for call in self.prints()])
        self.assertTrue(all("--resume" not in call["argv"] for call in repairs))

    def test_run_completes_through_the_built_in_provider(self):
        run = self.start()
        view = self.finish(run, run.status())
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertEqual("claude", view["provider"])
        self.assertEqual({"opencode"}, {route["engine"] for route in view["routes"].values()})
        prints = self.prints()
        self.assertGreater(len(prints), 5)
        for call in prints:
            argv = call["argv"]
            self.assertEqual(["-p", "--json-schema"], argv[:2])
            self.assertNotIn("--resume", argv)  # every ordinary stage starts a fresh session
            self.assertEqual("Bash,Read,Edit,Write,Glob,Grep", argv[argv.index("--tools") + 1])
            self.assertEqual("project", argv[argv.index("--setting-sources") + 1])
            for flag in ("--strict-mcp-config", "--disable-slash-commands"):
                self.assertIn(flag, argv)
        for call in self.calls():
            self.assertFalse(set(CANARIES) & set(call["env_names"]), call["argv"])
            self.assertIn("DISABLE_AUTOUPDATER", call["env_names"])
        self.assertEqual(round(0.01 * len(prints), 6), view["cost"]["reported_usd"])
        (builder,) = self.attempts(view, "terra")
        rows = self.events(builder)
        self.assertEqual(rows[0]["thread_id"], rows[-1]["claude"]["session_id"])  # the terminal row's diagnostics

    def test_a_run_with_no_engine_takes_the_signed_in_claude_code(self):
        # #2049: nothing names the engine (no flag, config home is empty, no OpenCode on this PATH), and
        # Claude Code is signed in, so the run takes it and says so instead of refusing for OpenCode.
        run = taskrun.TaskRun.start(self.project, BRIEF, options=(), start_options=START, env=self.env, timeout=300)
        view = run.status()
        self.assertEqual("claude", view["provider"], view)
        view = self.finish(run, view)
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertTrue(self.prints())
        started = subprocess.run(
            [sys.executable, str(TOOLS / "autocode.py"), "doctor", "--workspace", str(self.project), "--json"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        checks = {check["name"]: check for check in json.loads(started.stdout)["checks"]}
        self.assertEqual("ok", checks["engine"]["status"], started.stdout)
        self.assertIn("chosen from your connected accounts", checks["engine"]["detail"])

    def test_format_correction_resumes_the_stage_session_and_bills_it_once(self):
        self.env["AUTOCODE_FIXTURE_CLAUDE_NO_REPORT_STAGE"] = "sol"
        run = self.start()
        view = self.finish(run, run.status())
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        prints = self.prints()
        tester = [call for call in prints if call["prompt_head"].startswith("You are the Validator")]
        correction = [
            call for call in prints if call["prompt_head"].startswith("Your previous final message could not be parsed")
        ]
        self.assertEqual((1, 1), (len(tester), len(correction)))
        first = self.events(self.attempts(view, "sol")[0])[0]["thread_id"]
        argv = correction[0]["argv"]
        self.assertEqual(first, argv[argv.index("--resume") + 1])
        # Claude reports the session's cumulative cost: without the ledger the correction would count twice.
        self.assertEqual(round(0.01 * len(prints), 6), view["cost"]["reported_usd"])

    def doctor(self):
        done = subprocess.run(
            [
                sys.executable,
                str(TOOLS / "autocode.py"),
                "doctor",
                "--engine",
                "claude",
                "--workspace",
                str(self.project),
                "--json",
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        data = json.loads(done.stdout)
        self.assertEqual(0 if data["ok"] else 1, done.returncode, done.stderr)
        return done, {check["name"]: check for check in data["checks"]}

    def test_doctor_engine_claude_uses_the_agent_environment(self):
        done, checks = self.doctor()
        self.assertEqual("ok", checks["engine:claude"]["status"])
        self.assertEqual("Claude Code 2.1.299, logged in (claude.ai, firstParty)", checks["engine:claude"]["detail"])
        self.assertEqual("ok", checks["engine"]["status"])
        self.assertNotIn("fixture@example.test", done.stdout)
        (status,) = [call for call in self.calls() if call["argv"] == ["auth", "status", "--json"]]
        self.assertFalse(set(CANARIES) & set(status["env_names"]))
        self.assertIn("DISABLE_AUTOUPDATER", status["env_names"])
        self.env["AUTOCODE_FIXTURE_CLAUDE_AUTH"] = "logged_out"
        done, checks = self.doctor()
        self.assertEqual(("missing", 1), (checks["engine"]["status"], done.returncode))


if __name__ == "__main__":
    unittest.main()
