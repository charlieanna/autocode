"""Native diagnostics count observations, never private tool payloads or authority."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_request_usage as request_usage
import autocode_run_view as run_view
import autocode_tool_activity as activity
import autocode_usage as usage


def boundary(kind="step_start", reason="stop"):
    return {"type": kind, "sessionID": "s", "part": {"id": kind, "reason": reason}}


def tool(identity="one", name="bash", given=None, status="completed", **extra):
    return {
        "type": "tool_use",
        "sessionID": "s",
        "part": {
            "id": identity,
            "tool": name,
            "state": {"status": status, "input": {"command": "test"} if given is None else given, **extra},
        },
    }


def stream(*calls):
    return [boundary(), *calls, boundary("step_finish")]


class ToolActivityTests(unittest.TestCase):
    def test_distinct_calls_compare_exact_structured_arguments_not_lifecycle_updates(self):
        rows = stream(
            tool(status="running"),
            tool(),
            tool(),
            tool("two", given={"b": 2, "a": 1}),
            tool("three", given={"a": 1, "b": 2}),
            tool("four", given={"command": "different"}),
            tool("five", name="read"),
        )
        result = activity.summarize(rows)
        self.assertEqual(5, result["observed_calls"])
        self.assertEqual(1, result["exact_argument_repeats"])
        self.assertEqual(5, result["comparable_calls"])
        self.assertEqual({"succeeded": 5, "failed": 0, "unknown": 0}, result["outcomes"])
        self.assertEqual({"command": 4, "read": 1, "search": 0, "edit": 0, "agent": 0, "other": 0}, result["families"])
        self.assertTrue(result["complete"], result)

    def test_only_structured_outcomes_classify_errors_and_exact_tool_names_classify_families(self):
        result = activity.summarize(
            stream(
                tool("one", metadata={"exit": 3}, output="not an error"),
                tool("two", name="read", status="error", error="SECRET error reason"),
                tool("three", name="apply_patch"),
                tool("four", name="grep"),
                tool("five", name="task"),
                tool("six", name="SECRET unknown tool", output="ERROR exception fail"),
            )
        )
        self.assertEqual({"succeeded": 4, "failed": 2, "unknown": 0}, result["outcomes"])
        self.assertEqual({"nonzero_exit": 1, "tool_error": 1}, result["error_classes"])
        self.assertEqual(dict.fromkeys(activity.FAMILIES, 1), result["families"])
        self.assertTrue(result["complete"])
        self.assertNotIn("SECRET", json.dumps(result))

    def test_identity_namespace_and_missing_identity_never_invent_correlations(self):
        other = tool()
        other["sessionID"] = "other"
        missing = tool(None)
        result = activity.summarize(stream(tool(), other, missing, missing))
        self.assertEqual(2, result["observed_calls"])
        self.assertEqual(1, result["exact_argument_repeats"])
        self.assertFalse(result["complete"])
        self.assertIn("missing_call_identity", result["issues"])
        foreign = tool()
        foreign["part"]["sessionID"] = "foreign"
        self.assertEqual(0, activity.summarize(stream(foreign))["observed_calls"])

    def test_missing_partial_and_unsupported_streams_are_never_zero_complete(self):
        for rows in (
            [],
            [{"type": "future_provider"}],
            [boundary("step_finish")],
            [boundary()],
            stream()[:-1],
            stream(tool())[:-1],
        ):
            with self.subTest(rows=rows):
                self.assertFalse(activity.summarize(rows)["complete"])
        self.assertTrue(activity.summarize(stream())["complete"])
        self.assertFalse(activity.summarize(stream(tool(status="running")))["complete"])
        self.assertFalse(activity.summarize(stream(tool()), incomplete=True)["complete"])
        unknown = {"type": "item.completed", "item": {"type": "new_tool_kind", "id": "one"}}
        self.assertIn("unsupported_stream", activity.summarize(stream(unknown))["issues"])

    def test_replayed_or_foreign_boundaries_and_nonterminal_exit_do_not_complete(self):
        foreign = tool()
        foreign["sessionID"] = "foreign"
        second_start = boundary()
        second_start["part"]["id"] = "second"
        missing_session = boundary()
        missing_session.pop("sessionID")
        for rows in (
            stream(foreign),
            [*stream(), second_start, boundary("step_finish")],
            [missing_session, boundary("step_finish")],
            stream(tool(status="running", metadata={"exit": 7})),
            [{"type": "thread.started", "thread_id": "t"}, {"type": "turn.completed"}, {"type": "turn.started"}],
            [*stream(), {"type": "error"}],
        ):
            with self.subTest(rows=rows):
                self.assertFalse(activity.summarize(rows)["complete"])
        result = activity.summarize(stream(tool(), tool(), tool(status="running")))
        self.assertEqual(1, result["outcomes"]["succeeded"])
        self.assertTrue(result["complete"], "Old running lifecycle updates cannot reopen a completed call")

    def test_conflicting_lifecycle_arguments_or_outcomes_are_incomplete(self):
        for changed in (tool(given={"command": "different"}), tool(status="error"), tool(name="read")):
            with self.subTest(changed=changed):
                result = activity.summarize(stream(tool(), changed))
                self.assertEqual(1, result["observed_calls"])
                self.assertFalse(result["complete"])
                self.assertIn("conflicting_call", result["issues"])
                self.assertEqual(1, result["outcomes"]["unknown"])

    def test_codex_items_coalesce_but_rendered_commands_do_not_claim_full_arguments(self):
        started = {"type": "thread.started", "thread_id": "thread"}
        command = {
            "type": "item.completed",
            "item": {"id": "one", "type": "command_execution", "command": "SECRET", "exit_code": 0},
        }
        function = {
            "type": "item.completed",
            "item": {"id": "two", "type": "function_call", "name": "SECRET", "arguments": {"a": 1}, "status": "failed"},
        }
        result = activity.summarize([started, command, command, function, {"type": "turn.completed"}])
        self.assertEqual(2, result["observed_calls"])
        self.assertEqual({"succeeded": 1, "failed": 1, "unknown": 0}, result["outcomes"])
        self.assertEqual(1, result["comparable_calls"])
        self.assertIn("arguments_unavailable", result["issues"])
        self.assertFalse(result["complete"])
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertEqual(0, activity.summarize([command])["observed_calls"])

    def test_bounds_and_invalid_arguments_stay_incomplete(self):
        with mock.patch.object(activity, "MAX_CALLS", 1):
            limited = activity.summarize(stream(tool(), tool("two")))
        self.assertEqual(1, limited["observed_calls"])
        self.assertIn("observation_limit", limited["issues"])
        for given in ({"huge": "x" * (activity.MAX_ARGUMENT_BYTES + 1)}, {"bad": float("nan")}, "not-structured"):
            result = activity.summarize(stream(tool(given=given)))
            self.assertEqual(0, result["comparable_calls"])
            self.assertIn("arguments_unavailable", result["issues"])
            self.assertFalse(result["complete"])
        with mock.patch.object(activity, "MAX_ARGUMENT_TOTAL", 50):
            result = activity.summarize(stream(tool(given={"long": "x" * 30}), tool("two", given={"long": "y" * 30})))
        self.assertIn("observation_limit", result["issues"])
        with mock.patch.object(activity, "MAX_EVENTS", 2):
            self.assertIn("observation_limit", activity.summarize(stream(tool()))["issues"])
        cyclic = {}
        cyclic["self"] = cyclic
        self.assertIn("observation_limit", activity.summarize(stream(tool(given=cyclic)))["issues"])

    def test_saved_projection_is_allowlisted_and_active_or_conflicting_snapshots_are_incomplete(self):
        good = activity.summarize(stream(tool()))
        poisoned = {**good, "arguments": "SECRET", "scope": "SECRET", "families": {**good["families"], "SECRET": 77}}
        self.assertNotIn("SECRET", json.dumps(activity.projection(poisoned)))
        for field, value in (
            ("issues", ["SECRET"]),
            ("observed_calls", -1),
            ("families", {}),
            ("basis", "SECRET"),
            ("comparable_calls", 99),
        ):
            result = activity.projection({**good, field: value})
            self.assertEqual("unavailable", result["basis"])
        self.assertFalse(activity.projection(good, active=True)["complete"])
        incomparable = {**good, "comparable_calls": 0}
        self.assertFalse(activity.projection(incomparable)["complete"])
        unknown = {**good, "outcomes": {"succeeded": 0, "failed": 0, "unknown": 1}}
        self.assertFalse(activity.projection(unknown)["complete"])
        row = {"identity": "attempt", "identity_complete": True, "tool_activity": good}
        changed = copy.deepcopy(row)
        changed["tool_activity"] = activity.summarize(stream(tool(), tool("two")))
        result = usage.combine_accounting([row, changed])["attempts"][0]["tool_activity"]
        self.assertEqual(["conflicting_snapshots"], result["issues"])
        self.assertFalse(result["complete"])

    def test_public_status_refreshes_legacy_saved_receipts_and_exposes_only_counts(self):
        # The public status used for diagnosis previously had no tool-activity evidence.
        with tempfile.TemporaryDirectory() as root:
            events = Path(root) / "native.jsonl"
            canary = "SECRET-private-command-path-argument-output"
            rows = stream(
                tool(given={"command": canary}, output=canary),
                tool("two", given={"command": canary}, metadata={"exit": 7}),
            )
            events.write_text("".join(json.dumps(row) + "\n" for row in rows))
            legacy = request_usage.read(events)
            legacy["accounting"].pop("tool_activity")
            record = {
                "stage": "terra",
                "engine": "opencode",
                "events": str(events),
                "metrics": {"request_context": legacy},
            }
            state = {"status": "RUNNING", "stages": [record]}
            result = run_view.view(state)["usage"]["accounting"]["attempts"][0]["tool_activity"]
            self.assertEqual(2, result["observed_calls"])
            self.assertEqual(1, result["exact_argument_repeats"])
            self.assertEqual(1, result["error_classes"]["nonzero_exit"])
            self.assertTrue(result["complete"], result)
            self.assertNotIn(canary, json.dumps(result))
            self.assertNotIn("tool_activity", legacy["accounting"], "Status projection must not mutate saved receipts")
            events.write_text(events.read_text() + '{"truncated":')
            partial = run_view.view(state)["usage"]["accounting"]["attempts"][0]["tool_activity"]
            self.assertFalse(partial["complete"])
            self.assertIn("event_stream_incomplete", partial["issues"])
            events.unlink()
            missing = run_view.view(state)["usage"]["accounting"]["attempts"][0]["tool_activity"]
            self.assertEqual("unavailable", missing["basis"])
            self.assertFalse(missing["complete"])
