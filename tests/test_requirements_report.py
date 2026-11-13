"""An undrafted clarification needs no model call to spell an empty tests list."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode as runner
import autocode_requirements_handoff as handoff
import autocode_requirements_review as review
from units import autoplanner

from tests.test_requirements_handoff import QUESTION, report


class RequirementsReportTests(unittest.TestCase):
    def load(self, value, state, stage="requirements_gather"):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output, schema = folder / "report.json", folder / "schema.json"
        output.write_text(json.dumps(value))
        schema.write_text(json.dumps(autoplanner.schema_for(state or {}, "requirements_gather")))
        record = {"stage": stage, "output": str(output), "schema": str(schema)}
        return runner.load_stage_report(record, state=state), record

    def raw(self):
        value = report(intended_outcome="Understand the requested chat", assistant_reply=QUESTION["question"])
        del value["acceptance_tests"]
        return value

    def test_omitted_tests_clarification_is_preserved_and_asks_before_planning(self):
        for settings in ({}, {"spec_first": True}):
            for stage in ("requirements_gather", "requirements_gather_report_repair"):
                with self.subTest(settings=settings, stage=stage):
                    state = {"task": "Improve the chat", "settings": settings, "stages": []}
                    raw = self.raw()
                    value, record = self.load(raw, state, stage)
                    self.assertEqual([], value["acceptance_tests"])
                    self.assertEqual(raw, json.loads(Path(record["reported_output"]).read_text()))
                    self.assertIn("acceptance_tests", record["defaulted_fields"])
                    queued = []
                    handoff.accept(
                        state,
                        value,
                        record,
                        check_code_refs=lambda *_: self.fail("No code claim"),
                        queue=lambda *a, **kw: queued.append(kw),
                    )
                    self.assertEqual([QUESTION], queued[0]["questions"])
                    self.assertNotIn("goal_contract", state)
                    state["answers"] = {QUESTION["id"]: {"text": "Ask open questions"}}
                    review.after_answers(state)
                    self.assertEqual("requirements_gather", state["next_stage"])

    def test_missing_tests_are_not_filled_for_work_or_without_an_unanswered_question(self):
        cases = [
            ({"required_behaviors": ["Build a chat"]}, {}),
            ({"required_behaviors": None}, {}),
            ({"open_questions": []}, {}),
            ({"open_questions": "ask"}, {}),
            ({"open_questions": [{}]}, {}),
            ({"open_questions": [{**QUESTION, "id": ""}]}, {}),
            ({"open_questions": [{**QUESTION, "question": ""}]}, {}),
            ({}, {"answers": {QUESTION["id"]: {"text": "done"}}}),
            ({}, {"goal_contract": {"body": {}}}),
        ]
        for changes, state in cases:
            with self.subTest(changes=changes, state=state), self.assertRaises(ValueError):
                self.load({**self.raw(), **changes}, state)
        for stage in ("draft_plan", "tester"):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                self.load(self.raw(), {}, stage)
        with self.assertRaises(ValueError):
            self.load(self.raw(), None)

    def test_supplied_tests_and_other_missing_fields_keep_validation(self):
        for supplied in (None, "", [False]):
            with self.subTest(supplied=supplied), self.assertRaises(ValueError):
                self.load({**self.raw(), "acceptance_tests": supplied}, {})
        raw = {**self.raw(), "acceptance_tests": ["An explicit example"]}
        value, _ = self.load(copy.deepcopy(raw), {})
        self.assertEqual(raw["acceptance_tests"], value["acceptance_tests"])
        raw = self.raw()
        del raw["constraints"]
        with self.assertRaisesRegex(ValueError, "missing constraints"):
            self.load(raw, {})

    def test_old_schema_is_not_extended_and_repair_uses_its_original_stage(self):
        import autocode_requirements_report as clarification

        raw = self.raw()
        self.assertEqual(raw, clarification.normalize(raw, {}, {}, {"stage": "requirements_gather"}))
        schema = autoplanner.schema_for({}, "requirements_gather")
        record = {"stage": "report_repair", "original_stage": "requirements_gather", "defaulted_fields": ["conflicts"]}
        result = clarification.normalize(raw, {}, schema, record)
        self.assertEqual([], result["acceptance_tests"])
        self.assertEqual(["acceptance_tests", "conflicts"], record["defaulted_fields"])
        self.assertNotIn("acceptance_tests", raw)
