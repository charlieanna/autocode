"""Compact prompt schemas retain the full output contract."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from providers import command, opencode


class CompactSchemaPromptTests(unittest.TestCase):
    def test_standalone_review_keeps_its_schema_and_boundary_without_build_receipt_work(self):
        import autocode_review_job as review

        original, _ = review.prompt({"task": "Review greeting.patch", "workspace": "/project"})
        packet = original.split("\nCURRENT HANDOFF DATA\n", 1)[1]
        rendered = opencode.prompt_for_schema(original, review.SCHEMA, Path("/run/events.jsonl"))
        configured = command.CommandProvider(
            {"name": "fixture-kilo", "roles": {}, "output": "opencode_events"}, Path("unused.toml")
        )
        self.assertEqual(rendered, configured.prompt_for_schema(original, review.SCHEMA, Path("/run/events.jsonl")))
        self.assertIn("Review report only.", rendered)
        self.assertIn(json.dumps(review.SCHEMA, separators=(",", ":")), rendered)
        self.assertEqual(packet, rendered.split("\nCURRENT HANDOFF DATA\n", 1)[1])
        self.assertIn("runner independently proves", rendered)
        self.assertIn("strict\nfilesystem boundary", rendered)
        self.assertIn("Never write, truncate", rendered)
        self.assertIn("/run/events.jsonl", rendered)
        self.assertNotIn("For a captured Validator check", rendered)
        self.assertNotIn("A PASS report", rendered)
        self.assertNotIn("capture_command", rendered)
        self.assertIn("every blocking finding", rendered)
        # A report that actually declares build evidence must retain its protocol,
        # even when it has all the review fields too.
        schema = {**review.SCHEMA, "properties": {**review.SCHEMA["properties"], "checks": {"type": "array"}}}
        mixed = opencode.prompt_for_schema(original, schema, Path("/run/events.jsonl"))
        self.assertIn("For a captured Validator check", mixed)
        self.assertIn("capture_command", mixed)

    def test_review_verification_is_focused_fresh_and_never_discards_requested_or_blocking_proof(self):
        import autocode_review_job as review

        task = "Review HEAD^..HEAD. Run python -m unittest tests.test_calc once; independent CI runs the full affected suite."
        original, _ = review.prompt({"task": task, "workspace": "/project"})
        rendered = opencode.prompt_for_schema(original, review.SCHEMA, Path("/run/events.jsonl"))
        packet = json.loads(rendered.split("\nCURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(task, packet["task"])
        for rule in (
            "named verification commands early, once",
            "source and check inputs stay unchanged",
            "old supplied logs are",
            "concrete suspected regression",
            "request requires it",
            "demonstrated affected consumer",
            "Retain every requested check",
            "including failures",
            "Batch independent diff",
            "necessary remainder of truncated output",
            "test that source without applying it again",
            "Prove every blocking finding with a test",
            "each blocking finding's test must FAIL",
            "runner independently proves",
            "only the runner can exempt a proven pre-existing observation",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, rendered)
        self.assertLess(
            rendered.index("named verification commands early, once"), rendered.index("Read the change AND")
        )
        self.assertNotIn("run the test suite there", rendered)
        self.assertIn(json.dumps(review.SCHEMA, separators=(",", ":")), rendered)

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
                "settings": {"roles": {"sol": {"engine": "opencode", "model": "fixture"}}, "limits": {}},
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
                    state, "sol", workspace / "state.json", Path(support.__file__).parent / "autocode-schemas"
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

    def test_a_verdict_report_with_assistant_text_keeps_execution_evidence_rules(self):
        schema = {"properties": {"assistant_reply": {"type": "string"}, "checks": {"type": "array"}}}
        prompt = opencode.prompt_for_schema("Task\nCURRENT HANDOFF DATA\n{}", schema, Path("/run/events.jsonl"))
        self.assertIn("A PASS report", prompt)

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


if __name__ == "__main__":
    unittest.main()
