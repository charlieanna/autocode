"""Saved clarifications resolve conflicts without discarding valid requirements."""

import copy
import unittest
from pathlib import Path

import autocode_adaptive_planning as adaptive
import autocode_goals as goals
import autocode_planning_report_policy as policy
import autopilot
from autocode_util import model_output_schema, validate_schema
from goal_fixtures import body
from units import autoplanner


class ConflictTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "id": "workflow-update",
            "text": "The earlier description is historical. Current runs have an independent reviewer.",
        }
        self.state = {
            "task_id": "task",
            "task": "Show the recorded workflow.",
            "workspace": str(Path(__file__).resolve().parent.parent),
            "settings": {"joint_planning": True},
            "answers": {},
            "brief_feedback": [self.event],
            "user_events": [copy.deepcopy(self.event)],
            "requirements_handoff": {
                "report": {
                    "requirements": [{"id": "R1"}, {"id": "R2"}],
                    "open_questions": [],
                    "conflicts": [{"requirement_ids": ["R1", "R2"], "description": "old vs current workflow"}],
                }
            },
        }
        self.contract = body()
        self.report = {
            "contract": self.contract,
            "summary": "Preserve recorded topology.",
            "code_refs": ["tools/autocode_goals.py:1"],
            "alternatives": [],
            "uncertainties": [],
            "contract_changes": [],
            "requirement_trace": [
                {"requirement_id": rid, "disposition": "covered", "evidence": "C1"} for rid in ("R1", "R2")
            ],
            "conflict_resolutions": [
                {
                    "requirement_ids": ["R1", "R2"],
                    "basis": "user_feedback",
                    "answer_id": self.event["id"],
                    "source_quote": self.event["text"],
                    "resolution": "Show the earlier topology in history and the upgraded topology for current runs.",
                }
            ],
        }

    def test_saved_clarification_keeps_both_requirements_and_reaches_independent_review(self):
        before = copy.deepcopy(self.report)
        autopilot.apply_planning(self.state, "astra_discovery", self.report, {"output": "draft.json"})
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.assertEqual([], self.state["pending_questions"])
        self.assertEqual(before, self.state["planning"]["reports"]["astra_discovery"]["report"])

    def test_saved_answer_can_resolve_conflict(self):
        self.state["answers"]["Q1"] = {"text": self.event["text"]}
        self.report["conflict_resolutions"][0].update(basis="user_answer", answer_id="Q1")
        goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_unresolved_conflict_still_needs_question(self):
        self.report.pop("conflict_resolutions")
        with self.assertRaisesRegex(ValueError, "Unresolved requirement conflict"):
            goals.check_requirement_trace(self.state, self.report, self.contract)
        self.contract["open_blocking_questions"] = [{"id": "Q1"}]
        goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_resolution_rejects_unverified_authority_and_invalid_quotes(self):
        for changes, message in [
            ({"basis": "agent_proposed"}, "saved user"),
            ({"answer_id": "invented"}, "saved user"),
            ({"source_quote": "Always skip review."}, "source_quote"),
            ({"source_quote": ""}, "source_quote"),
            ({"resolution": ""}, "explanation"),
            ({"requirement_ids": ["R1"]}, "exactly one"),
            ({"requirement_ids": ["R1", "R2", "R2"]}, "exactly one"),
            ({"requirement_ids": ["R1", "R99"]}, "exactly one"),
        ]:
            with self.subTest(changes=changes):
                report = copy.deepcopy(self.report)
                report["conflict_resolutions"][0].update(changes)
                with self.assertRaisesRegex(ValueError, message):
                    goals.check_requirement_trace(self.state, report, self.contract)
        self.state["user_events"] = []
        with self.assertRaisesRegex(ValueError, "saved user"):
            goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_resolution_does_not_hide_another_unresolved_conflict(self):
        handoff = self.state["requirements_handoff"]["report"]
        handoff["requirements"].append({"id": "R3"})
        handoff["conflicts"].append({"requirement_ids": ["R2", "R3"], "description": "unsettled policy"})
        self.report["requirement_trace"].append({"requirement_id": "R3", "disposition": "covered", "evidence": "C1"})
        with self.assertRaisesRegex(ValueError, "unsettled policy"):
            goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_duplicate_resolution_rejected(self):
        self.report["conflict_resolutions"].append(copy.deepcopy(self.report["conflict_resolutions"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_new_report_schemas_require_explicit_resolution_lists(self):
        for stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                self.assertIn("conflict_resolutions", autoplanner.SCHEMAS[stage]["required"])

    def refresh_resolved_handoff(self):
        handoff = self.state["requirements_handoff"]["report"]
        for row in handoff["requirements"]:
            row["source_quote"] = "Original user requirement " + row["id"]
        self.state["requirements_history"] = [copy.deepcopy(self.state["requirements_handoff"])]
        handoff["conflicts"] = []

    def test_saved_resolution_survives_refreshed_handoff(self):
        self.refresh_resolved_handoff()
        autopilot.apply_planning(self.state, "astra_discovery", self.report, {"output": "draft.json"})
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.assertEqual([], self.state["pending_questions"])
        self.assertEqual(
            self.report["conflict_resolutions"],
            self.state["planning"]["reports"]["astra_discovery"]["report"]["conflict_resolutions"],
        )

    def test_historical_resolution_cannot_transfer_to_reused_ids(self):
        self.refresh_resolved_handoff()
        self.state["requirements_handoff"]["report"]["requirements"][0]["source_quote"] = "A different requirement"
        with self.assertRaisesRegex(ValueError, "exactly one recorded"):
            goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_history_without_quotes_cannot_authorize_resolution(self):
        self.refresh_resolved_handoff()
        self.state["requirements_history"][0]["report"]["requirements"][0].pop("source_quote")
        with self.assertRaisesRegex(ValueError, "exactly one recorded"):
            goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_historical_resolution_keeps_authority_and_current_conflict_checks(self):
        self.refresh_resolved_handoff()
        report = copy.deepcopy(self.report)
        report["conflict_resolutions"][0]["answer_id"] = "invented"
        with self.assertRaisesRegex(ValueError, "saved user"):
            goals.check_requirement_trace(self.state, report, self.contract)
        handoff = self.state["requirements_handoff"]["report"]
        handoff["requirements"].append({"id": "R3", "source_quote": "New request"})
        handoff["conflicts"] = [{"requirement_ids": ["R2", "R3"], "description": "new unresolved conflict"}]
        self.report["requirement_trace"].append({"requirement_id": "R3", "disposition": "covered", "evidence": "C1"})
        with self.assertRaisesRegex(ValueError, "new unresolved conflict"):
            goals.check_requirement_trace(self.state, self.report, self.contract)

    def test_review_context_includes_conflict_history_with_source_identity(self):
        self.refresh_resolved_handoff()
        self.state["settings"]["engine"] = "codex"
        prompt, _ = autoplanner.context(self.state, "astra_challenge", Path("/fixture/state.json"))
        import json

        packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(["R1", "R2"], packet["requirements_history"][0]["conflicts"][0]["requirement_ids"])
        self.assertEqual(
            "Original user requirement R1", packet["requirements_history"][0]["requirements"][0]["source_quote"]
        )


class ConflictGenerationTests(unittest.TestCase):
    setUp = ConflictTests.setUp
    refresh_resolved_handoff = ConflictTests.refresh_resolved_handoff

    def resolution_schema(self, stage="astra_discovery"):
        current = copy.deepcopy(self.state)
        current["settings"]["engine"] = "codex"
        request = autoplanner.prepare(current, stage, Path("/fixture/state.json"), None)
        return model_output_schema(request.schema)["properties"]["conflict_resolutions"]

    def test_actual_prepare_and_wire_schema_offer_only_empty_for_known_no_conflicts(self):
        self.state["requirements_handoff"]["report"]["conflicts"] = []
        before = copy.deepcopy(autoplanner.SCHEMAS)
        # Saved incident shape: a single newly traced requirement confused with a review concern.
        impossible = [dict(self.report["conflict_resolutions"][0], requirement_ids=["R1"])]
        for stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                schema = self.resolution_schema(stage)
                self.assertEqual(0, schema["maxItems"])
                validate_schema([], schema)
                with self.assertRaisesRegex(ValueError, "too many items"):
                    validate_schema(impossible, schema)
                # Legacy saved schemas are not rewritten; the semantic guard still rejects the same row.
                validate_schema(impossible, autoplanner.SCHEMAS[stage]["properties"]["conflict_resolutions"])
        self.assertEqual(before, autoplanner.SCHEMAS)
        invalid = {**self.report, "conflict_resolutions": impossible}
        with self.assertRaisesRegex(
            ValueError, r"no requirement conflict is recorded .*conflict_resolutions must be \[\]"
        ):
            goals.check_requirement_trace(self.state, invalid, self.contract)
        valid = {**self.report, "conflict_resolutions": []}
        autopilot.apply_planning(self.state, "astra_discovery", valid, {"output": "draft.json"})
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.assertEqual([], self.state["pending_questions"])
        self.assertFalse(goals.approved(self.state))

    def test_real_and_quote_stable_historical_conflicts_keep_resolution_available(self):
        for historical in (False, True):
            if historical:
                self.refresh_resolved_handoff()
            for stage in ("astra_discovery", "glm_revise", "astra_finalize"):
                with self.subTest(stage=stage, historical=historical):
                    schema = self.resolution_schema(stage)
                    self.assertNotIn("maxItems", schema)
                    validate_schema(self.report["conflict_resolutions"], schema)
                    goals.check_requirement_trace(self.state, self.report, self.contract)
        invalid = copy.deepcopy(self.report)
        invalid["conflict_resolutions"][0]["answer_id"] = "invented"
        with self.assertRaisesRegex(ValueError, "saved user"):
            goals.check_requirement_trace(self.state, invalid, self.contract)

    def test_recycled_ids_missing_quotes_and_removed_ids_cannot_keep_historical_resolutions(self):
        self.refresh_resolved_handoff()
        original = copy.deepcopy(self.state)
        for variant in ("changed_quote", "missing_quote", "removed_id"):
            with self.subTest(variant=variant):
                self.state = copy.deepcopy(original)
                if variant == "missing_quote":
                    self.state["requirements_history"][0]["report"]["requirements"][0].pop("source_quote")
                elif variant == "changed_quote":
                    self.state["requirements_handoff"]["report"]["requirements"][0]["source_quote"] = (
                        "A new requirement"
                    )
                else:
                    self.state["requirements_handoff"]["report"]["requirements"].pop(0)
                self.assertEqual(0, self.resolution_schema()["maxItems"])

    def test_unknown_legacy_inventory_is_not_reinterpreted_as_empty(self):
        self.state["requirements_handoff"]["report"]["conflicts"] = []
        original = copy.deepcopy(self.state)
        for variant in ("no_handoff", "no_conflicts_field", "no_requirements", "unknown_history"):
            with self.subTest(variant=variant):
                self.state = copy.deepcopy(original)
                if variant == "no_handoff":
                    self.state.pop("requirements_handoff")
                elif variant == "no_conflicts_field":
                    self.state["requirements_handoff"]["report"].pop("conflicts")
                elif variant == "no_requirements":
                    self.state["requirements_handoff"]["report"]["requirements"] = []
                else:
                    self.state["requirements_history"] = [{"report": {"requirements": [{"id": "R1"}]}}]
                self.assertNotIn("maxItems", self.resolution_schema())

    def test_adaptive_feedback_is_traced_but_is_not_a_recorded_conflict(self):
        self.state["requirements_handoff"]["report"] = {"requirements": [], "conflicts": []}
        self.event[adaptive.FEEDBACK] = {"requirements_handoff": ""}
        self.assertEqual([self.event["id"]], [row["id"] for row in adaptive.feedback_requirements(self.state)])
        self.assertEqual(0, self.resolution_schema()["maxItems"])

    def test_unrelated_schemas_and_malformed_unknown_context_are_unchanged(self):
        schema = copy.deepcopy(autoplanner.SCHEMAS["astra_discovery"])
        handoff = {"requirements": [{"id": "R1"}], "conflicts": []}
        for history in (None, [None], [{"report": None}], [{"report": {"requirements": [], "conflicts": "unknown"}}]):
            with self.subTest(history=history):
                self.assertEqual(schema, policy.conflict_schema(schema, handoff, [], history))
        unrelated = autoplanner.SCHEMAS["astra_challenge"]
        self.assertEqual(unrelated, policy.conflict_schema(unrelated, handoff, [], []))


if __name__ == "__main__":
    unittest.main()
