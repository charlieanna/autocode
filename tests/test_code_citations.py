"""Citations are authenticated against the files a stage's provider actually read (board #13, interface 1)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_code_citations as citations
from units import autoplanner


def codex_command(identity, command, output="", code=0):
    return {
        "type": "item.completed",
        "item": {
            "id": identity,
            "type": "command_execution",
            "command": command,
            "exit_code": code,
            "aggregated_output": output,
        },
    }


def claude_tool(identity, name, status="completed", **shown):
    return {
        "type": "item.completed",
        "item": {"id": identity, "type": "tool_call", "name": name, "display_input": shown, "status": status},
    }


def opencode_tool(identity, tool, status="completed", output="", metadata=None, **arguments):
    state = {"status": status, "input": arguments, "output": output}
    if metadata is not None:
        state["metadata"] = metadata
    return {"type": "tool_use", "sessionID": "session", "part": {"id": identity, "tool": tool, "state": state}}


class Workspace(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "project"
        (self.workspace / "pkg").mkdir(parents=True)
        (self.workspace / "app.py").write_text("one\ntwo\nthree\n")
        (self.workspace / "pkg" / "util.py").write_text("def helper():\n    return 1\n")
        (self.workspace / "README.md").write_text("# Project\n")
        (self.workspace / "notes.txt").write_text("notes\n")
        self.run = self.workspace / ".autocode" / "runs" / "current"
        self.run.mkdir(parents=True)
        self.state = {"workspace": str(self.workspace), "run_dir": str(self.run), "task": "Fix app", "stages": []}

    def record(self, rows, stage="requirements_gather", name=None, **extra):
        path = self.run / (name or f"events-{len(self.state['stages'])}.jsonl")
        path.write_text("".join((row if isinstance(row, str) else json.dumps(row)) + "\n" for row in rows))
        record = {"stage": stage, "events": str(path), **extra}
        self.state["stages"].append(record)
        return record


class CodeCitationTests(Workspace):
    def test_each_backend_native_and_shell_reads_count_and_listings_do_not(self):
        self.record(
            [
                "not json: a stray provider line",
                claude_tool("c1", "Read", file_path=str(self.workspace / "app.py")),
                claude_tool("c2", "Glob", pattern="**/*.md"),
                claude_tool("c3", "Read", status="failed", file_path=str(self.workspace / "notes.txt")),
                opencode_tool("o1", "read", filePath=str(self.workspace / "pkg" / "util.py")),
                opencode_tool("o2", "glob", output=str(self.workspace / "README.md"), pattern="*.md"),
                opencode_tool("o3", "read", status="error", filePath=str(self.workspace / "notes.txt")),
                codex_command("x1", "/bin/zsh -lc 'ls -la'", "README.md\nnotes.txt\n"),
                codex_command("x2", "cat notes.txt", "cat: notes.txt: Permission denied\n", code=1),
                {"type": "turn.completed"},
            ]
        )
        self.assertEqual(frozenset({"app.py", "pkg/util.py"}), citations.read_paths(self.state, "requirements_gather"))

    def test_shell_arguments_nested_shells_and_search_hits_count_as_reads(self):
        rows = [
            codex_command("x1", "/bin/bash -lc \"sed -n '1,80p' app.py | head\"", "one\n"),
            codex_command("x2", "rg -n helper .", "./pkg/util.py:1:def helper():\n"),
            codex_command("x3", "cat /etc/hosts ../outside.py", "x"),
        ]
        self.record(rows)
        self.assertEqual(frozenset({"app.py", "pkg/util.py"}), citations.read_paths(self.state, "requirements_gather"))
        self.state["stages"].clear()
        self.record(
            [
                opencode_tool("o1", "bash", output="# Project\n", metadata={"exit": 0}, command="cat README.md"),
                opencode_tool(
                    "o2", "bash", output="notes\n", metadata={"metadata": {"exit": 0}}, command="cat notes.txt"
                ),
                opencode_tool("o3", "bash", output="", metadata={"exit": 2}, command="cat app.py"),
            ]
        )
        self.assertEqual(frozenset({"README.md", "notes.txt"}), citations.read_paths(self.state, "requirements_gather"))

    def test_a_failed_shell_read_counts_the_files_whose_text_it_printed(self):
        # 2026-11-17: `cat linked.py __init__.py test.py` exited 1 because a sandbox refused the linked file, and the
        # two files printed beside it were refused as unread; Requirements paused twice.
        shown = "one\ntwo\nthree\ncat: README.md: Operation not permitted\ndef helper():\n    return 1\n"
        command = "cat app.py README.md pkg/util.py notes.txt"
        self.record(
            [
                opencode_tool("o1", "bash", output=shown, metadata={"exit": 1}, command=command),
                codex_command("x1", "python -m pytest notes.txt", "notes.txt:1: SyntaxError\n", code=1),
            ]
        )
        self.assertEqual(frozenset({"app.py", "pkg/util.py"}), citations.read_paths(self.state, "requirements_gather"))
        self.state["stages"].clear()
        self.record([codex_command("x2", command, shown, code=1)])
        self.assertEqual(frozenset({"app.py", "pkg/util.py"}), citations.read_paths(self.state, "requirements_gather"))

    def test_reads_come_from_the_stage_its_repairs_and_the_current_turn_only(self):
        self.record([codex_command("x", "cat notes.txt", "notes\n")])  # an earlier turn
        self.state["turns"] = [{"stage_index": 1}]
        self.record([codex_command("x", "cat app.py", "one\n")], timed_out=True)  # budget-stopped attempt
        self.record([codex_command("x", "cat README.md", "#\n")], stage="draft_plan")
        repair = self.record([], stage="requirements_gather_report_repair", report_only=True)
        self.assertEqual(frozenset({"app.py"}), citations.read_paths(self.state, "requirements_gather"))
        self.assertEqual(frozenset({"README.md"}), citations.read_paths(self.state, "draft_plan"))
        self.assertEqual(
            frozenset({"app.py", "README.md"}), citations.read_paths(self.state, ("requirements_gather", "draft_plan"))
        )
        self.assertEqual(frozenset({"app.py", "README.md"}), citations.read_paths(self.state, None))
        self.state["stages"].remove(repair)
        self.state["active_stage"] = {
            "stage": "requirements_gather",
            "original_stage": "requirements_gather",
            "events": str(self.run / "active.jsonl"),
        }
        (self.run / "active.jsonl").write_text(json.dumps(codex_command("y", "cat pkg/util.py", "x")) + "\n")
        self.assertIn("pkg/util.py", citations.read_paths(self.state, "requirements_gather"))

    def test_verify_citation_returns_reasons_and_never_raises(self):
        self.record([codex_command("x", "cat app.py", "one\ntwo\nthree\n")])
        verify = lambda ref: citations.verify_citation(self.state, "requirements_gather", ref, field="convention_ref")
        for ref in ("app.py", "app.py:2", "app.py:1-3", "app.py:2-3 why it matters", "app.py — the entry point"):
            with self.subTest(ref=ref):
                self.assertIsNone(verify(ref))
        self.assertIn("never read", verify("README.md:1"))
        self.assertIn("past the end", verify("app.py:9"))
        self.assertIn("no line number", verify("app.py:two"))
        self.assertIn("not a file in the workspace", verify("task"))
        self.assertIn("not a file in the workspace", verify("https://example.test/app.py"))
        self.assertIn("runner or a tool owns", verify(".autocode/runs/current/events-0.jsonl"))
        self.assertIn("names no workspace file", verify(""))
        self.assertIn("names no workspace file", verify(None))
        self.assertTrue(verify("README.md").startswith("convention_ref entry README.md"))

    def test_greenfield_has_nothing_to_read_and_passes(self):
        empty = self.root / "empty"
        empty.mkdir()
        for workspace in (str(empty), str(self.root / "missing"), ""):
            with self.subTest(workspace=workspace):
                state = {**self.state, "workspace": workspace}
                self.assertIsNone(citations.verify_citation(state, "requirements_gather", "app.py:1"))
                self.assertEqual(frozenset(), citations.read_paths(state, "requirements_gather"))

    def test_require_with_a_stage_refuses_unread_citations_and_lists_what_was_read(self):
        self.record([codex_command("x", "cat app.py", "one\n")])
        require = lambda refs: citations.require(
            self.state, refs, "source_refs", planning_unit=autoplanner, stage="requirements_gather"
        )
        require(["app.py:1"])
        with self.assertRaisesRegex(ValueError, r"never read: README.md:1\. .*\(read: app.py\)"):
            require(["app.py:1", "README.md:1"])
        citations.require(self.state, ["README.md:1"], "source_refs", planning_unit=autoplanner)  # no stage: as before

    def test_require_with_nothing_read_sends_the_stage_back_to_its_tools(self):
        from autocode_report_repair_queue import ExecutionEvidenceRequired

        self.record([codex_command("x", "ls", "app.py\n")])
        with self.assertRaisesRegex(ExecutionEvidenceRequired, "read no file"):
            citations.require(
                self.state, ["app.py:1"], "source_refs", planning_unit=autoplanner, stage="requirements_gather"
            )

    def test_single_file_grep_and_matched_grep_hits_count_while_directory_grep_does_not(self):
        self.record(
            [
                claude_tool("c1", "Grep", path=str(self.workspace / "app.py"), pattern="two"),
                claude_tool("c2", "Grep", path=str(self.workspace), pattern="two"),
                opencode_tool(
                    "o1",
                    "grep",
                    output="Found 1 match\npkg/util.py:\n  Line 1: def helper():\n",
                    path=".",
                    pattern="helper",
                ),
            ]
        )
        self.assertEqual(frozenset({"app.py", "pkg/util.py"}), citations.read_paths(self.state, "requirements_gather"))

    def test_replay_rebinds_native_read_paths_into_a_fresh_workspace(self):
        import autocode_replay_bindings as replay_bindings
        import autocode_replay_recording as recording

        payload = {
            "events": [
                claude_tool("c1", "Read", file_path=str(self.workspace / "app.py")),
                claude_tool("c2", "Grep", path=str(self.workspace), pattern="one"),
                opencode_tool("o1", "read", filePath=str(self.workspace / "pkg" / "util.py")),
                opencode_tool("o2", "read", filePath="/etc/hosts"),
                {"type": "turn.completed"},
            ],
            "report": {"source_refs": ["app.py:1", "pkg/util.py:1"]},
            "response": None,
        }
        bound = replay_bindings.bindings(payload, {}, {}, self.workspace, self.run, [])
        fresh = self.root / "replayed-project"
        (fresh / "pkg").mkdir(parents=True)
        (fresh / "app.py").write_text("one\ntwo\nthree\n")
        (fresh / "pkg" / "util.py").write_text("def helper():\n    return 1\n")
        fresh_run = fresh / ".autocode" / "runs" / "reproduction"
        fresh_run.mkdir(parents=True)
        rebound = recording.rebind(payload, bound, {"workspace": str(fresh), "run_dir": str(fresh_run)})
        self.assertEqual("/etc/hosts", rebound["events"][3]["part"]["state"]["input"]["filePath"])
        events_path = fresh_run / "events.jsonl"
        events_path.write_text("".join(json.dumps(row) + "\n" for row in rebound["events"]))
        replayed_state = {
            "workspace": str(fresh),
            "run_dir": str(fresh_run),
            "task": "Fix app",
            "stages": [{"stage": "requirements_gather", "events": str(events_path)}],
        }
        self.assertEqual(
            frozenset({"app.py", "pkg/util.py"}), citations.read_paths(replayed_state, "requirements_gather")
        )
        self.assertIsNone(citations.verify_citation(replayed_state, "requirements_gather", "app.py:2"))

    def test_split_and_cited_file_match_the_planning_units_parsing(self):
        for ref in ("app.py", "app.py:2", "app.py: why", "app.py why", "pkg/util.py:1-2 x"):
            with self.subTest(ref=ref):
                self.assertEqual(
                    autoplanner.split_code_ref(self.workspace, ref), citations.split_ref(self.workspace, ref)
                )
        self.assertEqual(
            autoplanner.cited_file(self.workspace, "app.py", "f", "app.py"),
            citations.cited_file(self.workspace, "app.py", "f", "app.py"),
        )


class RequirementsAcceptanceWiringTests(Workspace):
    """The controller authenticates Requirements citations and its audit; later stages keep the file check."""

    def accept(self, stage, value, record=None, *, verify=None):
        import autopilot

        seen = {}

        def prepare(state, stage, value, record, *, investigation_stages, check_code_refs):
            seen["prepare"] = check_code_refs
            return value, stage != "requirements_gather"  # deferred: a later stage stops here

        def accept(state, value, record, *, check_code_refs, queue):
            seen["accept"] = check_code_refs
            if verify is not None:
                verify(state, check_code_refs)

        with (
            mock.patch.object(autopilot.requirements_triage, "validate_application"),
            mock.patch.object(autopilot.planning_unit.planning_scope, "prepare_handoff", side_effect=prepare),
            mock.patch.object(autopilot.requirements_handoff, "accept", side_effect=accept),
            mock.patch.object(autopilot.planning_unit, "rerun_requirements", return_value=False),
        ):
            autopilot.apply_planning(self.state, stage, value, record or {"output": "report.json"})
        return seen

    def test_requirements_checks_are_bound_to_what_requirements_read(self):
        self.record([codex_command("x", "cat app.py", "one\n")])
        seen = self.accept("requirements_gather", {"summary": "s", "open_questions": []})
        for name in ("prepare", "accept"):
            with self.subTest(check=name):
                seen[name](self.state, ["app.py:1"], "source_refs")
                with self.assertRaisesRegex(ValueError, "never read: README.md"):
                    seen[name](self.state, ["README.md"], "source_refs")
        later = self.accept("draft_plan", {"summary": "s"})
        later["prepare"](self.state, ["README.md"], "code_refs")  # the Planner keeps the file check

    def test_inflight_record_not_yet_saved_in_stages_is_authenticated_during_acceptance(self):
        from autocode_report_repair_queue import ExecutionEvidenceRequired

        events = self.run / "overlap.jsonl"
        events.write_text(json.dumps({"type": "turn.completed"}) + "\n")
        inflight = {"stage": "requirements_gather", "output": "report.json", "events": str(events)}

        def check_empty(state, check_code_refs):
            with self.assertRaisesRegex(ExecutionEvidenceRequired, "read no file"):
                check_code_refs(state, ["app.py:1"], "source_refs")

        self.accept("requirements_gather", {"summary": "s", "open_questions": []}, inflight, verify=check_empty)
        events.write_text(
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {"tool": "read", "state": {"input": {"filePath": str(self.workspace / "app.py")}}},
                }
            )
            + "\n"
        )

        def check_read(state, check_code_refs):
            check_code_refs(state, ["app.py:1"], "source_refs")
            self.assertIsNone(citations.verify_citation(state, "requirements_gather", "app.py:1"))
            with self.assertRaisesRegex(ValueError, "never read: README.md"):
                check_code_refs(state, ["README.md"], "source_refs")

        self.accept("requirements_gather", {"summary": "s", "open_questions": []}, inflight, verify=check_read)

    def test_requirements_audit_is_checked_before_the_handoff(self):
        import autocode_ambiguity_audit as audit

        value = {
            "summary": "s",
            "open_questions": [],
            audit.FIELD: {dimension: {"status": "open_question", "refs": ["Q9"]} for dimension in audit.DIMENSIONS},
        }
        import autopilot

        with (
            mock.patch.object(autopilot.requirements_triage, "validate_application"),
            mock.patch.object(autopilot.requirements_handoff, "accept") as accepted,
            self.assertRaisesRegex(ValueError, "Q9, which are not open_questions ids"),
        ):
            autopilot.apply_planning(self.state, "requirements_gather", value, {"output": "report.json"})
        accepted.assert_not_called()

    def test_citation_verifier_stays_in_the_leaf_layer_below_source_log_and_report_repair(self):
        import autocode_report_repair_queue as report_repair_queue
        import autocode_util as util

        from tests.test_architecture import import_graph, reachable

        self.assertIs(util.ExecutionEvidenceRequired, report_repair_queue.ExecutionEvidenceRequired)
        self.assertEqual(
            {
                "autocode_code_citations",
                "autocode_project_kinds",
                "autocode_prompts",
                "autocode_run_state",
                "autocode_util",
                "autocode_workspace_inventory",
            },
            reachable(import_graph(), ["autocode_code_citations"]),
            "lower-layer modules (assumption ledger, requirements review) import code_citations without a cycle",
        )


if __name__ == "__main__":
    unittest.main()
