"""The pause transition and the gate that keeps it the only writer of the paused phase."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import autocode_run_status as run_status

from tests.source_inventory import python_sources

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def phase_writes(source: str) -> list[int]:
    """Lines in ``source`` that directly set a phase to the paused phase."""

    def paused(node):
        return (
            isinstance(node, ast.Constant)
            and node.value == run_status.PAUSE_PHASE
            or isinstance(node, ast.Attribute)
            and node.attr == "PAUSE_PHASE"
            or isinstance(node, ast.Name)
            and node.id == "PAUSE_PHASE"
        )

    def phase_key(node):
        return isinstance(node, ast.Constant) and node.value == "phase"

    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.keyword) and node.arg == "phase" and paused(node.value):
            lines.append(node.value.lineno)
        elif isinstance(node, ast.Dict):
            lines += [
                value.lineno
                for key, value in zip(node.keys, node.values, strict=False)
                if phase_key(key) and paused(value)
            ]
        elif (
            isinstance(node, ast.Assign)
            and paused(node.value)
            and any(isinstance(target, ast.Subscript) and phase_key(target.slice) for target in node.targets)
        ):
            lines.append(node.lineno)
    return lines


class PauseTests(unittest.TestCase):
    def test_pause_sets_status_phase_reason_and_fields(self):
        state = {"status": "RUNNING", "phase": "EXECUTING", "next_stage": "terra"}
        result = run_status.pause(state, "PAUSED_NO_PROGRESS", "no progress", next_stage="sol", paused_at="t")
        self.assertIs(result, state)
        self.assertEqual(
            {
                "status": "PAUSED_NO_PROGRESS",
                "phase": "PAUSED_OR_BLOCKED",
                "stop_reason": "no progress",
                "next_stage": "sol",
                "paused_at": "t",
            },
            state,
        )

    def test_pause_without_a_reason_keeps_the_stop_reason(self):
        state = {"status": "WAITING_FOR_USER", "stop_reason": "earlier"}
        run_status.pause(state, "PAUSED_RESOLVER")
        self.assertEqual("earlier", state["stop_reason"])
        self.assertEqual("PAUSED_OR_BLOCKED", state["phase"])

    def test_running_sets_status_and_phase_and_retires_stop_reason(self):
        state = {"status": "PAUSED_TIMEOUT", "phase": "PAUSED_OR_BLOCKED", "stop_reason": "timed out"}
        result = run_status.running(state, "EXECUTING", next_stage="terra")
        self.assertIs(result, state)
        self.assertEqual({"status": "RUNNING", "phase": "EXECUTING", "next_stage": "terra"}, state)

    def test_awaiting_approval_returns_to_the_human_gate_without_granting_it(self):
        state = {"status": "RUNNING", "phase": "PLANNING", "stop_reason": "old", "next_stage": "requirements_gather"}
        result = run_status.awaiting_approval(state, next_stage="astra_plan")
        self.assertIs(result, state)
        self.assertEqual(
            {"status": "AWAITING_GOAL_APPROVAL", "phase": "AWAITING_GOAL_APPROVAL", "next_stage": "astra_plan"}, state
        )
        with self.assertRaises(TypeError):
            run_status.awaiting_approval({}, status="TASK_COMPLETE")

    def test_transition_fields_cannot_replace_owned_fields(self):
        with self.assertRaises(TypeError):
            run_status.pause({}, "PAUSED_INVALID_OUTPUT", stop_reason="bypass")
        with self.assertRaises(TypeError):
            run_status.running({}, "EXECUTING", status="TASK_COMPLETE")


class PauseOwnerTests(unittest.TestCase):
    def test_only_the_pause_transition_writes_the_paused_phase(self):
        owner = TOOLS / "autocode_run_status.py"
        found = [
            f"{path.relative_to(TOOLS)}:{line}"
            for path in python_sources(TOOLS)
            if path != owner
            for line in phase_writes(path.read_text())
        ]
        self.assertEqual([], found, "pause the run with autocode_run_status.pause instead of writing its phase")

    def test_no_other_application_module_defines_the_paused_phase_literal(self):
        owner = TOOLS / "autocode_run_status.py"
        found = [
            f"{path.relative_to(TOOLS)}:{node.lineno}"
            for path in python_sources(TOOLS)
            if path != owner
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.Constant) and node.value == run_status.PAUSE_PHASE
        ]
        self.assertEqual([], found, "refer to autocode_run_status.PAUSE_PHASE instead of defining another name")

    def test_the_gate_sees_literal_and_named_writes(self):
        source = (
            'a.update(phase="PAUSED_OR_BLOCKED")\nb = {"phase": run_status.PAUSE_PHASE}\nc["phase"] = PAUSE_PHASE\n'
        )
        self.assertEqual([1, 2, 3], sorted(phase_writes(source)))


if __name__ == "__main__":
    unittest.main()
