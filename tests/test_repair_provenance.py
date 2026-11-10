"""A bounded report-edit transport retains the original validation contract."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode_repair_provenance as provenance
import autocode_report_edits as edits
import autocode_util as util


class RepairSchemaWitnessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.schema = self.root / "planner.schema.json"
        self.validation = {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        }
        util.atomic_json(self.schema, self.validation)
        self.original_output = self.root / "original.json"
        util.atomic_json(self.original_output, {"summary": "Rejected citation"})
        self.events = self.root / "original.events"
        self.events.write_text('{"type":"thread.started","thread_id":"original-session"}\n')
        self.output = self.root / "repaired.json"
        util.atomic_json(self.output, {"summary": "Accepted citation correction"})
        self.original = {
            "stage": "draft_plan",
            "exit_code": 0,
            "source_revision": "source",
            "role": "planner",
            "iteration": 1,
            "schema": str(self.schema),
            "contract_hash": "contract",
            "criteria_revision": 1,
            "thread_id": "original-session",
            "expected_session": "original-session",
            "supports_sessions": True,
            "output": str(self.original_output),
            "events": str(self.events),
            "rejected": True,
        }
        self.repair = {
            **self.original,
            "stage": "draft_plan_report_repair",
            "original_stage": "draft_plan",
            "report_only": True,
            "rejected": False,
            "applied_original_events": str(self.events),
            "output": str(self.output),
        }
        self.state = {
            "stages": [self.original, self.repair],
            "report_repair_history": [
                {
                    "result": "accepted",
                    "repair": self.repair,
                    "original_output": str(self.original_output),
                    "output_hash": util.file_hash(self.output),
                }
            ],
        }

    def offer(self):
        source = {
            "format": "json",
            "path": str(self.original_output),
            "content": json.loads(self.original_output.read_text()),
            "sha256": util.file_hash(self.original_output),
        }
        self.repair["schema"] = str(edits.prepare(self.schema, source, "draft_plan"))
        return Path(self.repair["schema"])

    def witness(self):
        return provenance.witness(self.state, "draft_plan", str(self.output))

    def test_same_schema_repair_keeps_its_original_session(self):
        self.assertEqual((self.original, "original-session"), self.witness())

    def test_core_bounded_edit_offer_keeps_its_original_validation_and_session(self):
        self.offer()
        before = copy.deepcopy(self.state)
        try:
            observed = self.witness()
        except ValueError as error:
            self.fail(f"A supported exact-bound edit repair lost its original witness: {error}")
        self.assertEqual((self.original, "original-session"), observed)
        self.assertEqual(before, self.state)

    def test_changed_edit_offer_or_original_evidence_is_refused(self):
        offered = self.offer()
        saved = json.loads(offered.read_text())
        for change in (
            "schema",
            "source",
            "hash",
            "stage",
            "format",
            "missing_metadata",
            "original",
            "output",
            "wire_validation",
            "wire_hash",
            "extra_branch",
            "malformed",
            "path",
            "missing",
            "symlink",
        ):
            with self.subTest(change=change):
                schema = copy.deepcopy(saved)
                if change == "schema":
                    schema[edits.META]["original_schema"]["properties"]["summary"]["type"] = "integer"
                elif change in ("source", "hash", "stage", "format"):
                    key = {"source": "source_path", "hash": "sha256"}.get(change, change)
                    schema[edits.META][key] = "foreign"
                elif change == "missing_metadata":
                    schema.pop(edits.META)
                elif change == "wire_validation":
                    schema["properties"][edits.KEY]["anyOf"][1]["properties"]["summary"]["type"] = "integer"
                elif change == "wire_hash":
                    schema["properties"][edits.KEY]["anyOf"][0]["properties"]["sha256"]["const"] = "foreign"
                elif change == "extra_branch":
                    schema["properties"][edits.KEY]["anyOf"].append({})
                elif change == "malformed":
                    schema["properties"][edits.KEY]["anyOf"] = []
                elif change in ("path", "missing", "symlink"):
                    alternate = self.root / "alternate.schema.json"
                    util.atomic_json(alternate, saved)
                    if change == "path":
                        self.repair["schema"] = str(alternate)
                    else:
                        offered.unlink()
                        if change == "symlink":
                            offered.symlink_to(alternate)
                elif change == "original":
                    self.original_output.write_text('{"summary":"Changed after acceptance"}')
                else:
                    self.output.write_text('{"summary":"Changed after acceptance"}')
                if change not in ("missing", "symlink"):
                    util.atomic_json(offered, schema)
                before = copy.deepcopy(self.state)
                with self.assertRaises(ValueError):
                    self.witness()
                self.assertEqual(before, self.state)
                util.atomic_json(self.original_output, {"summary": "Rejected citation"})
                util.atomic_json(self.output, {"summary": "Accepted citation correction"})
                self.repair["schema"] = str(offered)
                if offered.is_symlink():
                    offered.unlink()
                util.atomic_json(offered, saved)

    def test_original_role_source_iteration_contract_and_session_are_still_bound(self):
        self.offer()
        for key in ("role", "source_revision", "iteration", "contract_hash", "criteria_revision", "expected_session"):
            with self.subTest(key=key):
                previous = self.original[key]
                self.original[key] = "foreign"
                with self.assertRaises(ValueError):
                    self.witness()
                self.original[key] = previous

    def test_malformed_schema_files_refuse_through_the_provenance_boundary(self):
        offered = self.offer()
        for content in ("{", "[]", '{"properties": {}}'):
            with self.subTest(content=content):
                offered.write_text(content)
                with self.assertRaisesRegex(ValueError, "planning report lacks matching original provenance"):
                    self.witness()

    def test_full_report_or_unrelated_schema_path_does_not_gain_edit_authority(self):
        unrelated = self.root / "unrelated.schema.json"
        util.atomic_json(unrelated, self.validation)
        self.repair["schema"] = str(unrelated)
        with self.assertRaises(ValueError):
            self.witness()

    def test_an_unsupported_stage_cannot_invent_an_edit_offer(self):
        offered = self.offer()
        schema = json.loads(offered.read_text())
        schema[edits.META]["stage"] = "plan"
        util.atomic_json(offered, schema)
        self.original["stage"] = "plan"
        self.repair.update(stage="plan_report_repair", original_stage="plan")
        with self.assertRaises(ValueError):
            provenance.witness(self.state, "plan", str(self.output))
