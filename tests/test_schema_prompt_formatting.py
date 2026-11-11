"""Compact prompt schemas retain the full output contract."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_goals as goals
from autocode_util import validate_schema
from providers import opencode
from units import autoplanner


class CompactSchemaPromptTests(unittest.TestCase):
    def test_the_contract_prefix_is_identical_across_attempts_of_a_stage(self):
        # A2 (#2113): the per-attempt events-log path lives in the handoff, so the instruction
        # prefix a provider can reuse is byte-identical between attempts of the same stage.
        from providers import claude

        schema = {"type": "object", "properties": {"checks": {"type": "array"}}}
        base = "Do the work.\nCURRENT HANDOFF DATA\n" + json.dumps({"workspace": "/ws"})
        for name, render in (("opencode", opencode.prompt_for_schema), ("claude", claude.prompt_for_schema)):
            with self.subTest(provider=name):
                first = render(base, schema, Path("/run/iterations/001/builder-01.jsonl"))
                second = render(base, schema, Path("/run/iterations/002/builder-02.jsonl"))
                prefix_a, _, packet_a = first.partition("\nCURRENT HANDOFF DATA\n")
                prefix_b, _, packet_b = second.partition("\nCURRENT HANDOFF DATA\n")
                self.assertEqual(prefix_a, prefix_b, "instruction prefix must not name the attempt path")
                self.assertNotIn("builder-01.jsonl", prefix_a)
                self.assertEqual("/run/iterations/001/builder-01.jsonl", json.loads(packet_a)["stage_events_log"])
                self.assertEqual("/run/iterations/002/builder-02.jsonl", json.loads(packet_b)["stage_events_log"])

    def test_compiled_validator_prefers_receipt_fields_without_retyping_commands(self):
        import autocode_support as support
        from units import common

        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            (workspace / "answer.py").write_text("def answer(): return 42\n")
            state = {
                "version": 2,
                "task": "Verify the answer without changing the source",
                "workspace": str(workspace),
                "settings": {"roles": {"tester": {"engine": "opencode", "model": "fixture"}}, "limits": {}},
            }
            # Approval and filesystem identity are outside this prompt contract;
            # assemble the real request and role schema without a controller or provider.
            with (
                patch.object(common.goals, "execution_guard"),
                patch.object(
                    common.stage_context.source_scope, "snapshot", return_value={"revision": "s", "head": "h"}
                ),
            ):
                request = common.execution_request(
                    state, "tester", workspace / "state.json", Path(support.__file__).parent / "autocode-schemas"
                )
            schema = request.schema
            prompt = opencode.prompt_for_schema(request.prompt, schema, workspace / "events.jsonl")

        self.assertIn("independent read-only validation agent", prompt)
        self.assertIn("For a captured Validator check, prefer command null and exit_code null", prompt)
        self.assertIn("If either field is supplied, it must exactly match the receipt", prompt)
        self.assertIn("Other captured check reports must copy these exact values", prompt)
        self.assertNotIn("For a captured check, checks[].command must equal", prompt)
        self.assertIn("Never create or edit a receipt manually", prompt)
        self.assertIn("A PASS report must list at least one successful executed check", prompt)
        support.validate_schema(
            {"command": None, "exit_code": None, "evidence_ref": ".autocode/evidence/check.json"},
            schema["properties"]["checks"]["items"],
        )
        import autocode_changed_code_review as changed_code_review

        # The prompt copy renders the attempt-specific property last (A3, #2113); the bound schema keeps its order.
        self.assertIn(json.dumps(changed_code_review.prompt_order(schema), separators=(",", ":")), prompt)

    def test_conversation_keeps_the_schema_and_boundary_without_execution_evidence_instructions(self):
        schema = {"type": "object", "properties": {"assistant_reply": {"type": "string"}}}
        prompt = opencode.prompt_for_schema("Task\nCURRENT HANDOFF DATA\n{}", schema, Path("/run/events.jsonl"))
        self.assertIn(json.dumps(schema, separators=(",", ":")), prompt)
        self.assertIn("assistant_reply first", prompt)
        self.assertIn("filesystem boundary", prompt)
        self.assertIn("Never modify this log", prompt)
        self.assertIn("/run/events.jsonl", prompt)
        self.assertNotIn("A PASS report", prompt)
        self.assertNotIn("checks[].command", prompt)

    def test_planning_keeps_full_schema_without_execution_or_private_log_instructions(self):
        schema = {"properties": {"summary": {"type": "string"}, "concerns": {"type": "array"}}}
        packet = {"joint_planning": True, "goal_contract": {"task": "retained"}}
        original = "Review the plan\nCURRENT HANDOFF DATA\n" + json.dumps(packet)
        prompt = opencode.prompt_for_schema(original, schema, Path("/private-run/events.jsonl"))
        self.assertIn(json.dumps(schema, separators=(",", ":")), prompt)
        self.assertEqual(packet, json.loads(prompt.split("\nCURRENT HANDOFF DATA\n")[1]))
        self.assertNotIn("A PASS report", prompt)
        self.assertNotIn("checks[].command", prompt)
        self.assertNotIn("/private-run/events.jsonl", prompt)
        self.assertNotIn("--output .autocode/evidence", prompt)
        self.assertIn("strict filesystem boundary", prompt)
        self.assertIn("without tools", prompt)
        self.assertIn("missing evidence as a concern", prompt)
        instruction = " ".join(prompt.split("\nCURRENT HANDOFF DATA\n", 1)[0].split())
        self.assertIn("Use in-memory inputs for file-shaped examples", instruction)
        self.assertIn("leave the execution to the Tester", instruction)
        self.assertIn("copy the literal from the current handoff", instruction)
        self.assertIn("compare the complete strings in one read-only computation", instruction)

    def test_a_verdict_report_with_assistant_text_keeps_execution_evidence_rules(self):
        schema = {"properties": {"assistant_reply": {"type": "string"}, "checks": {"type": "array"}}}
        prompt = opencode.prompt_for_schema("Task\nCURRENT HANDOFF DATA\n{}", schema, Path("/run/events.jsonl"))
        self.assertIn("A PASS report", prompt)
        self.assertIn("submit them together as separate capture tool calls", prompt)
        self.assertIn("keep a unique receipt for each command", prompt)

    def test_schema_prompt_is_compact_and_round_trips_exactly(self):
        path = Path(__file__).resolve().parents[1] / "tools/autocode-schemas/v2/sol-report.schema.json"
        schema = json.loads(path.read_text())
        prompt = opencode.prompt_for_schema("Task\nCURRENT HANDOFF DATA\n{}", schema, Path("/run/events.jsonl"))
        prefix = prompt.split("\nCURRENT HANDOFF DATA\n", 1)[0]
        contract = prefix.index("\nOPENCODE OUTPUT CONTRACT\n")
        start = prefix.index("\n{", contract) + 1
        rendered, end = json.JSONDecoder().raw_decode(prefix[start:])
        rendered_json = prefix[start : start + end]

        self.assertEqual(schema, rendered)
        self.assertEqual(json.dumps(schema, separators=(",", ":")), rendered_json)
        self.assertLess(len(rendered_json), len(json.dumps(schema, indent=2)))


class AttemptSpecificSchemaLastTests(unittest.TestCase):
    """A3 (#2113): the attempt-specific schema property renders last in the provider's prompt copy.

    A schema read back from state.json is alphabetized by the JSON writer, so
    `changed_code_exceptions` sorts to the front of the prompt prefix; the
    prompt copy alone must render it last without touching the saved schema.
    """

    FIELD = "changed_code_exceptions"

    def bound_schema(self) -> dict:
        path = Path(__file__).resolve().parents[1] / "tools/autocode-schemas/v2/sol-report.schema.json"
        schema = json.loads(path.read_text())
        import autocode_changed_code_review as changed_code_review

        properties = {self.FIELD: copy.deepcopy(changed_code_review.SCHEMA)}
        properties.update(schema["properties"])
        return {**schema, "properties": properties}

    def rendered_schema(self, prompt: str) -> tuple[dict, str]:
        prefix = prompt.split("\nCURRENT HANDOFF DATA\n", 1)[0]
        decoder = json.JSONDecoder()
        for index in reversed([match for match in range(len(prefix)) if prefix.startswith("\n{", match)]):
            try:
                rendered, end = decoder.raw_decode(prefix[index + 1 :])
            except ValueError:
                continue
            if isinstance(rendered, dict) and "properties" in rendered:
                return rendered, prefix[index + 1 : index + 1 + end]
        raise AssertionError("no report schema found in the prompt prefix")

    def check_prompt_order(self, decorate):
        schema = self.bound_schema()
        before = copy.deepcopy(schema)
        prompt = decorate("Task\nCURRENT HANDOFF DATA\n{}", schema, Path("/run/events.jsonl"))
        rendered, rendered_json = self.rendered_schema(prompt)
        self.assertEqual(before, rendered)
        self.assertEqual(list(before["properties"]), list(schema["properties"]))
        self.assertEqual(self.FIELD, list(rendered["properties"])[-1])
        self.assertGreater(rendered_json.index('"findings"'), -1)
        self.assertIn(json.dumps(before, separators=(",", ":")).encode()[:40].decode(), rendered_json[:200])
        # A schema without the attempt-specific field renders byte-for-byte as before.
        plain = {"type": "object", "properties": {"summary": {"type": "string"}}}
        self.assertIn(
            json.dumps(plain, separators=(",", ":")),
            decorate("Task\nCURRENT HANDOFF DATA\n{}", plain, Path("/run/events.jsonl")),
        )

    def test_opencode_renders_the_attempt_specific_property_last(self):
        self.check_prompt_order(opencode.prompt_for_schema)

    def test_claude_renders_the_attempt_specific_property_last(self):
        import providers.claude as claude

        self.check_prompt_order(claude.prompt_for_schema)


class RequirementsPromptModeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        (self.workspace / "reports.py").write_text("def export():\n    return []\n")
        self.task = 'Add teh CSV export; keep literal "MiXeD" and do not remove the approval step.'

    def request(self, *, conversational=False, joint=True):
        state = {
            "version": 3,
            "task_id": "requirements-mode-fixture",
            "task": self.task,
            "workspace": str(self.workspace),
            "status": "RUNNING",
            "stages": [],
            "answers": {},
            "user_events": [],
            "settings": {
                "joint_planning": joint,
                "spec_first": conversational,
                "roles": {
                    name: {"engine": "opencode", "model": "fixture"}
                    for name in (
                        "task_planner",
                        "planner",
                        "builder",
                        "tester",
                        "requirements",
                        "plan_reviewer",
                        "resolver",
                    )
                },
            },
        }
        with patch.object(
            autoplanner.stage_context.source_scope,
            "snapshot",
            return_value={"head": "fixture-head", "revision": "fixture-source", "files": {}},
        ):
            request = autoplanner.prepare(
                state,
                "requirements_gather",
                self.workspace / "state.json",
                Path(autoplanner.__file__).parents[1] / "autocode-schemas",
            )
        rendered = opencode.prompt_for_schema(request.prompt, request.schema, self.workspace / "events.jsonl")
        return state, request, rendered

    def assert_complete_render(self, request, rendered):
        self.assertEqual(1, request.prompt.count("You are the Requirements Gatherer"))
        self.assertEqual(1, rendered.count("You are the Requirements Gatherer"))
        for slot in ("{conversation_intro}", "{reply_prefix}", "{confirmation_target}"):
            self.assertNotIn(slot, rendered)
        self.assertIn(json.dumps(request.schema, separators=(",", ":")), rendered)
        packet = json.loads(rendered.split("\nCURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(self.task, packet["task"])
        instruction = rendered.split("\nCURRENT HANDOFF DATA\n", 1)[0]
        self.assertEqual(1, instruction.count("clear, correctly spelled English"))
        self.assertIn("instead of quoting their typos or shorthand", instruction)
        self.assertIn("literal values, scope, negation", instruction)
        self.assertIn("source_quote evidence verbatim", instruction)
        self.assertIn("Do not imply that this approves an execution plan.", instruction)

    def test_batch_requirements_prompt_obeys_the_final_model_schema(self):
        state, request, rendered = self.request()
        self.assertIn("assistant_reply", autoplanner.schema_for(state, "requirements_gather")["properties"])
        self.assertNotIn("assistant_reply", request.schema["properties"])
        self.assertFalse(request.schema["additionalProperties"])
        self.assertFalse(
            "assistant_reply" in rendered.split("\nCURRENT HANDOFF DATA\n", 1)[0],
            "batch instructions request a field excluded by the final model schema",
        )
        self.assertIn("summarize what you understand in summary", rendered)
        self.assert_complete_render(request, rendered)

    def test_conversational_requirements_keeps_first_reply_and_original_user_evidence(self):
        _, request, rendered = self.request(conversational=True)
        self.assertIn("assistant_reply", request.schema["properties"])
        self.assertEqual(1, rendered.count("Write assistant_reply FIRST in your JSON"))
        self.assertIn("Write assistant_reply and natural-language summaries", rendered)
        self.assertIn("summarize what you understand in assistant_reply", rendered)
        # The chat hides a question already asked word for word in the reply; a reworded copy showed twice.
        self.assertIn("put that same sentence, word for word, as the question in open_questions", rendered)
        # A 2026-11-11 confirmation restated the whole fix plan in one paragraph before its question.
        self.assertIn("keep assistant_reply to what you understood in one or\ntwo plain sentences", rendered)
        # The same run quoted the issue it picked as the user's words, and lost its first answer.
        self.assertIn("is not the user's words: when the user asks you to pick the work", rendered)
        self.assert_complete_render(request, rendered)

    def test_final_model_schema_controls_the_prompt_even_when_the_mode_allows_chat(self):
        finalize = autoplanner.retention.model_schema

        def without_reply(schema, state, stage):
            result = copy.deepcopy(finalize(schema, state, stage))
            result["properties"].pop("assistant_reply", None)
            if "assistant_reply" in result.get("required", []):
                result["required"].remove("assistant_reply")
            return result

        with patch.object(autoplanner.retention, "model_schema", side_effect=without_reply):
            _, request, rendered = self.request(conversational=True)
        self.assertNotIn("assistant_reply", request.schema["properties"])
        self.assertFalse(
            "assistant_reply" in rendered.split("\nCURRENT HANDOFF DATA\n", 1)[0],
            "the rendered instructions must follow the attached final schema rather than the run mode",
        )
        self.assert_complete_render(request, rendered)

    def test_nonjoint_discovery_keeps_its_legacy_prompt_and_schema(self):
        state, request, rendered = self.request(joint=False)
        self.assertEqual("DISCOVERING", state["phase"])
        self.assertNotIn("You are the Requirements Gatherer", rendered)
        self.assertNotIn("assistant_reply", request.schema["properties"])
        self.assertNotIn("assistant_reply FIRST", rendered)
        self.assertEqual(set(goals.DISCOVERY_SCHEMA["required"]), set(request.schema["required"]))
        packet = json.loads(rendered.split("\nCURRENT HANDOFF DATA\n", 1)[1])
        self.assertNotIn("joint_planning", packet)
        self.assertEqual("requirements_gather", packet["stage"])

    def test_batch_response_with_an_assistant_reply_is_still_rejected(self):
        from .test_autoplanner_step2 import requirements

        _, request, _ = self.request()
        report = requirements([])
        validate_schema(report, request.schema)
        with self.assertRaisesRegex(ValueError, r"^\$: unexpected fields: assistant_reply$"):
            validate_schema({**report, "assistant_reply": "I understood the request."}, request.schema)


if __name__ == "__main__":
    unittest.main()
