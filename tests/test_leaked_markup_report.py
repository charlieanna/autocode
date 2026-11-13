"""A final message of leaked transport markup (issue #3310): recognized, refused and routed.

Pure rules, plus the provider seam that refuses before report processing while retaining the
exact raw message. The behavior a person sees through the CLI is covered by the existing
report-repair suites.
"""

import json
import tempfile
import unittest
from pathlib import Path

import autocode_format_correction as format_correction
import autocode_leaked_markup_report as leaked_markup
import autocode_report_source as report_source
from providers import opencode

from .test_opencode import event, terminal

TOOL_CALL = (
    "<tool_call><function=bash><parameter=command>python3 - <<'PY'\n"
    'import json\np = "/run/report.response.txt"\ns = open(p).read()\nprint(json.dumps({}))\n'
    "PY</parameter><parameter=description>Inspect the rejected report</parameter></function></tool_call>"
)
NOTICES = (
    "<system_warning>Long conversation exceeded 128k tokens: conversation truncated after system prompt. "
    "Continuing while preserving critical instructions and recent context.</system_warning> "
    "I need the archived file's exact defect near that offset to build a bounded text edit repair. "
    "<system_warning>Long conversation exceeded 128k tokens: conversation truncated after system prompt. "
    "Continuing while preserving critical instructions and recent context.</system_warning>"
)
REPORT = '{"summary":"Plan reviewed","concerns":[]}'


class LeakedMarkupShapeTests(unittest.TestCase):
    def test_the_two_saved_shapes_are_refused_under_their_own_name(self):
        for text, message in ((TOOL_CALL, leaked_markup.TOOL_CALL_ERROR), (NOTICES, leaked_markup.WARNING_ERROR)):
            with self.subTest(text=text[:40]):
                with self.assertRaises(leaked_markup.LeakedMarkupReport) as caught:
                    leaked_markup.refuse(text)
                self.assertEqual(message, str(caught.exception))
                self.assertTrue(leaked_markup.matches(str(caught.exception)))

    def test_a_report_or_salvageable_text_is_never_refused(self):
        for text in (
            REPORT,
            "```json\n" + REPORT + "\n```",
            "The review is done.\n" + REPORT,
            "I will inspect it.\n" + TOOL_CALL,
            TOOL_CALL.replace("<tool_call>", "prose <tool_call>", 1),
            '{"summary":"mentions <system_warning> inside a value"}',
            NOTICES + "\n" + REPORT,
            "",
            "plain prose with no markup at all",
        ):
            with self.subTest(text=text[:48]):
                self.assertEqual(text, leaked_markup.refuse(text))

    def test_a_message_that_is_only_a_notice_or_only_a_tool_call_holds_no_report(self):
        for text in ("<system_warning>notice</system_warning>", TOOL_CALL.strip()):
            with self.subTest(text=text[:44]):
                with self.assertRaises(leaked_markup.LeakedMarkupReport):
                    leaked_markup.refuse(text)

    def test_matches_accepts_a_trailing_explanation_and_rejects_other_errors(self):
        self.assertTrue(leaked_markup.matches(leaked_markup.TOOL_CALL_ERROR + ": detail"))
        for other in ("OpenCode final message is not a JSON report", None, 5, ""):
            with self.subTest(other=other):
                self.assertFalse(leaked_markup.matches(other))

    def test_the_exception_class_names_the_failure_ledger_identity(self):
        self.assertEqual("LeakedMarkupReport", leaked_markup.LeakedMarkupReport.__name__)
        self.assertTrue(issubclass(leaked_markup.LeakedMarkupReport, RuntimeError))


class LeakedMarkupRouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.events = Path(temporary.name) / "stage-01.jsonl"
        self.events.write_text(
            json.dumps({"type": "thread.started", "thread_id": "repairer-session"})
            + "\n"
            + json.dumps({"type": "turn.completed"})
            + "\n"
        )
        self.route = {"engine": "opencode", "provider": "opencode", "model": "m", "reasoning_effort": "high"}
        self.state = {"settings": {"provider": "opencode", "roles": {"tester": dict(self.route)}}}

    def pending(self, error, **original):
        record = {
            "role": "tester",
            "route_role": "tester",
            "stage": "tester",
            "engine": "opencode",
            "supports_sessions": True,
            "events": str(self.events),
            "launch_route": dict(self.route),
            **original,
        }
        return {"original": record, "attempts": 0, "error": error}

    def test_the_full_repair_is_told_the_rejected_message_is_markup(self):
        for error in (leaked_markup.TOOL_CALL_ERROR, leaked_markup.WARNING_ERROR):
            with self.subTest(error=error[:44]):
                self.assertEqual(
                    leaked_markup.REPAIR_INSTRUCTION, report_source.repair_report_instruction({"error": error})
                )
        self.assertNotEqual(
            leaked_markup.REPAIR_INSTRUCTION,
            report_source.repair_report_instruction({"error": "OpenCode final message is not a JSON report"}),
        )

    def test_the_correction_prompt_is_targeted(self):
        for error in (leaked_markup.TOOL_CALL_ERROR, leaked_markup.WARNING_ERROR):
            with self.subTest(error=error[:44]):
                self.assertEqual(leaked_markup.CORRECTION, format_correction.prompt(error))
        self.assertNotEqual(
            leaked_markup.CORRECTION, format_correction.prompt("OpenCode final message is not a JSON report")
        )

    def test_a_leaked_markup_final_resumes_its_own_session_like_a_command_final(self):
        for error in (leaked_markup.TOOL_CALL_ERROR, leaked_markup.WARNING_ERROR):
            with self.subTest(error=error[:44]):
                self.assertEqual("repairer-session", format_correction.session_for(self.state, self.pending(error)))
        self.assertIsNone(
            format_correction.session_for(self.state, self.pending(leaked_markup.TOOL_CALL_ERROR, engine="claude"))
        )


class LeakedMarkupProviderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.events = self.root / "events.jsonl"
        self.response = self.root / "report.response.txt"

    def final_report(self, text):
        self.events.write_text("\n".join(json.dumps(row) for row in (event("text", text=text), terminal())))
        return opencode.final_report(self.events, response_path=self.response)

    def test_a_markup_final_message_is_refused_with_the_exact_message_retained(self):
        for text, error in ((TOOL_CALL, leaked_markup.TOOL_CALL_ERROR), (NOTICES, leaked_markup.WARNING_ERROR)):
            with self.subTest(text=text[:40]):
                with self.assertRaises(leaked_markup.LeakedMarkupReport) as caught:
                    self.final_report(text)
                self.assertEqual(error, str(caught.exception))
                self.assertEqual(text, self.response.read_text())

    def test_a_report_final_message_still_parses_untouched(self):
        self.assertEqual({"summary": "Plan reviewed", "concerns": []}, self.final_report(REPORT))
        self.assertEqual(REPORT, self.response.read_text())
