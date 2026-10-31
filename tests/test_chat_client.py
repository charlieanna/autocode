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
import autocode_run_view as run_view

from tests.terminal_screen import TerminalScreen, require_tmux
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

    def test_a_cross_model_stop_asks_for_the_verifier_model_without_a_token(self):
        view = {
            "status": "PAUSED_CROSS_MODEL",
            "needs": {
                "kind": "answer",
                "questions": [{"id": "route-plan_reviewer", "question": "Name the Plan Reviewer's model"}],
                "route": {
                    "question_id": "route-plan_reviewer",
                    "role": "plan_reviewer",
                    "cause": "cross_model",
                    "stopped_model": "gpt-5.6-sol",
                    "candidates": ["gpt-5.6-terra"],
                },
            },
            "next_command": f"autocode {PLACE} --answer route-plan_reviewer=MODEL",
        }
        command = actions.named(view, WORKSPACE, RUN)
        self.assertEqual(["--answer", "route-plan_reviewer=MODEL"], command.argv)
        decided = actions.decisions(view, command)
        self.assertEqual([("route-plan_reviewer", "model")], [(row.key, row.kind) for row in decided])
        filled = actions.fill(view, command, {"route-plan_reviewer": "gpt-5.6-terra"})
        self.assertEqual(
            ["--answer", "route-plan_reviewer=gpt-5.6-terra"],
            filled.argv,
        )

    def test_a_cross_model_question_is_a_model_prompt_even_during_requirements(self):
        view = {
            "status": "PAUSED_CROSS_MODEL",
            "needs": {
                "kind": "answer",
                "questions": [{"id": "route-plan_reviewer", "question": "Name the Plan Reviewer's model"}],
                "route": {
                    "question_id": "route-plan_reviewer",
                    "role": "plan_reviewer",
                    "cause": "cross_model",
                    "stopped_model": "gpt-5.6-sol",
                    "candidates": ["gpt-5.6-terra"],
                },
            },
            "next_command": f"autocode {PLACE} --answer route-plan_reviewer=MODEL",
            "chat": {
                "phase": "requirements",
                "context": "c1",
                "messages": [
                    {
                        "id": "requirements-question",
                        "role": "assistant",
                        "text": "Name the Plan Reviewer's model",
                    }
                ],
            },
        }
        client = ScriptedChat(view, DONE, replies=["gpt-5.6-terra"])
        self.assertEqual(0, client.converse())
        out = client.text()
        self.assertIn("Which model should plan_reviewer continue on?", out)
        self.assertIn("Models it can continue on: gpt-5.6-terra", out)
        self.assertEqual([["--answer", "route-plan_reviewer=gpt-5.6-terra"]], client.taken)

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

    def test_the_completion_order_is_showable_when_present_and_malformed_never(self):
        # Milestones and the flow are optional in legacy plans but must be showable when the
        # plan carries them: a client never asks a person to approve what it cannot render (#2434).
        legacy = {
            "token": "r2:h",
            "acceptance_criteria": [{"id": "C1", "criterion": "c", "verification_method": "m", "human_review": False}],
            "constraints": [],
            "permission_boundaries": [],
        }
        view = {"needs": {"kind": "approve_plan", "token": "r2:h"}, "displayed_plan": legacy}
        self.assertIs(legacy, actions.plan_for_approval(view))
        modern = {
            **legacy,
            "milestones": [
                {"id": "M1", "objective": "Finish the member's local gates", "acceptance_criteria": ["C1"]},
                {"id": "M2", "objective": "Coordinator-owned joint proof", "acceptance_criteria": []},
            ],
            "end_to_end_flow": ["Local gates", "The coordinator's joint check"],
        }
        self.assertIs(modern, actions.plan_for_approval(view | {"displayed_plan": modern}))
        for malformed in (
            {**modern, "milestones": [{"id": "M1"}]},
            {**modern, "milestones": [{"id": "M1", "objective": None}]},
            {**modern, "end_to_end_flow": ["a step", 7]},
            {**modern, "end_to_end_flow": "local then joint"},
        ):
            with self.subTest(malformed=sorted(malformed)[:1]):
                self.assertIsNone(actions.plan_for_approval(view | {"displayed_plan": malformed}))

    def test_render_plan_shows_the_completion_order_not_only_criteria(self):
        plan = {
            "token": "r2:h",
            "acceptance_criteria": [
                {"id": "AC1", "criterion": "Local gates", "verification_method": "m", "human_review": False},
                {
                    "id": "AC5",
                    "criterion": "Joint proof by the whole-set check",
                    "verification_method": "m",
                    "human_review": False,
                },
            ],
            "constraints": [],
            "permission_boundaries": [],
            "milestones": [
                {"id": "M1", "objective": "Finish the member's local gates", "acceptance_criteria": ["AC1"]},
                {"id": "M2", "objective": "Coordinator-owned joint proof", "acceptance_criteria": ["AC5"]},
            ],
            "end_to_end_flow": ["Finish this member's local gates", "The coordinator's joint check discharges AC5"],
        }
        shown = chat.render_plan(plan)
        # The circular dependency a member plan once hid is exactly what the screen now names.
        self.assertIn("Milestone M2 (proves AC5): Coordinator-owned joint proof", shown)
        self.assertIn("Steps in order:\n  1. Finish this member's local gates", shown)
        self.assertIn("2. The coordinator's joint check discharges AC5", shown)
        legacy = {key: value for key, value in plan.items() if key not in ("milestones", "end_to_end_flow")}
        self.assertNotIn("Milestone", chat.render_plan(legacy))
        self.assertNotIn("Steps in order", chat.render_plan(legacy))


DONE = {"status": "TASK_COMPLETE", "done": True, "needs": None, "next_command": None, "chat": {}}


class CheckpointActionTests(unittest.TestCase):
    """A saved step's compare and restore are taken only as the view names them."""

    def view(self, *names, command=None):
        prefix = ["checkpoint", "--workspace", str(WORKSPACE), "--run-dir", str(RUN)]
        named = {
            "compare": [*prefix, "--compare", "s1"],
            "restore": [*prefix, "--restore", "s1", "--expected-token", "-", "--request-id", "REQUEST_ID"],
        }
        rows = [{"id": "s1", "actions": [{"id": name, "next_command": command or named[name]} for name in names]}]
        return {"code_checkpoints": {"rows": rows}}

    def test_the_named_compare_and_restore_are_returned_as_named(self):
        view = self.view("compare", "restore")
        self.assertEqual("--compare", actions.checkpoint(view, "s1", "compare", WORKSPACE, RUN)[5])
        self.assertEqual("REQUEST_ID", actions.checkpoint(view, "s1", "restore", WORKSPACE, RUN)[-1])

    def test_a_restore_the_view_does_not_name_or_names_differently_is_a_gap(self):
        with self.assertRaisesRegex(actions.ClientGap, "offers no restore"):
            actions.checkpoint(self.view("compare"), "s1", "restore", WORKSPACE, RUN)
        with self.assertRaisesRegex(actions.ClientGap, "Unsupported compare"):
            actions.checkpoint(
                self.view("compare", command=["checkpoint", "--compare", "s1"]), "s1", "compare", WORKSPACE, RUN
            )


class WorkingActionTests(unittest.TestCase):
    def test_the_named_live_diff_is_returned_as_named_and_nothing_else(self):
        named = ["checkpoint", "--workspace", str(WORKSPACE), "--run-dir", str(RUN), "--working"]
        view = {"code_checkpoints": {"working": {"id": "working", "next_command": named}}}
        self.assertEqual(named, actions.live_diff(view, WORKSPACE, RUN))
        with self.assertRaisesRegex(actions.ClientGap, "offers no live diff"):
            actions.live_diff({"code_checkpoints": {"working": None}}, WORKSPACE, RUN)
        view["code_checkpoints"]["working"]["next_command"] = ["checkpoint", "--working"]
        with self.assertRaisesRegex(actions.ClientGap, "Unsupported working-diff"):
            actions.live_diff(view, WORKSPACE, RUN)


class SavedStepTests(unittest.TestCase):
    """/diff and /undo through the real `autocode checkpoint` CLI, on a run paused after a saved Builder step."""

    def setUp(self):
        from tests.test_code_checkpoints import CodeCheckpoints

        # The checkpoint tests' fixture alone: a Git project, an approved plan and one saved step.
        fixture = CodeCheckpoints("setUp")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.said: list[str] = []
        self.client = chat.ChatClient(
            fixture.workspace,
            fixture.run,
            command=(sys.executable, str(AUTOCODE)),
            env={**os.environ, "AUTOCODE_HOME": str(fixture.root / "registry")},
            say=self.said.append,
        )

    def test_diff_shows_what_changed_since_the_step(self):
        self.assertIn("1. ", self.client.slash("/diff", ""))
        (self.fixture.workspace / "app.txt").write_text("later work\n")
        shown = self.client.slash("/diff", "1")
        self.assertIn("app.txt", shown)
        self.assertIn("+later work", shown)
        self.assertIn("There is no step 2", self.client.slash("/diff", "2"))

    def test_diff_now_shows_the_builders_edits_only_while_a_step_runs(self):
        self.assertIn("offers no live diff", self.client.slash("/diff", "now"))
        self.assertNotIn("/diff now", self.client.slash("/diff", ""))
        self.fixture.state["active_stage"] = {
            "stage": "terra",
            "iteration": 3,
            "output": str(self.fixture.run / "builder-03.json"),
        }
        self.fixture.save()
        self.assertIn("Type /diff now", self.client.slash("/diff", ""))
        self.assertIn("has not changed any files since step 1", self.client.slash("/diff", "now"))
        (self.fixture.workspace / "app.txt").write_text("builder is editing\n")
        shown = self.client.slash("/diff", "now")
        self.assertIn("1 file changed since step 1", shown)
        self.assertIn("+builder is editing", shown)

    def test_undo_asks_first_then_goes_back_on_a_new_run_the_chat_talks_to(self):
        before = self.client.run_dir
        confirm = self.client.slash("/undo", "1")
        self.assertIsInstance(confirm, chat.panel.Confirm)
        self.assertEqual(before, self.client.run_dir, "nothing changes before the yes")
        with self.assertRaises(chat.Restored):
            confirm.then()
        self.assertNotEqual(before, self.client.run_dir)
        self.assertEqual("PAUSED_REQUESTED", self.client.status()["status"])
        self.assertIn("Went back on a new branch", self.said[-1])
        self.assertEqual({}, self.client.restore_requests)


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

    def test_core_classified_abandonment_runs_without_a_human_confirmation(self):
        state = {
            "status": "PAUSED_CONTROLLER_EXIT",
            "stop_reason": "Controller exited while a stage response was uncertain.",
            "active_stage": {
                "iteration": 1,
                "output": str(RUN / "iterations" / "001" / "requirements-gather-01.jsonl"),
                "stage": "requirements_gather",
            },
        }
        recovery = {"status": state["status"], **run_view.action(state, run_dir=RUN, workspace=WORKSPACE)}
        abandoned = {
            "status": "PAUSED_STAGE_ABANDONED",
            "needs": {"kind": "resume"},
            "next_command": f"autocode {PLACE} --resume-paused",
        }
        client = ScriptedChat(recovery, abandoned)
        client.check_others = mock.Mock()
        client.ask = mock.Mock(side_effect=AssertionError("routine recovery must not ask the person"))

        self.assertIsNone(client.step())
        self.assertEqual([["--abandon-stage", "001/requirements-gather-01"]], client.taken)
        client.ask.assert_not_called()
        shown = client.text()
        self.assertNotIn("--abandon-stage", shown)
        self.assertNotIn("Next it runs:", shown)
        self.assertNotIn("Run it yourself:", shown)

    def test_saved_progress_recovery_continues_without_a_human_confirmation(self):
        state = {"status": "PAUSED_BUDGET"}
        with mock.patch(
            "autocode_run_view.recovery_progress.routine_resume",
            return_value={"remaining_count": 1},
        ):
            view = {"status": state["status"], **run_view.action(state, run_dir=RUN, workspace=WORKSPACE)}
        client = ScriptedChat(view, DONE)
        client.ask = mock.Mock(side_effect=AssertionError("routine recovery must not ask the person"))

        self.assertEqual(0, client.converse())
        self.assertEqual([["--resume-paused"]], client.taken)
        client.ask.assert_not_called()
        self.assertNotIn("Continue? [y/N]", client.text())
        self.assertNotIn("autocode --resume-paused", client.text())

    def test_named_feedback_at_semantic_stops_is_sent_without_a_resume_or_approval(self):
        for status in ("RESOLVER_PENDING", "PAUSED_COMPLETION_GATE"):
            with self.subTest(status=status):
                state = {"status": status, "next_stage": "astra_review", "task": "Fix the total"}
                view = run_view.view(state, workspace=WORKSPACE, run_dir=RUN)
                correction = "Keep the member flow local; retain the required actual-code joint proof."
                client = ScriptedChat(view, DONE, replies=[correction])
                self.assertEqual(0, client.converse())
                self.assertEqual([["--feedback", correction]], client.taken)
                self.assertIn("or type what to change", client.text())

    def test_a_named_correction_at_a_planning_resume_is_sent_as_a_message(self):
        state = {"status": "PAUSED_INVALID_OUTPUT", "next_stage": "astra_finalize", "task": "Fix it"}
        view = run_view.view(state, workspace=WORKSPACE, run_dir=RUN)
        correction = "Retain the exact original source quotes; add new citations separately."
        client = ScriptedChat(view, DONE, replies=[correction])
        self.assertEqual(0, client.converse())
        self.assertEqual([["--feedback", correction]], client.taken)
        self.assertIn("or type what to change", client.text())

    def test_a_resume_without_a_message_offer_does_not_send_free_text(self):
        view = {
            "status": "PAUSED_INVALID_OUTPUT",
            "next_stage": "terra",
            "needs": {"kind": "resume"},
            "next_command": f"autocode {PLACE} --resume-paused",
        }
        client = ScriptedChat(view, DONE, replies=["Change the report"])
        self.assertEqual(2, client.converse())
        self.assertEqual([], client.taken)

    def test_a_correction_at_one_option_question_uses_its_offered_feedback_action(self):
        correction = "These examples were added in the current unapproved draft."
        client = ScriptedChat(answer_view(), DONE, replies=[correction])
        self.assertEqual(0, client.converse())
        self.assertEqual([["--feedback", correction]], client.taken)

    def test_an_unsupported_named_effort_is_typed_as_one_of_the_declared_levels(self):
        """#2039: the chat offered only Continue? [y/N] here, and continuing stopped at the same launch."""
        from tests.test_effort_question import paused

        view = run_view.view(paused("plan_reviewer"), workspace=WORKSPACE, run_dir=RUN)
        client = ScriptedChat(view, DONE, replies=["medium", "2"])
        self.assertEqual(0, client.converse())
        out = client.text()
        self.assertIn("does not offer 'medium' effort for the Plan Reviewer", out)
        self.assertIn("Which effort should the Plan Reviewer run at?", out)
        self.assertIn("Choose one of low, high, max", out)  # the undeclared level is refused in the client
        self.assertNotIn("[y/N]", out)
        self.assertEqual([["--answer", "effort-plan_reviewer=high"]], client.taken)

    def test_option_labels_defaults_and_numbers_stay_answers(self):
        for text, expected in (("A", "A"), ("B", "B"), ("1", "A"), ("", "A"), ("3", "3"), ("-1", "-1")):
            with self.subTest(text=text):
                client = ScriptedChat(answer_view(), DONE, replies=[text])
                self.assertEqual(0, client.converse())
                self.assertEqual([["--answer", "Q1=" + expected, "--resolver-token", "-"]], client.taken)

    def test_a_correction_does_not_invent_a_feedback_offer(self):
        view = answer_view()
        view["chat"].pop("input")
        client = ScriptedChat(view, DONE, replies=["Different scope"])
        self.assertEqual(0, client.converse())
        self.assertEqual([["--answer", "Q1=Different scope", "--resolver-token", "-"]], client.taken)

    def test_a_slash_command_at_a_question_is_run_not_sent_as_the_answer(self):
        """Typed at a run's question, ``/runs`` went to the model as the reply and spent a planning call."""
        client = ScriptedChat(answer_view(), DONE, replies=["/runs", "/bogus", "/live", "//literal", "2"])
        client.line_commands = True
        self.assertEqual(0, client.converse())
        out = client.text()
        self.assertIn("This chat has no project list.", out)
        self.assertIn("/bogus is not a command.", out)
        self.assertIn("/live is only in the full-screen chat.", out)
        self.assertEqual(["--feedback", "/literal"], client.taken[0], "// sent the text as typed")
        self.assertNotIn("/runs", " ".join(" ".join(argv) for argv in client.taken))

    def test_slash_help_and_quit_work_at_a_line_chat_prompt(self):
        client = ScriptedChat(answer_view(), DONE, replies=["/help", "/quit"])
        client.line_commands = True
        client.converse()
        self.assertIn("/help", client.text())
        self.assertEqual([], client.taken)

    def test_the_full_screen_chat_keeps_slash_text_as_typed(self):
        client = ScriptedChat(answer_view(), DONE, replies=["/runs"])
        self.assertEqual(0, client.converse())
        self.assertEqual([["--feedback", "/runs"]], client.taken)

    def test_an_off_list_default_and_space_padded_label_still_answer_the_question(self):
        for text, expected in (
            ("", "Other valid default"),
            ("Other valid default", "Other valid default"),
            (" A ", "A"),
        ):
            view = answer_view()
            view["needs"]["questions"][0]["proposed_default"] = "Other valid default"
            view["needs"]["questions"][0]["options"] = [" A ", "B"]
            client = ScriptedChat(view, DONE, replies=[text])
            with self.subTest(text=text):
                self.assertEqual(0, client.converse())
                self.assertEqual([["--answer", "Q1=" + expected, "--resolver-token", "-"]], client.taken)

    def test_a_correction_never_discards_partial_answers_to_multiple_questions(self):
        view = answer_view()
        view["needs"]["questions"].append({"id": "Q2", "question": "Second?", "options": ["C", "D"]})
        view["next_command"] += " --answer Q2=ANSWER"
        client = ScriptedChat(view, DONE, replies=["Correction", "D"])
        self.assertEqual(0, client.converse())
        self.assertEqual([["--answer", "Q1=Correction", "--resolver-token", "-", "--answer", "Q2=D"]], client.taken)

    def test_a_correction_does_not_use_a_stale_conversation_context(self):
        view = answer_view()
        client = ScriptedChat(view)
        command = actions.named(view, WORKSPACE, RUN)

        def reply(prompt):
            client.views[0] = {**view, "chat": {**view["chat"], "context": "changed"}}
            return "A factual correction"

        client.ask = reply
        with self.assertRaisesRegex(actions.ClientGap, "conversation changed"):
            client.answer(view, command, actions.decisions(view, command))
        self.assertEqual([], client.taken)

    def test_a_dependency_shows_the_wait_command_and_takes_nothing(self):
        view = {
            "status": "WAITING_FOR_DEPENDENCY",
            "needs": {"kind": "dependency", "reason": "Waits for the API run to deliver"},
            "pause_category_label": "Waiting on another run",
            "next_command": f"autocode wait --run-dir {WORKSPACE}/api/.autocode/runs/r9",
        }
        client = ScriptedChat(view)
        self.assertEqual(2, client.converse())
        self.assertIn("This chat cannot act on the dependency.", client.text())
        self.assertNotIn("autocode wait", client.text())
        self.assertNotIn("Run it yourself:", client.text())
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
        self.assertNotIn("Next it runs:", out)
        self.assertNotIn("autocode --approve-review", out)
        self.assertNotIn("=rv", out, "the issued token is never printed")
        self.assertNotIn(str(RUN), out, "the run's own paths are left out")
        self.assertIn("\nAccept these criteria? [y/N]: ", out, "the question stays short after the criteria are shown")
        self.assertEqual([["--approve-review", "C1", "--review-token", "-"]], client.taken)

    def test_a_review_names_the_files_each_criterions_steps_changed(self):
        """The chat showed only the criteria; the CLI's review stop names the changed files (#2360, #2383)."""
        view = self.review_view("C1")
        view["changes_by_criterion"] = {
            "meaning": "where to look",
            "criteria": [{"id": "C1", "milestones": ["M1"], "checkpoints": ["code-1"], "files": ["app/logo.py"]}],
            "unattributed": [],
        }
        client = ScriptedChat(view, DONE, replies=["y"])
        self.assertEqual(0, client.converse())
        out = client.text()
        self.assertIn("[C1] The logo is centred\n      Changed: app/logo.py\n", out)
        self.assertLess(out.index("Changed: app/logo.py"), out.index("Accept these criteria"))
        plain = ScriptedChat(self.review_view("C1"), DONE, replies=["y"])
        plain.converse()
        self.assertNotIn("Changed:", plain.text())

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

    def test_a_refused_approval_keeps_the_chat_open_and_asks_again(self):
        """#2000: another runner held the run lock, the core refused the approval, and the chat left with
        the plan and the draft gone. A refusal is said, the run is read again and the plan asked again."""
        plan = {
            "token": "r2:h",
            "acceptance_criteria": [
                {"id": "C1", "criterion": "The logo is centred", "verification_method": "m", "human_review": False}
            ],
            "constraints": [],
            "permission_boundaries": ["only here"],
        }
        view = {
            "status": "WAITING_FOR_USER",
            "needs": {"kind": "approve_plan", "token": "r2:h"},
            "displayed_plan": plan,
            "next_command": f"AUTOCODE_PLAN_TOKEN=r2:h autocode {PLACE} --approve-plan --plan-token -",
        }
        client = ScriptedChat(view, replies=["y", "", ""])
        refusals = [chat.Refused("AutoCode refused that: autocode: Another autocode runner holds this run lock")]
        taken = client.take

        def take(command):
            taken(command)
            if refusals:
                raise refusals.pop()

        client.take = take
        self.assertEqual(2, client.converse(), "the person left at the second asking; the refusal did not end the chat")
        out = client.text()
        self.assertIn("Another autocode runner holds this run lock", out)
        self.assertIn("The run is unchanged. If another autocode runner holds it, wait for that one", out)
        self.assertIn("Press Enter to read the run again, or Ctrl-D to leave: ", out)
        self.assertEqual(2, out.count("Approve this plan? [y/N]"), "the plan is shown and asked again")
        self.assertIn("Plan not approved", out)
        self.assertEqual([["--approve-plan", "--plan-token", "-"]], client.taken)

    def test_a_model_typed_at_a_stopped_job_names_the_route_instead_of_declining(self):
        """#2005: a quota stop asked for a model in prose, offered only Continue? [y/N], and read the typed
        model as a decline. The view names the route answer as a command; the chat takes the model through it."""
        place = f"--workspace {WORKSPACE} --run-dir {RUN}"
        view = {
            "status": "PAUSED_JOB_FAILURE",
            "pause_category_label": "Model or provider",
            "needs": {
                "kind": "retry_job",
                "reason": "Analyst: the provider reported its quota used up on openai/gpt-6-sol.",
                "job_retry_token": "jt1",
                "route": {
                    "question_id": "route-analyst",
                    "role": "analyst",
                    "job": "Analyst",
                    "cause": "quota",
                    "stopped_model": "openai/gpt-6-sol",
                    "command": f"AUTOCODE_JOB_RETRY_TOKEN=jt1 autocode {place} --answer route-analyst=MODEL --job-retry-token -",
                },
            },
            "next_command": f"AUTOCODE_JOB_RETRY_TOKEN=jt1 autocode {place} --resume-paused --retry-failed-stage --job-retry-token -",
        }
        client = ScriptedChat(view, DONE, replies=["zai-coding-plan/glm-5.3"])
        self.assertEqual(0, client.converse())
        out = client.text()
        self.assertIn("Continue? [y/N], or type the model to continue on: ", out)
        self.assertEqual(
            [["--answer", "route-analyst=zai-coding-plan/glm-5.3", "--job-retry-token", "-"]], client.taken
        )
        self.assertIn("Analyst continues on zai-coding-plan/glm-5.3; the retry on it is next.", out)
        self.assertNotIn("Left as it is", out)
        self.assertNotIn("Next it runs:", out)
        self.assertNotIn("autocode --answer", out)
        plain = ScriptedChat(view, DONE, replies=["y"])
        self.assertEqual(0, plain.converse())
        self.assertEqual([["--resume-paused", "--retry-failed-stage", "--job-retry-token", "-"]], plain.taken)
        self.assertNotIn("Next it runs:", plain.text())
        self.assertNotIn("autocode --resume-paused", plain.text())

    def held_time_pause(self):
        """#1950's shape: AutoResolver holds a time pause, so next_command is a plain resume (#2044)."""
        raise_command = (
            f"AUTOCODE_EXPECTED_RECOVERY_TOKEN=rt autocode {PLACE} --resume-paused --max-seconds N "
            "--expected-recovery-token -"
        )
        row = {
            "id": "max_seconds",
            "kind": "integer",
            "flag": "--max-seconds",
            "placeholder": "N",
            "arg_index": 2,
            "label": "Total active time (seconds)",
            "current": 2400,
            "elapsed": 2410.5,
            "minimum": 2411,
            "maximum": actions.MAX_SAFE_INTEGER,
            "command": raise_command,
        }
        return {
            "status": "RESOLVER_PENDING",
            "pause_category_label": "Limit reached",
            "needs": {"kind": "resume", "reason": "Saved active-time limit reached.", "command_inputs": [row]},
            "recovery": {"token": "rt"},
            "next_command": f"autocode {PLACE} --resume-paused",
        }

    def test_a_number_typed_at_a_held_bound_takes_the_raise_the_view_names(self):
        """#1950 read the stop reason's prose and could only say a typed 3600 set nothing. The core now names
        the raise (#2044): the chat offers it, fills the person's number and takes that command."""
        client = ScriptedChat(self.held_time_pause(), DONE, replies=["3600"])
        self.assertEqual(0, client.converse())
        out = client.text()
        self.assertIn("or type a new total active time (seconds) (2411 or more): ", out)
        self.assertEqual([["--resume-paused", "--max-seconds", "3600", "--expected-recovery-token", "-"]], client.taken)
        self.assertNotIn("Left as it is", out)
        self.assertNotIn("Next it runs:", out)
        plain = ScriptedChat(self.held_time_pause(), DONE, replies=["y"])
        self.assertEqual(0, plain.converse())
        self.assertEqual([["--resume-paused"]], plain.taken)

    def test_a_number_below_the_bound_or_for_a_stale_offer_takes_nothing(self):
        low = ScriptedChat(self.held_time_pause(), DONE, replies=["2400"])
        low.converse()
        self.assertEqual([], low.taken)
        self.assertIn("Enter a whole number from 2411", low.text())
        stale = self.held_time_pause()
        stale["recovery"] = {"token": "other"}
        client = ScriptedChat(stale, DONE, replies=["3600"])
        client.converse()
        self.assertEqual([], client.taken)
        self.assertNotIn("or type a new", client.text())

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

    def test_going_back_at_a_question_carries_on_with_the_new_run(self):
        class GoingBack(ScriptedChat):
            def reply(self, prompt):
                if not self.replies:
                    self.views.pop(0)  # the restore made a new run; its view is the next one
                    raise chat.Restored
                return super().reply(prompt)

        client = GoingBack(answer_view(), DONE)
        self.assertEqual(0, client.converse())
        self.assertEqual([], client.taken, "the old run's question was never answered")
        self.assertIn("Done.", client.text())

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
        self.assertIn("Any text starts a new task in this chat; press Enter to finish:", client.text())

    REVIEW = {
        "status": "WAITING_FOR_USER",
        "needs": {"kind": "answer", "questions": [{"id": "RQ", "question": "Is this right?"}]},
        "next_command": f"autocode {PLACE} --answer RQ=ANSWER",
        "chat": {
            "phase": "requirements",
            "context": "c1",
            "review": {
                "question_id": "RQ",
                "answer": "plan",
                "label": "Approve understanding and prepare plan",
                "summary": "You want a greeting CLI.",
                "spend_confirmation": {
                    "match": "trim_lower",
                    "title": "Prepare a reviewed plan?",
                    "detail": "Planning uses your selected models.",
                    "label": "Prepare plan",
                },
            },
            "input": {"kind": "feedback", "next_command": f"autocode {PLACE} --feedback MESSAGE"},
        },
    }

    @staticmethod
    def asking(question_id, text, context, message_id="requirements-question"):
        return {
            "status": "WAITING_FOR_USER",
            "needs": {"kind": "answer", "questions": [{"id": question_id, "question": text}]},
            "next_command": f"autocode {PLACE} --answer {question_id}=ANSWER",
            "chat": {
                "phase": "requirements",
                "context": context,
                "messages": [{"id": message_id, "role": "assistant", "text": text}],
                "input": {"kind": "feedback", "next_command": f"autocode {PLACE} --feedback MESSAGE"},
            },
        }

    def test_a_second_requirements_question_is_shown_before_the_person_is_asked(self):
        # #1792: an older core gave every question one message id, so the second was never shown and
        # the person faced a bare prompt. The client now asks the question its prompt waits on.
        first = self.asking("Q1", "Who is it for?", "c1")
        second = self.asking("Q2", "What should happen with no name?", "c2")
        client = ScriptedChat(first, second, replies=["Me"])
        self.assertEqual(2, client.converse())
        out = client.out
        asked = out.index("\nAutoCode: What should happen with no name?")
        self.assertEqual("\nYou: ", out[asked + 1], "the question comes before the prompt that answers it")
        self.assertEqual([["--feedback", "Me"]], client.taken)

    def test_a_reply_the_run_has_not_taken_up_repeats_its_question_not_a_bare_prompt(self):
        view = self.asking("Q1", "Who is it for?", "c1")
        client = ScriptedChat(view, replies=["Me"], moves=False)
        self.assertEqual(2, client.converse())
        self.assertIn("AutoCode is still waiting on this question:\nWho is it for?", client.text())

    def test_valid_requirements_approval_advances_through_the_projected_boundary_action(self):
        ready = run_view.view(
            {"status": "RUNNING", "phase": "PLANNING", "next_stage": "astra_discovery"},
            workspace=WORKSPACE,
            run_dir=RUN,
        )
        client = ScriptedChat(self.REVIEW, ready, DONE, replies=["y", "y"])
        self.assertEqual(0, client.converse(), client.text())
        self.assertEqual([["--answer", "RQ=plan"], ["--no-chat"]], client.taken)
        self.assertNotIn("--feedback", str(client.taken))

    def test_planning_starts_only_after_its_spend_is_confirmed(self):
        client = ScriptedChat(self.REVIEW, DONE, replies=["y", "y"])
        self.assertEqual(0, client.converse())
        self.assertEqual([["--answer", "RQ=plan"]], client.taken)
        self.assertIn("Prepare a reviewed plan?\nPlanning uses your selected models.", client.text())
        self.assertIn("Prepare plan? [y/N]: ", client.text())

    def test_declining_the_spend_sends_nothing_and_the_understanding_still_waits(self):
        client = ScriptedChat(self.REVIEW, replies=["y", "n"])
        self.assertEqual(2, client.converse())
        self.assertEqual([], client.taken)
        self.assertIn("Not started. The understanding still waits for you.", client.text())

    def test_typing_the_reviews_own_answer_is_confirmed_like_yes(self):
        client = ScriptedChat(self.REVIEW, DONE, replies=["  PLAN ", "y"])
        self.assertEqual(0, client.converse())
        self.assertEqual([["--answer", "RQ=plan"]], client.taken, "not sent as a correction")

    def test_a_mode_that_plans_at_once_is_confirmed_before_it_starts(self):
        asked = []
        for mode, replies, expected in (
            ({"spend_confirmation": "planning"}, [], True),
            ({"spend_confirmation": "start"}, ["y"], True),
            ({"spend_confirmation": "start"}, [""], False),
            ({}, ["n"], False),  # an older core that says nothing: confirm at launch
        ):
            answers = iter(replies)
            self.assertEqual(
                expected, chat.confirm_launch(lambda p: (asked.append(p), next(answers))[1], asked.append, mode)
            )
        self.assertEqual(3, asked.count("Start it? [y/N]: "))

    def test_a_run_number_out_of_range_is_asked_again_not_started_as_a_request(self):
        runs = [
            {"name": f"run-{n}", "run_dir": f"{WORKSPACE}/.autocode/runs/r{n}", "bucket": "needs you"} for n in (1, 2)
        ]
        said, replies = [], iter(["7", "1"])
        with (
            mock.patch.object(chat, "project_runs", return_value=runs),
            mock.patch.object(chat, "start", side_effect=AssertionError("started a run")),
        ):
            client = chat.open_chat(WORKSPACE, ask=lambda prompt: next(replies), say=said.append)
        self.assertEqual(Path(runs[1]["run_dir"]), client.run_dir, "1 is the newest, as /runs numbers it")
        self.assertIn("There is no run 7 waiting.", said)

    def test_the_run_picker_names_and_numbers_runs_as_runs_does(self):
        """#1754: the picker listed raw run names, oldest first, numbered apart from /runs."""

        def run(n, bucket, title, day):
            return {
                "name": f"202610{day}-chat-{n}",
                "run_dir": f"{WORKSPACE}/.autocode/runs/r{n}",
                "workspace": str(WORKSPACE),
                "bucket": bucket,
                "title": title,
                "updated_at": f"2026-10-{day}T10:00:00",
                "pause_category_label": "Your decision" if bucket == "needs you" else None,
            }

        listed = [
            run(1, "needs you", "Old dashboard", "01"),
            run(2, "complete", "Fix the typo", "10"),
            run(3, "needs you", "Add a --shout flag", "20"),
        ]
        said, asked, replies = [], [], iter(["3"])
        with mock.patch.object(chat, "project_runs", return_value=listed):
            client = chat.open_chat(
                WORKSPACE, ask=lambda prompt: asked.append(prompt) or next(replies), say=said.append
            )
        self.assertEqual(
            [
                "Runs waiting here:",
                "  1. Add a --shout flag (Your decision) · 2026-10-20 10:00",
                "  3. Old dashboard (Your decision) · 2026-10-01 10:00",
            ],
            said,
        )
        self.assertEqual(["Pick one (1, 3), or type a new request: "], asked)
        self.assertEqual(Path(listed[0]["run_dir"]), client.run_dir)
        runs = chat.panel.runs_text(chat.panel.run_rows(listed))
        self.assertIn("  1. Add a --shout flag", runs)
        self.assertIn("  3. Old dashboard", runs, "the same number in /runs")
        # Two runs of one request are told apart by when each last moved (#2353).
        listed[0]["title"] = listed[2]["title"]
        said.clear()
        with mock.patch.object(chat, "project_runs", return_value=listed):
            chat.open_chat(WORKSPACE, ask=lambda prompt: "3", say=said.append)
        self.assertEqual(2, len({line for line in said if "Add a --shout flag" in line}))

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
            mock.patch.object(chat, "terminal_ui", return_value=None),
            mock.patch.object(chat, "run_mode", return_value=[]),
            mock.patch.object(chat, "start", return_value=started) as start,
        ):
            self.assertEqual(0, chat.cli(["--workspace", str(WORKSPACE), "--brief=-v should print more"]))
        self.assertEqual("-v should print more", start.call_args.args[1])


class HistoryTests(unittest.TestCase):
    """/runs, /open and /new: this project's chats from `autocode runs --json`, and switching between them."""

    ROWS = [
        {
            "run_dir": str(RUN),
            "workspace": str(WORKSPACE),
            "title": "This one",
            "status": "TASK_COMPLETE",
            "updated_at": "2026-10-19T13:00:00",
        },
        {
            "run_dir": f"{WORKSPACE}/.autocode/runs/r2",
            "workspace": str(WORKSPACE),
            "title": "The other one",
            "bucket": "needs you",
            "updated_at": "2026-10-19T14:00:00",
        },
    ]

    def client(self):
        client = ScriptedChat(DONE)
        client.history = chat.History(WORKSPACE)
        for name, rows in (("project_runs", self.ROWS), ("every_project_runs", [])):
            patcher = mock.patch.object(chat, name, return_value=rows)
            patcher.start()
            self.addCleanup(patcher.stop)
        client._overview.accept(chat.polling.ProjectRuns(str(WORKSPACE)), json.dumps({"runs": self.ROWS}), "")
        client._overview.accept((str(WORKSPACE), None), json.dumps({"runs": []}), "")
        client.slash("/runs", "")
        return client

    def test_runs_also_lists_other_projects_chats_under_their_project(self):
        client = self.client()
        other = {
            "run_dir": "/elsewhere/.autocode/runs/x",
            "workspace": "/elsewhere",
            "title": "Elsewhere",
            "status": "TASK_COMPLETE",
            "updated_at": "2026-10-19T15:00:00",
        }
        with mock.patch.object(chat, "every_project_runs", return_value=[other, self.ROWS[0]]):
            client._overview.accept((str(WORKSPACE), None), json.dumps({"runs": [other, self.ROWS[0]]}), "")
            text = client.slash("/runs", "")
        self.assertLess(text.index("The other one"), text.index("Elsewhere"), "this project first")
        self.assertIn("elsewhere  /elsewhere\n  3. Elsewhere", text)
        with self.assertRaises(chat.Switched):
            client.slash("/open", "3")
        self.assertEqual((Path("/elsewhere"), Path(other["run_dir"])), (client.workspace, client.run_dir))

    def test_runs_lists_the_projects_chats_and_marks_this_one(self):
        text = self.client().slash("/runs", "")
        self.assertIn("  1. The other one", text)
        self.assertIn("  2. This one  (this chat)", text)

    def test_open_switches_the_chat_to_that_run(self):
        client = self.client()
        client.slash("/runs", "")
        client.seen.add("m1")
        switched = []
        client.switched = lambda workspace, run_dir: switched.append(run_dir)
        with self.assertRaises(chat.Switched):
            client.slash("/open", "1")
        self.assertEqual(Path(self.ROWS[1]["run_dir"]), client.run_dir)
        self.assertEqual([Path(self.ROWS[1]["run_dir"])], switched)
        self.assertEqual(set(), client.seen, "the opened chat's whole conversation is shown")

    def test_open_refuses_this_chat_and_a_number_not_listed(self):
        client = self.client()
        self.assertEqual("That is this chat.", client.slash("/open", "2"))
        self.assertIn("There is no chat 9.", client.slash("/open", "9"))
        self.assertEqual(RUN, client.run_dir)

    def test_new_asks_for_a_request_and_nothing_typed_keeps_this_chat(self):
        client = self.client()
        with self.assertRaises(chat.NewChat):
            client.slash("/new", "")
        client.replies = [""]
        client.new_chat()
        self.assertIn("Nothing started; this chat carries on.", client.text())
        self.assertEqual(RUN, client.run_dir)

    def test_before_a_chat_is_open_open_hands_the_chosen_run_to_open_chat(self):
        history = chat.History(WORKSPACE)
        with (
            mock.patch.object(chat, "project_runs", return_value=self.ROWS),
            mock.patch.object(chat, "every_project_runs", return_value=[]),
        ):
            self.assertIn("This is a new chat", history.slash("/new", ""))
            with self.assertRaises(chat.Opened) as opened:
                history.slash("/open", "2")
        self.assertEqual(str(RUN), opened.exception.row["run_dir"])

    def test_open_chat_opens_the_run_picked_at_its_first_question(self):
        def ask(prompt):
            raise chat.Opened(self.ROWS[1])

        finished = [{**row, "bucket": "complete"} for row in self.ROWS]  # nothing waits: the first question is asked
        with mock.patch.object(chat, "project_runs", return_value=finished):
            said = []
            client = chat.open_chat(WORKSPACE, ask=ask, say=said.append, hint=True)
        self.assertEqual(Path(self.ROWS[1]["run_dir"]), client.run_dir)
        self.assertEqual(["2 earlier chats here. Type /runs to see them."], said)


class NewChatSetupTests(unittest.TestCase):
    """/mode, /engine and /connect through the History the full-screen chat binds."""

    def history(self, folder):
        from tests.test_chat_setup import CONNECTIONS, MODES

        history = chat.History(Path(folder))
        listings = {"modes": MODES, "connections": CONNECTIONS}
        checked = {
            "id": "opencode",
            "label": "OpenCode",
            "connection_status": "credentials_present",
            "detail": "Saved.",
        }
        history.read = lambda argv: checked if argv[:2] == ["connections", "check"] else listings[argv[0]]
        return history

    def test_mode_and_engine_change_what_new_chats_start_with_and_are_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / ".autocode").mkdir()
            history = self.history(folder)
            self.assertEqual("New chats start in Other mode.", history.slash("/mode", "other"))
            self.assertEqual("New chats run on Claude Code.", history.slash("/engine", "claude"))
            self.assertIn("There is no mode 'fast'", history.slash("/mode", "fast"))
            self.assertEqual(chat.setup.NewChat("other", ["--provider", "claude", "--engine", "claude"]), history.new)
            self.assertEqual(history.new, chat.setup.NewChat.load(Path(folder)))

    def test_a_saved_mode_the_core_no_longer_lists_gives_way_to_the_default(self):
        with tempfile.TemporaryDirectory() as folder:
            history = self.history(folder)
            history.new = chat.setup.NewChat("quick", ["--engine", "claude"])  # saved before 2026-10-22
            history.forget_unlisted_mode()
            self.assertEqual(chat.setup.NewChat(None, ["--engine", "claude"]), history.new, "the options stay")
            self.assertEqual("New chats: Full · Claude Code. /mode and /engine change them.", history.summary())
            history.new.mode = "other"
            history.forget_unlisted_mode()
            self.assertEqual("other", history.new.mode, "a listed choice is kept")

    def test_the_banner_engine_and_connect_name_the_same_default_connection(self):
        """#1756: with default_provider = "kilocode" they named kilocode, OpenCode and nothing."""
        from tests.test_chat_setup import CONNECTIONS

        with tempfile.TemporaryDirectory() as folder:
            history = self.history(folder)
            rows = [{**row, "default": row["id"] == "kilocode"} for row in CONNECTIONS["connections"]]
            listings = {"modes": history.read(["modes"]), "connections": {**CONNECTIONS, "connections": rows}}
            history.read = lambda argv: listings[argv[0]]
            banner, notice = history.summary().split("\n")
            self.assertEqual("New chats: Full · Kilo. /mode and /engine change them.", banner)
            self.assertEqual(
                "Kilo cannot start chats yet (unavailable). Installed here: OpenCode, Codex. "
                "Type /engine opencode, /engine codex to run new chats on it.",
                notice,
            )
            self.assertIn("New chats use the Kilo connection (kilocode)", history.slash("/engine", ""))
            self.assertNotIn("(current)", history.slash("/engine", ""))
            self.assertIn("  kilocode  Kilo: unavailable  (new chats use it)", history.slash("/connect", ""))

    def test_an_unusable_default_backend_is_named_in_the_banner_and_before_the_plain_chat_question(self):
        """Only Claude Code installed: the chat said nothing until the first request failed."""
        from tests.test_chat_setup import CONNECTIONS

        with tempfile.TemporaryDirectory() as folder:
            history = self.history(folder)
            rows = [
                {**row, "connection_status": "unavailable" if row["id"] == "opencode" else row["connection_status"]}
                for row in CONNECTIONS["connections"]
            ]
            rows.append({"id": "claude", "label": "Claude Code", "connection_status": "not_checked"})
            listings = {"modes": history.read(["modes"]), "connections": {**CONNECTIONS, "connections": rows}}
            history.read = lambda argv: listings[argv[0]]
            notice = "OpenCode cannot start chats yet (unavailable). Installed here: Codex, Claude Code. "
            notice += "Type /engine codex, /engine claude to run new chats on it."
            self.assertEqual(notice, history.notice())
            self.assertEqual(f"New chats: Full · OpenCode. /mode and /engine change them.\n{notice}", history.summary())
            history.slash("/engine", "claude")
            self.assertIsNone(history.notice(), "a chosen engine needs no warning")
            self.assertNotIn("cannot start", history.summary())

    def test_a_choice_made_at_the_first_question_starts_that_first_chat_with_it(self):
        """#2009: open_chat was handed the new-chat mode and options before its question, so /connect NAME use,
        /mode and /engine typed there changed only the next chat; the first ran on the default connection."""
        with tempfile.TemporaryDirectory() as folder:
            history = self.history(folder)

            def ask(prompt):
                self.assertEqual("What do you want done? ", prompt)
                self.assertIn("New chats use OpenCode.", history.slash("/connect", "opencode use"))
                self.assertIn("New chats start in Other mode.", history.slash("/mode", "other"))
                return "Add a --shout flag"

            started = []
            modes = lambda mode, **_: {
                "id": mode,
                "start_args": ["--mode-flag", mode],
                "spend_confirmation": "planning",
            }
            with (
                mock.patch.object(chat, "project_runs", return_value=[]),
                mock.patch.object(chat, "mode_row", side_effect=modes),
                mock.patch.object(
                    chat, "start", side_effect=lambda ws, brief, args, options, **_: (brief, args, options)
                ),
            ):
                started = chat.open_chat(Path(folder), None, [], ask=ask, say=lambda _: None, new=history.new)
            self.assertEqual(("Add a --shout flag", ["--mode-flag", "other"], ["--provider", "opencode"]), started)

    def test_a_slash_command_typed_at_the_plain_chat_question_is_taken_not_run_as_a_task(self):
        """The line-by-line chat started a run called "/engine claude" when the person typed what the
        unusable-default notice told them to; the full-screen chat already took it as the command."""
        with tempfile.TemporaryDirectory() as folder:
            history = self.history(folder)
            replies = iter(["/engine claude", "/bogus", "/runs", "Add a --shout flag"])
            said = []
            modes = lambda mode, **_: {"id": mode, "start_args": [], "spend_confirmation": "planning"}
            with (
                mock.patch.object(chat, "project_runs", return_value=[]),
                mock.patch.object(chat, "mode_row", side_effect=modes),
                mock.patch.object(
                    chat, "start", side_effect=lambda ws, brief, args, options, **_: (brief, args, options)
                ),
            ):
                started = chat.open_chat(
                    Path(folder),
                    None,
                    [],
                    ask=lambda _: next(replies),
                    say=said.append,
                    new=history.new,
                    slash=history.slash,
                )
            self.assertEqual(("Add a --shout flag", []), started[:2])
            self.assertEqual("claude", chat.setup.flag_value(started[2], "--engine"))
            self.assertIn("New chats run on Claude Code.", "\n".join(said))
            self.assertIn("Open or start a chat first: type a request, or /runs to see this project's chats.", said)
            self.assertIn("No chats yet. Type a request to start one.", said)

    def test_a_slash_command_typed_at_the_run_picker_is_taken_not_run_as_a_task(self):
        """/runs, /help and /quit at "Pick one, or type a new request" started a run called "/runs" (#2355)."""
        with tempfile.TemporaryDirectory() as folder:
            history = self.history(folder)
            waiting = [
                {
                    "name": f"202610{day}-chat-{n}",
                    "run_dir": f"{folder}/.autocode/runs/r{n}",
                    "workspace": folder,
                    "bucket": "needs you",
                    "title": title,
                    "updated_at": f"2026-10-{day}T10:00:00",
                    "pause_category_label": "Your decision",
                }
                for n, title, day in ((1, "Old dashboard", "01"), (2, "Add a --shout flag", "20"))
            ]

            def open_with(*replies):
                it, said = iter(replies), []
                with (
                    mock.patch.object(chat, "project_runs", return_value=waiting),
                    mock.patch.object(
                        chat, "mode_row", side_effect=lambda mode, **_: {"id": mode, "spend_confirmation": "planning"}
                    ),
                    mock.patch.object(chat, "start", side_effect=lambda ws, brief, *a, **_: brief),
                ):
                    return chat.open_chat(
                        Path(folder), None, [], ask=lambda _: next(it), say=said.append, slash=history.slash
                    ), said

            started, said = open_with("/runs", "/help", "/bogus", "7", "/quit")
            self.assertIsNone(started)
            self.assertIn("  2. Old dashboard", "\n".join(said), "/runs listed the chats")
            self.assertIn(chat.FIRST_PROMPT_HELP, said)
            self.assertIn("Open or start a chat first: type a request, or /runs to see this project's chats.", said)
            self.assertIn("There is no run 7 waiting.", said)
            self.assertEqual("Nothing started.", said[-1])
            started, _ = open_with("//slashes are my tool", "unused")
            self.assertEqual("/slashes are my tool", started)
            picked, _ = open_with("2")
            self.assertEqual(Path(waiting[0]["run_dir"]), picked.run_dir, "a number still picks a run")

    def test_help_quit_and_a_slash_request_work_at_the_plain_chat_question(self):
        """/help and /quit typed at "What do you want done?" both answered "Open or start a chat first"."""
        with tempfile.TemporaryDirectory() as folder:
            history = self.history(folder)

            def open_with(*replies):
                it, said = iter(replies), []
                with (
                    mock.patch.object(chat, "project_runs", return_value=[]),
                    mock.patch.object(
                        chat, "mode_row", side_effect=lambda mode, **_: {"id": mode, "spend_confirmation": "planning"}
                    ),
                    mock.patch.object(chat, "start", side_effect=lambda ws, brief, *a, **_: brief),
                ):
                    return chat.open_chat(
                        Path(folder), None, [], ask=lambda _: next(it), say=said.append, slash=history.slash
                    ), said

            started, said = open_with("/help", "/quit")
            self.assertIsNone(started)
            self.assertIn(chat.FIRST_PROMPT_HELP, said)
            self.assertEqual("Nothing started.", said[-1])
            self.assertNotIn("Open or start a chat first", "\n".join(said))
            started, _ = open_with("//slashes are my tool", "unused")
            self.assertEqual("/slashes are my tool", started)

    def test_connect_checks_signs_in_through_the_terminal_and_uses_a_backend(self):
        with tempfile.TemporaryDirectory() as folder:
            history = self.history(folder)
            self.assertIn("/connect opencode check|login|use", history.slash("/connect", ""))
            self.assertEqual("OpenCode: credentials present. Saved.", history.slash("/connect", "opencode check"))
            ran = []
            history.outside = lambda argv, label: ran.append((argv[-4:], label)) or 0
            self.assertIn("Sign in / Reconnect finished (exit 0)", history.slash("/connect", "opencode login"))
            self.assertEqual([(["connections", "login", "--provider", "opencode"], "Sign in / Reconnect")], ran)
            self.assertIn("offers no 'setup'", history.slash("/connect", "opencode setup"))
            self.assertIn("cannot start chats yet", history.slash("/connect", "kilocode use"))
            self.assertIn("New chats use OpenCode", history.slash("/connect", "opencode use"))
            self.assertIn("for this session", history.slash("/connect", "opencode use"), "no .autocode: not kept")
            self.assertEqual(["--provider", "opencode"], history.new.options)
            # Codex is an engine without a provider config: `use` chooses --engine codex (#2357).
            self.assertIn("New chats use Codex", history.slash("/connect", "codex use"))
            self.assertEqual(["--engine", "codex"], history.new.options)


class TabTests(unittest.TestCase):
    """This session's chats as tabs: numbered in the order opened, each with its state; /tab switches."""

    def client(self):
        view = {"task": "Fix the typo\nin README", "needs": {"kind": "approve_plan", "token": "t"}}
        client = ScriptedChat(view)  # its status() returns views as given, so record what a real read records
        client.identity, client.titles[str(RUN)] = chat.wait_identity(view), chat.title_of(view)
        other = f"{WORKSPACE}/.autocode/runs/r0"
        client.others = {other: (WORKSPACE, ("done",))}
        client.tab_order = [other, str(RUN)]
        client.titles[other] = "Add a flag"
        return client, other

    def test_tabs_list_each_chat_with_its_state_and_mark_the_one_shown(self):
        client, other = self.client()
        self.assertEqual(
            [
                {"number": 1, "run": other, "title": "Add a flag", "state": "done", "current": False},
                {"number": 2, "run": str(RUN), "title": "Fix the typo", "state": "needs you", "current": True},
            ],
            client.tabs(),
        )

    def test_tab_switches_by_number_or_next_and_previous(self):
        client, other = self.client()
        drawn = []
        client.tabs_changed = drawn.append
        self.assertEqual("That is this chat.", client.slash("/tab", "2"))
        self.assertIn("There is no tab 7. Choose 1 to 2.", client.slash("/tab", "7"))
        with self.assertRaises(chat.Switched):
            client.slash("/tab", "next")  # wraps around to the first
        self.assertEqual(Path(other), client.run_dir)
        self.assertEqual([1, 2], [row["number"] for row in drawn[-1]])
        self.assertTrue(drawn[-1][0]["current"])
        with self.assertRaises(chat.Switched):
            client.slash("/tab", "previous")
        self.assertEqual(RUN, client.run_dir)

    def test_tab_leaves_the_request_composer_for_this_chat(self):
        """#1997: on the tab Ctrl-T opened, /tab to this chat said "That is this chat." and the draft stayed."""
        client, other = self.client()
        restored = []
        client.switched = lambda workspace, run: restored.append((workspace, run))
        client._composing_new = True
        with self.assertRaises(chat.Switched):
            client.slash("/tab", "2")
        self.assertEqual([(WORKSPACE, RUN)], restored, "the chat's own display comes back")
        self.assertEqual(RUN, client.run_dir)
        self.assertIn("Nothing started; this chat carries on.", client.text())
        with self.assertRaises(chat.Switched):
            client.slash("/tab", "next")  # another tab: switched to as always
        self.assertEqual(Path(other), client.run_dir)

    def test_a_chat_switched_to_keeps_its_last_state_until_it_is_read_again(self):
        """#1757: the tabs drawn on /tab showed a chat that waits for the person as working."""
        client, other = self.client()
        client.others[other] = (WORKSPACE, ("approve_plan", "t0"))
        drawn = []
        client.tabs_changed = drawn.append
        with self.assertRaises(chat.Switched):
            client.slash("/tab", "1")
        self.assertEqual(["needs you", "needs you"], [row["state"] for row in drawn[0]])

    def test_a_long_task_makes_a_short_title(self):
        self.assertEqual("Hierarchical planning", chat.title_of({"task": "# Hierarchical planning\nbody"}))
        self.assertEqual(40, len(chat.title_of({"task": "x" * 99})))


class StreamAndNoticeTests(unittest.TestCase):
    def test_a_new_turn_shows_its_completion_even_when_an_earlier_turn_said_the_same_text(self):
        client = ScriptedChat({})
        self.addCleanup(client.close)
        text = "The requested task is complete."
        prior = {"id": "feedback-previous-response", "role": "assistant", "text": text}
        client.show({"chat": {"messages": [prior], "notice": "I couldn’t finish this step."}})
        client.show({"chat": {"messages": [prior], "notice": "Working on your request…"}})
        completed = {
            "done": True,
            "chat": {"messages": [prior, {"id": "response-1", "role": "assistant", "text": text}]},
        }
        client.show(completed)
        client.show(completed)
        self.assertEqual(2, client.text().count(f"AutoCode: {text}"))
        self.assertEqual(f"\nAutoCode: {text}", client.out[-1])

    def test_messages_without_ids_have_distinct_stable_role_and_text_identities(self):
        client = ScriptedChat({})
        self.addCleanup(client.close)
        view = {
            "chat": {
                "messages": [
                    {"role": "assistant", "text": "First reply"},
                    {"role": "assistant", "text": "Second reply"},
                    {"role": "user", "text": "First reply"},
                ]
            }
        }
        client.show(view)
        client.show(view)
        self.assertEqual(["\nAutoCode: First reply", "\nAutoCode: Second reply", "\nYou: First reply"], client.out)

    def test_a_local_prompt_echo_is_consumed_once_without_hiding_another_public_message(self):
        view = ConversationTests.REVIEW
        client = ScriptedChat(view)
        self.addCleanup(client.close)
        with self.assertRaises(chat.Left):
            client.requirements(view, actions.named(view, WORKSPACE, RUN))
        text = view["chat"]["review"]["summary"]
        message = {"id": "recorded-question", "role": "assistant", "text": text}
        client.show({"chat": {"messages": [message]}})
        self.assertEqual(1, client.text().count(f"AutoCode: {text}"))
        client.show({"chat": {"messages": [message, {**message, "id": "later-reply"}]}})
        self.assertEqual(2, client.text().count(f"AutoCode: {text}"))

    def test_an_unmatched_local_prompt_cannot_hide_a_later_turn_message(self):
        view = ConversationTests.REVIEW
        client = ScriptedChat(view)
        self.addCleanup(client.close)
        with self.assertRaises(chat.Left):
            client.requirements(view, actions.named(view, WORKSPACE, RUN))
        client.show({"chat": {"messages": []}})
        text = view["chat"]["review"]["summary"]
        client.show({"chat": {"messages": [{"id": "later-reply", "role": "assistant", "text": text}]}})
        self.assertEqual(2, client.text().count(f"AutoCode: {text}"))

    def test_a_local_prompt_echo_survives_its_counterpart_still_being_written(self):
        view = ConversationTests.REVIEW
        client = ScriptedChat(view)
        self.addCleanup(client.close)
        with self.assertRaises(chat.Left):
            client.requirements(view, actions.named(view, WORKSPACE, RUN))
        text = view["chat"]["review"]["summary"]
        live = {"id": "requirements-live", "role": "assistant", "text": text, "streaming": True}
        client.show({"chat": {"messages": [live]}})
        client.show({"chat": {"messages": [{"id": "recorded-question", "role": "assistant", "text": text}]}})
        self.assertEqual(1, client.text().count(f"AutoCode: {text}"), "the question is printed once")

    def test_a_local_user_echo_does_not_hide_an_assistant_message_with_the_same_text(self):
        client = ScriptedChat({})
        self.addCleanup(client.close)
        client._local_echoes = {("user", "Same words")}
        client.show({"chat": {"messages": [{"id": "new-reply", "role": "assistant", "text": "Same words"}]}})
        self.assertEqual(["\nAutoCode: Same words"], client.out)

    def test_a_reply_being_written_is_streamed_then_replaced_by_the_finished_message(self):
        streamed = []
        client = ScriptedChat({})
        client._stream = streamed.append
        live = {
            "chat": {
                "messages": [
                    {"id": "requirements-live", "role": "assistant", "text": "Thinking about", "streaming": True}
                ]
            }
        }
        client.show(live)
        client.show({"chat": {"messages": [{"id": "m2", "role": "assistant", "text": "Done thinking."}]}})
        self.assertEqual(["Thinking about", None], streamed)
        self.assertNotIn("Thinking about", client.text(), "the live text is never printed as a message")
        self.assertIn("AutoCode: Done thinking.", client.text())

    def test_reads_come_fast_only_while_the_streaming_reply_changes(self):
        client = ScriptedChat({})

        def streaming(text):
            return {"chat": {"messages": [{"id": "live", "role": "assistant", "text": text, "streaming": True}]}}

        self.assertEqual(2.0, client.pace(2.0), "nothing is streaming")
        client.show(streaming("I'll check"))
        self.assertEqual(chat.STREAM_POLL_SECONDS, client.pace(2.0))
        self.assertEqual(0.1, client.pace(0.1), "a shorter ordinary pace is never slowed down")
        for _ in range(chat.STREAM_QUIET_POLLS):
            client.show(streaming("I'll check"))
        self.assertEqual(2.0, client.pace(2.0), "a reply that stopped changing is read at the ordinary pace")
        client.show(streaming("I'll check the tests"))
        self.assertEqual(chat.STREAM_POLL_SECONDS, client.pace(2.0), "a change makes reads fast again")
        client.show({"chat": {"messages": [{"id": "m2", "role": "assistant", "text": "Done."}]}})
        self.assertEqual(2.0, client.pace(2.0), "the finished message ends streaming")

    def test_an_unchanged_notice_is_said_once_while_the_progress_line_changes(self):
        client = ScriptedChat({})
        for line, stage in (
            ("Requirements working", "requirements_gather"),
            ("Plan Reviewer working", "astra_challenge"),
            ("Tester working", "astra_checkpoint"),
        ):
            client.show({"chat": {"notice": "Working on your request…"}, "progress": {"line": line, "stage": stage}})
        self.assertEqual(1, client.text().count("Working on your request…"))
        self.assertNotIn("Requirements working", client.text(), "the notice stands for the first stage")
        self.assertIn("· Plan Reviewer working", client.text())
        self.assertIn("· Tester working", client.text())

    def test_a_notice_that_returns_after_a_transient_gap_is_not_said_again(self):
        client = ScriptedChat({})
        working = {"chat": {"notice": "Working on your request…"}, "needs": {"kind": "continue"}}
        client.show({**working, "progress": {"line": "Requirements working", "stage": "requirements_gather"}})
        client.show(
            {"needs": {"kind": "continue"}, "progress": {"line": "Next: Requirements", "stage": "requirements_gather"}}
        )
        client.show({**working, "progress": {"line": "Requirements working", "stage": "requirements_gather"}})
        self.assertEqual(1, client.text().count("Working on your request…"))
        self.assertIn("· Next: Requirements", client.text())
        self.assertNotIn("· Requirements working", client.text(), "the same stage is said once")

    def test_one_progress_line_per_stage_when_it_flips_between_working_finished_and_next(self):
        """#1844: a stage's line was said again for working, finished and next with unchanged counts."""
        client = ScriptedChat({})

        def view(headline, stage, checked=0):
            return {
                "chat": {"notice": "Working on your request…"},
                "needs": {"kind": "continue"},
                "progress": {
                    "line": f"{headline} · 0 of 1 task done · {checked} of 5 requirements checked",
                    "stage": stage,
                    "tasks": {"label": "0 of 1 task done"},
                    "requirements": {"label": f"{checked} of 5 requirements checked"},
                    "problems": {"open": 0},
                },
            }

        for shown in (
            view("Next: Orchestrator", "orchestrator"),
            view("Builder working", "terra"),
            view("Next: Builder", "terra"),
            view("Builder working", "terra"),
            view("Builder finished", "terra"),
            view("Next: Tester", "sol"),
            view("Tester working", "sol"),
            view("Tester finished", "sol"),
            view("Next: Completion Reviewer", "astra_review", checked=5),
            view("Completion Reviewer working", "astra_review", checked=5),
            view("Completion Reviewer finished", "astra_review", checked=5),
        ):
            client.show(shown)
        text = client.text()
        self.assertEqual(1, text.count("· Builder working"))
        self.assertEqual(1, text.count("· Next: Tester"))
        self.assertEqual(1, text.count("· Next: Completion Reviewer"))
        for repeated in (
            "· Next: Builder",
            "Builder finished",
            "Tester working",
            "Tester finished",
            "Completion Reviewer working",
            "Completion Reviewer finished",
        ):
            self.assertNotIn(repeated, text)

    def test_a_notice_that_returns_after_the_run_waited_for_the_person_is_said_again(self):
        client = ScriptedChat({})
        working = {"chat": {"notice": "Working on your request…"}, "needs": {"kind": "continue"}}
        client.show(working)
        client.show({"chat": {"notice": None}, "needs": {"kind": "answer"}})
        client.show(working)
        self.assertEqual(2, client.text().count("Working on your request…"))

    def test_a_different_notice_after_a_gap_is_still_said(self):
        client = ScriptedChat({})
        client.show({"chat": {"notice": "Working on your request…"}})
        client.show({"chat": {"notice": "Paused as you asked. Your work so far is saved; resume when you’re ready."}})
        self.assertEqual(1, client.text().count("Paused as you asked"))

    def test_the_wait_a_notice_tells_apart(self):
        self.assertEqual(("done",), chat.wait_identity({"done": True}))
        self.assertIsNone(chat.wait_identity({"needs": {"kind": "continue"}}))
        self.assertEqual(
            ("approve_plan", "", "", "", "r2:h", ""),
            chat.wait_identity({"needs": {"kind": "approve_plan", "token": "r2:h"}}),
        )

    def test_a_chat_left_earlier_is_announced_once_when_it_starts_to_wait(self):
        client = ScriptedChat(DONE)
        client.others = {f"{WORKSPACE}/.autocode/runs/r0": (WORKSPACE, None)}
        waiting = {
            "view": {"needs": {"kind": "approve_plan", "token": "t"}, "progress": {"needs_you": "plan approval needed"}}
        }
        key = (str(WORKSPACE), f"{WORKSPACE}/.autocode/runs/r0")
        client._poller.tick = lambda targets: [(key, json.dumps(waiting), "")]
        client.check_others(every=0)
        client.check_others(every=0)
        self.assertEqual(1, client.text().count("Another chat needs you: plan approval needed (r0)"))


class RefusedStartTests(unittest.TestCase):
    """A new run AutoCode refuses before saving anything: the chat says why, not that it has no status."""

    def test_the_refusal_is_shown_instead_of_a_missing_status(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "autocode"
            script.write_text(
                "import sys\n"
                "if '--status' in sys.argv:\n"
                "    sys.exit(\"autocode: [Errno 2] No such file or directory: 'state.json'\")\n"
                "print('autocode: OpenCode is not on PATH; no provider request was launched', file=sys.stderr)\n"
                "sys.exit(2)\n"
            )
            said: list[str] = []
            with self.assertRaises(actions.ClientGap) as caught:
                chat.start(
                    Path(folder),
                    "hi",
                    [],
                    [],
                    command=(sys.executable, str(script)),
                    say=said.append,
                    poll_seconds=0.01,
                    wait=lambda seconds: None,
                )
        self.assertEqual(
            "AutoCode did not start the run: autocode: OpenCode is not on PATH; no provider request was launched",
            str(caught.exception),
        )


class AdvancingDiagnosticTests(unittest.TestCase):
    def test_existing_run_exit_two_displays_refusal_without_retry_or_token_leak(self):
        client = ScriptedChat({"status": "PAUSED_JOB_FAILURE"})
        self.addCleanup(client.close)
        client.viewed.add(str(client.run_dir))
        client.workspace = ROOT
        client._status_view = client.status()
        secret = "jr:" + "a" * 64
        client.command = (
            sys.executable,
            "-c",
            "import sys; print('Input rejected: Provider configuration or limits changed; retry is stale'); "
            "print(sys.argv[1]); sys.exit(2)",
            secret,
        )
        client.advance(["--resume-paused"], {"AUTOCODE_JOB_RETRY_TOKEN": secret})
        self.assertIn("Provider configuration or limits changed; retry is stale", client.text())
        self.assertNotIn(secret, client.text())
        self.assertEqual([], client.taken)


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

    def test_a_pause_asked_for_between_polls_is_taken_through_the_view(self):
        with tempfile.TemporaryDirectory() as folder:
            client = ScriptedChat({"status": "RUNNING"})
            client.workspace, client.command = Path(folder), (sys.executable, "-c", "import time; time.sleep(1)")
            asked = iter(["pause"])
            client._wait = lambda seconds: next(asked, None) or time.sleep(0.05)
            with mock.patch.object(client, "request_pause") as pause, mock.patch.object(client, "show"):
                client.advance(["--no-chat"])
        pause.assert_called_once_with(again="Try /pause again in a moment.")

    def test_every_status_view_read_reaches_the_renderer(self):
        seen = []
        client = chat.ChatClient(WORKSPACE, RUN, observe=seen.append)
        payload = json.dumps({"view": {"status": "RUNNING", "workspace": str(WORKSPACE), "run_dir": str(RUN)}})
        key = (str(WORKSPACE), str(RUN))
        with mock.patch.object(client._poller, "tick", return_value=[(key, payload, "")]):
            view = client.status()
        self.assertEqual([view], seen)

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
        proc = self.chat(BRIEF, replies="y\ny\ny\n\n")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        out = proc.stdout
        self.assertIn("Approve understanding and prepare plan? [y/N]", out)
        # The core's spend confirmation, as the extension's modal shows it, before paid planning.
        self.assertIn("Prepare a reviewed plan?\nPlanning uses your selected models and saved limits.", out)
        self.assertIn("Prepare plan? [y/N]: ", out)
        self.assertNotIn(chat.LAUNCH_WARNING, out, "Full mode confirms at planning, not at launch")
        self.assertIn("Plan to approve:\n  [C1] Greeting contract holds", out)
        self.assertLess(out.index("Plan to approve:"), out.index("The requested task is complete."))
        self.assertTrue(out.rstrip().endswith("Done."), out[-500:])
        self.assertTrue((self.workspace / "greet.py").is_file())

    def test_a_question_is_answered_by_choosing_an_option(self):
        ask_one_question_once(self, self.env)
        # Requirements first (the only mode since 2026-10-22): confirm, prepare the plan, answer, approve.
        proc = self.chat(BRIEF, replies="y\ny\n2\ny\n\n")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertLess(proc.stdout.index("Prepare plan? [y/N]"), proc.stdout.index("Should the README target"))
        self.assertEqual(1, proc.stdout.count("Should the README target beginners"), "the question is asked once")
        self.assertIn("  2. Experienced Python users", proc.stdout)
        self.assertEqual("Experienced Python users", self.answers()["readme-audience"])

    def test_a_correction_at_an_option_question_is_saved_as_feedback_without_approval(self):
        ask_one_question_once(self, self.env)
        correction = "The question needs to reflect the current draft."
        proc = self.chat(BRIEF, replies="y\ny\n" + correction + "\n")
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn("Input rejected", proc.stdout)
        events = subprocess.run(
            [sys.executable, str(AUTOCODE), "events", "--workspace", str(self.workspace)],
            capture_output=True,
            text=True,
            env=self.env,
            check=True,
        )
        rows = [json.loads(line) for line in events.stdout.splitlines()]
        self.assertEqual([correction], [row["text"] for row in rows if row.get("kind") == "brief_feedback"])
        self.assertFalse(any(row.get("kind") == "goal_approval" for row in rows))
        self.assertNotIn("readme-audience", self.answers())

    def test_a_saved_mode_the_core_no_longer_lists_starts_the_default_instead_of_failing(self):
        (self.workspace / ".autocode").mkdir(exist_ok=True)
        saved = {"mode": "quick", "options": list(FIXTURE_OPTIONS)}  # kept by a chat before 2026-10-22
        (self.workspace / ".autocode" / "chat-settings.json").write_text(json.dumps(saved), encoding="utf-8")
        proc = self.chat(BRIEF, replies="y\ny\ny\n\n", options=())
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn("no run mode", proc.stderr)
        self.assertIn("Approve understanding and prepare plan? [y/N]", proc.stdout, "Requirements comes first")
        self.assertTrue((self.workspace / "greet.py").is_file())

        explicit = self.chat("--mode", "quick", BRIEF, options=())
        self.assertEqual(1, explicit.returncode, explicit.stdout + explicit.stderr)
        self.assertIn(
            "no run mode 'quick'; choose one of: full", explicit.stderr, "a mode asked for by name is not guessed"
        )

    def test_leaving_at_the_plan_changes_nothing_and_chat_picks_the_run_up_again(self):
        left = self.chat(BRIEF, replies="y\ny\n")
        self.assertEqual(2, left.returncode, left.stdout + left.stderr)
        self.assertIn("Left the chat; the run is unchanged.", left.stdout)
        self.assertNotIn("autocode chat --run-dir", left.stdout)
        self.assertFalse((self.workspace / "greet.py").exists(), "nothing is built before the person approves")

        resumed = self.chat(replies="y\n\n", options=())
        self.assertEqual(0, resumed.returncode, resumed.stdout + resumed.stderr)
        self.assertIn("Plan to approve:", resumed.stdout)
        self.assertTrue((self.workspace / "greet.py").is_file())

    def test_with_nothing_open_the_chat_asks_what_to_do_and_starts_it(self):
        proc = self.chat(replies=BRIEF + "\ny\ny\ny\n\n")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.startswith("What do you want done? "), proc.stdout[:200])
        self.assertTrue((self.workspace / "greet.py").is_file())

    def test_with_several_runs_open_the_person_picks_one(self):
        for _ in range(2):
            self.assertEqual(2, self.chat(BRIEF, replies="y\ny\n").returncode)
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
                (b"Prepare plan?", b"y\n"),
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

    def test_a_plan_taller_than_the_screen_opens_at_its_top_and_pages_to_its_end(self):
        """#2434: an approval also carries its milestones and flow, so the plan can outgrow the
        screen. It opens at its top beside the approval prompt, PageDown reaches its end, PageUp
        returns to its head, and only then does the person approve — nothing is hidden, only paged."""
        tmux = require_tmux(self)
        argv = [
            sys.executable,
            str(AUTOCODE),
            "chat",
            "--workspace",
            str(self.workspace),
            BRIEF,
            "--",
            *FIXTURE_OPTIONS,
        ]
        with TerminalScreen(argv, cwd=self.workspace, env=self.env, cols=100, rows=20, tmux=tmux) as screen:
            for gate in ("Approve understanding", "Prepare plan?"):
                screen.wait_for(gate, 300)
                screen.type("y")
                screen.keys("Enter")
            screen.wait_for(
                lambda shown: (
                    "Approve this plan" in shown
                    and "Plan to approve:" in shown
                    and "Greeting contract holds" in shown
                    and "Steps in order" not in shown
                    and "Run greet.py" not in shown
                ),
                300,
                what="the plan's first page at its approval prompt, without its paged-away steps",
            )
            screen.keys("NPage")
            screen.wait_for(
                lambda shown: (
                    "Steps in order" in shown
                    and "Read Hello, NAME or a usage error" in shown
                    and "Plan to approve:" not in shown
                ),
                60,
                what="the plan's last flow steps after PageDown, past its head",
            )
            screen.keys("PPage")
            screen.wait_for(
                lambda shown: (
                    "Approve this plan" in shown and "Plan to approve:" in shown and "Greeting contract holds" in shown
                ),
                60,
                what="the plan's head after PageUp, with the approval prompt still shown",
            )
            screen.type("y")
            screen.keys("Enter")
            screen.wait_for("Anything else?", 300)
            screen.keys("Enter")
            self.assertEqual(0, screen.exit_status())
            screen.wait_for("Done.", what="the closing words after the screen closes")
            self.assertTrue((self.workspace / "greet.py").is_file())

    def test_plain_autocode_in_a_terminal_opens_the_full_screen_chat(self):
        seen, code = self.run_terminal(until=b"What do you want done?")
        self.assertIn(b"AutoCode chat", seen)
        self.assertEqual(2, code)
        self.assertIn(b"Nothing started.", seen)
        self.assertFalse((self.workspace / ".autocode" / "runs").exists())

    def test_a_multi_line_paste_keeps_one_line_break_per_line(self):
        """#1758: the terminal turned each pasted CR into NL, so a CRLF reached the composer as two breaks.
        Keeping CR as CR leaves Enter, which most terminals send as CR, still sending."""
        leader, child = self.spawn_terminal()
        try:
            read_terminal(leader, until=b"What do you want done?")
            os.write(leader, b"\x1b[200~Line 1\r\nLine 2\x1b[201~")
            shown = read_terminal(leader, until=b"Line 2").decode(errors="replace")
            os.write(leader, b"\x15/help\r")  # Ctrl-U clears the composer; a CR is still Enter
            read_terminal(leader, until=b"List these commands")
            os.write(leader, b"\x04")
            read_terminal(leader)
        finally:
            os.close(leader)
            if child.poll() is None:
                child.kill()
        self.assertEqual(2, child.wait(timeout=60))
        self.assertIn("Line 1↵Line 2", shown)
        self.assertNotIn("↵↵", shown)

    def test_an_error_before_the_conversation_is_shown_after_the_full_screen_closes(self):
        leader, child = self.spawn_terminal("chat", "--workspace", str(self.workspace), "--mode", "bogus", BRIEF)
        try:
            seen = read_terminal(leader)
        finally:
            os.close(leader)
            if child.poll() is None:
                child.kill()
        self.assertEqual(1, child.wait(timeout=60))
        self.assertIn(b"\x1b[?1049l", seen, "the full-screen chat opened and closed")
        self.assertGreater(seen.rindex(b"no run mode 'bogus'"), seen.rindex(b"\x1b[?1049l"), seen[-500:])

    def test_line_mode_flag_skips_the_full_screen_chat(self):
        seen, code = self.run_terminal("chat", "--line", until=b"What do you want done?")
        self.assertNotIn(b"AutoCode chat", seen)
        self.assertEqual(2, code)
        self.assertIn(b"Nothing started.", seen)

    def test_a_run_option_typed_as_a_chat_option_is_told_to_go_after_the_separator(self):
        """#2416: `autocode chat --engine codex "brief"` said only "unrecognized arguments"."""
        proc = self.chat("--engine", "codex", BRIEF, options=())
        self.assertEqual(2, proc.returncode)
        self.assertIn("unrecognized arguments: --engine", proc.stderr)
        self.assertIn('go after --: autocode chat "BRIEF" -- --engine codex', proc.stderr)
        self.assertFalse((self.workspace / ".autocode").exists())
        proc = self.chat("--help", options=())
        self.assertEqual(0, proc.returncode)
        self.assertIn('go after --: autocode chat "BRIEF" -- --engine codex', proc.stdout)

    def test_a_brief_and_a_run_are_not_both_named(self):
        proc = self.chat(BRIEF, "--run-dir", str(self.workspace), options=())
        self.assertEqual(2, proc.returncode)
        self.assertIn("not both", proc.stderr)
        self.assertFalse((self.workspace / ".autocode").exists())


if __name__ == "__main__":
    unittest.main()


class WorkspaceGuardTests(unittest.TestCase):
    def test_a_git_checkout_or_a_project_inside_one_needs_no_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(chat.in_git_checkout(Path(folder)))
            (Path(folder) / ".git").mkdir()
            self.assertTrue(chat.in_git_checkout(Path(folder)))
            nested = Path(folder) / "src" / "deep"
            nested.mkdir(parents=True)
            self.assertTrue(chat.in_git_checkout(nested))

    def test_a_plain_directory_is_confirmed_before_a_new_chat_sees_none_of_its_files(self):
        with tempfile.TemporaryDirectory() as folder:
            said, answers = [], []
            ask = lambda prompt: (said.append(prompt), answers.pop(0))[1]
            with self.subTest(declined="n"):
                answers.append("n")
                self.assertFalse(chat.guard_workspace(Path(folder), ask, said.append))
                self.assertTrue(any("not in a Git checkout" in row for row in said))
                self.assertTrue(any("[y/N]" in row for row in said))
            with self.subTest(proceed="y"):
                answers.append("y")
                self.assertTrue(chat.guard_workspace(Path(folder), ask, said.append))

    def test_a_new_chat_in_a_plain_directory_declines_without_starting(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            (workspace / "billing").mkdir()
            client = ScriptedChat(
                {"status": "TASK_COMPLETE", "done": True},
                replies=["fix the billing bug", "n"],
            )
            client.workspace = workspace
            with mock.patch.object(chat, "mode_row", return_value={"spend_confirmation": "planning"}):
                client.new_chat()
            self.assertTrue(any("not in a Git checkout" in row for row in client.out))
            self.assertTrue(any("Nothing started; this chat carries on." in row for row in client.out))
            self.assertFalse((workspace / ".autocode").exists())



