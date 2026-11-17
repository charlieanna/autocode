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

    def test_a_status_that_disagrees_with_its_refs_names_the_status_to_use(self):
        # A live Requirements report (2026-11-16) cited files under specified_in_source; told only that the
        # refs were wrong, both repairs deleted entries instead of relabeling them.
        code = self.problems(self.changed("inputs_and_edge_cases", entry("specified_in_source", "cli.py:4", "R9")))
        self.assertIn("cli.py:4 is code this stage read, which a repo_convention dimension cites", code)
        self.assertNotIn("R9 is code", code)
        unread = self.problems(self.changed("inputs_and_edge_cases", entry("specified_in_source", "README.md")))
        self.assertNotIn("repo_convention", unread, "code the stage never read is not offered as a convention")
        sourced = self.problems(self.changed("error_and_failure_behavior", entry("repo_convention", "R1", "Q0")))
        self.assertIn("R1, Q0 are source ids, which a specified_in_source dimension cites", sourced)
        self.assertIsNone(self.problems(self.changed("ux_and_output_contract", entry("repo_convention", "cli.py:4"))))

    def test_every_decision_question_is_named_by_an_open_dimension(self):
        report = self.changed("inputs_and_edge_cases", entry("not_applicable"))
        self.assertIn("open_questions Q1 ask the person to decide", self.problems(report))
        report["open_questions"][0]["kind"] = "discoverable"
        self.assertIsNone(self.problems(report), "a discoverable or technical question is not the person's")

    def test_tier1_missing_intent_puts_requested_outcome_question_under_outcome_scope(self):
        tier1 = {
            "requirements": [],
            "open_questions": [
                {"id": "Q1", "kind": "decision", "category": "requested_outcome", "question": "What should happen?"}
            ],
            audit.FIELD: {dimension: entry("not_applicable") for dimension in audit.DIMENSIONS},
        }
        self.assertIn("open_questions Q1 ask the person to decide", self.problems(tier1))
        tier1[audit.FIELD]["outcome_scope"] = entry("open_question", "Q1")
        self.assertIsNone(self.problems(tier1))

    def test_source_inventory_linked_sources_and_pinned_catalog_refs_are_accepted(self):
        import hashlib
        from unittest import mock

        import autocode_catalog_context as source_catalog
        import autocode_source_log as source_log

        design = self.workspace / "docs" / "spec.md"
        design.parent.mkdir()
        design.write_text("# Spec\nReturn JSON.\n")
        state = {
            **self.state,
            "task": "Implement docs/spec.md for https://github.com/acme/app/issues/42",
        }
        issue_record = {
            "id": "issue:acme:app:42",
            "kind": source_log.linked_issues.LINKED_ISSUE_KIND,
            "parent_id": "task:0",
            "url": "https://github.com/acme/app/issues/42",
            "text": "Issue body",
            "sha256": hashlib.sha256(b"Issue body").hexdigest(),
        }
        with mock.patch.object(
            source_log.linked_issues,
            "fetch_linked_records",
            side_effect=lambda _ws, parent_id, *_args, **_kw: [issue_record] if parent_id == "task:0" else [],
        ):
            source_log.seed(state)
            source_log.save_answer(
                state, "Q0", {"id": "A0", "kind": "answer", "actor": "user_cli", "question_id": "Q0", "text": "yes"}
            )
            source_log.save_feedback(
                state, {"id": "E1", "kind": "brief_feedback", "actor": "user_cli", "text": "keep v1"}
            )
        report = {
            "requirements": [],
            "open_questions": [],
            audit.FIELD: {
                "outcome_scope": entry("specified_in_source", "task:0"),
                "inputs_and_edge_cases": entry("specified_in_source", "answer:Q0", "feedback:E1"),
                "error_and_failure_behavior": entry("specified_in_source", "issue:acme:app:42"),
                "compatibility_and_callers": entry("specified_in_source", "design:docs:spec.md"),
                "ux_and_output_contract": entry("specified_in_source", "catalog/repo-a/api.py:1"),
                "state_and_side_effects": entry("not_applicable"),
            },
        }
        with mock.patch.object(
            source_catalog,
            "evidence",
            side_effect=lambda _state, ref: {"path": ref} if ref == "catalog/repo-a/api.py:1" else None,
        ):
            self.assertIsNone(audit.validate(state, report))
            bad = copy.deepcopy(report)
            bad[audit.FIELD]["ux_and_output_contract"] = entry("specified_in_source", "catalog/repo-a/missing.py:1")
            with self.assertRaisesRegex(ValueError, "catalog/repo-a/missing.py:1"):
                audit.validate(state, bad)

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
