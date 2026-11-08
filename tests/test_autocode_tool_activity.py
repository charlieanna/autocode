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
from providers import claude_stream


def boundary(kind="step_start", reason="stop"):
    return {"type": kind, "sessionID": "s", "part": {"id": kind, "reason": reason}}


def tool(identity="one", name="bash", given=None, status="completed", **extra):
    if name in ("bash", "shell") and "metadata" not in extra:
        extra["metadata"] = {"exit": 0}
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
    def test_public_status_refreshes_stale_diagnostic_version_with_unchanged_events(self):
        with tempfile.TemporaryDirectory() as root:
            events = Path(root) / "native.jsonl"
            events.write_text(
                "".join(json.dumps(row) + "\n" for row in stream(tool(metadata={"metadata": {"exit": 7}})))
            )
            measured = request_usage.read(events)
            record = {"stage": "terra", "events": str(events), "metrics": {"request_context": measured}}
            state = {"status": "RUNNING", "stages": [record]}
            original = copy.deepcopy(state)
            original_stat = events.stat()
            # Model a saved projection from the parser that missed nested exit7.
            stale = measured["accounting"]["tool_activity"]
            stale.update(
                outcomes={"succeeded": 1, "failed": 0, "unknown": 0}, error_classes={"nonzero_exit": 0, "tool_error": 0}
            )
            for version in (None, 0, 1, activity.VERSION + 1, "2", True):
                with self.subTest(version=version):
                    if version is None:
                        stale.pop("version", None)
                    else:
                        stale["version"] = version
                    before = copy.deepcopy(state)
                    result = run_view.view(state)["usage"]["accounting"]["attempts"][0]["tool_activity"]
                    self.assertEqual(1, result["error_classes"]["nonzero_exit"])
                    self.assertEqual(1, result["outcomes"]["failed"])
                    self.assertTrue(result["complete"])
                    self.assertEqual(before, state, "Read-only refresh must not rewrite saved diagnostics")
            self.assertEqual(
                (original_stat.st_size, original_stat.st_mtime_ns), (events.stat().st_size, events.stat().st_mtime_ns)
            )
            # A current, unchanged projection retains the existing receipt cache.
            with mock.patch.object(request_usage, "read", side_effect=AssertionError("current cache must be reused")):
                current = usage.accounting(original)["attempts"][0]["tool_activity"]
            self.assertEqual(1, current["outcomes"]["failed"])
            events.unlink()
            unavailable = run_view.view(state)["usage"]["accounting"]["attempts"][0]["tool_activity"]
            self.assertEqual("unavailable", unavailable["basis"])
            self.assertFalse(unavailable["complete"])
            record.pop("events")
            unavailable = run_view.view(state)["usage"]["accounting"]["attempts"][0]["tool_activity"]
            self.assertEqual("unavailable", unavailable["basis"])
            self.assertFalse(unavailable["complete"])

    def test_legacy_counts_survive_missing_native_evidence_with_unknown_search_classification(self):
        with tempfile.TemporaryDirectory() as root:
            events = Path(root) / "native.jsonl"
            events.write_text(
                "".join(json.dumps(row) + "\n" for row in stream(tool(name="grep", given={"pattern": "AGENTS.md"})))
            )
            measured = request_usage.read(events)
            legacy = measured["accounting"]["tool_activity"]
            legacy["version"] = 1
            legacy.pop("search_arguments")
            legacy["private"] = "SECRET-canary"
            events.unlink()
            record = {"stage": "terra", "events": str(events), "metrics": {"request_context": measured}}
            state = {"status": "RUNNING", "stages": [record]}
            before = copy.deepcopy(state)
            result = run_view.view(state)["usage"]["accounting"]["attempts"][0]["tool_activity"]
            for key in (
                "basis",
                "observed_calls",
                "families",
                "outcomes",
                "error_classes",
                "exact_argument_repeats",
                "comparable_calls",
                "complete",
                "issues",
                "scope",
            ):
                self.assertEqual(legacy[key], result[key], key)
            self.assertEqual(activity.VERSION, result["version"])
            self.assertEqual(
                {"with_instruction_name": 0, "without_instruction_name": 0, "unknown": 1, "complete": False},
                result["search_arguments"],
            )
            self.assertEqual(before, state, "Migration is a read-only public projection")
            self.assertNotIn("SECRET", json.dumps(result))
            # A saved legacy projection with no declared source also retains its counts.
            record.pop("events")
            self.assertEqual(result, run_view.view(state)["usage"]["accounting"]["attempts"][0]["tool_activity"])
            # Unsupported versions or invalid legacy counts cannot be migrated.
            for field, value in (("version", 0), ("version", True), ("comparable_calls", -1)):
                invalid = {**legacy, field: value}
                self.assertEqual("unavailable", activity.projection(invalid)["basis"])

    def test_public_status_refreshes_legacy_search_classification_when_native_evidence_is_available(self):
        with tempfile.TemporaryDirectory() as root:
            events = Path(root) / "native.jsonl"
            events.write_text(
                "".join(json.dumps(row) + "\n" for row in stream(tool(name="glob", given={"pattern": "**/AGENTS.md"})))
            )
            measured = request_usage.read(events)
            measured["accounting"]["tool_activity"]["version"] = 1
            measured["accounting"]["tool_activity"].pop("search_arguments")
            state = {
                "status": "RUNNING",
                "stages": [{"stage": "terra", "events": str(events), "metrics": {"request_context": measured}}],
            }
            before = copy.deepcopy(state)
            result = run_view.view(state)["usage"]["accounting"]["attempts"][0]["tool_activity"]
            self.assertEqual(
                {"with_instruction_name": 1, "without_instruction_name": 0, "unknown": 0, "complete": True},
                result["search_arguments"],
            )
            self.assertEqual(before, state)

    def test_search_arguments_count_literal_instruction_name_values_without_exporting_payloads(self):
        canary = "SECRET-query-path-output-canary"
        rows = stream(
            tool("glob", "glob", {"pattern": "**/AGENTS.md", "path": canary}),
            tool("grep", "grep", {"pattern": "find CLAUDE.md and AGENTS.md", "path": canary}),
            tool("nested", "search", {"terms": [{"query": [None, 7, True, "'CLAUDE.md'"]}]}),
            tool("code", "grep", {"pattern": "def select_tier", "include": "*.py"}, output="AGENTS.md"),
            tool("keys", "search", {"AGENTS.md": "ordinary code query", "private": canary}),
            tool("web", "websearch", {"query": "CLAUDE.md mentioned in unrelated prose"}),
            tool("read", "read", {"file_path": "AGENTS.md"}),
            tool("shell", "bash", {"command": "rg --files -g AGENTS.md"}),
        )
        result = activity.summarize(rows)
        self.assertEqual(
            {"with_instruction_name": 4, "without_instruction_name": 2, "unknown": 0, "complete": True},
            result["search_arguments"],
        )
        self.assertEqual(6, result["families"]["search"])
        self.assertEqual(8, result["observed_calls"])
        self.assertEqual(8, result["comparable_calls"])
        self.assertEqual(0, result["exact_argument_repeats"])
        for private in (canary, "AGENTS.md", "CLAUDE.md", "select_tier", "query", "file_path"):
            self.assertNotIn(private, json.dumps(result))

    def test_instruction_name_tokens_preserve_literal_case_boundaries_and_escaping(self):
        for pattern in (
            "AGENTS.md",
            "**/CLAUDE.md",
            "/tmp/AGENTS.md",
            "(AGENTS.md|CLAUDE.md)",
            "AGENTS.md?raw=true",
            "\u0041GENTS.md",
            r"C:\repo\AGENTS.md",
        ):
            with self.subTest(pattern=pattern):
                result = activity.summarize(stream(tool(name="glob", given={"pattern": pattern})))
                self.assertEqual(1, result["search_arguments"]["with_instruction_name"])
        for pattern in (
            "MYAGENTS.md",
            "AGENTS.md.bak",
            "CLAUDE.mdpy",
            "AGENTS-md",
            "agents.md",
            "CLAUDE.MD",
            "x-AGENTS.md",
            "éAGENTS.md",
            r"AGENTS\.md",
            r"\u0041GENTS.md",
            "AGENTS[.]md",
        ):
            with self.subTest(pattern=pattern):
                result = activity.summarize(stream(tool(name="grep", given={"pattern": pattern})))
                self.assertEqual(
                    {"with_instruction_name": 0, "without_instruction_name": 1, "unknown": 0, "complete": True},
                    result["search_arguments"],
                )
        decoded = json.loads(r'{"pattern":"\u0041GENTS.md"}')
        self.assertEqual(
            1, activity.summarize(stream(tool(name="glob", given=decoded)))["search_arguments"]["with_instruction_name"]
        )

    def test_search_argument_unknowns_reuse_existing_bounds_and_never_parse_other_shapes(self):
        missing = tool("missing", "grep")
        missing["part"]["state"].pop("input")
        for given in (
            "AGENTS.md",
            ["AGENTS.md"],
            {"pattern": float("nan")},
            {"pattern": "AGENTS.md" + "x" * activity.MAX_ARGUMENT_BYTES},
        ):
            with self.subTest(given_type=type(given)):
                with mock.patch.object(
                    activity, "_mentions_instruction_name", side_effect=AssertionError("unbounded scan")
                ):
                    result = activity.summarize(stream(tool(name="grep", given=given)))
                self.assertEqual(
                    {"with_instruction_name": 0, "without_instruction_name": 0, "unknown": 1, "complete": False},
                    result["search_arguments"],
                )
        with (
            mock.patch.object(activity, "MAX_ARGUMENT_TOTAL", 0),
            mock.patch.object(activity, "_mentions_instruction_name", side_effect=AssertionError("over-total scan")),
        ):
            self.assertEqual(1, activity.summarize(stream(tool(name="grep")))["search_arguments"]["unknown"])
        self.assertEqual(1, activity.summarize(stream(missing))["search_arguments"]["unknown"])
        # A rendered Codex query is not the complete structured argument set.
        codex = [
            {"type": "thread.started", "thread_id": "t"},
            {"type": "item.completed", "item": {"id": "one", "type": "web_search", "query": "AGENTS.md"}},
            {"type": "turn.completed"},
        ]
        self.assertEqual(1, activity.summarize(codex)["search_arguments"]["unknown"])
        for rows in ([], [{"type": "unsupported", "arguments": {"pattern": "AGENTS.md"}}], stream()[:-1]):
            self.assertFalse(activity.summarize(rows)["search_arguments"]["complete"])

    def test_search_argument_lifecycle_counts_and_projection_remain_bounded_and_allowlisted(self):
        call = tool(name="glob", given={"pattern": "**/AGENTS.md"})
        result = activity.summarize(
            stream(tool(name="glob", status="running"), call, call, tool("two", "glob", {"pattern": "**/AGENTS.md"}))
        )
        self.assertEqual(2, result["search_arguments"]["with_instruction_name"])
        self.assertEqual(1, result["exact_argument_repeats"])
        self.assertEqual(2, result["comparable_calls"])
        poisoned = copy.deepcopy(result)
        poisoned["search_arguments"]["SECRET"] = "PRIVATE-canary"
        self.assertEqual(result, activity.projection(poisoned))
        for value in (
            None,
            {},
            {**result["search_arguments"], "unknown": True},
            {**result["search_arguments"], "unknown": 3},
        ):
            with self.subTest(value=value):
                self.assertEqual("unavailable", activity.projection({**result, "search_arguments": value})["basis"])
        self.assertFalse(activity.projection(result, active=True)["search_arguments"]["complete"])
        self.assertFalse(activity.summarize(stream(call), incomplete=True)["search_arguments"]["complete"])
        self.assertEqual(result["search_arguments"], activity.projection(result)["search_arguments"])

    def test_generic_calls_require_tool_and_mcp_server_identity_for_argument_comparison(self):
        def measure(kind, **identifiers):
            calls = [
                {
                    "type": "item.completed",
                    "item": {
                        "id": identity,
                        "type": kind,
                        "status": "completed",
                        "arguments": {"value": 7},
                        **identifiers,
                    },
                }
                for identity in ("one", "two")
            ]
            return activity.summarize(
                [{"type": "thread.started", "thread_id": "thread"}, *calls, {"type": "turn.completed"}]
            )

        for kind in ("mcp_tool_call", "tool_call", "function_call", "collab_agent_tool_call"):
            for identifiers in ({}, {"tool": None}, {"tool": {}}, {"name": ""}, {"name": " \t"}, {"name": "x" * 1025}):
                with self.subTest(kind=kind, identifiers=identifiers):
                    result = measure(kind, server="PRIVATE_SERVER", **identifiers)
                    self.assertEqual(2, result["observed_calls"])
                    self.assertEqual(2, result["outcomes"]["succeeded"])
                    self.assertEqual(2, result["families"]["agent" if kind == "collab_agent_tool_call" else "other"])
                    self.assertEqual(0, result["comparable_calls"])
                    self.assertEqual(0, result["exact_argument_repeats"])
                    self.assertFalse(result["complete"])
                    self.assertIn("arguments_unavailable", result["issues"])
            for field in ("name", "tool"):
                with self.subTest(kind=kind, valid_field=field):
                    result = measure(kind, server="PRIVATE_SERVER", **{field: "PRIVATE_TOOL"})
                    self.assertEqual(2, result["comparable_calls"])
                    self.assertEqual(1, result["exact_argument_repeats"])
                    self.assertTrue(result["complete"])
                    self.assertNotIn("PRIVATE", json.dumps(result))
        for server in (None, {}, "", " \t", "x" * 1025):
            with self.subTest(server=server):
                result = measure("mcp_tool_call", tool="PRIVATE_TOOL", server=server)
                self.assertEqual(0, result["comparable_calls"])
                self.assertFalse(result["complete"])
        self.assertFalse(measure("mcp_tool_call", tool="PRIVATE_TOOL")["complete"])
        self.assertTrue(measure("function_call", name="PRIVATE_TOOL")["complete"])
        fixed = measure("command_execution", exit_code=0)
        self.assertEqual(2, fixed["comparable_calls"])
        self.assertEqual(1, fixed["exact_argument_repeats"])
        self.assertTrue(fixed["complete"], "A concrete item kind determines its tool without a separate name")

    def test_large_numeric_arguments_are_bounded_before_serialization(self):
        given = {"values": [10**999] * 1000}
        with mock.patch.object(activity.json, "dumps", side_effect=AssertionError("serializer must not run")):
            result = activity.summarize(stream(tool(given=given)))
        self.assertEqual(1, result["observed_calls"])
        self.assertEqual(1, result["families"]["command"])
        self.assertEqual(1, result["outcomes"]["succeeded"])
        self.assertEqual(0, result["comparable_calls"])
        self.assertFalse(result["complete"])
        self.assertIn("observation_limit", result["issues"])
        self.assertIn("arguments_unavailable", result["issues"])

    def test_complete_public_view_sanitizes_saved_diagnostics_without_mutating_metrics(self):
        good = activity.summarize(stream(tool()))
        native = {"tool_activity": {**good, "arguments": "PRIVATE_CANARY"}, "legacy_receipt": {"keep": True}}
        state = {
            "status": "RUNNING",
            "stages": [{"stage": "terra", "metrics": {"request_context": {"accounting": native}}}],
        }
        before = copy.deepcopy(state)
        public = run_view.view(state)
        self.assertNotIn("PRIVATE_CANARY", json.dumps(public))
        self.assertNotIn("arguments", public["request_context"]["last_completed_stage"]["accounting"]["tool_activity"])
        self.assertEqual(
            {"keep": True}, public["request_context"]["last_completed_stage"]["accounting"]["legacy_receipt"]
        )
        self.assertEqual(before, state)

    def test_shell_exit_metadata_matches_supported_opencode_precedence(self):
        cases = (
            ({"metadata": {"exit": 7}}, "failed", 1),
            ({"metadata": {"exit": 0}}, "succeeded", 0),
            ({"exit": 0, "metadata": {"exit": 7}}, "succeeded", 0),
            ({"exit": 7, "metadata": {"exit": 0}}, "failed", 1),
            ({"exit": "7", "metadata": {"exit": 0}}, "unknown", 0),
            ({"exit": False, "metadata": {"exit": 7}}, "unknown", 0),
            ({"metadata": {"exit": "7", "output": "SECRET"}}, "unknown", 0),
            ({"metadata": {"exit": True}}, "unknown", 0),
            ({"metadata": "SECRET"}, "unknown", 0),
            ("SECRET", "unknown", 0),
            ({}, "unknown", 0),
        )
        for metadata, outcome, nonzero in cases:
            with self.subTest(metadata=metadata):
                result = activity.summarize(stream(tool(name="shell", metadata=metadata)))
                self.assertEqual(1, result["observed_calls"])
                self.assertEqual(1, result["outcomes"][outcome])
                self.assertEqual(nonzero, result["error_classes"]["nonzero_exit"])
                self.assertEqual(outcome != "unknown", result["complete"])
                self.assertNotIn("SECRET", json.dumps(result))

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
                tool("two", given={"command": canary}, metadata={"metadata": {"exit": 7}}),
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


def claude_rows(*calls):
    """Claude Code stream-json through the real translator: (name, input, result content, tool_use_result)."""
    translator = claude_stream.Translator(clock=lambda: 0.0)
    rows = translator.feed({"type": "system", "subtype": "init", "session_id": "claude-session"})
    for index, (name, given, content, meta) in enumerate(calls):
        tool_id = f"toolu_{index}"
        block = {"type": "tool_use", "id": tool_id, "name": name, "input": given}
        rows += translator.feed({"type": "assistant", "message": {"id": f"msg_{index}", "content": [block]}})
        result = {"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": False}
        rows += translator.feed(
            {"type": "user", "message": {"role": "user", "content": [result]}, "tool_use_result": meta}
        )
    end = {"type": "result", "subtype": "success", "is_error": False, "session_id": "claude-session"}
    rows += translator.feed(end)
    return rows + translator.finish(0)[0]


def codex_item(kind, identity, **item):
    return {"type": kind, "item": {"id": identity, **item}}


class BackendShapeTests(unittest.TestCase):
    def test_claude_code_builtin_tools_keep_their_families(self):
        result = activity.summarize(
            claude_rows(
                ("Read", {"file_path": "a.py"}, "x", {}),
                ("Grep", {"pattern": "a"}, "x", {}),
                ("Glob", {"pattern": "*.py"}, "x", {}),
                ("WebFetch", {"url": "u"}, "x", {}),
                ("Task", {"prompt": "p"}, "x", {}),
                ("TodoWrite", {"todos": []}, "x", {}),
                ("Bash", {"command": "true"}, "x", {"stdout": "x", "stderr": ""}),
            )
        )
        self.assertEqual({"command": 1, "read": 1, "search": 3, "edit": 0, "agent": 1, "other": 1}, result["families"])
        self.assertEqual({"succeeded": 7, "failed": 0, "unknown": 0}, result["outcomes"])
        mcp = activity.summarize(
            [
                {"type": "thread.started", "thread_id": "t"},
                codex_item("item.completed", "m", type="mcp_tool_call", server="s", tool="read", status="completed"),
                codex_item("item.completed", "f", type="function_call", name="shell", status="completed"),
                {"type": "turn.completed"},
            ]
        )
        self.assertEqual({"command": 0, "read": 0, "search": 0, "edit": 0, "agent": 0, "other": 2}, mcp["families"])

    def test_claude_code_commands_without_a_vouched_exit_are_unknown_not_failed(self):
        result = activity.summarize(
            claude_rows(
                ("Bash", {"command": "sleep 9"}, "running", {"stdout": "", "stderr": "", "backgroundTaskId": "b"}),
                ("Bash", {"command": "make"}, "partial", {"stdout": "partial", "stderr": "", "interrupted": True}),
                ("Bash", {"command": "false"}, "x", {"stdout": "", "stderr": "", "returnCodeInterpretation": "x"}),
            )
        )
        self.assertEqual({"succeeded": 0, "failed": 0, "unknown": 3}, result["outcomes"])
        self.assertEqual({"nonzero_exit": 0, "tool_error": 0}, result["error_classes"])
        self.assertIn("unknown_outcome", result["issues"])
        clean = activity.summarize(claude_rows(("Bash", {"command": "true"}, "x", {"stdout": "", "stderr": ""})))
        self.assertEqual({"succeeded": 1, "failed": 0, "unknown": 0}, clean["outcomes"])

    def test_codex_plan_notice_search_and_collaboration_items(self):
        rows = [
            {"type": "thread.started", "thread_id": "t"},
            {"type": "turn.started"},
            codex_item("item.completed", "plan", type="todo_list", items=[{"text": "fix", "completed": False}]),
            codex_item("item.completed", "notice", type="error", message="non-fatal"),
            codex_item("item.started", "search", type="web_search", query="q", action={"type": "search"}),
            codex_item("item.completed", "search", type="web_search", query="q", action={"type": "search"}),
            codex_item("item.started", "agent", type="collab_tool_call", tool="spawn_agent", status="in_progress"),
            codex_item("item.completed", "agent", type="collab_tool_call", tool="spawn_agent", status="completed"),
            codex_item(
                "item.completed", "mcp", type="mcp_tool_call", server="s", tool="t", arguments={}, status="failed"
            ),
            {"type": "turn.completed"},
        ]
        result = activity.summarize(rows)
        self.assertEqual(3, result["observed_calls"])
        self.assertEqual({"command": 0, "read": 0, "search": 1, "edit": 0, "agent": 1, "other": 1}, result["families"])
        self.assertEqual({"succeeded": 2, "failed": 1, "unknown": 0}, result["outcomes"])
        self.assertNotIn("unsupported_stream", result["issues"])
        self.assertNotIn("unknown_outcome", result["issues"])
        running = activity.summarize(rows[:5] + rows[-1:])
        self.assertEqual(1, running["outcomes"]["unknown"], "A started search is not a finished one")

    def test_autocode_compaction_row_is_not_an_unsupported_stream(self):
        compaction = {"type": "autocode_compaction", "version": 1, "sessionID": "s", "compaction": {"auto": True}}
        result = activity.summarize(stream(tool(), compaction))
        self.assertTrue(result["complete"], result)
