"""The built-in gemini provider end to end: the real CLI, TaskRun and the offline `gemini` fixture.

`tools/fake_gemini.py` stands in for Gemini CLI and `tools/fake_claude.py` for Claude Code: each runs the
scripted Codex fixture for a stage and answers in its tool's own stream shapes. PATH is the fixture directory
plus the system default, so a `gemini` or `claude` installed on the host can never be reached. The Google
sign-in is a settings file in a fixture Gemini home. Assertions read the status view, the CLI's output and
the fixtures' own logs.
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
START = ("--workflow", "build", "--joint-planning", "--evidence-provenance", "fake")
CANARIES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def signed_in(home: Path, kind="oauth-personal", creds=True):
    folder = home / ".gemini"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "settings.json").write_text(json.dumps({"security": {"auth": {"selectedType": kind}}}))
    if creds:
        (folder / "oauth_creds.json").write_text("{}")


class GeminiProviderCliTests(unittest.TestCase):
    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        self.bin = self.root / "fixture-bin"
        for name in ("gemini", "claude"):
            shutil.copy2(TOOLS / f"fake_{name}.py", self.bin / name)
            (self.bin / name).chmod(0o755)
        self.log = self.root / "gemini-calls.jsonl"
        self.home = self.root / "gemini-home"
        signed_in(self.home)
        self.env.update(
            {
                "PATH": f"{self.bin}{os.pathsep}{os.defpath}",
                "GEMINI_CLI_HOME": str(self.home),
                "AUTOCODE_FIXTURE_GEMINI_LOG": str(self.log),
                "AUTOCODE_FIXTURE_CLAUDE_HOME": str(self.root / "claude-home"),
                "AUTOCODE_FIXTURE_MODE": "no-human",
                **dict.fromkeys(CANARIES, "CANARY"),
            }
        )
        self.env.pop("AUTOCODE_PASS_ENV", None)
        self.env["AUTOCODE_SECOND_VENDOR"] = "on"  # these tests check the second vendor itself

    def start(self, options, *extra):
        return taskrun.TaskRun.start(
            self.project, BRIEF, options=options, start_options=(*START, *extra), env=self.env, timeout=300
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
                    return view
                view = run.answer(question["id"], "CLI", resolver_token=need.get("resolver_token"))
            elif need["kind"] == "approve_plan":
                view = run.approve_plan(need["token"])
            view = run.advance_until_input()
        return view

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.is_file() else []

    def stages(self):
        return [call for call in self.calls() if "stream-json" in call["argv"]]

    def test_a_claude_code_run_has_its_work_checked_by_gemini(self):
        run = self.start(
            ("--engine", "claude"), "--role-transport", "tester=gemini", "--tester-model", "gemini-2.5-pro"
        )
        view = self.finish(run, run.status())
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertEqual("claude", view["provider"])
        stages = self.stages()
        self.assertTrue(stages, "the Tester ran on Gemini CLI")
        for call in stages:
            argv = call["argv"]
            self.assertEqual("gemini-2.5-pro", argv[argv.index("--model") + 1])
            self.assertEqual("yolo", argv[argv.index("--approval-mode") + 1])
            self.assertEqual("none", argv[argv.index("-e") + 1])
            policy = Path(argv[argv.index("--policy") + 1]).read_text()
            self.assertIn('"git push"', policy)
            self.assertFalse(set(CANARIES) & set(call["env_names"]), call["argv"])
        self.assertTrue(all(call["prompt_head"].startswith("You are the Validator") for call in stages), stages)

    def test_a_claude_code_run_moves_only_its_tester_to_a_gemini_whose_pro_is_older_than_its_flash(self):
        """Every checking stage moved to gemini-2.5-pro, so a model older than the one Gemini CLI itself builds
        with replaced Claude Code's Opus as Plan Reviewer and Completion Reviewer."""
        # No transport flag: the launch finds Gemini CLI signed in. Its catalogue's Pro is behind its Flash.
        run = self.start(("--engine", "claude"))
        view = self.finish(run, run.status())
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        stages = self.stages()
        self.assertEqual({"gemini-2.5-pro"}, {call["argv"][call["argv"].index("--model") + 1] for call in stages})
        heads = {call["prompt_head"].split("\n", 1)[0] for call in stages}
        self.assertEqual(1, len(heads), heads)  # the Tester alone; the final reviews ran on Claude Code
        self.assertIn("You are the Validator", "\n".join(heads))
        moved = json.loads((Path(run.run_dir) / "state.json").read_text())["settings"]["second_vendor"]
        self.assertEqual(
            ({"tester": "gemini-2.5-pro"}, ["plan_reviewer", "completion"]), (moved["roles"], moved["own"])
        )
        self.assertIn("stay on Claude Code, because gemini-2.5-pro is older", moved["reason"])

    def test_a_claude_code_run_moves_its_checks_to_the_newest_pro_a_signed_in_gemini_lists(self):
        self.env["AUTOCODE_FIXTURE_GEMINI_MODELS"] = "gemini-3.5-pro,gemini-3.5-pro-preview-11-20"
        run = self.start(("--engine", "claude"))
        view = self.finish(run, run.status())
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        stages = self.stages()
        self.assertEqual({"gemini-3.5-pro"}, {call["argv"][call["argv"].index("--model") + 1] for call in stages})
        heads = {call["prompt_head"].split("\n", 1)[0] for call in stages}
        self.assertGreater(len(heads), 1, heads)
        self.assertIn("You are the Validator", "\n".join(heads))
        moved = json.loads((Path(run.run_dir) / "state.json").read_text())["settings"]["second_vendor"]
        self.assertEqual(dict.fromkeys(("plan_reviewer", "tester", "completion"), "gemini-3.5-pro"), moved["roles"])
        self.assertNotIn("own", moved)

    def test_a_run_on_gemini_completes(self):
        run = self.start(("--provider", "gemini"))
        view = self.finish(run, run.status())
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertEqual("gemini", view["provider"])
        stages = self.stages()
        self.assertGreater(len(stages), 4)
        read_only = [call for call in stages if "read-only" in call["argv"][call["argv"].index("--policy") + 1]]
        self.assertTrue(read_only)
        for call in read_only:
            policy = Path(call["argv"][call["argv"].index("--policy") + 1]).read_text()
            self.assertIn('"write_file"', policy)

    def test_a_run_with_no_engine_takes_a_signed_in_gemini(self):
        # Gemini comes after Claude Code, Codex and Kilo: with none of them on PATH it is the one taken.
        (self.bin / "claude").unlink()
        (self.bin / "codex").unlink()
        run = taskrun.TaskRun.start(self.project, BRIEF, options=(), start_options=START, env=self.env, timeout=300)
        self.assertEqual("gemini", run.status()["provider"])

    def doctor(self):
        done = subprocess.run(
            [sys.executable, str(TOOLS / "autocode.py"), "doctor", "--workspace", str(self.project), "--json"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return done, {check["name"]: check for check in json.loads(done.stdout)["checks"]}

    def test_doctor_names_the_workspace_project_gemini_needs(self):
        self.env["AUTOCODE_FIXTURE_GEMINI_REFRESH"] = "workspace"
        _done, checks = self.doctor()
        self.assertEqual("warn", checks["engine:gemini"]["status"], checks["engine:gemini"])
        self.assertIn("GOOGLE_CLOUD_PROJECT", checks["engine:gemini"]["detail"])
        self.assertIn("export GOOGLE_CLOUD_PROJECT", checks["engine:gemini"]["fix"])

    def setup_text(self):
        done = subprocess.run(
            [sys.executable, str(TOOLS / "autocode.py"), "setup"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=self.project,
            stdin=subprocess.DEVNULL,
        )
        return done.stdout

    def test_setup_lists_a_signed_in_gemini_and_how_it_checks_claude_code(self):
        shown = self.setup_text()
        self.assertIn("✔ Gemini CLI: signed in", shown)
        self.assertIn("New runs use Claude Code", shown)
        # The stages a launch would move: this account's Pro is older than the model Gemini CLI builds with.
        self.assertIn(
            "Gemini CLI is signed in too, so it checks the work of new runs on Claude Code (Tester); the Plan Reviewer "
            "and Completion Reviewer stay on Claude Code until your account lists a Gemini CLI model for it as new as "
            "the one Gemini CLI builds with",
            shown,
        )
        self.assertNotIn("own models check each other's work", shown)
        self.env["AUTOCODE_FIXTURE_GEMINI_MODELS"] = "gemini-3.5-pro"
        self.assertIn(
            "Gemini CLI is signed in too, so it checks the work of new runs on Claude Code (Plan Reviewer, Tester, "
            "Completion Reviewer); a stage",
            self.setup_text(),
        )


if __name__ == "__main__":
    unittest.main()
