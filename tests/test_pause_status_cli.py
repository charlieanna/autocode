"""A real CLI pause exposes an actionable category and current private token."""

import shlex
import subprocess
import unittest

from autocode_taskrun import TaskRun

from . import test_subprocess


class PauseStatusCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp

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
