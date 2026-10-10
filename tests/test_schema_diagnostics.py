"""Closed-schema rejections identify the fields a report must repair."""

import unittest

from autocode_util import validate_schema


class SchemaDiagnosticTests(unittest.TestCase):
    def test_root_rejection_names_misplaced_initial_task(self):
        schema = {
            "type": "object",
            "properties": {"contract": {"type": "object"}},
            "required": ["contract"],
            "additionalProperties": False,
        }

        with self.assertRaisesRegex(ValueError, r"^\$: unexpected fields: initial_task$"):
            validate_schema({"contract": {}, "initial_task": {}}, schema)

    def test_multiple_unexpected_fields_are_sorted(self):
        schema = {"type": "object", "properties": {}, "additionalProperties": False}

        with self.assertRaisesRegex(ValueError, r"^\$: unexpected fields: access_blockers, machine_resolutions$"):
            validate_schema({"machine_resolutions": [], "access_blockers": []}, schema)

    def test_nested_rejection_keeps_object_and_array_path(self):
        schema = {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"rationale": {"type": "string"}},
                        "additionalProperties": False,
                    },
                }
            },
        }

        with self.assertRaisesRegex(ValueError, r"^\$\.decisions\[0\]: unexpected fields: evidence_refs$"):
            validate_schema({"decisions": [{"rationale": "Existing file supports this", "evidence_refs": []}]}, schema)


if __name__ == "__main__":
    unittest.main()
