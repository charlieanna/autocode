"""Citations are authenticated against the files a stage's provider actually read (board #13, interface 1)."""

import json
import tempfile
import unittest
from pathlib import Path

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


class CodeCitationTests(unittest.TestCase):
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
                codex_command("x2", "cat notes.txt", "notes\n", code=1),
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


if __name__ == "__main__":
    unittest.main()
