"""Background sources are optional to adopt, but every adopted fact stays faithful."""

from __future__ import annotations

import copy
import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_brief_obligations as obligations
import autocode_report_check as report_check
import autocode_source_log as source_log
import autocode_source_trace as source_trace


class SourceCitationAuthorityTests(unittest.TestCase):
    def setUp(self):
        no_process = patch("subprocess.Popen", side_effect=AssertionError("Pure citation tests launch no process"))
        no_process.start()
        self.addCleanup(no_process.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        config = self.workspace / "fake-config.json"
        config.write_text(json.dumps({"check": "python -m unittest", "paths": ["review/report.json"]}))
        with patch.dict(os.environ, {"SCENARIO_FAKE_CONFIG": str(config)}):
            self.fake = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scenarios/harness/fake_codex.py"))
        self.task = {"id": "task:0", "kind": "task", "text": "Review the proposal for correctness."}
        self.background = "Proposed capacity is 1200 items. Expire after 45 seconds using `cache_key`."

    def records(self, kind):
        return [self.task, {"id": "proposal:background", "kind": kind, "text": self.background}]

    def body(self, text="The review identifies correctness gaps.", *, sources=None):
        row = {"id": "C1", "criterion": text, "verification_method": "test: review", "human_review": False}
        if sources is not None:
            row["sources"] = copy.deepcopy(sources)
        return {"acceptance_criteria": [row], "required_behaviors": [self.task["text"]], "constraints": []}

    def cite(self, body, records):
        self.fake["DATA"].update(source_inventory=copy.deepcopy(records))
        return self.fake["cite_sources"](body)

    def ready(self, body, records):
        source_trace.check_citations(body, records)
        source_trace.check_ready(body, records, source_trace.DEFAULT_TRACE)

    def errors(self, body, records):
        spec = report_check.build_spec(
            stage="finalize_plan",
            workspace=self.workspace,
            schema={"type": "object", "required": ["contract"], "properties": {"contract": {"type": "object"}}},
        )
        spec["sources"] = records
        return report_check.check_report(json.dumps({"contract": body}), spec=spec)

    def test_generic_fake_review_does_not_adopt_every_background_fact(self):
        for kind in ("design document", "linked issue"):
            with self.subTest(kind=kind):
                records = self.records(kind)
                body = self.cite(self.body(), records)
                self.assertEqual(
                    [{"source_id": "task:0", "quote": self.task["text"]}],
                    body["acceptance_criteria"][0]["sources"],
                )
                self.ready(body, records)

    def test_fake_keeps_explicit_supporting_quotes_and_adds_primary_authority(self):
        for kind in ("design document", "linked issue"):
            with self.subTest(kind=kind):
                records = self.records(kind)
                authored = {"source_id": "proposal:background", "quote": self.background}
                body = self.cite(self.body(self.background, sources=[authored]), records)
                self.assertEqual(
                    [authored, {"source_id": "task:0", "quote": self.task["text"]}],
                    body["acceptance_criteria"][0]["sources"],
                )
                self.ready(body, records)

    def test_fake_keeps_authored_quote_scope_without_broadening_it(self):
        records = self.records("design document")
        authored = {"source_id": "proposal:background", "quote": "Proposed capacity is 1200 items."}
        body = self.cite(self.body("Review the 1200 item capacity.", sources=[authored]), records)
        self.assertEqual(authored, body["acceptance_criteria"][0]["sources"][0])
        self.assertNotIn(self.background, [row["quote"] for row in body["acceptance_criteria"][0]["sources"]])
        self.ready(body, records)

    def test_fake_does_not_duplicate_existing_or_repeated_primary_citations(self):
        authored = {"source_id": self.task["id"], "quote": self.task["text"]}
        for sources in (None, [authored]):
            with self.subTest(sources=sources):
                body = self.cite(self.body(sources=sources), [self.task, self.task])
                self.assertEqual([authored], body["acceptance_criteria"][0]["sources"])

    def test_fake_does_not_mask_malformed_authored_citations(self):
        body = self.body()
        body["acceptance_criteria"][0]["sources"] = "malformed authored citation"
        self.cite(body, self.records("design document"))
        self.assertEqual("malformed authored citation", body["acceptance_criteria"][0]["sources"])

    def test_primary_implementation_values_remain_required(self):
        self.task = {"id": "task:0", "kind": "task", "text": "Implement capacity 1200 and TTL 45 using `cache_key`."}
        records = self.records("design document")
        body = self.cite(self.body(), records)
        body["required_behaviors"] = []
        with self.assertRaisesRegex(ValueError, "changed a number.*1200.*45"):
            self.ready(body, records)
        body["acceptance_criteria"][0]["criterion"] = self.task["text"]
        self.ready(body, records)

    def test_preliminary_checker_rejects_cited_background_loss_like_final_seal(self):
        for kind in ("design document", "linked issue"):
            with self.subTest(kind=kind):
                records = self.records(kind)
                body = self.body(sources=[{"source_id": row["id"], "quote": row["text"]} for row in records])
                with self.assertRaisesRegex(ValueError, "changed a number.*1200.*45"):
                    self.ready(body, records)
                errors = self.errors(body, records)
                self.assertTrue(any("changed a number" in error and "1200" in error for error in errors), errors)
                self.assertTrue(any("dropped a literal" in error and "cache_key" in error for error in errors), errors)

    def test_preliminary_checker_keeps_uncited_background_optional(self):
        for kind in ("design document", "linked issue"):
            with self.subTest(kind=kind):
                records = self.records(kind)
                body = self.body(sources=[{"source_id": self.task["id"], "quote": self.task["text"]}])
                self.assertEqual([], self.errors(body, records))
                self.ready(body, records)

    def test_preliminary_checker_accepts_faithfully_cited_review_facts(self):
        for kind in ("design document", "linked issue"):
            with self.subTest(kind=kind):
                records = self.records(kind)
                body = self.body(
                    "Review capacity 1200, TTL 45 and `cache_key` for correctness.",
                    sources=[{"source_id": row["id"], "quote": row["text"]} for row in records],
                )
                self.assertEqual([], self.errors(body, records))
                self.ready(body, records)

    def follow_up_case(self, *, same_job=False):
        design = self.workspace / "docs/design/proposal.md"
        design.parent.mkdir(parents=True, exist_ok=True)
        design.write_text(self.background)
        state = {"task": "Review `docs/design/proposal.md` for correctness.", "workspace": str(self.workspace)}
        source_log.seed(state)
        # The earlier source remains authenticated historical context even when
        # the workspace's proposal changes before the next job.
        design.write_text("Revised proposal: the review no longer adopts the old capacity or key.")
        event = {
            "id": "next-job",
            "kind": "brief_feedback",
            "actor": "user_cli",
            "text": "Review the operational notes too." if same_job else "Build the accepted change.",
        }
        source_log.save_feedback(state, event)
        state["turns"] = [{"event_id": event["id"], "say": event["text"], "previous": {"workflow": "design"}}]
        state["workflow"] = {"kind": "design" if same_job else "build"}
        records = obligations.sources(state)
        body = self.body(
            state["task"] + " " + event["text"],
            sources=[{"source_id": row["id"], "quote": row["text"]} for row in records],
        )
        body["required_behaviors"] = []
        spec = report_check.build_spec(
            stage="finalize_plan",
            workspace=self.workspace,
            schema={"type": "object", "required": ["contract"], "properties": {"contract": {"type": "object"}}},
            state=state,
        )
        return state, records, body, spec

    def test_cross_job_followup_uses_the_same_retired_source_scope_as_final_seal(self):
        state, records, body, spec = self.follow_up_case()
        active = source_trace._turn_scoped(state, records)
        self.assertEqual(["feedback:next-job"], [row["id"] for row in active])
        source_trace.check_citations(body, records)
        source_trace.check_ready(body, records, source_trace.DEFAULT_TRACE, coverage_records=active)
        errors = report_check.check_report(json.dumps({"contract": body}), spec=spec)
        self.assertEqual((["feedback:next-job"], []), (spec.get("active_source_ids"), errors))
        self.assertEqual(records, spec["sources"], "Historical citations still authenticate against full inventory")

    def test_same_job_followup_keeps_earlier_cited_design_facts_active(self):
        state, records, body, spec = self.follow_up_case(same_job=True)
        active = source_trace._turn_scoped(state, records)
        self.assertEqual([row["id"] for row in records], [row["id"] for row in active])
        self.assertEqual([row["id"] for row in active], spec.get("active_source_ids"))
        with self.assertRaisesRegex(ValueError, "changed a number.*1200.*45"):
            source_trace.check_ready(body, records, source_trace.DEFAULT_TRACE, coverage_records=active)
        errors = report_check.check_report(json.dumps({"contract": body}), spec=spec)
        self.assertTrue(any("changed a number" in error and "1200" in error for error in errors), errors)
        self.assertTrue(any("dropped a literal" in error and "cache_key" in error for error in errors), errors)

    def test_current_cited_background_remains_active_in_the_public_spec(self):
        state, records, body, spec = self.follow_up_case(same_job=True)
        state.pop("turns")
        spec = report_check.build_spec(
            stage="finalize_plan", workspace=self.workspace, schema=spec["schema"], state=state
        )
        self.assertEqual([row["id"] for row in records], spec.get("active_source_ids"))
        errors = report_check.check_report(json.dumps({"contract": body}), spec=spec)
        self.assertTrue(any("cache_key" in error for error in errors), errors)

    def test_inactive_historical_citation_still_requires_authentic_quote(self):
        _, records, body, spec = self.follow_up_case()
        design_id = next(row["id"] for row in records if row["kind"] == "design document")
        citation = next(row for row in body["acceptance_criteria"][0]["sources"] if row["source_id"] == design_id)
        citation["quote"] = "Invented historical capacity 1201."
        errors = report_check.check_report(json.dumps({"contract": body}), spec=spec)
        self.assertTrue(any("without quoting it verbatim" in error for error in errors), errors)

    def test_malformed_duplicate_or_foreign_active_source_ids_fail_closed(self):
        _, records, body, spec = self.follow_up_case()
        body["acceptance_criteria"][0]["criterion"] += " " + self.background
        for ids in (None, {}, "feedback:next-job", [None], [True], [""], [" "], ["foreign"], [records[0]["id"]] * 2):
            with self.subTest(ids=ids):
                malformed = {**spec, "active_source_ids": ids}
                errors = report_check.check_report(json.dumps({"contract": body}), spec=malformed)
                self.assertTrue(any("active_source_ids" in error for error in errors), errors)

    def test_scope_ids_cannot_select_an_ambiguous_inventory_identity(self):
        _, records, body, spec = self.follow_up_case()
        ambiguous = {**spec, "sources": [*records, records[-1]], "active_source_ids": [records[-1]["id"]]}
        errors = report_check.check_report(json.dumps({"contract": body}), spec=ambiguous)
        self.assertTrue(any("active_source_ids" in error for error in errors), errors)

    def test_older_spec_without_active_ids_keeps_conservative_full_survival(self):
        _, _, body, spec = self.follow_up_case()
        spec.pop("active_source_ids", None)
        errors = report_check.check_report(json.dumps({"contract": body}), spec=spec)
        self.assertTrue(any("changed a number" in error and "1200" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
