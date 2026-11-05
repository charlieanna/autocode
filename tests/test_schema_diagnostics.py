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

    def test_every_violation_is_named_at_once(self):
        # A Requirements report missing acceptance_tests and an assumption's text took two
        # repairs when only the first violation was named.
        schema = {
            "type": "object",
            "required": ["acceptance_tests", "proposed_assumptions"],
            "properties": {
                "acceptance_tests": {"type": "array"},
                "proposed_assumptions": {
                    "type": "array",
                    "items": {"type": "object", "required": ["id", "text"], "properties": {"id": {"type": "string"}}},
                },
                "clarity": {"type": "string", "enum": ["clear", "unclear"]},
            },
        }

        with self.assertRaises(ValueError) as caught:
            validate_schema({"proposed_assumptions": [{"id": "A1"}, {"text": "x"}], "clarity": 3}, schema)

        self.assertEqual(
            str(caught.exception),
            "$: missing acceptance_tests; $.proposed_assumptions[0]: missing text; "
            "$.proposed_assumptions[1]: missing id; $.clarity: expected string",
        )

    def test_a_type_mismatch_is_not_also_checked_inside(self):
        schema = {"type": "object", "properties": {"rows": {"type": "array", "minItems": 1}}}

        with self.assertRaisesRegex(ValueError, r"^\$\.rows: expected array$"):
            validate_schema({"rows": {"a": 1}}, schema)

    def test_a_long_list_of_violations_is_bounded(self):
        schema = {"type": "array", "items": {"type": "string"}}

        with self.assertRaises(ValueError) as caught:
            validate_schema(list(range(25)), schema)

        message = str(caught.exception)
        self.assertTrue(message.startswith("$[0]: expected string; $[1]: expected string"))
        self.assertTrue(message.endswith("$[19]: expected string; and 5 more"))

    def test_a_valid_value_passes(self):
        validate_schema({"a": ["x"]}, {"type": "object", "required": ["a"], "properties": {"a": {"type": "array"}}})


if __name__ == "__main__":
    unittest.main()
