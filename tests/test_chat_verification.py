"""Current verification is separate from saved messages, edits and command exits."""

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_chat_panel as panel

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux


def current_view(*, freshness=None, delivered=False):
    freshness = freshness or ("not_inspected" if delivered else "stale_or_unverified")
    return {
        "status": "TASK_COMPLETE",
        "done": True,
        "progress": {"headline": "Complete", "requirements": {"label": "1 recorded PASS"}},
        "verification": {
            "freshness": freshness,
            "reasons": [
                "The saved report has not been inspected."
                if freshness == "not_inspected"
                else "The report matches the inspected source."
                if freshness == "current"
                else "The source changed after these checks."
            ],
            "coverage": [
                {
                    "id": "AC1",
                    "criterion": "Return the correct sum",
                    "state": "checked" if freshness == "current" and delivered else "unchecked",
                    "recorded_status": "PASS",
                    "human_acceptance_pending": not delivered,
                    "evidence_refs": ["checks/result.json"],
                }
            ],
        },
        "delivery": {"verified_complete": delivered},
    }


def details(view):
    return panel.text("/details", view, panel.StageLog())


class VerificationDisplayTests(unittest.TestCase):
    def test_completed_review_uses_its_recorded_result_instead_of_build_delivery(self):
        import autocode_chat_verification as verification

        for freshness in ("current", "stale_or_unverified", "unknown"):
            view = current_view(delivered=False)
            view.update(
                workflow="review",
                evidence={
                    "workflow_result": {
                        "outcome": "request_changes",
                        "summary": "1 blocking, 2 advisory findings",
                        "artifact": "review/findings.json",
                        "artifact_sha256": "a" * 64,
                        "freshness": freshness,
                    }
                },
            )
            # Inspection is separate from immutable canonical outcome facts.
            view["review_result"] = view["evidence"]["workflow_result"].copy()
            view["evidence"]["workflow_result"].pop("freshness")
            before = copy.deepcopy(view)
            self.assertIn("Review result", {section.title for section in panel.sections(view)})
            self.assertIn("recorded request changes", verification.cue(view))
            self.assertIn("1 blocking, 2 advisory", details(view))
            self.assertIn("review/findings.json", details(view))
            self.assertNotIn("Current delivery:", details(view))
            self.assertEqual(before, view)
        legacy = {
            "workflow": "review",
            "status": "TASK_COMPLETE",
            "evidence": {
                "workflow_result": {
                    "outcome": "approve",
                    "artifact": "review/findings.json",
                }
            },
        }
        self.assertIn("current review unknown", verification.cue(legacy))

    def test_stale_recorded_pass_is_not_presented_as_current_delivery(self):
        view = current_view()
        before = copy.deepcopy(view)
        shown = {section.title: section for section in panel.sections(view)}
        self.assertIn("Verification", shown)
        text = details(view)
        self.assertIn("Current delivery: not verified", text)
        self.assertIn("stale or unverified", text)
        self.assertIn("recorded PASS", text)
        self.assertIn("unchecked", text)
        self.assertIn("human acceptance pending", text)
        self.assertIn("checks/result.json", text)
        self.assertEqual(before, view)

    def test_gate_verification_does_not_invent_an_explicit_report_inspection(self):
        view = current_view(freshness="not_inspected", delivered=True)
        text = details(view)
        self.assertIn("Current delivery: verified by core gate", text)
        self.assertIn("Report inspection: not inspected", text)

    def test_saved_edits_commands_and_recovery_are_distinct_from_accepted_evidence(self):
        view = current_view()
        view.update(
            status="PAUSED_INVALID_OUTPUT",
            done=False,
            runner_check={"summary": "Checking regressions", "command": "pytest -q"},
            code_checkpoints={"working": {"changed_files": ["app.py"]}, "rows": [{"id": "s1"}]},
            recovery={
                "what_happened": "Builder ended before returning its report.",
                "retained": "Saved edits remain available; partial work needs verification.",
                "actions": [{"label": "Recover saved work", "effect": "Reconcile this exact interrupted attempt."}],
            },
            needs={"recovery_hint": "Inspect the retained attempt first."},
            next_command="AUTOCODE_RESOLVER_TOKEN=private-value autocode --resume-paused",
        )
        text = details(view)
        for expected in (
            "Current delivery: not verified",
            "edits still need verification",
            "recorded command alone",
            "Recover saved work",
            "Inspect the retained attempt",
            "/diff",
        ):
            self.assertIn(expected, text)
        self.assertNotIn("private-value", text)

    def test_timing_uses_supplied_execution_and_waiting_without_inference(self):
        view = current_view()
        view["efficiency"] = {
            "time": {
                "summed_execution_seconds": None,
                "known_summed_execution_seconds": 123.5,
                "wall_union_seconds": 100,
                "exclusive_wall_seconds_by_category": {"waiting": 600},
            }
        }
        view["usage"] = {"accounting": {"attempts": [{"job": "Tester", "active": True, "duration_seconds": None}]}}
        text = details(view)
        self.assertIn("Summed execution: unknown", text)
        self.assertIn("Known execution subtotal: 123.5s", text)
        self.assertIn("Observed interval wall time: 100.0s", text)
        self.assertIn("Answered-request waiting: 600.0s", text)
        self.assertIn("Current attempt recorded duration (Tester): unknown", text)
        self.assertNotIn("823.5", text)
        self.assertNotIn("ETA", text)

    def test_missing_and_malformed_optional_fields_remain_unknown(self):
        view = {"status": "TASK_COMPLETE", "verification": {"reasons": None, "coverage": [None]}, "delivery": {}}
        text = details(view)
        self.assertIn("Current delivery: unknown", text)
        self.assertIn("Report inspection: unknown", text)
        self.assertIn("Answered-request waiting: unknown", text)
        self.assertNotIn("verified by core gate", text)
        self.assertIn("unknown", panel.sections(view)[0].summary)

    def test_unavailable_first_read_does_not_claim_a_previous_snapshot(self):
        from autocode_chat_verification import cue

        self.assertEqual("Status unavailable; no previous read", cue(None, unavailable=True))
        self.assertEqual("Reading run status", cue(None))

    def test_invalid_supplied_durations_do_not_become_elapsed_time(self):
        for value in (-1, True, float("nan"), float("inf"), 10**1000, "12"):
            with self.subTest(value_type=type(value).__name__):
                view = current_view()
                view["efficiency"] = {"time": {"summed_execution_seconds": value}}
                self.assertIn("Summed execution: unknown", details(view))


@unittest.skipUnless(os.name == "posix", "the TUI uses a POSIX curses terminal")
class VerificationHeaderTests(unittest.TestCase):
    def test_background_messages_and_streams_preserve_the_history_viewport(self):
        from tests.test_chat_tui import screen_ui

        ui = screen_ui()
        ui._screen.getmaxyx.return_value = (24, 80)
        ui.observe({"status": "RUNNING", "progress": {"headline": "Builder editing"}})
        for number in range(40):
            ui.say(f"AutoCode: History message {number:02}")
        import autocode_chat_tui as tui

        ui._handle(tui.curses.KEY_PPAGE, asking=False)

        def visible():
            drawn = []
            ui._put = lambda row, col, text, attr=0: drawn.append((row, text))
            ui._draw()
            layout = ui._layout(24, 80)
            return [text for row, text in drawn if layout.body_start <= row < layout.body_end], drawn

        before, _ = visible()
        ui.say("AutoCode: A new background stage has started.")
        self.assertEqual(before, visible()[0])
        ui.stream("A new streamed reply " * 30)
        self.assertEqual(before, visible()[0])
        ui.observe({"status": "RUNNING", "progress": {"headline": "Tester checking"}})
        after, drawn = visible()
        self.assertEqual(before, after)
        self.assertTrue(any(row == 2 and "History" in text and "Tester checking" in text for row, text in drawn))

    def test_only_the_current_unavailable_tab_marks_the_last_read_stale(self):
        from tests.test_chat_tui import screen_ui

        ui = screen_ui()
        ui.observe(current_view(delivered=True))
        drawn = []
        ui._put = lambda row, col, text, attr=0: drawn.append((row, text))
        for current, expected in ((False, False), (True, True)):
            ui.tabs([{"number": 1, "title": "Chat", "current": current, "state": "unavailable"}])
            drawn.clear()
            ui._header(ui._layout(24, 80))
            self.assertEqual(expected, any(row == 2 and "Status unavailable" in text for row, text in drawn))


@unittest.skipUnless(os.name == "posix", "the TUI uses a POSIX curses terminal")
class VerificationTerminalTests(unittest.TestCase):
    def test_a_later_completion_with_identical_text_is_visible_after_resume(self):
        tmux = require_tmux(self)
        program = """import json,pathlib,sys
sys.path.insert(0,sys.argv[1])
from autocode_chat_client import ChatClient
from autocode_chat_tui import ChatTUI
control=pathlib.Path(sys.argv[2])
seen=None
with ChatTUI() as ui:
 client=ChatClient('/fixture/project','/fixture/project/.autocode/runs/repeated',say=ui.say)
 def idle():
  global seen
  text=control.read_text()
  if text==seen:return
  seen=text
  view=json.loads(text)
  ui.observe(view)
  client.show(view)
 ui.idle=idle
 idle()
 ui.ask('Resume? [y/N]: ')
 ui.ask('Anything else? ')
 client.close()
"""
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.json"
            text = "The requested task is complete."
            prior = {"id": "feedback-previous-response", "role": "assistant", "text": text}

            def update(view):
                pending = control.with_suffix(".pending")
                pending.write_text(json.dumps(view))
                pending.replace(control)

            update(
                {
                    "status": "PAUSED_PLANNING_BUDGET",
                    "chat": {"messages": [prior], "notice": "I couldn’t finish this step."},
                }
            )
            with TerminalScreen(
                [sys.executable, "-c", program, str(ROOT / "tools"), str(control)],
                cwd=ROOT,
                env=os.environ,
                cols=100,
                rows=30,
                tmux=tmux,
            ) as screen:
                screen.wait_for("Resume? [y/N]")
                update(
                    {
                        "status": "RUNNING",
                        "needs": {"kind": "continue"},
                        "chat": {"messages": [prior], "notice": "Working on your request…"},
                    }
                )
                screen.wait_for("Working on your request…")
                screen.type("y")
                screen.keys("Enter")
                screen.wait_for("Anything else?")
                update(
                    {
                        **current_view(delivered=True),
                        "chat": {"messages": [prior, {"id": "response-1", "role": "assistant", "text": text}]},
                    }
                )
                shown = screen.wait_for(lambda body: body.count(text) == 2, what="the new completion reply")
                self.assertGreater(shown.rfind(text), shown.index("Working on your request…"))
                self.assertLess(shown.rfind(text), shown.index("Anything else?"))
                self.assertIn("Complete; current delivery verified", shown)
                screen.keys("Enter")
                self.assertEqual(0, screen.exit_status())

    def test_current_cue_updates_during_history_reading_and_resize(self):
        tmux = require_tmux(self)
        program = """import json,pathlib,sys
sys.path.insert(0,sys.argv[1])
from autocode_chat_tui import ChatTUI
control=pathlib.Path(sys.argv[2])
seen=None
with ChatTUI() as ui:
 def idle():
  global seen
  text=control.read_text()
  if text==seen:return
  seen=text
  data=json.loads(text)
  if data.get('unavailable'):ui.status_unavailable('fixture status read failed')
  else:ui.observe(data['view'])
  ui.tabs([{'number':1,'title':'Fixture','current':True,'state':'unavailable' if data.get('unavailable') else 'working'}])
  if data.get('message'):ui.say('AutoCode: '+data['message'])
 ui.idle=idle
 idle()
 for n in range(40):ui.say(f'AutoCode: History message {n:02}')
 ui.ask('Fixture ready. Press Enter to finish.')
"""
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / "control.json"

            def update(view, **extra):
                view = {
                    **view,
                    "workspace": "/fixture/project",
                    "run_dir": "/fixture/project/.autocode/runs/verification",
                    "observation": {
                        "version": 1,
                        "workspace": "/fixture/project",
                        "run_dir": "/fixture/project/.autocode/runs/verification",
                        "checkpoint_token": "c" * 64,
                        "sampled_at": "2026-10-24T05:00:00+00:00",
                    },
                }
                pending = control.with_suffix(".pending")
                pending.write_text(json.dumps({"view": view, **extra}))
                pending.replace(control)

            update({"status": "RUNNING", "progress": {"headline": "Builder editing"}})
            with TerminalScreen(
                [sys.executable, "-c", program, str(ROOT / "tools"), str(control)],
                cwd=ROOT,
                env=os.environ,
                cols=80,
                rows=24,
                tmux=tmux,
            ) as screen:
                screen.wait_for("Fixture ready")
                screen.keys("PPage")
                old = screen.wait_for("History · ")
                anchor = next(line for line in old.splitlines()[3:17] if "History message" in line)
                update(
                    {"status": "RUNNING", "progress": {"headline": "Tester checking 你好"}},
                    message="Background update arrived",
                )
                shown = screen.wait_for("Tester checking 你好")
                self.assertIn(anchor, shown)
                screen.resize(120, 34)
                screen.wait_for("Tester checking 你好")
                update({"status": "RUNNING", "progress": {"headline": "Completion Reviewer checking"}})
                screen.wait_for("Completion Reviewer checking")
                update(current_view())
                screen.wait_for("Saved complete; current delivery not verified")
                update(current_view(), unavailable=True)
                screen.wait_for("Status unavailable; showing the previous read")
                update(current_view(delivered=True))
                shown = screen.wait_for("Complete; current delivery verified")
                self.assertIn("Fixture ready", shown)
                self.assertIn("status read", shown)
                screen.type("/details ")
                screen.keys("Enter")
                screen.wait_for("Core snapshot sampled at: 2026-10-24T05:00:00+00:00")
                from autocode_chat_tui import display_width

                self.assertTrue(all(display_width(line) <= 120 for line in shown.splitlines()))
                screen.keys("Enter")
                self.assertEqual(0, screen.exit_status())
