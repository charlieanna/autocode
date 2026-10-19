"""`autocode chat`, the terminal client: the view's named actions, and fake runs driven by typed replies."""

import contextlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import autocode_client_actions as actions

from tests.test_agent_step import ask_one_question_once, fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
AUTOCODE = ROOT / "tools" / "autocode.py"
WORKSPACE = Path("/work/project")
RUN = WORKSPACE / ".autocode" / "runs" / "r1"
PLACE = f"--workspace {WORKSPACE} --run-dir {RUN}"


def answer_view(**need):
    return {
        "status": "WAITING_FOR_USER",
        "needs": {
            "kind": "answer",
            "questions": [{"id": "Q1", "question": "Which?", "options": ["A", "B"], "proposed_default": "A"}],
            **need,
        },
        "next_command": f"AUTOCODE_RESOLVER_TOKEN=t1 autocode {PLACE} --answer Q1=ANSWER --resolver-token -",
        "chat": {
            "context": "c1",
            "input": {"kind": "feedback", "next_command": f"autocode {PLACE} --feedback MESSAGE"},
        },
    }


class NamedActionTests(unittest.TestCase):
    """The rules a client reads the view by, the ones next-command.ts and the conformance client follow."""

    def test_a_named_command_keeps_its_token_out_of_argv_and_leaves_the_run_to_the_caller(self):
        command = actions.named(answer_view(), WORKSPACE, RUN)
        self.assertEqual(["--answer", "Q1=ANSWER", "--resolver-token", "-"], command.argv)
        self.assertEqual({"AUTOCODE_RESOLVER_TOKEN": "t1"}, command.env)

    def test_a_command_naming_another_run_is_a_gap(self):
        view = {**answer_view(), "next_command": f"autocode --run-dir {WORKSPACE}/other --answer Q1=ANSWER"}
        with self.assertRaisesRegex(actions.ClientGap, "not this run's"):
            actions.named(view, WORKSPACE, RUN)

    def test_an_active_run_continues_with_no_chat(self):
        view = {"status": "RUNNING", "needs": {"kind": "continue"}, "next_command": None}
        self.assertEqual(["--no-chat"], actions.named(view, WORKSPACE, RUN).argv)
        self.assertTrue(actions.advancing(actions.named(view, WORKSPACE, RUN)))

    def test_the_person_fills_the_answer_slot_and_nothing_else(self):
        view = answer_view()
        command = actions.named(view, WORKSPACE, RUN)
        [decision] = actions.decisions(view, command)
        self.assertEqual(("Q1", "answer", "Which?"), (decision.key, decision.kind, decision.question["question"]))
        filled = actions.fill(view, command, {"Q1": "B"})
        self.assertEqual(["--answer", "Q1=B", "--resolver-token", "-"], filled.argv)
        with self.assertRaisesRegex(actions.ClientGap, "decision for a person"):
            actions.fill(view, command, {})

    def test_a_placeholder_the_view_cannot_supply_is_never_filled(self):
        view = {
            "status": "PAUSED_X",
            "needs": {"kind": "resume"},
            "next_command": f"autocode {PLACE} --edit-goal GOAL_FILE",
        }
        command = actions.named(view, WORKSPACE, RUN)
        self.assertEqual("GOAL_FILE", actions.unsupplied(view, command))
        with self.assertRaisesRegex(actions.ClientGap, "does not supply"):
            actions.fill(view, command, {"GOAL_FILE": "x.json"})

    def test_a_number_is_typed_only_into_the_recovery_offer_the_core_advertises(self):
        row = {
            "id": "grant_recovery",
            "kind": "integer",
            "placeholder": "N",
            "flag": "--grant-recovery",
            "arg_index": 2,
            "label": "Additional recoveries",
            "current": 3,
            "used": 3,
            "minimum": 1,
            "maximum": 3,
        }
        view = {
            "status": "PAUSED_TIMEOUT_RECOVERY",
            "needs": {"kind": "resume", "command_inputs": [row]},
            "recovery": {"token": "rt"},
            "next_command": f"AUTOCODE_EXPECTED_RECOVERY_TOKEN=rt autocode {PLACE} --resume-paused --grant-recovery N "
            "--expected-recovery-token -",
        }
        command = actions.named(view, WORKSPACE, RUN)
        self.assertIsNone(actions.unsupplied(view, command))
        self.assertEqual(["integer"], [row.kind for row in actions.decisions(view, command)])
        self.assertEqual("2", actions.fill(view, command, {"grant_recovery": "2"}).argv[2])
        with self.assertRaisesRegex(actions.ClientGap, "from 1 to 3"):
            actions.fill(view, command, {"grant_recovery": "4"})
        stale = {**view, "recovery": {"token": "other"}}
        with self.assertRaisesRegex(actions.ClientGap, "stale numeric"):
            actions.decisions(stale, actions.named(stale, WORKSPACE, RUN))

    def test_a_message_goes_through_the_offered_input_and_only_for_the_conversation_read(self):
        view = answer_view()
        self.assertEqual(["--feedback", "Use B"], actions.message(view, "Use B", "c1", WORKSPACE, RUN).argv)
        with self.assertRaisesRegex(actions.ClientGap, "conversation changed"):
            actions.message(view, "Use B", "c0", WORKSPACE, RUN)
        with self.assertRaisesRegex(actions.ClientGap, "no message action"):
            actions.message({**view, "chat": {"context": "c1"}}, "Use B", "c1", WORKSPACE, RUN)

    def test_a_resolver_reply_carries_its_request_binding_and_nothing_more(self):
        view = answer_view(resolver_scope="blocker", resolver_request_id="R1", resolver_token="t1")
        flags = "--resolver-request R1 --resolver-token - --resolver-response provide_information"
        view["chat"]["input"] = {
            "kind": "resolver_response",
            "next_command": f"AUTOCODE_RESOLVER_TOKEN=t1 autocode {PLACE} {flags} --resolver-message MESSAGE",
        }
        command = actions.message(view, "The disk is free now", "c1", WORKSPACE, RUN)
        self.assertEqual("The disk is free now", command.argv[-1])
        self.assertEqual({"AUTOCODE_RESOLVER_TOKEN": "t1"}, command.env)
        self.assertTrue(actions.leaves_for_person(view["needs"]))
        view["chat"]["input"]["next_command"] += " --resume-paused"
        with self.assertRaisesRegex(actions.ClientGap, "Unsupported message"):
            actions.message(view, "x", "c1", WORKSPACE, RUN)

    def test_the_pause_is_the_offered_one_with_this_request_id(self):
        command = ["intervention", "submit", "--workspace", str(WORKSPACE), "--run-dir", str(RUN)]
        command += ["--request-id", "REQUEST_ID", "--kind", "pause", "--json"]
        view = {"pause": {"requested": False, "next_command": command}}
        self.assertEqual("p1", actions.pause(view, "p1", WORKSPACE, RUN)[7])
        with self.assertRaisesRegex(actions.ClientGap, "no pause"):
            actions.pause({"pause": {"requested": True, "next_command": None}}, "p1", WORKSPACE, RUN)

    def test_only_the_complete_plan_the_token_names_can_be_approved(self):
        plan = {
            "token": "r2:h",
            "acceptance_criteria": [{"id": "C1", "criterion": "c", "verification_method": "m", "human_review": False}],
            "constraints": [],
            "permission_boundaries": ["only here"],
        }
        view = {"needs": {"kind": "approve_plan", "token": "r2:h"}, "displayed_plan": plan}
        self.assertIs(plan, actions.plan_for_approval(view))
        self.assertIsNone(actions.plan_for_approval({**view, "needs": {"kind": "approve_plan", "token": "r3:h"}}))
        self.assertIsNone(actions.plan_for_approval({**view, "displayed_plan": {**plan, "constraints": None}}))


class TerminalChatTests(unittest.TestCase):
    """`python tools/autocode.py chat` with the offline fixture provider; the person's replies arrive on stdin."""

    def setUp(self):
        self.workspace, env = fixture_workspace(self)
        self.env = {**os.environ, **env}

    def chat(self, *args, replies="", options=FIXTURE_OPTIONS):
        argv = [sys.executable, str(AUTOCODE), "chat", "--workspace", str(self.workspace), *args]
        if options:
            argv += ["--", *options]
        return subprocess.run(argv, input=replies, capture_output=True, text=True, env=self.env, timeout=600)

    def answers(self):
        proc = subprocess.run(
            [sys.executable, str(AUTOCODE), "events", "--workspace", str(self.workspace)],
            capture_output=True,
            text=True,
            env=self.env,
        )
        rows = [json.loads(line) for line in proc.stdout.splitlines()]
        return {row["question_id"]: row["text"] for row in rows if row.get("kind") == "answer"}

    def test_a_person_confirms_the_understanding_approves_the_plan_and_the_run_completes(self):
        proc = self.chat(BRIEF, replies="y\ny\n\n")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        out = proc.stdout
        self.assertIn("Approve understanding and prepare plan? [y/N]", out)
        self.assertIn("Plan to approve:\n  [C1] Greeting contract holds", out)
        self.assertLess(out.index("Plan to approve:"), out.index("The requested task is complete."))
        self.assertTrue(out.rstrip().endswith("Done."), out[-500:])
        self.assertTrue((self.workspace / "greet.py").is_file())

    def test_a_question_is_answered_by_choosing_an_option(self):
        ask_one_question_once(self, self.env)
        proc = self.chat("--mode", "quick", BRIEF, replies="2\ny\n\n")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(1, proc.stdout.count("Should the README target beginners"), "the question is asked once")
        self.assertIn("  2. Experienced Python users", proc.stdout)
        self.assertEqual("Experienced Python users", self.answers()["readme-audience"])

    def test_leaving_at_the_plan_changes_nothing_and_chat_picks_the_run_up_again(self):
        left = self.chat(BRIEF, replies="y\n")
        self.assertEqual(2, left.returncode, left.stdout + left.stderr)
        self.assertIn("Left the chat; the run is unchanged. Pick it up with: autocode chat --run-dir", left.stdout)
        self.assertFalse((self.workspace / "greet.py").exists(), "nothing is built before the person approves")

        resumed = self.chat(replies="y\n\n", options=())
        self.assertEqual(0, resumed.returncode, resumed.stdout + resumed.stderr)
        self.assertIn("Plan to approve:", resumed.stdout)
        self.assertTrue((self.workspace / "greet.py").is_file())

    def test_with_nothing_open_the_chat_asks_what_to_do_and_starts_it(self):
        proc = self.chat(replies=BRIEF + "\ny\ny\n\n")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("What do you want done? "), proc.stdout[:200])
        self.assertTrue((self.workspace / "greet.py").is_file())

    def test_with_several_runs_open_the_person_picks_one(self):
        for _ in range(2):
            self.assertEqual(2, self.chat(BRIEF, replies="y\n").returncode)
        proc = self.chat(replies="2\ny\n\n", options=())
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("Runs waiting here:\n  1. ", proc.stdout)
        self.assertIn("Pick one (1-2), or type a new request: ", proc.stdout)
        self.assertTrue((self.workspace / "greet.py").is_file())

    def test_plain_autocode_in_a_terminal_opens_the_chat(self):
        import pty

        leader, follower = pty.openpty()
        child = subprocess.Popen(
            [sys.executable, str(AUTOCODE)],
            stdin=follower,
            stdout=follower,
            stderr=follower,
            cwd=self.workspace,
            env=self.env,
            close_fds=True,
        )
        os.close(follower)
        seen = b""
        while b"What do you want done?" not in seen:
            seen += os.read(leader, 1024)
        os.write(leader, b"\x04")  # Ctrl-D: leave without starting anything
        # Read to the end before reaping: once the child closes the terminal, macOS drops unread output.
        with contextlib.suppress(OSError):
            while chunk := os.read(leader, 4096):
                seen += chunk
        os.close(leader)
        self.assertEqual(2, child.wait(timeout=60))
        self.assertIn(b"Nothing started.", seen)
        self.assertFalse((self.workspace / ".autocode" / "runs").exists())

    def test_a_brief_and_a_run_are_not_both_named(self):
        proc = self.chat(BRIEF, "--run-dir", str(self.workspace), options=())
        self.assertEqual(2, proc.returncode)
        self.assertIn("not both", proc.stderr)
        self.assertFalse((self.workspace / ".autocode").exists())


if __name__ == "__main__":
    unittest.main()
