"""Codex as a second vendor for a Claude Code run, end to end: the real CLI, TaskRun and the offline fixtures.

`tools/fake_claude.py` stands in for Claude Code and `tools/fake_codex_config.py` for Codex: each runs the
scripted fixture for a stage. Codex runs single stages through the bundled provider config
(`tools/providers/configs/codex.toml`), either named (`--role-transport tester=codex`) or chosen by the launch
because Codex is signed in (`autocode_second_vendor`). PATH is the fixture directory plus the system default, so
a `codex` or `claude` installed on the host can never be reached. Assertions read the status view and the
fixtures' own call logs.
"""

import json
import os
import shutil
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from tests import test_subprocess

TOOLS = Path(__file__).resolve().parents[1] / "tools"
BRIEF = "Build a greeting tool"
START = ("--workflow", "build", "--joint-planning", "--evidence-provenance", "fake")


class CodexTransportCliTests(unittest.TestCase):
    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        self.bin = self.root / "fixture-bin"
        shutil.copy2(TOOLS / "fake_claude.py", self.bin / "claude")
        (self.bin / "codex").rename(self.bin / "codex-scripted")
        shutil.copy2(TOOLS / "fake_codex_config.py", self.bin / "codex")
        for name in ("claude", "codex"):
            (self.bin / name).chmod(0o755)
        self.log = self.root / "codex-calls.jsonl"
        self.env.update(
            {
                "PATH": f"{self.bin}{os.pathsep}{os.defpath}",
                "AUTOCODE_FIXTURE_CODEX_LOG": str(self.log),
                "AUTOCODE_FIXTURE_CLAUDE_HOME": str(self.root / "claude-home"),
                "AUTOCODE_FIXTURE_MODE": "no-human",
            }
        )
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "AUTOCODE_PASS_ENV"):
            self.env.pop(name, None)
        # These tests check the second vendor itself (tests/__init__.py turns it off for every other test).
        self.env["AUTOCODE_SECOND_VENDOR"] = "on"

    def start(self, *extra):
        return taskrun.TaskRun.start(
            self.project,
            BRIEF,
            options=("--engine", "claude"),
            start_options=(*START, *extra),
            env=self.env,
            timeout=300,
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

    def stages(self):
        """The calls AutoCode made to Codex: the fixture Claude Code also runs the scripted fixture through `codex`,
        without the bundled config's `--ephemeral`."""
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.is_file() else []
        return [call for call in calls if "--ephemeral" in call["argv"] and call["prompt_head"].startswith("You are")]

    def test_a_claude_code_run_has_its_tester_on_codex(self):
        run = self.start("--role-transport", "tester=codex", "--tester-model", "gpt-5.6-sol")
        view = self.finish(run, run.status())
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertEqual("claude", view["provider"])
        stages = self.stages()
        self.assertTrue(stages, "the Tester ran on Codex")
        for call in stages:
            argv = call["argv"]
            self.assertEqual("exec", argv[0])
            self.assertEqual("gpt-5.6-sol", argv[argv.index("--model") + 1])
            self.assertIn('forced_login_method="chatgpt"', argv)
            self.assertIn("--ignore-user-config", argv)
            self.assertIn("-o", argv)
        self.assertTrue(all(call["prompt_head"].startswith("You are the Validator") for call in stages), stages)

    def test_a_claude_code_run_moves_its_checks_to_a_signed_in_codex(self):
        # No transport flag and no Gemini CLI: the launch finds Codex signed in and gives it the checking stages.
        run = self.start()
        view = self.finish(run, run.status())
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        stages = self.stages()
        models = {call["argv"][call["argv"].index("--model") + 1] for call in stages}
        self.assertLessEqual(models, {"gpt-6-astra", "gpt-5.6-sol"}, models)
        heads = "\n".join(call["prompt_head"].split("\n", 1)[0] for call in stages)
        self.assertIn("You are the Validator", heads)
        self.assertIn("gpt-6-astra", models, "the Plan Reviewer ran on Codex")

    def codex_run(self, start_options):
        """A Codex-engine run, and the stage calls the fixture Claude Code answered for it."""
        claude_log = self.root / "claude-calls.jsonl"
        self.env["AUTOCODE_FIXTURE_CLAUDE_LOG"] = str(claude_log)
        run = taskrun.TaskRun.start(
            self.project, BRIEF, options=("--engine", "codex"), start_options=start_options, env=self.env, timeout=300
        )
        view = self.finish(run, run.status())
        calls = [json.loads(line) for line in claude_log.read_text().splitlines()] if claude_log.is_file() else []
        return view, [call for call in calls if call.get("prompt_head")]

    def test_a_codex_run_moves_its_checks_to_a_signed_in_claude_code(self):
        view, stages = self.codex_run(START)
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        heads = "\n".join(call["prompt_head"].split("\n", 1)[0] for call in stages)
        self.assertIn("You are the Validator", heads)
        models = {call["argv"][call["argv"].index("--model") + 1] for call in stages}
        # Opus reviews the plan and completes; Sonnet tests.
        self.assertEqual({"claude-opus-5-5", "claude-sonnet-5-5"}, models, heads)
        self.assertFalse([call for call in self.stages()], "no stage ran through the Codex provider config")

    def test_a_codex_run_without_joint_planning_moves_its_tester_and_completion_review(self):
        view, stages = self.codex_run(tuple(option for option in START if option != "--joint-planning"))
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        heads = "\n".join(call["prompt_head"].split("\n", 1)[0] for call in stages)
        self.assertIn("You are the Validator", heads)

    def test_an_api_key_codex_moves_nothing(self):
        # Codex signed in with an API key would bill per token: the checks stay on Claude Code.
        fake = (self.bin / "codex-scripted").read_text()
        (self.bin / "codex-scripted").write_text(fake.replace("Logged in using ChatGPT", "Logged in using an API key"))
        run = self.start()
        view = self.finish(run, run.status())
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertEqual([], self.stages())


if __name__ == "__main__":
    unittest.main()
