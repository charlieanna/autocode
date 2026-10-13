"""Provider wire schemas must not include OpenAI-rejected uniqueItems."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import autocode_brief_acceptance as brief_acceptance
import autocode_bug_job as bug_job
import autocode_design_check_job as design_check_job
import autocode_design_job as design_job
import autocode_discuss_job as discuss_job
import autocode_review_job as review_job
import autocode_risk_acceptance as risk_acceptance
import autocode_role_schema as role_schema
import autocode_stuck_job as stuck_job
from autocode_util import model_output_schema
from units import autoplanner

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "tools" / "autocode-schemas"


def _has_unique_items(node) -> bool:
    if isinstance(node, dict):
        if "uniqueItems" in node:
            return True
        return any(_has_unique_items(child) for child in node.values())
    if isinstance(node, list):
        return any(_has_unique_items(child) for child in node)
    return False


class ModelOutputSchemaTests(unittest.TestCase):
    def test_strips_unique_items_and_keeps_other_array_keywords(self):
        schema = {
            "type": "object",
            "properties": {
                "brief_observations": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "uniqueItems": True,
                    "items": {
                        "type": "object",
                        "properties": {
                            "criterion_ids": {
                                "type": "array",
                                "minItems": 1,
                                "items": {"type": "string"},
                                "uniqueItems": True,
                            },
                            "module": {"type": "string", "pattern": r"^[A-Za-z]+$"},
                        },
                    },
                }
            },
        }
        strict = model_output_schema(schema)
        obs = strict["properties"]["brief_observations"]
        self.assertNotIn("uniqueItems", obs)
        self.assertEqual(1, obs["minItems"])
        self.assertEqual(8, obs["maxItems"])
        ids = obs["items"]["properties"]["criterion_ids"]
        self.assertNotIn("uniqueItems", ids)
        self.assertEqual(1, ids["minItems"])
        self.assertEqual(r"^[A-Za-z]+$", obs["items"]["properties"]["module"]["pattern"])
        self.assertEqual(strict["required"], ["brief_observations"])
        self.assertFalse(strict["additionalProperties"])
        self.assertTrue(schema["properties"]["brief_observations"]["uniqueItems"])
        self.assertTrue(
            schema["properties"]["brief_observations"]["items"]["properties"]["criterion_ids"]["uniqueItems"]
        )

    def test_every_model_facing_schema_strips_unique_items(self):
        schemas = []
        for name, schema in autoplanner.SCHEMAS.items():
            schemas.append((f"autoplanner.{name}", schema))
        for path in sorted((SCHEMA_DIR / "v2").glob("*.schema.json")):
            raw = json.loads(path.read_text())
            for role in ("astra", "terra", "sol", "glm"):
                try:
                    schemas.append((f"{path.name}:{role}", role_schema.role_schema(raw, role)))
                except (KeyError, ValueError, TypeError):
                    continue
        for name, schema in (
            ("bug_job", bug_job.SCHEMA),
            ("discuss_job", discuss_job.SCHEMA),
            ("review_job", review_job.SCHEMA),
            ("design_check_job", design_check_job.SCHEMA),
            ("design_job.REVISION_SCHEMA", design_job.REVISION_SCHEMA),
            ("stuck_job", stuck_job.SCHEMA),
        ):
            schemas.append((name, schema))
        for name, schema in schemas:
            with self.subTest(schema=name):
                self.assertFalse(_has_unique_items(model_output_schema(schema)), name)

    def test_duplicate_criterion_ids_still_rejected_after_strip(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            brief_acceptance.normalized_proposal(
                {
                    "id": "D1",
                    "program": ["python", "todo.py"],
                    "commands": [["add", "*"]],
                },
                {
                    "declaration_id": "D1",
                    "criterion_ids": ["AC1", "AC1"],
                    "steps": [{"argv": ["add", "milk"]}],
                    "observe_step": 0,
                    "bindings": [],
                },
            )
        with self.assertRaisesRegex(ValueError, "unique"):
            risk_acceptance._proposal({"declaration_id": "R1", "criterion_ids": ["AC1", "AC1"], "module": "timesheet"})


if __name__ == "__main__":
    unittest.main()
