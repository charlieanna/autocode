"""A real CLI pause exposes an actionable category and current private token."""

import json
import shlex
import subprocess
import unittest

from autocode_taskrun import TaskRun

from . import test_subprocess


class PauseStatusCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp

    def start(self):
        return TaskRun.start(
            self.project,
            "Build a greeting tool",
            options=("--engine", "codex"),
            command=self.entry,
            env=self.env,
            timeout=240,
            cwd=self.root,
        )

    def invoke(self, run, *flags):
        return subprocess.run(
            [*self.entry, "--workspace", str(self.project), "--run-dir", str(run.run_dir), *flags],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=240,
        )

    def assert_refusal_retains_current_command(self, run, *flags):
        before = run.status()
        saved = (run.run_dir / "state.json").read_bytes()
        provider_outputs = {
            str(path): path.read_bytes() for path in (run.run_dir / "iterations").rglob("*") if path.is_file()
        }
        result = self.invoke(run, *flags)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("Next command: " + before["next_command"], result.stderr)
        self.assertEqual(saved, (run.run_dir / "state.json").read_bytes())
        self.assertEqual(
            provider_outputs,
            {str(path): path.read_bytes() for path in (run.run_dir / "iterations").rglob("*") if path.is_file()},
            "A refused input must not launch a provider",
        )
        self.assertEqual(before["needs"], run.status()["needs"])
        self.assertFalse(any(self.project.glob("greet*.py")))
        return result

    def test_invalid_question_answers_show_the_current_command_without_changing_the_request(self):
        run = self.start()
        need = run.status()["needs"]
        question = need["questions"][0]["id"]
        for flags in (
            ("--answer", question + "=CLI"),
            ("--answer", question + "=CLI", "--resolver-token", "forged-token"),
            ("--answer", question, "--resolver-token", need["resolver_token"]),
            (
                "--resolver-response",
                "provide_information",
                "--resolver-request",
                need["resolver_request_id"],
                "--resolver-token",
                need["resolver_token"],
                "--resolver-message",
                "CLI",
            ),
        ):
            with self.subTest(flags=flags):
                self.assert_refusal_retains_current_command(run, *flags)
        # The command displayed by the refusal still authorizes only this answer.
        run.answer(question, "CLI", resolver_token=need["resolver_token"])
        self.assertFalse(any(self.project.glob("greet*.py")))

    def test_expired_plan_token_shows_current_approval_command_without_approving(self):
        run = self.start()
        need = run.status()["needs"]
        run.answer(need["questions"][0]["id"], "CLI", resolver_token=need["resolver_token"])
        view = run.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"])
        self.assert_refusal_retains_current_command(run, "--approve-goal", "r0:expired")
        self.assertFalse(run.status()["done"])

    def test_wrong_transport_acceptance_keeps_abandoned_frontier_and_shows_plain_resume(self):
        run = self.start()
        need = run.status()["needs"]
        run.answer(need["questions"][0]["id"], "CLI", resolver_token=need["resolver_token"])
        self.env["AUTOCODE_FIXTURE_SESSION_DRIFT"] = "1"
        run.advance()
        result = self.invoke(run, "--status")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        attempt = json.loads(result.stdout)["attempt_id"]
        self.assertTrue(attempt)
        view = run.abandon_stage(attempt)
        self.assertEqual("PAUSED_STAGE_ABANDONED", view["status"])
        self.assertIn("--resume-paused", view["next_command"])
        self.assertNotIn("--accept-transport-change", view["next_command"])
        self.assert_refusal_retains_current_command(run, "--resume-paused", "--accept-transport-change")

    def test_the_printed_question_command_is_accepted_without_bypassing_approval(self):
        run = TaskRun.start(
            self.project,
            "Build a greeting tool",
            options=("--engine", "codex"),
            command=self.entry,
            env=self.env,
            timeout=240,
            cwd=self.root,
        )
        view = run.status()
        self.assertEqual("WAITING_FOR_USER", view["status"])
        self.assertEqual("your_decision", view["pause_category"])
        self.assertEqual("Your decision", view["pause_category_label"])
        self.assertIn("Your decision\nState: WAITING_FOR_USER\nNext command:", run.last_advance.stdout)
        need = view["needs"]
        self.assertEqual("answer", need["kind"])
        words = shlex.split(view["next_command"])
        private = {}
        while words[0] != "autocode":
            name, value = words.pop(0).split("=", 1)
            private[name] = value
        words.pop(0)
        self.assertNotIn(need["resolver_token"], words)
        self.assertEqual(need["resolver_token"], private["AUTOCODE_RESOLVER_TOKEN"])
        answer = words.index("--answer") + 1
        self.assertEqual(need["questions"][0]["id"] + "=ANSWER", words[answer])
        words[answer] = need["questions"][0]["id"] + "=CLI"
        proc = subprocess.run(
            [*self.entry, *words],
            cwd=self.root,
            env={**self.env, **private},
            text=True,
            capture_output=True,
            timeout=240,
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        after = run.status()
        self.assertFalse(after["done"])
        self.assertNotEqual(need.get("resolver_token"), (after["needs"] or {}).get("resolver_token"))
        self.assertNotEqual("answer", (after["needs"] or {}).get("kind"))
        self.assertFalse(any(self.project.glob("greet*.py")), "An answer is not plan approval or build authority")
