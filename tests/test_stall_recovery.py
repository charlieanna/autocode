"""A stalled Tester's archived tool calls reach its relaunch as bounded context (#3309)."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_source_scope as source_scope
import autocode_stage_recovery as stage_recovery
import autocode_stall_recovery as stall_recovery
import autocode_util as util


def tool_row(tool, status="completed", input=None, output="done", *, part_id=None, metadata=None, session="s1"):
    state = {"status": status, "input": input, "output": output}
    if metadata is not None:
        state["metadata"] = metadata
    return {
        "type": "tool_use",
        "sessionID": session,
        "part": {
            "id": part_id or f"p{tool_row.count}",
            "tool": tool,
            "state": state,
        },
    }


tool_row.count = 0


def bash(command, output="", status="completed", *, part_id=None, exit_code=None):
    tool_row.count += 1
    metadata = {"exit": exit_code} if exit_code is not None else None
    return tool_row("bash", status, {"command": command}, output, part_id=part_id, metadata=metadata)


def read(path, offset=None, limit=None, output=""):
    tool_row.count += 1
    return tool_row("read", "completed", {"filePath": path, "offset": offset, "limit": limit}, output)


class StallRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.test")
        (self.root / "app.py").write_text("committed\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.run = self.root / ".autocode/runs/fixture"
        self.directory = self.run / "iterations/001"
        self.directory.mkdir(parents=True)
        self.state = {
            "version": 2,
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "status": "RUNNING",
            "phase": "EXECUTING",
            "iteration": 1,
            "sessions": {},
            "stages": [],
            "task": "Repair app",
            "settings": {
                "roles": {
                    role: {"model": "fixture", "reasoning_effort": "high"}
                    for role in ("builder", "tester", "task_planner")
                },
                "workflow": {"mode": "standard"},
            },
            "goal_contract": {
                "task_id": "T1",
                "hash": "h",
                "revision": 1,
                "body": {"acceptance_criteria": [{"id": "C1", "criterion": "Repair app"}]},
            },
            "current_task": {
                "id": "T1",
                "kind": "implement",
                "milestone_id": "M1",
                "acceptance_criteria": ["C1"],
                "affected_paths": ["app.py"],
            },
            "next_stage": "tester",
        }
        contract = self.state["goal_contract"]
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        self.record = self.record_for("tester")

    def record_for(self, stage, *, events=None):
        return {
            "role": stage,
            "stage": stage,
            "iteration": 1,
            "task_id": "T1",
            "output": str(self.directory / f"{stage}-01.json"),
            "events": str(events or self.directory / f"{stage}-01.jsonl"),
            "before_ref": str(self.directory / f"{stage}-01.before.json"),
            "contract_revision": 1,
            "contract_hash": "h",
            "timed_out": True,
            "timeout_kind": "stage",
            "exit_code": -15,
            "processes": [],
        }

    def write_events(self, record, rows):
        Path(record["events"]).write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    def timeout(self, record=None):
        record = record or self.record
        self.state["active_stage"] = record
        self.assertTrue(
            stage_recovery.automatically_recover_timed_out_stage(
                self.state, self.run, self.root, util.Paused("PAUSED_PROVIDER_TIMEOUT", "stage exceeded 300s idle")
            )
        )
        return self.state["recovery_context"]

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.PIPE)

    def snapshot(self, record):
        before = source_scope.snapshot(self.root, self.state)
        util.atomic_json(Path(record["before_ref"]), before)
        return before

    def test_a_stalled_tester_relaunch_receives_its_archived_calls(self):
        self.snapshot(self.record)
        self.write_events(
            self.record,
            [
                {"type": "thread.started", "thread_id": "expired"},
                bash("pytest -q tests/test_app.py", output="3 passed", status="completed", exit_code=0),
                read("/repo/app.py", offset=1, limit=20, output="1: committed"),
                bash("python3 -c 'boom()'", output="Traceback (most recent call last):", status="error"),
                {"type": "step_start", "part": {"id": "s1"}},
            ],
        )
        recovery = self.timeout()
        self.assertEqual("tester", recovery["next_stage"])
        packet = recovery["executed_work"]
        self.assertTrue(packet["available"])
        self.assertEqual("unverified", packet["verification"])
        self.assertEqual(
            [
                {
                    "tool": "bash",
                    "detail": "pytest -q tests/test_app.py",
                    "status": "completed",
                    "output_tail": "3 passed",
                    "exit_code": 0,
                },
                {"tool": "read", "detail": "/repo/app.py @1:20", "status": "completed", "output_tail": "1: committed"},
                {
                    "tool": "bash",
                    "detail": "python3 -c 'boom()'",
                    "status": "error",
                    "output_tail": "Traceback (most recent call last):",
                },
            ],
            packet["calls"],
        )
        self.assertFalse(packet["truncated"])
        self.assertIn("executed_work packet", recovery["instruction"])
        self.assertIn("never citable evidence", recovery["instruction"])

    def test_the_packet_is_bounded_and_keeps_the_true_output_tail(self):
        self.snapshot(self.record)
        banner = "==== test session starts ====\n" + ("collecting...\n" * 30)
        verdict_tail = "FAILED tests/test_app.py::test_edge - AssertionError: 1 != 2\n1 failed, 19 passed in 0.42s"
        rows = [
            bash(f"command-{index}-" + ("y" * 250), output=banner + verdict_tail, exit_code=1 if index == 0 else 0)
            for index in range(stall_recovery.MAX_CALLS + 2)
        ]
        rows.insert(0, {"type": "thread.started", "thread_id": "expired"})
        self.write_events(self.record, rows)
        packet = self.timeout()["executed_work"]
        self.assertTrue(packet["truncated"])
        self.assertEqual(stall_recovery.MAX_CALLS, len(packet["calls"]))
        self.assertLessEqual(max(len(call["detail"]) for call in packet["calls"]), stall_recovery.MAX_DETAIL + 1)
        self.assertLessEqual(max(len(call["output_tail"]) for call in packet["calls"]), stall_recovery.MAX_OUTPUT + 1)
        first = packet["calls"][0]
        self.assertTrue(first["detail"].startswith("command-0-"))
        self.assertTrue(first["output_tail"].startswith("…"))
        self.assertIn("1 failed, 19 passed in 0.42s", first["output_tail"])
        self.assertNotIn("test session starts", first["output_tail"])
        self.assertEqual("failed", first["status"])
        self.assertEqual(1, first["exit_code"])

    def test_a_log_without_tool_calls_or_unreadable_declines_cleanly(self):
        self.snapshot(self.record)
        for rows in (
            [{"type": "thread.started", "thread_id": "expired"}, {"type": "step_start", "part": {"id": "s1"}}],
            [{"type": "tool_use", "part": {"id": "p1", "tool": "bash", "state": {"status": "pending"}}}],
            [{"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "pytest"}}],
        ):
            with self.subTest(rows=rows):
                self.write_events(self.record, rows)
                recovery = self.timeout()
                self.assertFalse(recovery["executed_work"]["available"])
                self.assertTrue(recovery["executed_work"]["reason"])
                self.assertNotIn("executed_work packet", recovery["instruction"])
        Path(self.record["events"]).unlink()
        self.assertFalse(stall_recovery.executed(self.record)["available"])

    def test_a_builder_timeout_keeps_the_builder_packet_and_no_tester_packet(self):
        builder = self.record_for("builder")
        self.snapshot(builder)
        self.write_events(builder, [{"type": "thread.started", "thread_id": "expired"}])
        recovery = self.timeout(builder)
        self.assertEqual("builder", recovery["next_stage"])
        self.assertIn("partial_work", recovery)
        self.assertNotIn("executed_work", recovery)


class ExecutedPacketTests(unittest.TestCase):
    def test_repeated_part_snapshots_deduplicate_and_opencode2_nested_exit_is_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            rows = [
                tool_row("bash", "running", {"command": "pytest -q"}, "", part_id="p-same"),
                tool_row("bash", "completed", {"command": "pytest -q"}, "partial", part_id="p-same"),
                tool_row(
                    "bash",
                    "completed",
                    {"command": "pytest -q"},
                    "2 failed",
                    part_id="p-same",
                    metadata={"metadata": {"exit": 2}},
                ),
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            packet = stall_recovery.executed({"events": str(path)})
        self.assertTrue(packet["available"])
        self.assertEqual(
            [
                {
                    "tool": "bash",
                    "detail": "pytest -q",
                    "status": "failed",
                    "output_tail": "2 failed",
                    "exit_code": 2,
                }
            ],
            packet["calls"],
        )

    def test_codex_and_claude_item_completed_events_and_secret_redaction(self):
        secret = "sk-" + ("A1b2C3d4" * 6)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            rows = [
                {"type": "thread.started", "thread_id": "t1"},
                {"type": "item.completed", "item": {"id": "m1", "type": "reasoning", "text": "thinking"}},
                {"type": "item.completed", "item": {"id": "ip", "type": "command_execution", "status": "in_progress"}},
                {
                    "type": "item.completed",
                    "item": {
                        "id": "c1",
                        "type": "command_execution",
                        "command": f"echo {secret} && pytest -q",
                        "exit_code": 1,
                        "aggregated_output": f"leaked {secret}\n1 failed in 0.12s",
                        "status": "failed",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": "r1",
                        "type": "tool_call",
                        "name": "Read",
                        "display_input": {"file_path": "/repo/app.py", "offset": 10, "limit": 30},
                        "status": "completed",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": "f1",
                        "type": "file_change",
                        "changes": [{"path": "tests/test_app.py"}],
                        "status": "completed",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": "fn1",
                        "type": "function_call",
                        "name": "grep",
                        "arguments": json.dumps({"path": "app.py"}),
                        "output": "1: committed",
                        "status": "completed",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": "ws1",
                        "type": "web_search",
                        "query": "python unittest subTest",
                        "status": "completed",
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            packet = stall_recovery.executed({"events": str(path)})
        self.assertTrue(packet["available"])
        self.assertEqual(5, len(packet["calls"]))
        cmd, read_call, file_call, fn_call, ws_call = packet["calls"]
        self.assertEqual("command_execution", cmd["tool"])
        self.assertEqual("failed", cmd["status"])
        self.assertEqual(1, cmd["exit_code"])
        self.assertNotIn(secret, cmd["detail"])
        self.assertNotIn(secret, cmd["output_tail"])
        self.assertIn("1 failed in 0.12s", cmd["output_tail"])
        self.assertEqual(
            {"tool": "Read", "detail": "/repo/app.py @10:30", "status": "completed", "output_tail": ""},
            read_call,
        )
        self.assertEqual(
            {"tool": "file_change", "detail": "tests/test_app.py", "status": "completed", "output_tail": ""},
            file_call,
        )
        self.assertEqual(
            {"tool": "grep", "detail": "app.py", "status": "completed", "output_tail": "1: committed"},
            fn_call,
        )
        self.assertEqual(
            {"tool": "web_search", "detail": "python unittest subTest", "status": "completed", "output_tail": ""},
            ws_call,
        )

    def test_garbage_rows_and_odd_inputs_are_skipped_or_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            path.write_text(
                "\n".join(
                    [
                        "not json at all",
                        "null",
                        "[1, 2, 3]",
                        "42",
                        json.dumps({"type": "tool_use"}),
                        json.dumps(
                            {
                                "type": "tool_use",
                                "part": {"tool": "grep", "state": {"status": "completed", "input": "plain string"}},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "tool_use",
                                "part": {
                                    "tool": "bash",
                                    "state": {"status": "completed", "input": {"command": 5}, "output": None},
                                },
                            }
                        ),
                    ]
                )
                + "\n"
            )
            packet = stall_recovery.executed({"events": str(path)})
        self.assertTrue(packet["available"])
        self.assertEqual(2, len(packet["calls"]))
        self.assertEqual(["grep", "bash"], [call["tool"] for call in packet["calls"]])
        self.assertEqual("", packet["calls"][1]["output_tail"])
        self.assertFalse(stall_recovery.executed({})["available"])
        self.assertFalse(stall_recovery.executed(None)["available"])
