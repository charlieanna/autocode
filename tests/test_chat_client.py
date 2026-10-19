"""`autocode chat`, the terminal client: the view's named actions, and fake runs driven by typed replies."""

import json
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import autocode_chat_client as chat
import autocode_client_actions as actions

from tests.test_agent_step import ask_one_question_once, fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
AUTOCODE = ROOT / "tools" / "autocode.py"
WORKSPACE = Path("/work/project")
RUN = WORKSPACE / ".autocode" / "runs" / "r1"
PLACE = f"--workspace {WORKSPACE} --run-dir {RUN}"


def read_terminal(leader: int, *, until: bytes | None = None, seconds: float = 60) -> bytes:
    """What a pty shows, up to ``until`` (or to its end), within a deadline; a child that dies early is EOF."""
    seen, deadline = b"", time.monotonic() + seconds
    while until is None or until not in seen:
        left = deadline - time.monotonic()
        if left <= 0:
            raise AssertionError(f"the terminal never showed {until!r}: {seen!r}")
        if not select.select([leader], [], [], left)[0]:
            continue
        try:
            chunk = os.read(leader, 4096)
        except OSError:  # Linux reports the child's end closing as EIO
            chunk = b""
        if not chunk:
            if until is not None:
                raise AssertionError(f"the terminal closed before showing {until!r}: {seen!r}")
            return seen
        seen += chunk
    return seen


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

    def test_no_second_confirmation_does_not_authorize_an_automatic_resume(self):
        self.assertTrue(actions.leaves_for_person({"kind": "resume", "confirmation_required": False}))

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


DONE = {"status": "TASK_COMPLETE", "done": True, "needs": None, "next_command": None, "chat": {}}


class ScriptedChat(chat.ChatClient):
    """The chat over a scripted sequence of views: each action taken moves to the next view."""

    def __init__(self, *views, replies=(), moves=True):
        self.views, self.replies, self.moves = list(views), list(replies), moves
        self.taken, self.out = [], []
        super().__init__(WORKSPACE, RUN, ask=self.reply, say=self.out.append, poll_seconds=0)

    def reply(self, prompt):
        self.out.append(prompt)
        if not self.replies:
            raise EOFError
        return self.replies.pop(0)

    def status(self):
        return self.views[0]

    def take(self, command):
        self.taken.append(command.argv)
        if self.moves and len(self.views) > 1:
            self.views.pop(0)

    def text(self):
        return "\n".join(self.out)


class ConversationTests(unittest.TestCase):
    """What the chat shows and takes at each kind of stop, without a run."""

    def test_a_dependency_shows_the_wait_command_and_takes_nothing(self):
        view = {
            "status": "WAITING_FOR_DEPENDENCY",
            "needs": {"kind": "dependency", "reason": "Waits for the API run to deliver"},
            "pause_category_label": "Waiting on another run",
            "next_command": f"autocode wait --run-dir {WORKSPACE}/api/.autocode/runs/r9",
        }
        client = ScriptedChat(view)
        self.assertEqual(2, client.converse())
        self.assertIn(f"Run it yourself:\n  autocode wait --run-dir {WORKSPACE}/api", client.text())
        self.assertEqual([], client.taken)

    def test_a_stop_with_no_command_explains_itself_and_takes_nothing(self):
        view = {"status": "PAUSED_STOPPED", "needs": {"kind": "resume"}, "next_command": None, "stop_reason": "Stopped"}
        client = ScriptedChat(view)
        self.assertEqual(2, client.converse())
        self.assertIn("There is nothing to run from here", client.text())
        self.assertEqual([], client.taken)

    def review_view(self, *criteria):
        return {
            "status": "WAITING_FOR_USER",
            "needs": {
                "kind": "review",
                "criteria": list(criteria),
                "token": "rv",
                "question": "Does the logo look right?",
            },
            "approved_contract": {"body": {"acceptance_criteria": [{"id": "C1", "criterion": "The logo is centred"}]}},
            "next_command": f"AUTOCODE_REVIEW_TOKEN=rv autocode {PLACE} --approve-review C1 --review-token -",
        }

    def test_a_review_shows_its_question_and_criteria_before_asking_to_accept(self):
        client = ScriptedChat(self.review_view("C1"), DONE, replies=["y"])
        self.assertEqual(0, client.converse())
        out = client.text()
        self.assertLess(out.index("Does the logo look right?"), out.index("Accept these criteria"))
        self.assertLess(out.index("[C1] The logo is centred"), out.index("Accept these criteria"))
        self.assertEqual([["--approve-review", "C1", "--review-token", "-"]], client.taken)

    def test_a_review_whose_criteria_cannot_be_shown_is_not_offered(self):
        client = ScriptedChat(self.review_view("C9"))
        self.assertEqual(2, client.converse())
        self.assertNotIn("[y/N]", client.text(), "nothing is offered for acceptance")
        self.assertEqual([], client.taken)

    def test_a_relaunch_that_changes_nothing_stops_instead_of_looping(self):
        view = {"status": "RUNNING", "needs": {"kind": "continue"}, "next_command": None, "next_stage": "terra"}
        client = ScriptedChat(view, moves=False)
        self.assertEqual(1, client.converse())
        self.assertIn("made no progress at RUNNING", client.text())
        self.assertEqual([["--no-chat"]], client.taken)

    def test_a_refused_pause_leaves_the_interrupt_to_a_second_ctrl_c(self):
        command = ["intervention", "submit", "--workspace", str(WORKSPACE), "--run-dir", str(RUN)]
        command += ["--request-id", "REQUEST_ID", "--kind", "pause", "--json"]
        client = ScriptedChat({"pause": {"requested": False, "next_command": command}})
        refused = subprocess.CompletedProcess([], 2, "", "inbox is closed")
        with mock.patch.object(client, "cli", return_value=refused):
            self.assertTrue(client.request_pause())
        self.assertIn("Press Ctrl-C again to interrupt", client.text())

    def test_a_model_question_shows_what_stopped_and_the_models_offered(self):
        view = answer_view(
            questions=[{"id": "route-sol", "question": "The Tester's quota ran out. Which model now?"}],
            route={"candidates": ["model-a", "model-b"], "stopped_model": "model-x", "cause": "quota"},
        )
        view["next_command"] = (
            f"AUTOCODE_RESOLVER_TOKEN=t1 autocode {PLACE} --answer route-sol=MODEL --resolver-token -"
        )
        client = ScriptedChat(view, DONE, replies=["model-b"])
        self.assertEqual(0, client.converse())
        out = client.text()
        self.assertLess(out.index("Models it can continue on: model-a, model-b"), out.index("Which model should sol"))
        self.assertIn("Stopped on: model-x (quota)", out)
        self.assertEqual([["--answer", "route-sol=model-b", "--resolver-token", "-"]], client.taken)

    def test_a_follow_up_goes_through_the_offered_input(self):
        done = {
            **DONE,
            "chat": {
                "context": "c9",
                "input": {"kind": "follow_up", "next_command": f"autocode {PLACE} --follow-up MESSAGE"},
            },
        }
        client = ScriptedChat(done, DONE, replies=["Add a --shout flag"])
        self.assertEqual(0, client.converse())
        self.assertEqual([["--follow-up", "Add a --shout flag"]], client.taken)

    def test_a_run_number_out_of_range_is_asked_again_not_started_as_a_request(self):
        runs = [
            {"name": f"run-{n}", "run_dir": f"{WORKSPACE}/.autocode/runs/r{n}", "bucket": "needs you"} for n in (1, 2)
        ]
        said, replies = [], iter(["7", "2"])
        with (
            mock.patch.object(chat, "unfinished", return_value=runs),
            mock.patch.object(chat, "start", side_effect=AssertionError("started a run")),
        ):
            client = chat.open_chat(WORKSPACE, ask=lambda prompt: next(replies), say=said.append)
        self.assertEqual(Path(runs[1]["run_dir"]), client.run_dir)
        self.assertIn("There is no run 7.", said)

    def test_a_failing_run_listing_is_reported_not_taken_for_an_empty_project(self):
        broken = subprocess.CompletedProcess([], 2, "", "Traceback: registry unreadable")
        with mock.patch.object(chat.subprocess, "run", return_value=broken):
            with self.assertRaisesRegex(actions.ClientGap, "registry unreadable"):
                chat.unfinished(WORKSPACE)
        empty = subprocess.CompletedProcess([], 2, "", f"No runs found for {WORKSPACE}.\n")
        with mock.patch.object(chat.subprocess, "run", return_value=empty):
            self.assertEqual([], chat.unfinished(WORKSPACE))

    def test_a_brief_that_starts_with_a_dash_is_given_with_brief(self):
        started = mock.Mock(**{"converse.return_value": 0})
        with (
            mock.patch.object(chat, "run_mode", return_value=[]),
            mock.patch.object(chat, "start", return_value=started) as start,
        ):
            self.assertEqual(0, chat.cli(["--workspace", str(WORKSPACE), "--brief=-v should print more"]))
        self.assertEqual("-v should print more", start.call_args.args[1])


class InterruptTests(unittest.TestCase):
    """Ctrl-C while a stage works, against a real child that stands in for AutoCode."""

    def watch(self, child_code, interrupts, *, pause=None):
        """Advance with ``child_code`` as AutoCode; each of the first ``interrupts`` polls is a Ctrl-C."""
        with tempfile.TemporaryDirectory() as folder:
            ready = Path(folder) / "ready"
            client = ScriptedChat({"status": "RUNNING"})
            client.workspace, client.command = Path(folder), (sys.executable, "-c", child_code, str(ready))
            presses = iter(range(interrupts))

            def show(view, live=False):
                deadline = time.monotonic() + 30
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                if next(presses, None) is not None:
                    raise KeyboardInterrupt

            with (
                mock.patch.object(client, "show", side_effect=show),
                mock.patch.object(client, "request_pause", side_effect=pause or (lambda: True)),
            ):
                started = time.monotonic()
                try:
                    client.advance(["--no-chat"])
                    error = None
                except actions.ClientGap as gap:
                    error = gap
                self.assertLess(time.monotonic() - started, 20, "the chat waited out the child")
            return client, error

    def test_ctrl_c_while_the_pause_is_asked_for_interrupts_the_step_instead_of_leaving_it_running(self):
        child = (
            "import pathlib, signal, sys, time\n"
            "signal.signal(signal.SIGINT, lambda *a: sys.exit(0))\n"
            "pathlib.Path(sys.argv[1]).touch()\n"
            "time.sleep(60)\n"
        )

        def pressed_again():
            raise KeyboardInterrupt

        client, error = self.watch(child, 1, pause=pressed_again)
        self.assertIsNone(error)
        self.assertIn("Interrupting the current step", client.text())

    def test_a_third_ctrl_c_stops_a_step_that_ignores_the_interrupt(self):
        child = (
            "import pathlib, signal, sys, time\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            "pathlib.Path(sys.argv[1]).touch()\n"
            "time.sleep(60)\n"
        )
        client, error = self.watch(child, 3)
        self.assertIn("picks the run up", str(error))
        self.assertIn("Stopping AutoCode...", client.text())


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

    def run_terminal(self, *argv, until: bytes, reply: bytes = b"\x04") -> tuple[bytes, int]:
        """Spawn the CLI on a pty sized for curses, answer ``until``, and leave with ``reply``."""
        leader, child = self.spawn_terminal(*argv)
        try:
            seen = read_terminal(leader, until=until)
            os.write(leader, reply)
            # Read to the end before reaping: once the child closes the terminal, macOS drops unread output.
            seen += read_terminal(leader)
        finally:
            os.close(leader)
            if child.poll() is None:
                child.kill()
        return seen, child.wait(timeout=60)

    def spawn_terminal(self, *argv):
        import fcntl
        import pty
        import struct
        import termios

        leader, follower = pty.openpty()
        fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
        child = subprocess.Popen(
            [sys.executable, str(AUTOCODE), *argv],
            stdin=follower,
            stdout=follower,
            stderr=follower,
            cwd=self.workspace,
            env={**self.env, "TERM": "xterm-256color"},
            close_fds=True,
        )
        os.close(follower)
        return leader, child

    def test_the_full_screen_chat_runs_a_task_to_completion(self):
        leader, child = self.spawn_terminal("chat", "--workspace", str(self.workspace), BRIEF, "--", *FIXTURE_OPTIONS)
        seen = b""
        try:
            for until, reply in (
                (b"Approve understanding and prepare plan?", b"y\n"),
                (b"Approve this plan?", b"y\n"),
                (b"Anything else?", b"\n"),
            ):
                seen += read_terminal(leader, until=until)
                os.write(leader, reply)
            seen += read_terminal(leader)
        finally:
            os.close(leader)
            if child.poll() is None:
                child.kill()
        self.assertEqual(0, child.wait(timeout=60), seen.decode(errors="replace")[-2000:])
        self.assertIn(b"AutoCode chat", seen)
        self.assertIn(b"Plan to approve:", seen)
        self.assertIn(b"Done.", seen)
        self.assertTrue((self.workspace / "greet.py").is_file())

    def test_plain_autocode_in_a_terminal_opens_the_full_screen_chat(self):
        seen, code = self.run_terminal(until=b"What do you want done?")
        self.assertIn(b"AutoCode chat", seen)
        self.assertEqual(2, code)
        self.assertIn(b"Nothing started.", seen)
        self.assertFalse((self.workspace / ".autocode" / "runs").exists())

    def test_line_mode_flag_skips_the_full_screen_chat(self):
        seen, code = self.run_terminal("chat", "--line", until=b"What do you want done?")
        self.assertNotIn(b"AutoCode chat", seen)
        self.assertEqual(2, code)
        self.assertIn(b"Nothing started.", seen)

    def test_a_brief_and_a_run_are_not_both_named(self):
        proc = self.chat(BRIEF, "--run-dir", str(self.workspace), options=())
        self.assertEqual(2, proc.returncode)
        self.assertIn("not both", proc.stderr)
        self.assertFalse((self.workspace / ".autocode").exists())


if __name__ == "__main__":
    unittest.main()
