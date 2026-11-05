"""Changed-line reasons need an independent, exact-bound and evidenced decision."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode as runner
import autocode_changed_code_evidence as evidence
import autocode_changed_code_review as review
import autocode_report_schema as reports
import autocode_role_schema as roles
import autocode_verification_view as view
import autocode_workflow as workflow
from autocode_util import model_output_schema, validate_schema

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "tools/autocode-schemas"


def assessment():
    return evidence.assess(
        [{"path": "calc.py", "sha256": "a" * 64, "lines": [2]}],
        None,
        None,
        source_revision="source-one",
        proof_context="proof-one",
    )


def wire(stage="sol", state=None):
    schema = (
        workflow.checkpoint_schema(SCHEMAS)
        if stage == "astra_checkpoint"
        else roles.role_schema(json.loads((SCHEMAS / "v2/sol-report.schema.json").read_text()), "sol")
    )
    return model_output_schema(
        reports.review_generation_schema(
            schema, state or {"regression_proof": {"changed_code_evidence": assessment()}}, stage
        )
    )


class ChangedCodeReviewTests(unittest.TestCase):
    def setUp(self):
        self.assessment = assessment()
        self.gap = self.assessment["obligations"][0]
        self.decision = {
            "gap_id": self.gap["gap_id"],
            "decision": "accept",
            "reason_kind": "equivalent_mutant",
            "reason": "The replaced literal is unreachable under the independently checked input invariant.",
            "evidence_refs": ["check:1", "calc.py"],
        }
        self.record = {
            "stage": "sol",
            "role": "sol",
            "source_revision": "source-one",
            "changed_files": [],
            "output": "/run/tester.json",
        }
        self.refs = {"check:1": "event:verified", "calc.py": "/workspace/calc.py"}

    def bind(self, decisions=None, **options):
        return review.bind(
            options.pop("assessment", self.assessment),
            [self.decision] if decisions is None else decisions,
            options.pop("record", self.record),
            offered_schema=options.pop("offered_schema", wire()),
            verified_refs=options.pop("verified_refs", self.refs),
            **options,
        )

    def test_generation_offers_only_current_gaps_without_mutating_schema_or_state(self):
        schema = roles.role_schema(json.loads((SCHEMAS / "v2/sol-report.schema.json").read_text()), "sol")
        state = {"regression_proof": {"changed_code_evidence": self.assessment}}
        original = copy.deepcopy((schema, state))
        field = reports.review_generation_schema(schema, state, "sol")["properties"][review.FIELD]
        self.assertEqual(["L1", self.gap["gap_id"]], field["items"]["properties"]["gap_id"]["enum"])
        self.assertEqual(1, field["maxItems"])
        self.assertEqual(["array", "null"], field["type"])
        self.assertNotIn(review.FIELD, schema["required"])
        self.assertEqual(original, (schema, state))
        field = reports.review_generation_schema(schema, {}, "sol")["properties"][review.FIELD]
        self.assertEqual(0, field["maxItems"])
        with self.assertRaises(ValueError):
            validate_schema([self.decision], field)

    def test_checkpoint_offers_same_binding_but_builder_self_check_does_not(self):
        field = wire("astra_checkpoint")["properties"]["validation"]["properties"][review.FIELD]
        self.assertEqual(["L1", self.gap["gap_id"]], field["items"]["properties"]["gap_id"]["enum"])
        builder = workflow.implementation_schema(SCHEMAS)
        self.assertNotIn(
            review.FIELD, builder["properties"]["continuation"]["properties"]["self_assessment"]["properties"]
        )
        before = copy.deepcopy(builder)
        result = reports.review_generation_schema(
            builder, {"regression_proof": {"changed_code_evidence": self.assessment}}, "terra"
        )
        self.assertNotIn(
            review.FIELD, result["properties"]["continuation"]["properties"]["self_assessment"]["properties"]
        )
        self.assertEqual(before, builder)

    def test_independent_accepted_reason_is_bound_and_discharged_with_unchanged_inputs(self):
        before = copy.deepcopy((self.assessment, self.decision, self.record, self.refs))
        bound = self.bind()
        self.assertEqual("source-one", bound[0]["source_revision"])
        self.assertEqual("proof-one", bound[0]["proof_context"])
        self.assertEqual(["event:verified", "/workspace/calc.py"], bound[0]["evidence"])
        self.assertEqual({"stage": "sol", "role": "sol", "output": "/run/tester.json"}, bound[0]["reviewer"])
        result = evidence.assess(
            [{"path": "calc.py", "sha256": "a" * 64, "lines": [2]}],
            None,
            None,
            source_revision="source-one",
            proof_context="proof-one",
            exceptions=bound,
        )
        self.assertEqual("PASS", result["status"])
        self.assertEqual("excepted", result["obligations"][0]["status"])
        self.assertEqual(before, (self.assessment, self.decision, self.record, self.refs))

    def test_handle_binds_the_exact_gap_its_full_id_names(self):
        self.assertEqual("L1", self.gap["handle"])
        by_id = self.bind()
        self.decision["gap_id"] = "L1"
        self.assertEqual(by_id, self.bind())
        self.assertEqual(self.gap["gap_id"], review.resolve(self.assessment, "L1"))

    def test_handle_and_id_of_one_gap_are_one_decision(self):
        twice = [self.decision, {**self.decision, "gap_id": "L1"}]
        with self.assertRaisesRegex(ValueError, "duplicate, unknown or stale"):
            self.bind(twice)

    def test_handle_numbering_is_stable_and_unknown_handles_bind_nothing(self):
        inventory = [{"path": "calc.py", "sha256": "a" * 64, "lines": [2, 3, 4]}]
        first = evidence.assess(inventory, None, None, source_revision="source-one", proof_context="proof-one")
        self.assertEqual(["L1", "L2", "L3"], [row["handle"] for row in first["obligations"]])
        decision = {**self.decision, "gap_id": "L2"}
        bound = review.bind(
            first, [decision], self.record, offered_schema=wire(state=self.state(first)), verified_refs=self.refs
        )
        again = evidence.assess(
            inventory, None, None, source_revision="source-one", proof_context="proof-one", exceptions=bound
        )
        # Settling L2 renumbers nothing: L3 still names line 4.
        self.assertEqual(["L1", "L2", "L3"], [row["handle"] for row in again["obligations"]])
        self.assertEqual("excepted", again["obligations"][1]["status"])
        self.assertEqual(4, again["obligations"][2]["line"])
        for name in ("L4", "l2", "L02"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "duplicate, unknown or stale"):
                review.bind(
                    first,
                    [{**self.decision, "gap_id": name}],
                    self.record,
                    offered_schema=wire(state=self.state(first)),
                    verified_refs=self.refs,
                )

    def test_legacy_id_only_offer_still_binds_ids_and_refuses_handles(self):
        legacy = wire()
        props = legacy["properties"][review.FIELD]["items"]["properties"]
        props["gap_id"]["enum"] = [self.gap["gap_id"]]
        self.assertTrue(self.bind(offered_schema=legacy))
        self.decision["gap_id"] = "L1"
        with self.assertRaisesRegex(ValueError, "duplicate, unknown or stale"):
            self.bind(offered_schema=legacy)

    @staticmethod
    def state(value):
        return {"regression_proof": {"changed_code_evidence": value}}

    def test_rejected_reason_is_recorded_but_never_accepted(self):
        self.decision["decision"] = "reject"
        bound = self.bind()
        self.assertFalse(bound[0]["accepted"])
        result = evidence.assess(
            [{"path": "calc.py", "sha256": "a" * 64, "lines": [2]}],
            None,
            None,
            source_revision="source-one",
            proof_context="proof-one",
            exceptions=bound,
        )
        self.assertEqual("UNVERIFIED", result["status"])

    def test_checkpoint_and_original_stage_repair_are_independent(self):
        for stage, role, original in (
            ("astra_checkpoint", "astra", None),
            ("sol_report_repair", "sol", "sol"),
            ("astra_checkpoint_report_repair", "astra", "astra_checkpoint"),
        ):
            with self.subTest(stage=stage):
                record = {**self.record, "stage": stage, "role": role}
                if original:
                    record["original_stage"] = original
                bound = self.bind(record=record, offered_schema=wire(original or stage))
                self.assertTrue(bound[0]["accepted"])

    def test_builder_self_check_wrong_role_and_source_edits_never_authorize(self):
        for patch in (
            {"stage": "terra", "role": "terra"},
            {"stage": "self_check", "role": "terra"},
            {"stage": "astra_review", "role": "astra"},
            {"role": "terra"},
            {"source_revision": "other"},
            {"changed_files": ["calc.py"]},
            {"output": ""},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.bind(record={**self.record, **patch})

    def test_stale_offer_duplicate_unknown_and_forged_authority_are_refused(self):
        old = wire()
        old["properties"][review.FIELD]["items"]["properties"]["gap_id"]["enum"] = ["old"]
        with self.assertRaises(ValueError):
            self.bind(offered_schema=old)
        for rows in (
            [self.decision, self.decision],
            [{**self.decision, "gap_id": "unknown"}],
            [{**self.decision, "accepted": True}],
        ):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.bind(rows)

    def test_changed_context_or_file_binding_cannot_reuse_an_offered_gap(self):
        for key, value in (("proof_context", "new-proof"), ("path", "other.py"), ("sha256", "b" * 64), ("line", 3)):
            current = copy.deepcopy(self.assessment)
            (current if key == "proof_context" else current["obligations"][0])[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.bind(assessment=current)

    def test_unpinned_evidence_and_empty_or_unknown_judgments_are_refused(self):
        for patch in (
            {"evidence_refs": ["unread.py"]},
            {"evidence_refs": []},
            {"reason": " "},
            {"decision": "PASS"},
            {"reason_kind": "too_expensive"},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.bind([{**self.decision, **patch}])

    def test_null_or_empty_legacy_decision_grants_nothing(self):
        for value in (None, []):
            self.assertEqual([], review.bind({}, value, {}, offered_schema={}, verified_refs={}))
        self.assertEqual("UNVERIFIED", self.assessment["status"])

    def test_real_loader_preserves_legacy_raw_and_defaults_only_nullable_tester_field(self):
        for stage in ("sol", "astra_checkpoint", "sol_report_repair", "astra_checkpoint_report_repair"):
            base = stage.removesuffix("_report_repair")
            nested = base == "astra_checkpoint"
            fields = {review.FIELD: copy.deepcopy(review.SCHEMA)}
            schema = roles.obj({"validation": roles.obj(fields)}) if nested else roles.obj(fields)
            raw = {"validation": {}} if nested else {}
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "report.json"
                saved = root / "schema.json"
                output.write_text(json.dumps(raw))
                saved.write_text(json.dumps(model_output_schema(schema)))
                record = {"stage": stage, "output": str(output), "schema": str(saved)}
                if stage.endswith("_report_repair"):
                    record["original_stage"] = base
                loaded = runner.load_stage_report(record, state={})
                self.assertIsNone((loaded["validation"] if nested else loaded)[review.FIELD])
                self.assertEqual(raw, json.loads(Path(record["reported_output"]).read_text()))
                self.assertEqual(loaded, runner.load_stage_report(record, state={}))
        for stage in ("terra", "astra_review"):
            self.assertEqual({}, reports.normalize_absent_authority({}, roles.obj(fields), {"stage": stage}))
        self.assertEqual({}, reports.normalize_absent_authority({}, roles.obj({}), {"stage": "sol"}))
        supplied = {review.FIELD: [self.decision]}
        self.assertEqual(supplied, reports.normalize_absent_authority(supplied, roles.obj(fields), {"stage": "sol"}))

    def test_public_verification_projection_copies_reasons_gaps_and_fallback(self):
        state = {"regression_proof": {"changed_code_evidence": self.assessment}}
        projected = view.project(state)
        self.assertEqual(self.assessment, projected["changed_code_evidence"])
        self.assertEqual("not_recorded", projected["freshness"])
        projected["changed_code_evidence"]["obligations"].clear()
        self.assertEqual(1, len(self.assessment["obligations"]))
        self.assertIsNone(view.project({})["changed_code_evidence"])


class TesterOnlyGapTests(unittest.TestCase):
    """A gap no Builder test can close goes to the Tester, never back to the unchanged Builder."""

    def setUp(self):
        # Line 2 has a surviving mutant (a test can kill it); line 3 has no mutation outcome at all.
        self.assessment = evidence.assess(
            [{"path": "calc.py", "sha256": "a" * 64, "lines": [2, 3]}],
            None,
            {
                "source_revision": "source-one",
                "proof_context": "proof-one",
                "files": [
                    {
                        "path": "calc.py",
                        "sha256": "a" * 64,
                        "mutants": [{"line": 2, "outcome": "survived", "operator": "swap_bound", "tests": []}],
                    }
                ],
            },
            source_revision="source-one",
            proof_context="proof-one",
        )
        self.survivor, self.no_site = self.assessment["obligations"]
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.schema = Path(folder.name) / "tester.schema.json"
        self.schema.write_text(json.dumps(wire(state={"regression_proof": {"changed_code_evidence": self.assessment}})))
        self.state = {"regression_proof": {"changed_code_evidence": self.assessment, "path": "/run/proof.json"}}
        self.record = {"stage": "sol", "source_revision": "source-one", "schema": str(self.schema)}

    def test_only_the_line_with_no_mutation_outcome_needs_the_testers_decision(self):
        self.assertEqual("reason_required", self.no_site["status"])
        self.assertEqual([], self.no_site["mutation_outcomes"])
        self.assertTrue(self.survivor["mutation_outcomes"])
        offered = json.loads(self.schema.read_text())
        self.assertEqual([self.no_site], review.tester_only_gaps(self.assessment, [], offered, "sol"))
        decided = [{"gap_id": self.no_site["gap_id"], "decision": "reject"}]
        self.assertEqual([], review.tester_only_gaps(self.assessment, decided, offered, "sol"))
        by_handle = [{"gap_id": self.no_site["handle"], "decision": "reject"}]
        self.assertEqual([], review.tester_only_gaps(self.assessment, by_handle, offered, "sol"))
        self.assertEqual([], review.tester_only_gaps(self.assessment, [], {"properties": {}}, "sol"))

    def test_a_pass_that_leaves_it_undecided_is_returned_for_that_decision(self):
        import autocode_changed_code_review_application as application

        with self.assertRaisesRegex(ValueError, r"calc\.py:3 \(changed-code:[0-9a-f]+\)") as raised:
            application.require_tester_decisions(
                self.state, {"verdict": "PASS", "changed_code_exceptions": []}, self.record
            )
        self.assertNotIn("calc.py:2", str(raised.exception))
        self.assertIn(self.no_site["handle"] + " calc.py:3 (", str(raised.exception))
        self.assertIn("/run/proof.json", str(raised.exception))
        for value, record in (
            ({"verdict": "FAIL", "changed_code_exceptions": []}, self.record),
            ({"verdict": "PASS", "changed_code_exceptions": None}, {**self.record, "stage": "sol_report_repair"}),
            ({"verdict": "PASS", "changed_code_exceptions": []}, {**self.record, "source_revision": "stale"}),
            (
                {"verdict": "PASS", "changed_code_exceptions": [{"gap_id": self.no_site["gap_id"]}]},
                self.record,
            ),
        ):
            with self.subTest(value=value, stage=record["stage"]):
                self.assertIsNone(application.require_tester_decisions(self.state, value, record))

    def test_the_builder_receives_the_testers_rejected_reason(self):
        import autocode_changed_code_rework as rework

        state = {
            "status": "RUNNING",
            "iteration": 1,
            "regression_proof": {"verdict": "PASS", "changed_code_evidence": self.assessment},
            "current_task": {"id": "task-1"},
            "goal_contract": {"hash": "h" * 64, "revision": 1},
        }
        record = {"stage": "sol", "role": "sol", "source_revision": "source-one", "output": "/run/tester.json"}
        rejected = [{"path": "calc.py", "line": 3, "reason": "No test calls adjust(0); add one asserting 0."}]
        self.assertTrue(rework.route(state, record, investigation_enabled=False, rejected=rejected))
        self.assertEqual(("terra", "builder"), (state["next_stage"], state["regression_proofs"][-1]["action"]))
        self.assertIn(
            "Tester rejected a reason for calc.py:3: No test calls adjust(0); add one asserting 0.",
            state["next_action"],
        )
