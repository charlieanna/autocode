"""One response-language policy reaches every stage without rewriting its handoff."""

import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_role_launch as launch
import autocode_tool_handoff as handoff


class ResponseLanguageLaunchTests(unittest.TestCase):
    def prepared(self, prompt, *, stage="draft_plan", report_only=False, native=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schema = root / "schema.json"
            schema.write_text('{"type":"object"}')
            with patch.object(launch.support, "review_generation_schema", side_effect=lambda value, *_: value):
                returned_schema, prepared = launch.prepare_schema_and_report_check(
                    {"settings": {}},
                    stage,
                    root,
                    root,
                    root / "attempt",
                    schema,
                    prompt,
                    report_only,
                    True,
                    native,
                    Mock(),
                )
            self.assertIsNotNone(returned_schema)
            return prepared

    def test_every_stage_and_its_report_repair_get_the_same_language_policy(self):
        packet = json.dumps(
            {"task": "Preserve greet('李雷') and the literal café.", "source_quote": "你好"}, ensure_ascii=False
        )
        original = "The stage instruction.\n" + handoff.MARKER + packet
        stages = (
            "requirements_gather",
            "draft_plan",
            "revise_plan",
            "review_plan",
            "finalize_plan",
            "plan_task",
            "builder",
            "tester",
            "review_checkpoint",
            "review_completion",
            "resolve",
            "investigate_stuck",
            "check_design",
        )
        policies = []
        for stage in stages:
            for report_only in (False, True):
                for native in (None, object()):
                    with self.subTest(stage=stage, report_only=report_only, native=native is not None):
                        prepared = self.prepared(original, stage=stage, report_only=report_only, native=native)
                        instructions, marker, retained = prepared.partition(handoff.MARKER)
                        self.assertEqual(handoff.MARKER, marker)
                        self.assertEqual(
                            packet, retained, "Language guidance must not translate or rewrite source data"
                        )
                        self.assertEqual(1, instructions.count("RESPONSE LANGUAGE"))
                        self.assertIn("English", instructions)
                        self.assertIn("explicitly requests another response language", instructions)
                        self.assertIn("approved", instructions)
                        policies.append(instructions)
        self.assertTrue(all(policy == policies[0] for policy in policies))

    def test_explicit_language_requests_and_multilingual_literals_reach_the_model_unchanged(self):
        for task in (
            "Please answer in Spanish. Keep the literal 你好 unchanged.",
            "Write a Spanish README, but explain your plan in English.",
        ):
            with self.subTest(task=task):
                packet = json.dumps({"task": task}, ensure_ascii=False)
                prepared = self.prepared("Plan this.\n" + handoff.MARKER + packet)
                self.assertEqual(packet, prepared.partition(handoff.MARKER)[2])
                self.assertIn("explicitly requests another response language", prepared.partition(handoff.MARKER)[0])

    def test_repeated_preparation_does_not_duplicate_the_policy_or_trust_a_quoted_heading(self):
        packet = json.dumps({"source_quote": "RESPONSE LANGUAGE is a literal heading, not a preference."})
        original = "Prepare a report.\n" + handoff.MARKER + packet
        once = self.prepared(original)
        self.assertEqual(once, self.prepared(once))
        self.assertEqual(1, once.partition(handoff.MARKER)[0].count("RESPONSE LANGUAGE"))

    def test_markerless_requests_receive_the_policy_without_changing_their_existing_instruction(self):
        original = "Return exactly the required report."
        prepared = self.prepared(original, report_only=True)
        self.assertTrue(prepared.startswith(original))
        self.assertIn("RESPONSE LANGUAGE", prepared)
        self.assertNotIn(handoff.MARKER, prepared)

    def test_packaged_runtime_loads_the_same_versioned_policy(self):
        package_name = "isolated_response_language_package"
        package = types.ModuleType(package_name)
        package.__path__ = [str(Path(launch.__file__).parent)]
        with patch.dict(sys.modules, {package_name: package}):
            packaged = importlib.import_module(package_name + ".autocode_response_language")
            original = "Keep the exact literal café."
            self.assertEqual(self.prepared(original), packaged.apply(original))
            self.assertIn("RESPONSE LANGUAGE", packaged.apply(original))
