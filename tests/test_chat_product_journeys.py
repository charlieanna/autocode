"""Focused terminal-chat acceptance for reopening an already completed task.

The surrounding journey is covered by ``test_chat_client`` and the real-terminal
checks in ``test_chat_screens``; this module pins the missing completed-reopen
boundary without starting a provider.
"""

import unittest
from pathlib import Path

import autocode_chat_client as chat


class CompletedRunReopenTests(unittest.TestCase):
    @staticmethod
    def client_for(view):
        workspace = Path("/workspace/project")
        run_dir = workspace / ".autocode" / "runs" / "completed"

        class ReopenedChat(chat.ChatClient):
            def __init__(self):
                self.output = []
                self.dispatched = []
                self.prompts = []
                super().__init__(
                    workspace,
                    run_dir,
                    ask=lambda prompt: (self.prompts.append(prompt), "")[1],
                    say=self.output.append,
                )

            def status(self):
                return view

            def take(self, command):
                self.dispatched.append(command.argv)

        return ReopenedChat()

    def test_reopening_a_completed_run_shows_its_saved_result_and_dispatches_no_task_action(self):
        view = {
            "status": "TASK_COMPLETE",
            "done": True,
            "needs": None,
            "next_command": None,
            "task": "Add a greeting command",
            "code_checkpoints": {"rows": [{"changed_files": ["greet.py"]}]},
            "acceptance_tests": {"files": ["tests/test_greeting.py"]},
            "chat": {
                "messages": [
                    {"id": "saved-result", "role": "assistant", "text": "Added greet.py and verified its output."},
                ],
                "input": {"kind": "follow_up", "next_command": "autocode --follow-up MESSAGE"},
                "context": "saved-context",
            },
        }
        client = self.client_for(view)
        self.addCleanup(client.close)

        self.assertEqual(0, client.step())

        shown = "\n".join(client.output)
        self.assertIn("Added greet.py and verified its output.", shown)
        self.assertIn("Changed 1 file: greet.py.", shown)
        self.assertIn("Acceptance tests in tests/test_greeting.py.", shown)
        self.assertTrue(any(prompt.startswith("\nThis task is complete. Anything else?") for prompt in client.prompts))
        self.assertIn("Done.", shown)
        self.assertEqual([], client.dispatched, "reopening a completed task cannot submit its plan again")

    def test_an_unfinished_failure_gets_a_next_step_without_a_completion_claim(self):
        view = {
            "status": "PAUSED_JOB_FAILURE",
            "done": False,
            "needs": {"kind": "blocker", "reason": "The provider action failed."},
            "next_command": None,
            "pause_category_label": "Model or provider",
            "chat": {"messages": [], "input": None},
        }
        client = self.client_for(view)
        self.addCleanup(client.close)

        self.assertEqual(2, client.step())

        shown = "\n".join(client.output)
        self.assertIn("AutoCode needs you for this one (Model or provider). The provider action failed.", shown)
        self.assertIn("There is nothing to run from here. No next step is available for this run.", shown)
        self.assertIn("The run is saved; start a new request to carry on with something else.", shown)
        self.assertNotIn("Done.", shown)
        self.assertFalse(any(prompt.startswith("\nThis task is complete.") for prompt in client.prompts))
        self.assertEqual([], client.dispatched)


if __name__ == "__main__":
    unittest.main()
