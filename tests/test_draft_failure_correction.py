"""A reviewer may correct a model inference, never grant authority over human intent (#2453)."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_draft_failure_correction as correction
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_run_view as run_view
import autocode_source_log as source_log
import autopilot
from goal_fixtures import body
from units import autoplanner as planner

from . import test_report_repair as repair_support
from .test_planner_invariants import state

BEFORE = "Preserve lookup([]) raising LookupError"
AFTER = "Preserve lookup([]) returning []"
RATIONALE = "The repository returns []; the first draft inferred an exception absent from the user's requirements."


def fixture(*, investigation=None):
    current = state()
    current["iteration"] = 0
    if investigation:
        current["investigation"] = investigation
    draft = body()
    draft["important_failure_cases"].append(BEFORE)
    if investigation:
        draft["acceptance_criteria"][0]["verification_method"] = "test: test_c1_lookup"
    draft["acceptance_criteria"][0]["sources"] = [{"source_id": "task:0", "quote": current["task"]}]
    draft["initial_task"] = {
        "kind": "implement",
        "objective": "Deliver the CLI",
        "milestone_id": "M1",
        "requirements": ["Greet"],
        "acceptance_criteria": ["C1"],
        "validation_plan": ["Run"],
        "affected_paths": ["greet.py"],
    }
    lifecycle.install_draft(current, draft, origin="glm_draft")
    receipt = {
        "snapshot": correction.snapshot(current),
        "judgment": "unapproved_model_inference",
        "before": BEFORE,
        "after": AFTER,
        "rationale": RATIONALE,
    }
    review = {
        "summary": "Correct the unsupported inference.",
        "concerns": [
            {
                "id": "C3",
                "concern": "The draft invented a failure case.",
                "evidence_refs": ["goal_contract.body"],
                "requested_change": AFTER,
                "acceptance_test": "lookup([]) returns []",
                "blocking": True,
                correction.FIELD: receipt,
            }
        ],
    }
    changed = copy.deepcopy(current["goal_contract"]["body"])
    changed["important_failure_cases"][-1] = AFTER
    changes = [{"item": BEFORE, "change": "reworded", "basis": "agent_proposed", "answer_id": "", "replacement": AFTER}]
    return current, review, changed, changes


class DraftFailureCorrectionTests(unittest.TestCase):
    def admit(self, current, review):
        autopilot.apply_planning(current, "astra_challenge", review, {"output": "review.json"})

    def test_legacy_concern_still_refuses_the_original_unbacked_change(self):
        current, review, changed, changes = fixture()
        del review["concerns"][0][correction.FIELD]
        self.admit(current, review)
        with self.assertRaisesRegex(ValueError, "needs a saved user answer or feedback event"):
            lifecycle.install_draft(current, changed, origin="glm_revise", changes=changes)

    def test_fresh_review_accepts_only_exact_pair_and_preserves_every_existing_proof(self):
        current, review, changed, changes = fixture()
        self.admit(current, review)
        old = copy.deepcopy(current["goal_contract"]["body"])
        lifecycle.install_draft(current, changed, origin="glm_revise", changes=changes)
        self.assertEqual(old["acceptance_criteria"], current["goal_contract"]["body"]["acceptance_criteria"])
        self.assertEqual(AFTER, current["goal_contract"]["body"]["important_failure_cases"][-1])
        saved = current["goal_contract"]["declared_changes"][0]
        self.assertEqual(["C3"], saved["review_concerns"])
        self.assertEqual(review["concerns"][0][correction.FIELD], saved[correction.FIELD])
        self.assertEqual("astra_finalize", current["next_stage"])
        self.assertFalse(goals.approved(current))

    def test_null_receipt_preserves_legacy_review_admission(self):
        current, review, _, _ = fixture()
        review["concerns"][0][correction.FIELD] = None
        self.admit(current, review)
        self.assertEqual("glm_revise", current["next_stage"])

    def test_malformed_stale_nonblocking_and_duplicate_receipts_fail_at_review_admission(self):
        for mutation in ("stale", "judgment", "empty", "extra", "nonblocking", "duplicate", "wrong_before"):
            with self.subTest(mutation=mutation):
                current, review, _, _ = fixture()
                row = review["concerns"][0]
                receipt = row[correction.FIELD]
                if mutation == "stale":
                    receipt["snapshot"] += "x"
                elif mutation == "judgment":
                    receipt["judgment"] = "user_requirement"
                elif mutation == "empty":
                    receipt["rationale"] = " "
                elif mutation == "extra":
                    receipt["authority"] = True
                elif mutation == "nonblocking":
                    row["blocking"] = False
                elif mutation == "duplicate":
                    review["concerns"].append({**copy.deepcopy(row), "id": "C4"})
                else:
                    receipt["before"] = "An unrelated item"
                with self.assertRaises(ValueError):
                    self.admit(current, review)

    def test_changed_source_requirements_feedback_and_draft_invalidate_review(self):
        for key, value in (
            ("requirements_handoff", {"report": {"summary": "Different requirements"}}),
            ("requirements_history", [{"report": {"summary": "Earlier requirements"}}]),
            ("source_log", source_log.task_records("Build a greeting CLI")),
            ("answers", {"Q2": {"kind": "answer", "text": "Changed answer"}}),
            ("brief_feedback", [{"kind": "brief_feedback", "text": "Changed feedback"}]),
            ("investigation", {"report": {"diagnosis": "Different finding"}}),
            ("task", "Different task"),
        ):
            with self.subTest(key=key):
                current, review, changed, changes = fixture()
                self.admit(current, review)
                current[key] = value
                self.assertEqual({}, correction.corrections(current, changed, changes, "glm_revise"))
                with self.assertRaises(ValueError):
                    lifecycle.install_draft(current, changed, origin="glm_revise", changes=changes)
        current, review, changed, changes = fixture()
        self.admit(current, review)
        current["goal_contract"]["body"]["important_failure_cases"].append("Another case")
        self.assertEqual({}, correction.corrections(current, changed, changes, "glm_revise"))

    def test_approved_edited_unknown_or_malformed_lineage_never_offers_authority(self):
        for mutation in (
            "approved",
            "event",
            "historical_event",
            "edited",
            "unknown",
            "missing",
            "malformed_history",
            "gap",
            "human_delta",
            "malformed_event",
        ):
            with self.subTest(mutation=mutation):
                current, _, _, _ = fixture()
                contract = current["goal_contract"]
                if mutation == "approved":
                    contract["approval_status"] = "approved"
                elif mutation == "event":
                    contract["approval_event"] = {"token": goals.token(contract)}
                elif mutation == "historical_event":
                    current["user_events"] = [{"kind": "goal_approval", "token": goals.token(contract)}]
                elif mutation in ("edited", "unknown"):
                    contract["origin"] = "user_cli_edit" if mutation == "edited" else "unknown"
                elif mutation == "missing":
                    del contract["declared_changes"]
                elif mutation == "malformed_history":
                    current["contract_history"] = [None]
                elif mutation == "gap":
                    contract["revision"] = 2
                    contract["hash"] = runner.util.digest({k: contract[k] for k in ("task_id", "revision", "body")})
                elif mutation == "human_delta":
                    contract["declared_changes"] = [
                        {"item": BEFORE, "basis": "user_feedback", "answer_id": "feedback-1"}
                    ]
                else:
                    current["user_events"] = [None]
                self.assertIsNone(correction.snapshot(current))

    def test_exact_human_quotes_and_dual_owned_items_are_not_model_inferences(self):
        for mutation in ("quoted", "logged", "confirmed", "dual", "criterion", "assumption"):
            with self.subTest(mutation=mutation):
                current, review, _, _ = fixture()
                if mutation == "quoted":
                    current["task"] += ". " + BEFORE
                elif mutation == "logged":
                    current["source_log"] = source_log.task_records(BEFORE)
                elif mutation == "confirmed":
                    current["requirements_handoff"] = {"report": {"required_behaviors": [BEFORE]}}
                    current["workflow"] = {"requirements_approved": True}
                elif mutation == "dual":
                    current["goal_contract"]["body"]["required_behaviors"].append(BEFORE)
                elif mutation == "criterion":
                    current["goal_contract"]["body"]["acceptance_criteria"][0]["criterion"] = BEFORE
                else:
                    current["goal_contract"]["body"]["accepted_assumptions"].append(
                        {"text": BEFORE, "basis": "user_answer", "answer_id": "Q1"}
                    )
                contract = current["goal_contract"]
                contract["hash"] = runner.util.digest({k: contract[k] for k in ("task_id", "revision", "body")})
                review["concerns"][0][correction.FIELD]["snapshot"] = correction.snapshot(current)
                with self.assertRaisesRegex(ValueError, "human or dual-owned"):
                    self.admit(current, review)

    def test_receipt_cannot_authorize_other_roles_pairs_or_weaken_other_obligations(self):
        for mutation in (
            "wrong_role",
            "wrong_pair",
            "undeclared",
            "proof",
            "criterion",
            "source",
            "behavior",
            "constraint",
            "failure",
            "extra_failure",
        ):
            with self.subTest(mutation=mutation):
                current, review, changed, changes = fixture()
                self.admit(current, review)
                origin = "glm_revise"
                if mutation == "wrong_role":
                    origin = "astra_finalize"
                elif mutation == "wrong_pair":
                    changed["important_failure_cases"][-1] = changes[0]["replacement"] = "Other behavior"
                elif mutation == "undeclared":
                    changes = []
                elif mutation == "proof":
                    changed["acceptance_criteria"][0]["verification_method"] = "No check"
                elif mutation == "criterion":
                    changed["acceptance_criteria"][0]["criterion"] = "Weakened criterion"
                elif mutation == "source":
                    changed["acceptance_criteria"][0]["sources"] = []
                elif mutation == "behavior":
                    changed["required_behaviors"] = []
                elif mutation == "constraint":
                    changed["constraints"] = []
                elif mutation == "extra_failure":
                    changed["important_failure_cases"].append("Another inferred failure")
                else:
                    changed["important_failure_cases"] = [AFTER]
                self.assertEqual({}, correction.corrections(current, changed, changes, origin))
                with self.assertRaises(ValueError):
                    lifecycle.revision_guard(current, changed, changes, origin)

    def test_additive_criteria_and_source_coverage_remain_available(self):
        current, review, changed, changes = fixture()
        self.admit(current, review)
        changed["acceptance_criteria"][0]["sources"].append({"source_id": "answer:Q2", "quote": "Also preserve lookup"})
        changed["acceptance_criteria"].append(
            {**changed["acceptance_criteria"][0], "id": "C2", "criterion": "lookup([]) returns []"}
        )
        self.assertEqual({BEFORE}, set(correction.corrections(current, changed, changes, "glm_revise")))

    def test_bound_diagnosis_filling_does_not_block_the_exact_correction(self):
        diagnosis = {
            "outcome": "reproduced",
            "observed": "Lookup returns a stale value",
            "output_hash": "a" * 64,
            "source_revision": "source-revision",
            "probe_result": {"exit_code": 0},
        }
        current, review, changed, changes = fixture(investigation=diagnosis)
        self.admit(current, review)
        self.assertEqual(diagnosis["observed"], changed["acceptance_criteria"][0].pop("current_behavior"))
        lifecycle.install_draft(current, changed, origin="glm_revise", changes=changes)
        self.assertEqual(
            diagnosis["observed"], current["goal_contract"]["body"]["acceptance_criteria"][0]["current_behavior"]
        )
        self.assertEqual(AFTER, current["goal_contract"]["body"]["important_failure_cases"][-1])

    def test_effective_prepare_and_saved_wire_schema_deliver_receipt_only_to_first_review(self):
        current, review, _, _ = fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = planner.prepare(current, "astra_challenge", root / "state.json", None)
            self.assertIn(correction.snapshot(current), request.prompt)
            self.assertIn("Absence of a matching quote does not establish", request.prompt)
            schema = runner.util.model_output_schema(request.schema)
            output, schema_path, events = root / "review.json", root / "schema.json", root / "review.jsonl"
            review["source_trace"] = {"out_of_scope": [], "negation_waivers": []}
            review["obligation_decisions"] = []
            output.write_text(json.dumps(review))
            schema_path.write_text(json.dumps(schema))
            events.write_text('{"type":"turn.completed"}\n')
            record = {
                "stage": "astra_challenge",
                "engine": "codex",
                "output": str(output),
                "schema": str(schema_path),
                "events": str(events),
            }
            loaded = runner.load_stage_report(record, state=current)
            self.assertEqual(review["concerns"], loaded["concerns"])
            self.admit(current, loaded)
        self.assertNotIn(
            correction.FIELD, planner.SCHEMAS["plan_review"]["properties"]["concerns"]["items"]["properties"]
        )
        self.assertNotIn(correction.FIELD, planner.CONCERN["properties"])

    def test_report_repair_respects_the_pinned_old_or_new_concern_schema(self):
        for supported in (False, True):
            with self.subTest(supported=supported):
                harness = repair_support.RepairTests()
                harness.setUp()
                self.addCleanup(harness.doCleanups)
                current, review, _, _ = fixture()
                settings = harness.state["settings"]
                harness.state.update(current)
                harness.state["settings"] = settings
                harness.state["workspace"] = str(harness.root)
                harness.state["next_stage"] = "astra_challenge"
                schema = runner.util.model_output_schema(planner.SCHEMAS["astra_challenge"])
                if not supported:
                    concern = schema["properties"]["concerns"]["items"]
                    concern["properties"].pop(correction.FIELD)
                    concern["required"].remove(correction.FIELD)
                    review["concerns"][0].pop(correction.FIELD)
                saved = harness.run / "saved.schema.json"
                saved.write_text(json.dumps(schema))
                before = saved.read_bytes()
                harness.queue(stage="astra_challenge", role="astra", schema=str(saved), report=json.dumps(review))
                prompt = harness.repair_request()["prompt"]
                instruction, raw = prompt.split("CURRENT HANDOFF DATA\n", 1)
                handoff = json.loads(raw)
                self.assertEqual(supported, "An unapproved important_failure_cases inference" in instruction)
                self.assertEqual(supported, "draft_failure_correction_snapshot" in handoff)
                self.assertEqual(before, saved.read_bytes())

    def test_final_review_displays_the_receipt_and_requires_exact_plan_approval(self):
        current, review, changed, changes = fixture()
        self.admit(current, review)
        initial_task = changed.pop("initial_task")
        revise = {
            "contract": changed,
            "summary": "Correct inference",
            "code_refs": [],
            "contract_changes": changes,
            "requirement_trace": [],
            "responses": [
                {
                    "concern_id": "C3",
                    "response": "Corrected",
                    "evidence_refs": ["goal_contract.body"],
                    "change": AFTER,
                    "acceptance_test": "lookup([]) returns []",
                }
            ],
        }
        autopilot.apply_planning(current, "glm_revise", revise, {"output": "revision.json"})
        reviewed = copy.deepcopy(current["goal_contract"]["body"])
        reviewed["initial_task"] = initial_task
        final = {
            "contract": reviewed,
            "summary": "Reviewed",
            "contract_changes": [],
            "requirement_trace": [],
            "decisions": [
                {
                    "concern_id": "C3",
                    "decision": "Resolved",
                    "rationale": RATIONALE,
                    "acceptance_test": "lookup([]) returns []",
                    "resolved": True,
                }
            ],
        }
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(lifecycle.source_scope, "snapshot", return_value={"revision": "source-revision"}),
        ):
            output, events = Path(temp) / "final.json", Path(temp) / "final.jsonl"
            output.write_text(json.dumps(final))
            events.write_text('{"type":"turn.completed"}\n')
            current["stages"] = [
                {
                    "stage": "astra_finalize",
                    "output": str(output),
                    "events": str(events),
                    "source_revision": "source-revision",
                }
            ]
            autopilot.apply_planning(current, "astra_finalize", final, current["stages"][0])
            lifecycle.human.evaluate(current)
            display = lifecycle.present(current)
            self.assertFalse(goals.approved(current))
            public = run_view.view(current)
            self.assertIn(BEFORE, display)
            self.assertIn(AFTER, display)
            self.assertIn(RATIONALE, display)
            self.assertIn(review["concerns"][0][correction.FIELD]["snapshot"], display)
            self.assertEqual("AWAITING_GOAL_APPROVAL", current["status"])
            with self.assertRaises(ValueError):
                lifecycle.approve(current, "r0:stale")
            lifecycle.approve(current, goals.token(current["goal_contract"]))
            self.assertTrue(goals.approved(current))
            self.assertIn("approve", str(public))
