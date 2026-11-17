"""Text added to a built stage prompt goes before its handoff packet, never after it.

Every launch helper reads everything after ``CURRENT HANDOFF DATA`` as one JSON object. Resolver
guidance appended after the packet paused a ``replan`` -> ``draft_plan`` with
``PAUSED_INVALID_OUTPUT`` ("Provider handoff must contain a JSON object") before any provider
launched (#3734). Every injector now goes through ``tool_handoff.before_handoff``.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_publish_guard as publish_guard
import autocode_resolve_repair_job as resolve_repair_job
import autocode_tool_handoff as tool_handoff
import autocode_unit_request as unit_request
from providers import opencode
from units.model_request import ModelRequest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
PACKET = {"stage": "draft_plan", "request": "Build mapreduce"}


def handoff_prompt(indent=None) -> str:
    return "PLANNER INSTRUCTIONS\n" + tool_handoff.MARKER + json.dumps(PACKET, indent=indent)


def guided_state(stage="draft_plan") -> dict:
    return {
        "resolve_repair_request": {
            "in_force": True,
            "stage": "builder",
            "target_stage_for_guidance": stage,
            "guidance": "Split the server milestone so coordinator changes are owned.",
            "diagnosis": "M4 repair needs coordinator.py",
            "next_objective": "Replan M4",
            "affected_paths": ["mapreduce/server.py", "mapreduce/coordinator.py"],
        }
    }


def packet_of(prompt: str) -> dict:
    instructions, marker, packet = prompt.partition(tool_handoff.MARKER)
    assert marker, "the prompt lost its handoff marker"
    return json.loads(packet)


class BeforeHandoffTests(unittest.TestCase):
    def test_text_goes_into_the_instructions_and_the_packet_stays_last(self):
        for indent in (None, 2):
            with self.subTest(indent=indent):
                prompt = tool_handoff.before_handoff(handoff_prompt(indent), "\nNOTE\n")
                instructions, _, _ = prompt.partition(tool_handoff.MARKER)
                self.assertTrue(instructions.endswith("\nNOTE\n"))
                self.assertEqual(PACKET, packet_of(prompt))

    def test_a_prompt_without_handoff_takes_the_text_at_its_end(self):
        self.assertEqual("BASE\nNOTE\n", tool_handoff.before_handoff("BASE", "\nNOTE\n"))


class ResolverGuidanceTests(unittest.TestCase):
    def test_guidance_keeps_the_packet_readable_by_every_launch_helper(self):
        request = ModelRequest("task_planner", "task_planner", handoff_prompt(), {}, {}, False)
        guided = resolve_repair_job.with_guidance(guided_state(), "draft_plan", request)
        instructions, _, _ = guided.prompt.partition(tool_handoff.MARKER)
        self.assertIn("RESOLVER GUIDANCE (resolve_repair)", instructions)
        self.assertIn("Target paths: mapreduce/server.py, mapreduce/coordinator.py", instructions)
        self.assertEqual(PACKET, packet_of(guided.prompt))
        # Each helper the launch applies parses the packet; none may raise.
        prompt = tool_handoff.with_capture_command(guided.prompt)
        prompt = tool_handoff.with_stage_events_log(prompt, "/tmp/events.jsonl")
        prompt = tool_handoff.with_report_check_command(prompt, "/tmp/spec.json")
        prompt = opencode.prompt_for_schema(prompt, {"type": "object", "properties": {}}, "/tmp/events.jsonl")
        data = packet_of(prompt)
        self.assertEqual("Build mapreduce", data["request"])
        self.assertIn("capture_command", data)
        self.assertIn("report_check_command", data)

    def test_prepared_request_with_guidance_and_publish_rule_stays_launchable(self):
        state = guided_state()
        request = unit_request.prepare(
            state,
            "draft_plan",
            Path("/tmp/state.json"),
            Path("/tmp"),
            select_unit=lambda _stage: SimpleNamespace(
                prepare=lambda *a, **kw: ModelRequest("task_planner", "task_planner", handoff_prompt(2), {}, {}, False)
            ),
        )
        instructions, _, _ = request.prompt.partition(tool_handoff.MARKER)
        self.assertIn("RESOLVER GUIDANCE (resolve_repair)", instructions)
        self.assertIn(publish_guard.RULE, instructions)
        self.assertEqual("Build mapreduce", packet_of(tool_handoff.with_capture_command(request.prompt))["request"])

    def test_other_stages_and_settled_guidance_leave_the_prompt_unchanged(self):
        request = ModelRequest("builder", "builder", handoff_prompt(), {}, {}, True)
        self.assertIs(request, resolve_repair_job.with_guidance(guided_state(), "builder", request))
        state = guided_state()
        state["resolve_repair_request"]["in_force"] = False
        self.assertIs(request, resolve_repair_job.with_guidance(state, "draft_plan", request))


class NoInjectorAppendsAfterTheHandoffTests(unittest.TestCase):
    def test_no_tool_adds_text_after_a_built_request_prompt(self):
        """A built ``<request>.prompt`` takes added text through ``tool_handoff.before_handoff`` only."""
        offenders = []
        for path in sorted(TOOLS.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                appended = (
                    isinstance(node, ast.BinOp)
                    and isinstance(node.op, ast.Add)
                    and isinstance(node.left, ast.Attribute)
                    and node.left.attr == "prompt"
                ) or (
                    isinstance(node, ast.AugAssign)
                    and isinstance(node.op, ast.Add)
                    and isinstance(node.target, ast.Attribute)
                    and node.target.attr == "prompt"
                )
                if appended:
                    offenders.append(f"{path.relative_to(TOOLS)}:{node.lineno}")
        self.assertEqual([], offenders, "use tool_handoff.before_handoff to add text to a stage prompt")


if __name__ == "__main__":
    unittest.main()
