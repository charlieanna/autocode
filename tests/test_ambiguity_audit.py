"""Requirements' ambiguity audit is held to its refs, consistently with its open questions (board #11 P1.2)."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode_ambiguity_audit as audit
import autocode_util as util
from units import autoplanner


def entry(status, *refs):
    return {"status": status, "refs": list(refs)}


class AmbiguityAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve() / "project"
        self.workspace.mkdir()
        (self.workspace / "cli.py").write_text("import sys\n\ndef main():\n    sys.exit(2)\n")
        (self.workspace / "README.md").write_text("# cli\n")
        events = self.workspace / ".autocode" / "events.jsonl"
        events.parent.mkdir()
        events.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "read",
                        "type": "command_execution",
                        "command": "cat cli.py",
                        "exit_code": 0,
                        "aggregated_output": "import sys\n",
                    },
                }
            )
            + "\n"
        )
        self.state = {
            "workspace": str(self.workspace),
            "run_dir": str(self.workspace / ".autocode"),
            "task": "Add a --json flag to cli.py",
            "answers": {"Q0": {"answer": "yes"}},
            "user_events": [{"id": "E1", "kind": "brief_feedback"}],
            "stages": [{"stage": "requirements_gather", "events": str(events)}],
        }
        self.report = {
            "requirements": [{"id": "R1", "text": "Add --json", "source_quote": "Add a --json flag"}],
            "open_questions": [
                {"id": "Q1", "kind": "decision", "category": "behavior", "question": "Empty input?"},
                {"id": "Q2", "kind": "decision", "category": "technical", "question": "Which parser?"},
            ],
            audit.FIELD: {
                "outcome_scope": entry("specified_in_source", "R1", "task"),
                "inputs_and_edge_cases": entry("open_question", "Q1"),
                "error_and_failure_behavior": entry("repo_convention", "cli.py:4"),
                "compatibility_and_callers": entry("specified_in_source", "Q0", "E1"),
                "ux_and_output_contract": entry("not_applicable"),
                "state_and_side_effects": entry("not_applicable"),
            },
        }

    def problems(self, report):
        try:
            audit.validate(self.state, report)
        except ValueError as error:
            return str(error)
        return None

    def changed(self, dimension, value):
        report = copy.deepcopy(self.report)
        report[audit.FIELD][dimension] = value
        return report

    def test_a_supported_audit_and_a_report_without_one_are_accepted(self):
        self.assertIsNone(self.problems(self.report))
        legacy = {key: value for key, value in self.report.items() if key != audit.FIELD}
        self.assertIsNone(self.problems(legacy))

    def test_each_status_is_held_to_its_refs(self):
        cases = {
            "names Q9, which are not open_questions": self.changed(
                "inputs_and_edge_cases", entry("open_question", "Q9")
            ),
            "is open_question but cites nothing": self.changed("inputs_and_edge_cases", entry("open_question")),
            "cites R9: cite a requirement id": self.changed("outcome_scope", entry("specified_in_source", "R9")),
            "which this stage never read": self.changed(
                "error_and_failure_behavior", entry("repo_convention", "README.md")
            ),
            "points past the end": self.changed("error_and_failure_behavior", entry("repo_convention", "cli.py:40")),
            "is not a file in the workspace": self.changed(
                "error_and_failure_behavior", entry("repo_convention", "the usual convention")
            ),
            "has no status": self.changed("state_and_side_effects", entry("assumed")),
        }
        for expected, report in cases.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, self.problems(report) or "")

    def test_every_decision_question_is_named_by_an_open_dimension(self):
        report = self.changed("inputs_and_edge_cases", entry("not_applicable"))
        self.assertIn("open_questions Q1 ask the person to decide", self.problems(report))
        report["open_questions"][0]["kind"] = "discoverable"
        self.assertIsNone(self.problems(report), "a discoverable or technical question is not the person's")

    def test_the_audit_must_state_exactly_the_six_dimensions(self):
        report = copy.deepcopy(self.report)
        del report[audit.FIELD]["state_and_side_effects"]
        self.assertIn("exactly these dimensions", self.problems(report))

    def test_greenfield_conventions_have_nothing_to_read(self):
        empty = self.workspace.parent / "empty"
        empty.mkdir()
        self.state["workspace"] = str(empty)
        self.assertIsNone(self.problems(self.report))

    def test_the_schema_offers_the_audit_and_five_questions_on_the_wire_but_keeps_saved_reports_valid(self):
        schema = autoplanner.SCHEMAS["requirements_gather"]
        self.assertEqual(audit.SCHEMA, schema["properties"][audit.FIELD])
        self.assertNotIn(audit.FIELD, schema.get("required", []))
        self.assertEqual(5, schema["properties"]["open_questions"]["maxItems"])
        wire = util.model_output_schema(schema)
        self.assertIn(audit.FIELD, wire["required"])
        self.assertEqual(list(audit.DIMENSIONS), wire["properties"][audit.FIELD]["required"])
        self.assertEqual(list(audit.STATUSES), audit.ENTRY["properties"]["status"]["enum"])


if __name__ == "__main__":
    unittest.main()
