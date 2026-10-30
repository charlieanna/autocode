"""The built-in claude provider's stream translator (tools/providers/claude_stream.py, #717).

Claude Code's ``--output-format stream-json`` rows become Codex rows the runner already reads. The
three saved streams are trimmed live samples from Claude Code 2.1.299 on claude-haiku-4-5-20251001
(tools/fixtures/claude-2.1.299-run.jsonl, claude-2.1.299-resume.jsonl and
claude-2.1.299-length-stop.jsonl):

- run: ``claude -p --setting-sources project --model claude-haiku-4-5-20251001 --output-format stream-json
  --verbose --include-partial-messages --json-schema <schema> --permission-mode acceptEdits --allowedTools
  Bash Read``, prompt on stdin; three Bash calls exiting 7, 1 and 0, then the report. claude exited 0.
- resume: the same command with ``--resume <run's session>``; one Bash call, then the report. claude exited 0.
- length-stop: ``--tools Bash,Read,Edit,Write,Glob,Grep`` and the provider's other flags, with
  ``CLAUDE_CODE_MAX_OUTPUT_TOKENS=64``; three automatic continuations, then a length stop. claude exited 1.

Cases labelled "synthesized" are shapes not yet seen live (Claude Code's own error texts, a refusal).
"""

import json
import math
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import autocode_output_cap as output_cap
import autocode_provider_env as provider_env
import autocode_report_edits as report_edits
import autocode_support as support
import autocode_usage
from autocode_activity import ActivityMonitor
from autocode_util import file_hash
from providers import claude, claude_stream

FIXTURES = Path(__file__).resolve().parents[1] / "tools" / "fixtures"
RUN = FIXTURES / "claude-2.1.299-run.jsonl"
RESUME = FIXTURES / "claude-2.1.299-resume.jsonl"
LENGTH_STOP = FIXTURES / "claude-2.1.299-length-stop.jsonl"
SESSION = "11111111-2222-4333-8444-555555555555"
STREAM = Path(claude_stream.__file__)
CODEX_TYPES = {
    "thread.started",
    "turn.started",
    "item.started",
    "item.updated",
    "item.completed",
    "usage.partial",
    "turn.completed",
    "turn.failed",
    "error",
}


def sample(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def translate(rows, exit_code=0, clock=None, **options):
    translator = claude_stream.Translator(clock=clock or (lambda: 0.0), **options)
    out = []
    for row in rows:
        out += translator.feed(row)
    end, code = translator.finish(exit_code)
    return out + end, code


def items(rows, kind=None, event="item.completed"):
    return [row["item"] for row in rows if row["type"] == event and (kind is None or row["item"]["type"] == kind)]


def init(session=SESSION):
    return {"type": "system", "subtype": "init", "session_id": session}


def tool_use(tool_id, name, arguments):
    message = {
        "id": f"msg_{tool_id}",
        "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": arguments}],
    }
    return {"type": "assistant", "message": message}


def tool_result(tool_id, content, *, is_error=False, meta=None):
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": is_error}
    return {"type": "user", "message": {"role": "user", "content": [block]}, "tool_use_result": meta}


def result(**fields):
    row = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": SESSION,
        "usage": {
            "input_tokens": 7,
            "cache_creation_input_tokens": 1,
            "cache_read_input_tokens": 2,
            "output_tokens": 3,
        },
        "total_cost_usd": 0.25,
    }
    row.update(fields)
    return row


def api_error(kind, text):
    message = {"id": "synthetic", "model": "<synthetic>", "content": [{"type": "text", "text": text}]}
    return {"type": "assistant", "message": message, "error": kind, "is_api_error_message": True}


class TempMixin:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rows, name="events.jsonl"):
        path = self.root / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path


class LiveSampleTests(TempMixin, unittest.TestCase):
    def test_live_bash_results_keep_their_real_exit_codes(self):
        rows, code = translate(sample(RUN))
        commands = items(rows, "command_execution")
        self.assertEqual([7, 1, 0], [item["exit_code"] for item in commands])
        self.assertEqual(["seven", "", "hello"], [item["aggregated_output"] for item in commands])
        self.assertEqual(["failed", "failed", "completed"], [item["status"] for item in commands])
        self.assertTrue(all(item["id"].startswith("item_") and item["id"][5:].isdigit() for item in commands))
        self.assertEqual([], [row for row in rows if "StructuredOutput" in json.dumps(row.get("item", {}))[:80]])
        reports = [item for item in items(rows) if item["id"] == claude_stream.REPORT_ITEM]
        self.assertEqual([{"ok": True, "codes": "7, 1, 0"}], [json.loads(item["text"]) for item in reports])
        self.assertEqual(
            [{"type": "thread.started", "thread_id": sample(RUN)[2]["session_id"]}, {"type": "turn.started"}], rows[:2]
        )
        self.assertEqual(1, sum(row["type"] == "thread.started" for row in rows))
        last = rows[-1]
        self.assertEqual("turn.completed", last["type"])
        self.assertEqual(
            {"input_tokens": 60174, "cached_input_tokens": 29842, "output_tokens": 559, "reasoning_output_tokens": 238},
            last["usage"],
        )
        self.assertEqual(0.0665591, last["cost_usd"])
        self.assertEqual(sample(RUN)[2]["session_id"], last["claude"]["session_id"])
        self.assertEqual(0, code)

    def test_resumed_call_reports_only_its_own_cost(self):
        session = sample(RESUME)[2]["session_id"]
        rows, _ = translate(sample(RESUME), resumed=session, previous_cost=0.0665591)
        self.assertTrue(math.isclose(0.0120008, rows[-1]["cost_usd"], abs_tol=1e-9))
        unknown, _ = translate(sample(RESUME), resumed=session, previous_cost=None)
        self.assertNotIn("cost_usd", unknown[-1])
        self.assertIsNone(autocode_usage.reported_cost(unknown))
        # A resume Claude Code answered with another session forgets the ledger's total.
        other, _ = translate(sample(RESUME), resumed="another-session", previous_cost=0.0665591)
        self.assertNotIn("cost_usd", other[-1])

    def test_live_length_stop_is_an_authenticated_output_cap(self):
        rows, code = translate(sample(LENGTH_STOP), exit_code=1)
        self.assertEqual(0, code)
        last = rows[-1]
        self.assertEqual(("turn.failed", "output_token_limit"), (last["type"], last["error"]["code"]))
        message = last["error"]["message"]
        for words in ("output token limit", "64 output token maximum", "3 automatic continuations"):
            self.assertIn(words, message)
        self.assertFalse(message.startswith(output_cap.LENGTH_STOP))
        path = self.write(rows)
        self.assertTrue(output_cap.terminal_transport(path.read_text()))
        record = {"finished_at": "x", "exit_code": 0, "expected_session": None}
        self.assertEqual(output_cap.STATUS, support.failure_status(path, record=record))
        self.assertEqual(256, last["usage"]["output_tokens"])
        self.assertEqual("Claude Code exhausted its output token limit", support.terminal_failure_reason(path)[:44])

    def test_context_window_is_not_an_output_cap(self):
        """Synthesized: the same Claude error kind with Claude Code's context-window text."""
        rows = []
        for row in sample(LENGTH_STOP):
            text = json.dumps(row).replace(
                "Claude's response exceeded the 64 output token maximum.",
                "The model has reached its context window limit.",
            )
            rows.append(json.loads(text))
        out, code = translate(rows, exit_code=1)
        self.assertEqual("context_window_exceeded", out[-1]["error"]["code"])
        self.assertEqual(1, code)
        # A context-window stop is typed (#1942): a person names a model with a larger window.
        self.assertEqual("PAUSED_CONTEXT_WINDOW", support.failure_status(self.write(out)))

    def test_thinking_notes_earn_liveness(self):
        rows, _ = translate(sample(RUN))
        notes = [
            row
            for row in rows
            if row["type"] in ("item.updated", "item.completed") and row["item"]["type"] == "reasoning"
        ]
        self.assertTrue(
            notes and all("thinking ~" in row["item"]["text"] or "input" in row["item"]["text"] for row in notes)
        )
        self.assertTrue(
            notes[0]["item"]["text"].startswith("thinking ~50 tokens"), notes[0]
        )  # 2.1.299 streams no thinking text
        path = self.root / "events.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows[:2]))
        now = [0.0]
        monitor = ActivityMonitor(path, idle_seconds=300, tool_seconds=1800, clock=lambda: now[0])
        monitor.poll()
        now[0] = 200.0
        self.assertEqual(200.0, monitor.poll()["idle_seconds"])
        with path.open("a") as handle:
            handle.write(json.dumps(notes[0]) + "\n")
        snapshot = monitor.poll()
        self.assertEqual(("provider_active", 0), (snapshot["activity"], snapshot["idle_seconds"]))
        now[0] = 290.0
        with path.open("a") as handle:  # the same note again is not new progress
            handle.write(json.dumps(notes[0]) + "\n")
        self.assertEqual(90.0, monitor.poll()["idle_seconds"])
        with path.open("a") as handle:  # the grown note is
            handle.write(json.dumps(notes[1]) + "\n")
        self.assertEqual(0, monitor.poll()["idle_seconds"])


class FailClosedTests(TempMixin, unittest.TestCase):
    def bash(self, block_content, *, is_error=False, meta=None, command="make test"):
        rows, _ = translate(
            [
                init(),
                tool_use("t1", "Bash", {"command": command}),
                tool_result("t1", block_content, is_error=is_error, meta=meta),
                result(structured_output={"ok": True}),
            ]
        )
        return rows, items(rows, "command_execution")[0]

    def test_unknown_exits_fail_closed(self):
        """Synthesized: results Claude Code reports without an exit code it vouches for."""
        cases = {
            "background": (
                "Command running in background",
                False,
                {"stdout": "", "stderr": "", "backgroundTaskId": "b1"},
            ),
            "timeout": ("partial", False, {"stdout": "partial", "stderr": "", "timedOutAfterMs": 120000}),
            "interrupted": ("partial", False, {"stdout": "partial", "stderr": "", "interrupted": True}),
            "denied": (
                "Permission to use Bash has been denied.",
                True,
                "Error: Permission to use Bash has been denied.",
            ),
            "disagreement": ("Exit code 7\nboom", True, "Error: Exit code 3\nboom"),
            "interpretation": (
                "done",
                False,
                {"stdout": "done", "stderr": "", "returnCodeInterpretation": "Files copied"},
            ),
        }
        for name, (content, is_error, meta) in cases.items():
            with self.subTest(name):
                rows, item = self.bash(content, is_error=is_error, meta=meta)
                self.assertIsNone(item["exit_code"])
                self.assertTrue(item["exit_code_unknown"])
                self.assertEqual("failed", item["status"])
                path = self.write(rows)
                check = {"command": "make test", "exit_code": None, "evidence_ref": "event:"}
                with self.assertRaises(ValueError):
                    support.verify_checks([check], self.root, path)
                cited = {"command": "make test", "exit_code": 0, "evidence_ref": "event:" + item["id"]}
                with self.assertRaises(ValueError):
                    support.verify_checks([cited], self.root, path)
        rows, item = self.bash("Exit code 2\nlong", is_error=True, meta="Error: Exit code 2\nlong")
        self.assertEqual((2, "long", "failed"), (item["exit_code"], item["aggregated_output"], item["status"]))
        meta = {"stdout": "head", "stderr": "", "persistedOutputPath": "/tmp/out.txt"}
        rows, item = self.bash("head", meta=meta)
        self.assertEqual((0, True), (item["exit_code"], item["output_truncated"]))
        rows, item = self.bash(
            "(Bash completed with no output)",
            meta={"stdout": "", "stderr": "", "returnCodeInterpretation": "No matches found"},
        )
        self.assertEqual((1, ""), (item["exit_code"], item["aggregated_output"]))
        path = self.write(rows)
        check = {"command": "make test", "exit_code": None, "evidence_ref": "event:"}
        support.verify_checks([check], self.root, path)
        self.assertEqual(1, check["exit_code"])

    def test_large_output_keeps_its_head_and_tail(self):
        text = "a" * 70000 + "MIDDLE" + "z" * 70000
        _, item = self.bash(text, meta={"stdout": text, "stderr": ""})
        self.assertTrue(
            item["aggregated_output"].startswith("a" * 100) and item["aggregated_output"].endswith("z" * 100)
        )
        self.assertNotIn("MIDDLE", item["aggregated_output"])
        self.assertIn("characters omitted", item["aggregated_output"])

    def test_api_errors_reach_their_pause(self):
        """Synthesized table: Claude Code's API-error shapes (binary 2.1.299) reach the matching pause."""
        allowed = {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour", "overageStatus": "rejected"},
        }
        rejected = {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": 1791966600, "rateLimitType": "five_hour"},
        }
        cases = {
            "usage limit": (
                [rejected, api_error("rate_limit", "You've hit your limit · resets 5pm")],
                {"api_error_status": 429},
                "PAUSED_BUDGET",
            ),
            "usage credits": (
                [api_error("unknown", "API Error")],
                {"api_error": "model_requires_usage_credits"},
                "PAUSED_BUDGET",
            ),
            "rate limit": (
                [allowed, api_error("rate_limit", "API Error: Request rejected (429)")],
                {"api_error_status": 429},
                "PAUSED_RATE_LIMIT",
            ),
            "overloaded": (
                [api_error("overloaded", "API Error: Overloaded")],
                {"api_error_status": 529},
                "PAUSED_PROVIDER_CAPACITY",
            ),
            "authentication": (
                [allowed, api_error("authentication_failed", "Invalid API key · Please run /login")],
                {"api_error_status": 401},
                "PAUSED_AUTH",
            ),
            "server": (  # a rate_limit_event arrives on every live call: a failure row must not read as one
                [allowed, api_error("server_error", "API Error: 500")],
                {"api_error_status": 500},
                "PAUSED_PROVIDER_UNCERTAIN",
            ),
        }
        for name, (before, fields, status) in cases.items():
            with self.subTest(name):
                said = before[-1]["message"]["content"][0]["text"]
                rows, code = translate([init(), *before, result(is_error=True, result=said, **fields)], exit_code=1)
                self.assertEqual("turn.failed", rows[-1]["type"])
                self.assertEqual(1, code)
                path = self.write(rows)
                self.assertEqual(status, support.failure_status(path), rows[-1])
                # An access probe can identify rejected authentication; a
                # launched stage still requires partial-work reconciliation.
                stage_status = "PAUSED_PROVIDER_UNCERTAIN" if name == "authentication" else status
                self.assertEqual(stage_status, support.failure_status(path, record={"stage": "requirements_gather"}))
                hint = provider_env.authentication_hint(rows, ["CLAUDE_CODE_OAUTH_TOKEN"])
                self.assertEqual(name == "authentication", bool(hint))
        rows, _ = translate(sample(RUN)[:4])  # init, status, rate_limit_event with overageStatus "rejected"
        self.assertNotIn("usage_limit", json.dumps(rows))
        rows, _ = translate([init(), allowed, result(structured_output={"ok": True})])
        self.assertEqual("turn.completed", rows[-1]["type"])
        self.assertEqual("five_hour", rows[-1]["claude"]["rate_limit"]["type"])
        refusal = [
            init(),
            {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "msg_r", "usage": {}}}},
            {
                "type": "stream_event",
                "event": {"type": "message_delta", "delta": {"stop_reason": "refusal"}, "usage": {}},
            },
            result(result=""),
        ]
        rows, code = translate(refusal)
        self.assertEqual(("content_filter", 1), (rows[-1]["error"]["code"], code))
        self.assertEqual("PAUSED_CONTENT_FILTER", support.failure_status(self.write(rows)))

    def test_accepted_report_survives_a_later_api_error(self):
        """A live ladder-16 Builder (2026-09-30) submitted its report, then the API failed mid-response."""
        rows, code = translate(
            [
                init(),
                tool_use("t1", "StructuredOutput", {"summary": "done"}),
                tool_result("t1", "Structured output provided successfully", is_error=None),
                result(is_error=True, result="API Error: Server error mid-response"),
            ],
            exit_code=1,
        )
        self.assertEqual(("turn.completed", 0), (rows[-1]["type"], code))
        response = self.root / "response.txt"
        self.assertEqual({"summary": "done"}, claude.final_report(self.write(rows), response_path=response))
        self.assertEqual({"summary": "done"}, json.loads(response.read_text()))

    def test_a_rejected_report_is_never_the_report(self):
        rows, code = translate(
            [
                init(),
                tool_use("t1", "StructuredOutput", {"summary": "done"}),
                tool_result("t1", "Output does not match the schema", is_error=True),
                result(is_error=True, result="API Error: Server error mid-response"),
            ],
            exit_code=1,
        )
        self.assertEqual(("turn.failed", 1), (rows[-1]["type"], code))
        self.assertIn("Server error mid-response", rows[-1]["error"]["message"])
        with self.assertRaisesRegex(RuntimeError, "no successful terminal result"):
            claude.final_report(self.write(rows))
        retries = [
            init(),
            tool_use("t1", "StructuredOutput", {"summary": "wrong"}),
            tool_result("t1", "Output does not match the schema", is_error=True),
            result(is_error=True, subtype="error_max_structured_output_retries", result=""),
        ]
        rows, code = translate(retries, exit_code=1)
        self.assertEqual(("turn.completed", 0), (rows[-1]["type"], code))
        response = self.root / "response.txt"
        with self.assertRaises(RuntimeError) as raised:
            claude.final_report(self.write(rows), response_path=response)
        self.assertEqual(claude.FORMAT_REJECTION, str(raised.exception))
        self.assertEqual({"summary": "wrong"}, json.loads(response.read_text()))

    def test_a_retracted_report_is_never_the_report(self):
        """Synthesized from binary 2.1.299: a model fallback tombstones an attempt whose StructuredOutput was
        already accepted. stream-json never shows the tombstone; the result says no report survived."""
        accepted = [
            init(),
            tool_use("t1", "StructuredOutput", {"verdict": "PASS"}),
            tool_result("t1", "Structured output provided successfully", is_error=None),
        ]
        said = "Structured output was retracted by a model fallback and no retry produced a valid result"
        endings = {
            "retraction": {
                "subtype": "error_max_structured_output_retries",
                "terminal_reason": "structured_output_retry_exhausted",
            },
            "retry count": {"subtype": "error_max_structured_output_retries"},
        }
        for name, fields in endings.items():
            with self.subTest(name):
                rows, code = translate([*accepted, result(is_error=True, result=said, **fields)], exit_code=1)
                self.assertEqual(("turn.completed", 0), (rows[-1]["type"], code))
                self.assertEqual([], [item for item in items(rows) if item["id"] == claude_stream.REPORT_ITEM])
                with self.assertRaises(RuntimeError) as raised:  # the same-session format correction follows
                    claude.final_report(self.write(rows))
                self.assertEqual(claude.FORMAT_REJECTION, str(raised.exception))

    def test_a_lost_session_is_never_named_in_the_failure(self):
        """The runner matches a failure's row text; a session id can contain 429 (raw sidecar keeps stderr)."""
        session = "5e1f0a2c-4290-4b7e-9a1b-0c2d3e4f5a6b"
        lost = f"No conversation found with session ID: {session}\n"
        rows, code = claude_stream.Translator(resumed=session).finish(1, stderr_tail=lost)
        self.assertEqual(("session_not_found", 1), (rows[-1]["error"]["code"], code))
        self.assertNotIn("4290", json.dumps(rows))
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.write(rows)))
        rows, _ = claude_stream.Translator().finish(1, stderr_tail=f"Error: session {session} is busy")
        self.assertEqual("provider_start_failed", rows[-1]["error"]["code"])
        self.assertNotIn("4290", json.dumps(rows))

    def test_a_failed_turns_numbers_never_read_as_a_rate_limit(self):
        """A failed row keeps its usage and cost for accounting; a 429 among their digits is no rate limit."""
        heavy = {
            "input_tokens": 12,
            "cache_creation_input_tokens": 3000,
            "cache_read_input_tokens": 54290,
            "output_tokens": 900,
        }
        cases = {
            "provider error": ("server_error", {"api_error_status": 500, "total_cost_usd": 0.04291}),
            "authentication": ("authentication_failed", {"api_error_status": 401, "usage": heavy}),
            "context window": (None, {"terminal_reason": "prompt_too_long", "total_cost_usd": 0.4291}),
            "context window text": (
                None,
                {"terminal_reason": "prompt_too_long", "total_cost_usd": 0.4291},
                "Prompt is too long \u00b7 the request is ~214290 tokens (limit 200000)",
            ),
        }
        for name, (kind, fields, *said) in cases.items():
            with self.subTest(name):
                text = said[0] if said else "API Error"
                before = [api_error(kind, text)] if kind else []
                rows, _ = translate([init(), *before, result(is_error=True, result=text, **fields)], 1)
                last = rows[-1]
                self.assertEqual("turn.failed", last["type"])
                self.assertIn("429", json.dumps([last["usage"], last["cost_usd"]]))
                path = self.write(rows)
                status = {"authentication": "PAUSED_AUTH"}.get(
                    name, "PAUSED_CONTEXT_WINDOW" if name.startswith("context window") else "PAUSED_PROVIDER_UNCERTAIN"
                )
                self.assertEqual(status, support.failure_status(path), last)
                self.assertEqual(
                    "PAUSED_CONTEXT_WINDOW" if name.startswith("context window") else "PAUSED_PROVIDER_UNCERTAIN",
                    support.failure_status(path, record={"stage": "requirements_gather"}),
                    last,
                )
                self.assertEqual(last["cost_usd"], autocode_usage.reported_cost(rows))  # still counted

    def test_an_api_key_source_stops_the_stage(self):
        """An init row naming an API key source (synthesized: live subscription calls say "none")."""
        for source in ("ANTHROPIC_API_KEY", "apiKeyHelper", "/login managed key"):
            with self.subTest(source):
                row = {**init(), "apiKeySource": source}
                rows, code = translate([row, result(structured_output={"ok": True})])
                self.assertEqual(("billing_route", 1), (rows[-1]["error"]["code"], code))
                self.assertEqual([], [item for item in items(rows) if item["id"] == claude_stream.REPORT_ITEM])
                self.assertEqual("PAUSED_BILLING_ROUTE", support.failure_status(self.write(rows)))
        rows, code = translate([{**init(), "apiKeySource": "none"}, result(structured_output={"ok": True})])
        self.assertEqual(("turn.completed", 0), (rows[-1]["type"], code))

    def test_the_models_prose_is_never_the_provider_error(self):
        said = "I added the content filter quota check and a 429 retry but wrote no report."
        rows, code = translate([init(), result(result=said)])
        self.assertEqual(("turn.completed", 0), (rows[-1]["type"], code))
        self.assertEqual([], [row for row in rows if row["type"] in ("turn.failed", "error")])
        self.assertEqual(
            said, [item["text"] for item in items(rows) if item["id"] == claude_stream.RESULT_TEXT_ITEM][0]
        )
        response = self.root / "response.txt"
        with self.assertRaises(RuntimeError) as raised:
            claude.final_report(self.write(rows), response_path=response)
        self.assertEqual(claude.FORMAT_REJECTION, str(raised.exception))
        self.assertEqual(said, response.read_text())

    def test_a_cut_stream_is_incomplete_never_zero(self):
        rows = sample(RUN)
        last_bash = max(
            index for index, row in enumerate(rows) if "tool_use_result" in row and "hello" in json.dumps(row)
        )
        out, code = translate(rows[: last_bash + 1], exit_code=1)
        self.assertEqual(1, code)
        self.assertEqual(("turn.failed", "incomplete_turn"), (out[-1]["type"], out[-1]["error"]["code"]))
        partial = [row for row in out if row["type"] == "usage.partial"]
        self.assertEqual(1, len(partial))
        # The first message's own usage (message_start, then its latest message_delta); known zeros only.
        expected = {
            "input_tokens": 29852,
            "cached_input_tokens": 0,
            "output_tokens": 337,
            "reasoning_output_tokens": 95,
        }
        self.assertEqual(expected, partial[0]["usage"])
        self.assertNotIn("usage", out[-1])
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.write(out)))
        metrics = support.event_metrics(self.root / "events.jsonl")
        self.assertTrue(metrics["provider_tokens_partial"])

    def test_exit_policy(self):
        success = [init(), result(structured_output={"ok": True})]
        self.assertEqual(9, translate(success, exit_code=9)[1])
        retries = [init(), result(is_error=True, subtype="error_max_structured_output_retries", result="")]
        self.assertEqual(0, translate(retries, exit_code=1)[1])
        translator = claude_stream.Translator()
        self.assertEqual(
            "provider_start_failed", translator.start_failure("/x/claude", FileNotFoundError("gone"))["error"]["code"]
        )
        mixed = [init(), init("other-session"), result(structured_output={"ok": True})]
        rows, code = translate(mixed)
        self.assertEqual(("session_changed", 1), (rows[-1]["error"]["code"], code))

    def test_only_codex_rows_reach_stdout(self):
        streams = [sample(RUN), sample(RESUME), sample(LENGTH_STOP), sample(RUN)[:40]]
        streams += [
            [init(), result(result="prose")],
            [
                init(),
                api_error("authentication_failed", "bad"),
                result(is_error=True, result="bad", api_error_status=401),
            ],
            [],
        ]
        for kind, status in (("rate_limit", 429), ("overloaded", 529), ("server_error", 500)):
            said = f"API Error: {status}"
            streams.append([init(), api_error(kind, said), result(is_error=True, result=said, api_error_status=status)])
        for index, stream in enumerate(streams):
            with self.subTest(index):
                rows, _ = translate(stream, exit_code=1)
                self.assertTrue(all(row["type"] in CODEX_TYPES for row in rows))
                self.assertFalse(any("sessionID" in json.dumps(row) for row in rows))
                terminals = [
                    position for position, row in enumerate(rows) if row["type"] in ("turn.completed", "turn.failed")
                ]
                self.assertEqual([len(rows) - 1], terminals)
                # An error row appears only right before an authentication failure (SPEC #717 3.3).
                errors = [position for position, row in enumerate(rows) if row["type"] == "error"]
                last = rows[-1]
                authentication = last["type"] == "turn.failed" and last["error"]["code"] == "authentication_failed"
                self.assertEqual([len(rows) - 2] if authentication else [], errors, rows)


class TextItemTests(unittest.TestCase):
    def stream(self, message, index, kind, **delta):
        if kind == "start":
            return {
                "type": "stream_event",
                "event": {"type": "content_block_start", "index": index, "content_block": delta},
            }
        if kind == "stop":
            return {"type": "stream_event", "event": {"type": "content_block_stop", "index": index}}
        return {"type": "stream_event", "event": {"type": "content_block_delta", "index": index, "delta": delta}}

    def message(self, identifier, *texts):
        rows = [
            {"type": "stream_event", "event": {"type": "message_start", "message": {"id": identifier, "usage": {}}}},
            self.stream(identifier, 0, "start", type="text", text=""),
        ]
        rows += [self.stream(identifier, 0, "delta", type="text_delta", text=text) for text in texts]
        return rows + [self.stream(identifier, 0, "stop"), {"type": "stream_event", "event": {"type": "message_stop"}}]

    def test_text_items_are_append_only_bounded_and_flushed(self):
        rows, _ = translate([init(), *self.message("msg_a", "Hello", " world"), *self.message("msg_b", "Second")])
        texts = items(rows, "agent_message")
        self.assertEqual(["Hello world", "Second"], [item["text"] for item in texts])
        self.assertEqual(2, len({item["id"] for item in texts}))
        self.assertEqual(["Hello"], [item["text"] for item in items(rows, "agent_message", "item.updated")][:1])
        big = "x" * 40000
        rows, _ = translate([init(), *self.message("msg_c", big)])
        chunks = items(rows, "agent_message")
        self.assertEqual(3, len(chunks))
        self.assertTrue(all(len(item["text"].encode()) <= claude_stream.ITEM_TEXT_LIMIT for item in chunks))
        self.assertEqual(big, "".join(item["text"] for item in chunks))
        self.assertTrue(
            all(
                len(row["item"]["text"].encode()) <= claude_stream.ITEM_TEXT_LIMIT
                for row in rows
                if "text" in row.get("item", {})
            )
        )
        notes = [
            init(),
            {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "msg_t", "usage": {}}}},
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 50},
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 50},
            {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 120},
            {"type": "stream_event", "event": {"type": "message_stop"}},
        ]
        rows, _ = translate(notes)
        self.assertEqual(
            ["thinking ~50 tokens; thinking ~120 tokens; "], [item["text"] for item in items(rows, "reasoning")]
        )
        now = [0.0]

        def clock():
            now[0] += 0.5
            return now[0]

        pieces = [f"part {index} " for index in range(20)]
        rows, _ = translate([init(), *self.message("msg_d", *pieces)], clock=clock)
        updates = items(rows, "agent_message", "item.updated")
        self.assertLessEqual(len(updates), 1 + (20 * 0.5) // claude_stream.THROTTLE_SECONDS)
        self.assertTrue(all(b["text"].startswith(a["text"]) for a, b in zip(updates, updates[1:], strict=False)))
        self.assertEqual("".join(pieces), items(rows, "agent_message")[-1]["text"])


class ProcessTests(TempMixin, unittest.TestCase):
    def fake(self, body):
        path = self.root / "claude"
        path.write_text(f"#!{sys.executable}\nimport json, os, sys\n" + textwrap.dedent(body))
        path.chmod(0o755)
        return path

    def run_stream(self, claude_path, *options, schema=None, env=None, ledger="ledger"):
        schema_path = self.root / "schema.json"
        schema_path.write_text(json.dumps(schema if schema is not None else {"type": "object"}))
        argv = [sys.executable, "-I", str(STREAM), "--ledger", str(self.root / ledger), "--schema", str(schema_path)]
        argv += [*options, "--", str(claude_path), "-p", "--model", "m"]
        environment = {**os.environ, **(env or {})}
        return subprocess.run(argv, input="prompt", capture_output=True, text=True, timeout=60, env=environment)

    def test_main_startup_failures_are_one_terminal_row(self):
        fake = self.fake(
            """
            sys.stderr.write(os.environ.get("FAKE_ERROR", "Error: --json-schema must be a JSON object") + "\\n")
            sys.exit(1)
            """
        )
        done = self.run_stream(fake)
        rows = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual(1, len(rows), done.stdout)
        self.assertEqual(("turn.failed", "provider_start_failed"), (rows[0]["type"], rows[0]["error"]["code"]))
        self.assertIn("Error: --json-schema must be a JSON object", rows[0]["error"]["message"])
        self.assertEqual((1, ""), (done.returncode, done.stderr))
        missing = self.run_stream(self.root / "no-such-claude")
        self.assertEqual(127, missing.returncode)
        self.assertEqual("provider_start_failed", json.loads(missing.stdout)["error"]["code"])
        lost = self.run_stream(fake, "--resumed", "s1", env={"FAKE_ERROR": "No conversation found with session ID: s1"})
        self.assertEqual("session_not_found", json.loads(lost.stdout)["error"]["code"])
        marker = self.root / "spawned"
        spawned = self.fake(f"open({str(marker)!r}, 'w').close()\n")
        huge = self.run_stream(spawned, schema={"description": "x" * 130000})
        self.assertEqual((2, ""), (huge.returncode, huge.stderr))
        self.assertIn("larger than one argv string", json.loads(huge.stdout)["error"]["message"])
        self.assertFalse(marker.exists())
        (self.root / "not-a-directory").write_text("a file")
        bad = self.run_stream(spawned, ledger="not-a-directory")
        self.assertEqual((70, ""), (bad.returncode, bad.stderr))
        self.assertEqual(["adapter_error"], [json.loads(line)["error"]["code"] for line in bad.stdout.splitlines()])
        self.assertNotIn("Traceback", bad.stdout)
        self.assertFalse(marker.exists())

    def test_main_stops_claude_that_would_bill_an_api_key(self):
        fake = self.fake(
            """
            import signal
            signal.alarm(20)  # only if the translator never stops it
            print(json.dumps({"type": "system", "subtype": "init", "session_id": "s-key",
                              "apiKeySource": "ANTHROPIC_API_KEY"}), flush=True)
            signal.pause()
            """
        )
        done = self.run_stream(fake, "--raw", str(self.root / "raw.claude.jsonl"))
        rows = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual(("billing_route", 1, ""), (rows[-1]["error"]["code"], done.returncode, done.stderr))
        raw = [json.loads(line) for line in (self.root / "raw.claude.jsonl").read_text().splitlines()]
        self.assertEqual({"type": "exit", "code": 143}, raw[-1])  # SIGTERM from the translator, not the alarm

    def test_main_inserts_json_schema_after_print_and_writes_the_ledger(self):
        argv_log = self.root / "argv.json"
        fake = self.fake(
            f"""
            json.dump(sys.argv, open({str(argv_log)!r}, "w"))
            print(json.dumps({{"type": "system", "subtype": "init", "session_id": "s-ledger"}}))
            print("not json")
            print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "session_id": "s-ledger",
                               "structured_output": {{"ok": True}}, "total_cost_usd": 0.5,
                               "usage": {{"input_tokens": 1, "cache_creation_input_tokens": 0,
                                          "cache_read_input_tokens": 0, "output_tokens": 1}}}}))
            """
        )
        done = self.run_stream(fake, "--raw", str(self.root / "raw.claude.jsonl"))
        self.assertEqual((0, ""), (done.returncode, done.stderr))
        argv = json.loads(argv_log.read_text())
        self.assertEqual(["-p", "--json-schema", '{"type":"object"}', "--model", "m"], argv[1:])
        ledger = json.loads((self.root / "ledger" / "s-ledger.json").read_text())
        self.assertEqual(("s-ledger", 0.5), (ledger["session_id"], ledger["total_cost_usd"]))
        rows = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual(0.5, rows[-1]["cost_usd"])
        raw = [json.loads(line) for line in (self.root / "raw.claude.jsonl").read_text().splitlines()]
        self.assertEqual(["system", "result", "stderr", "exit"], [row["type"] for row in raw])
        resumed = self.run_stream(fake, "--resumed", "s-ledger")
        self.assertNotIn("cost_usd", json.loads(resumed.stdout.splitlines()[-1]))  # same total: nothing new spent

    def test_a_report_edit_offer_reaches_claude_code_without_autocodes_annotation(self):
        # Live, Claude Code 2.1.314 refused the Requirements report repair before any request:
        # "Error: --json-schema is not a valid JSON Schema: strict mode: unknown keyword:
        # "x-autocode-report-edits"". This fake refuses an unknown top-level keyword the same way.
        argv_log = self.root / "argv.json"
        fake = self.fake(
            f"""
            schema = json.loads(sys.argv[sys.argv.index("--json-schema") + 1])
            json.dump(schema, open({str(argv_log)!r}, "w"))
            unknown = sorted(set(schema) - {{"type", "properties", "required", "additionalProperties"}})
            if unknown:
                sys.stderr.write(f'Error: --json-schema is not a valid JSON Schema: strict mode: unknown keyword: "{{unknown[0]}}"\\n')
                sys.exit(1)
            print(json.dumps({{"type": "system", "subtype": "init", "session_id": "s-edits"}}))
            print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "session_id": "s-edits",
                               "structured_output": {{"summary": "fixed"}}, "total_cost_usd": 0.01}}))
            """
        )
        source = self.root / "requirements-gather-01.json"
        source.write_text(json.dumps({"summary": "first"}))
        original = self.root / "requirements.schema.json"
        named = {"summary": {"type": "string"}, "x-note": {"type": "string"}}  # a field named x-…, kept
        original.write_text(json.dumps({"type": "object", "properties": named, "required": ["summary"]}))
        descriptor = {
            "path": str(source),
            "sha256": file_hash(source),
            "format": "json",
            "truncated": False,
            "content": {"summary": "first"},
        }
        offered = json.loads(report_edits.prepare(original, descriptor, "requirements_gather").read_text())
        self.assertIn(report_edits.META, offered)  # the runner reads it back from the file
        done = self.run_stream(fake, schema=offered)
        self.assertEqual((0, ""), (done.returncode, done.stderr), done.stdout)
        sent = json.loads(argv_log.read_text())
        self.assertEqual({key: value for key, value in offered.items() if key != report_edits.META}, sent)
        self.assertEqual({"summary", "x-note", report_edits.KEY}, set(sent["properties"]))
        self.assertEqual("turn.completed", json.loads(done.stdout.splitlines()[-1])["type"])


if __name__ == "__main__":
    unittest.main()
