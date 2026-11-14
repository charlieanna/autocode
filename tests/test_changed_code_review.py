"""Changed-line reasons need an independent, exact-bound and evidenced decision."""

import copy
import hashlib
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


CALC = {"calc.py": {"sha256": "a" * 64, "lines": {"2": "return a + b"}}}


def assessment():
    return evidence.assess(
        [{"path": "calc.py", "sha256": "a" * 64, "lines": [2]}],
        None,
        None,
        source_revision="source-one",
        proof_context="proof-one",
        texts=CALC,
    )


def wire(stage="tester", state=None):
    schema = (
        workflow.checkpoint_schema(SCHEMAS)
        if stage == "review_checkpoint"
        else roles.role_schema(json.loads((SCHEMAS / "v2/sol-report.schema.json").read_text()), "tester")
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
            "source_line": "return a + b",
            "decision": "accept",
            "reason_kind": "equivalent_mutant",
            "reason": "The replaced literal is unreachable under the independently checked input invariant.",
            "evidence_refs": ["check:1", "calc.py"],
        }
        self.record = {
            "stage": "tester",
            "role": "tester",
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
        schema = roles.role_schema(json.loads((SCHEMAS / "v2/sol-report.schema.json").read_text()), "tester")
        state = {"regression_proof": {"changed_code_evidence": self.assessment}}
        original = copy.deepcopy((schema, state))
        field = reports.review_generation_schema(schema, state, "tester")["properties"][review.FIELD]
        self.assertEqual(["L1", self.gap["gap_id"]], field["items"]["properties"]["gap_id"]["enum"])
        self.assertEqual(1, field["maxItems"])
        self.assertEqual(["array", "null"], field["type"])
        self.assertNotIn(review.FIELD, schema["required"])
        self.assertEqual(original, (schema, state))
        field = reports.review_generation_schema(schema, {}, "tester")["properties"][review.FIELD]
        self.assertEqual(0, field["maxItems"])
        with self.assertRaises(ValueError):
            validate_schema([self.decision], field)

    def test_checkpoint_offers_same_binding_but_builder_self_check_does_not(self):
        field = wire("review_checkpoint")["properties"]["validation"]["properties"][review.FIELD]
        self.assertEqual(["L1", self.gap["gap_id"]], field["items"]["properties"]["gap_id"]["enum"])
        builder = workflow.implementation_schema(SCHEMAS)
        self.assertNotIn(
            review.FIELD, builder["properties"]["continuation"]["properties"]["self_assessment"]["properties"]
        )
        before = copy.deepcopy(builder)
        result = reports.review_generation_schema(
            builder, {"regression_proof": {"changed_code_evidence": self.assessment}}, "builder"
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
        self.assertEqual({"stage": "tester", "role": "tester", "output": "/run/tester.json"}, bound[0]["reviewer"])
        result = evidence.assess(
            [{"path": "calc.py", "sha256": "a" * 64, "lines": [2]}],
            None,
            None,
            source_revision="source-one",
            proof_context="proof-one",
            exceptions=bound,
            texts=CALC,
        )
        self.assertEqual("PASS", result["status"])
        self.assertEqual("excepted", result["obligations"][0]["status"])
        self.assertEqual(before, (self.assessment, self.decision, self.record, self.refs))

    def test_missing_test_reason_kind_binds_for_reject_but_never_grants_accept(self):
        rejected = {
            **self.decision,
            "decision": "reject",
            "reason_kind": "missing_test",
            "reason": "Add a unit test asserting negative inputs raise ValueError.",
        }
        schema = wire()
        validate_schema([rejected], schema["properties"][review.FIELD])
        bound = self.bind([rejected], offered_schema=schema)
        self.assertFalse(bound[0]["accepted"])
        self.assertEqual("missing_test", bound[0]["reason_kind"])
        result = evidence.assess(
            [{"path": "calc.py", "sha256": "a" * 64, "lines": [2]}],
            None,
            None,
            source_revision="source-one",
            proof_context="proof-one",
            exceptions=bound,
            texts=CALC,
        )
        self.assertEqual("UNVERIFIED", result["status"])
        self.assertEqual("reason_required", result["obligations"][0]["status"])

        invalid_accept = {**rejected, "decision": "accept"}
        with self.assertRaisesRegex(ValueError, "explicit judgment, reason and evidence"):
            self.bind([invalid_accept], offered_schema=schema)

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

    def test_legacy_id_only_offer_still_binds_ids_and_handles_of_current_gaps(self):
        legacy = wire()
        props = legacy["properties"][review.FIELD]["items"]["properties"]
        props["gap_id"]["enum"] = [self.gap["gap_id"]]
        self.assertTrue(self.bind(offered_schema=legacy))
        # A handle is a runner-assigned identity in the proof document the Tester reads, so it
        # names the same gap the legacy id names; only a name no current obligation has is stale (#3286).
        self.decision["gap_id"] = "L1"
        self.assertTrue(self.bind(offered_schema=legacy))
        for stale in ("L9", self.gap["gap_id"] + "0"):
            with self.subTest(stale=stale):
                self.decision["gap_id"] = stale
                with self.assertRaisesRegex(ValueError, "duplicate, unknown or stale"):
                    self.bind(offered_schema=legacy)

    @staticmethod
    def state(value):
        return {"regression_proof": {"changed_code_evidence": value}}

    GROWN_TEXTS = {"calc.py": {"sha256": "a" * 64, "lines": {"2": "return a + b", "3": "return a - b"}}}

    def grown(self, mutation=None):
        return evidence.assess(
            [{"path": "calc.py", "sha256": "a" * 64, "lines": [2, 3]}],
            None,
            mutation,
            source_revision="source-one",
            proof_context="proof-one",
            texts=self.GROWN_TEXTS,
        )

    def test_a_gap_the_proof_made_current_after_the_offer_still_binds(self):
        """#3286: the generation enum is a launch-time snapshot; the proof a Tester read can be newer."""
        offered = wire(state=self.state(self.assessment))  # one gap: L1
        grown = self.grown()
        fresh = grown["obligations"][1]
        self.assertEqual("reason_required", fresh["status"])
        self.assertNotIn(fresh["handle"], offered["properties"][review.FIELD]["items"]["properties"]["gap_id"]["enum"])
        widened = reports.review_validation_schema(
            offered, self.state(grown), {"stage": "tester"}, {"changed_code_exceptions": []}
        )
        field = widened["properties"][review.FIELD]
        offered_field = offered["properties"][review.FIELD]
        self.assertIn(fresh["handle"], field["items"]["properties"]["gap_id"]["enum"])
        decision = {**self.decision, "gap_id": fresh["handle"], "source_line": "return a - b"}
        with self.assertRaises(ValueError):
            validate_schema([decision], offered_field)
        validate_schema([decision], field)
        bound = review.bind(grown, [decision], self.record, offered_schema=offered, verified_refs=self.refs)
        self.assertEqual(fresh["gap_id"], bound[0]["gap_id"])

    def test_validation_widening_still_refuses_a_gap_no_current_obligation_names(self):
        grown = self.grown()
        widened = reports.review_validation_schema(
            wire(state=self.state(grown)), self.state(grown), {"stage": "tester"}, {}
        )
        field = widened["properties"][review.FIELD]
        for stale in ("L99", grown["obligations"][0]["gap_id"] + "0"):
            with self.subTest(stale=stale):
                self.decision["gap_id"] = stale
                with self.assertRaises(ValueError):
                    validate_schema([self.decision], field)

    def test_a_decision_on_an_already_tested_line_binds_but_never_applies(self):
        mutation = {
            "source_revision": "source-one",
            "proof_context": "proof-one",
            "files": [
                {
                    "path": "calc.py",
                    "sha256": "a" * 64,
                    "mutants": [
                        {"line": 2, "outcome": "killed", "evidence": ["receipt:m2"], "failed_tests": ["test_add"]}
                    ],
                }
            ],
        }
        mixed = self.grown(mutation)
        tested, undecided = mixed["obligations"]
        self.assertEqual(("tested", "reason_required"), (tested["status"], undecided["status"]))
        offered = wire(state=self.state(mixed))
        enum = offered["properties"][review.FIELD]["items"]["properties"]["gap_id"]["enum"]
        self.assertIn(undecided["handle"], enum)
        self.assertNotIn(tested["handle"], enum)
        decision = {
            **self.decision,
            "gap_id": tested["handle"],
            "source_line": "return a + b",
            "reason": "Independently checked: the mutant on this line is already killed by test_add.",
        }
        bound = review.bind(mixed, [decision], self.record, offered_schema=offered, verified_refs=self.refs)
        self.assertEqual(tested["gap_id"], bound[0]["gap_id"])
        again = evidence.assess(
            [{"path": "calc.py", "sha256": "a" * 64, "lines": [2, 3]}],
            None,
            mutation,
            source_revision="source-one",
            proof_context="proof-one",
            exceptions=bound,
            texts=self.GROWN_TEXTS,
        )
        # A decision on a demonstrated line never displaces its mutation evidence.
        self.assertEqual(["tested", "reason_required"], [row["status"] for row in again["obligations"]])
        self.assertEqual("UNVERIFIED", again["status"])

    def test_an_offer_naming_no_current_obligation_still_refuses(self):
        foreign = wire()
        props = foreign["properties"][review.FIELD]["items"]["properties"]
        props["gap_id"]["enum"] = ["changed-code:" + "9" * 64]
        with self.assertRaisesRegex(ValueError, "no exact current offered gap binding"):
            self.bind(offered_schema=foreign)

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
            ("review_checkpoint", "task_planner", None),
            ("tester_report_repair", "tester", "tester"),
            ("review_checkpoint_report_repair", "task_planner", "review_checkpoint"),
        ):
            with self.subTest(stage=stage):
                record = {**self.record, "stage": stage, "role": role}
                if original:
                    record["original_stage"] = original
                bound = self.bind(record=record, offered_schema=wire(original or stage))
                self.assertTrue(bound[0]["accepted"])

    def test_builder_self_check_wrong_role_and_source_edits_never_authorize(self):
        for patch in (
            {"stage": "builder", "role": "builder"},
            {"stage": "self_check", "role": "builder"},
            {"stage": "review_completion", "role": "task_planner"},
            {"role": "builder"},
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
        for stage in ("tester", "review_checkpoint", "tester_report_repair", "review_checkpoint_report_repair"):
            base = stage.removesuffix("_report_repair")
            nested = base == "review_checkpoint"
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
        for stage in ("builder", "review_completion"):
            self.assertEqual({}, reports.normalize_absent_authority({}, roles.obj(fields), {"stage": stage}))
        self.assertEqual({}, reports.normalize_absent_authority({}, roles.obj({}), {"stage": "tester"}))
        supplied = {review.FIELD: [self.decision]}
        self.assertEqual(supplied, reports.normalize_absent_authority(supplied, roles.obj(fields), {"stage": "tester"}))

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
        self.record = {"stage": "tester", "source_revision": "source-one", "schema": str(self.schema)}

    def test_only_the_line_with_no_mutation_outcome_needs_the_testers_decision(self):
        self.assertEqual("reason_required", self.no_site["status"])
        self.assertEqual([], self.no_site["mutation_outcomes"])
        self.assertTrue(self.survivor["mutation_outcomes"])
        offered = json.loads(self.schema.read_text())
        self.assertEqual([self.no_site], review.tester_only_gaps(self.assessment, [], offered, "tester"))
        decided = [{"gap_id": self.no_site["gap_id"], "decision": "reject"}]
        self.assertEqual([], review.tester_only_gaps(self.assessment, decided, offered, "tester"))
        by_handle = [{"gap_id": self.no_site["handle"], "decision": "reject"}]
        self.assertEqual([], review.tester_only_gaps(self.assessment, by_handle, offered, "tester"))
        self.assertEqual([], review.tester_only_gaps(self.assessment, [], {"properties": {}}, "tester"))

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
            ({"verdict": "PASS", "changed_code_exceptions": None}, {**self.record, "stage": "tester_report_repair"}),
            # autocode_repaired_result applies a repaired report with its original stage's record, so
            # the bounded repair is recognized by report_repaired: an undecided gap it leaves goes to
            # the Builder and Investigator rounds instead of rejecting the whole repaired PASS.
            ({"verdict": "PASS", "changed_code_exceptions": []}, {**self.record, "report_repaired": True}),
            ({"verdict": "PASS", "changed_code_exceptions": []}, {**self.record, "source_revision": "stale"}),
            (
                {"verdict": "PASS", "changed_code_exceptions": [{"gap_id": self.no_site["gap_id"]}]},
                self.record,
            ),
        ):
            with self.subTest(value=value, stage=record["stage"]):
                self.assertIsNone(application.require_tester_decisions(self.state, value, record))

    def test_only_an_explicit_repair_mark_skips_the_decision_request(self):
        import autocode_changed_code_review_application as application

        for mark in (False, "true", 1, None):
            with self.subTest(mark=mark), self.assertRaisesRegex(ValueError, "no mutation outcome"):
                application.require_tester_decisions(
                    self.state,
                    {"verdict": "PASS", "changed_code_exceptions": []},
                    {**self.record, "report_repaired": mark},
                )

    def test_missing_or_stale_bindings_do_not_establish_an_omitted_current_decision(self):
        import autocode_changed_code_review_application as application

        new_proof = evidence.assess(
            [{"path": "calc.py", "sha256": "a" * 64, "lines": [3]}],
            None,
            None,
            source_revision="source-one",
            proof_context="new-proof-context",
        )
        for state, record in (
            ({}, self.record),
            (self.state, {**self.record, "schema": None}),
            (self.state, {**self.record, "source_revision": "different-source"}),
            ({"regression_proof": {"changed_code_evidence": new_proof}}, self.record),
        ):
            with self.subTest(state=state, record=record):
                # This is no acceptance: the existing fresh checks and exact
                # evidence binder must still establish their own prerequisites.
                self.assertIsNone(application.require_tester_decisions(state, {"verdict": "PASS"}, record))

    def test_preflight_requires_executed_checks_before_requesting_gap_decisions(self):
        import autocode_changed_code_review_application as application

        value = {"verdict": "PASS", "checks": []}
        with self.assertRaisesRegex(ValueError, "lacks successful executed checks"):
            application.preflight(self.state, value, self.record, passing_claim=True)
        value["checks"] = [{"exit_code": 0, "evidence_ref": "check-output"}]
        with self.assertRaisesRegex(ValueError, "no mutation outcome"):
            application.preflight(self.state, value, self.record, passing_claim=True)
        value[review.FIELD] = [{"gap_id": self.no_site["gap_id"], "decision": "reject"}]
        before = copy.deepcopy((self.state, value, self.record))
        application.preflight(self.state, value, self.record, passing_claim=True)
        self.assertEqual(before, (self.state, value, self.record))
        # Supplying a decision only makes the report complete enough to replay;
        # no accepted changed-line decision or validation is installed here.
        self.assertNotIn("validation", self.state)
        self.assertNotIn("changed_code_decisions", value)

    def test_the_proof_file_binds_by_its_workspace_relative_path_too(self):
        """A live Tester cited the proof as .autocode/runs/<run>/regression/<proof>/verification.json."""
        import autocode_changed_code_review_application as application

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        workspace = Path(folder.name) / "project"
        proof = workspace / ".autocode/runs/run-1/regression/proof-07-abc/verification.json"
        proof.parent.mkdir(parents=True)
        proof.write_text("{}")
        state = {"regression_proof": {"changed_code_evidence": self.assessment, "path": str(proof)}}
        record = {
            **self.record,
            "role": "tester",
            "changed_files": [],
            "output": str(workspace / ".autocode/runs/run-1/tester.json"),
        }
        relative = ".autocode/runs/run-1/regression/proof-07-abc/verification.json"

        def validation(ref):
            decisions = [
                {
                    "gap_id": gap["handle"],
                    "source_line": "",
                    "decision": "reject",
                    "reason_kind": "equivalent_mutant",
                    "reason": "No test observes this line.",
                    "evidence_refs": [ref],
                }
                for gap in (self.survivor, self.no_site)
            ]
            return {"checks": [], "evidence_hashes": {}, "changed_code_exceptions": decisions}

        def pin(refs):
            return {ref: "h" * 64 for ref in refs}

        accepted = validation(relative)
        application.accept(state, accepted, record, pin=pin, workspace=workspace)
        self.assertEqual([[str(proof)], [str(proof)]], [row["evidence"] for row in accepted["changed_code_decisions"]])
        # Without the workspace, or spelled any other way, the citation stays unauthenticated.
        for ref, options in ((relative, {}), ("../project/" + relative, {"workspace": workspace})):
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, "unauthenticated evidence"):
                application.accept(state, validation(ref), record, pin=pin, **options)

    def test_the_builder_receives_the_testers_rejected_reason(self):
        import autocode_changed_code_rework as rework

        state = {
            "status": "RUNNING",
            "iteration": 1,
            "regression_proof": {"verdict": "PASS", "changed_code_evidence": self.assessment},
            "current_task": {"id": "task-1"},
            "goal_contract": {"hash": "h" * 64, "revision": 1},
        }
        record = {"stage": "tester", "role": "tester", "source_revision": "source-one", "output": "/run/tester.json"}
        rejected = [{"path": "calc.py", "line": 3, "reason": "No test calls adjust(0); add one asserting 0."}]
        self.assertTrue(rework.route(state, record, investigation_enabled=False, rejected=rejected))
        self.assertEqual(("builder", "builder"), (state["next_stage"], state["regression_proofs"][-1]["action"]))
        self.assertIn(
            "Tester rejected a reason for calc.py:3: No test calls adjust(0); add one asserting 0.",
            state["next_action"],
        )


def corpus_source():
    """A TypeScript helper shaped like the S6 sample in #2977 (vscode-extension/test/helpers/action-corpus.ts)."""
    lines = [f"// filler {number}" for number in range(1, 46)]
    lines[0] = 'import { readFileSync } from "node:fs";'
    lines[3] = "/**"
    lines[4] = " * The shared action corpus every client's next-command test reads."
    lines[5] = " */"
    lines[13] = 'export const SURFACE = "typescript_client";'
    lines[28] = "export interface CorpusCase {"
    lines[29] = "  surfaces:   string[];"
    lines[30] = "}"
    lines[39] = "export function repositoryRoot(): string {"
    lines[40] = "  return " + " + ".join(f'"segment{n}"' for n in range(30)) + ";"
    return ("\n".join(lines) + "\n").encode()


class ExactLineTests(unittest.TestCase):
    """A reason must describe the exact line its handle binds (#2977)."""

    PATH = "vscode-extension/test/helpers/action-corpus.ts"
    LINES = [5, 14, 30, 40, 41]

    def setUp(self):
        self.data = corpus_source()
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.texts = {self.PATH: evidence.line_texts(self.data, self.digest, self.LINES)}
        self.inventory = [{"path": self.PATH, "sha256": self.digest, "lines": self.LINES}]
        self.assessment = self.assess()
        self.comment, self.surface, self.typing, self.root, self.long = self.assessment["obligations"]
        self.record = {
            "stage": "tester",
            "role": "tester",
            "source_revision": "source-one",
            "changed_files": [],
            "output": "/run/tester.json",
        }
        self.refs = {"check:1": "event:verified", self.PATH: "/workspace/" + self.PATH}

    def assess(self, exceptions=()):
        return evidence.assess(
            self.inventory,
            None,
            None,
            source_revision="source-one",
            proof_context="proof-one",
            exceptions=exceptions,
            texts=self.texts,
        )

    def decision(self, gap, source_line, reason, kind="instrumentation_unavailable"):
        return {
            "gap_id": gap["handle"],
            "source_line": source_line,
            "decision": "accept",
            "reason_kind": kind,
            "reason": reason,
            "evidence_refs": ["check:1", self.PATH],
        }

    def bind(self, decisions):
        return review.bind(
            self.assessment,
            decisions,
            self.record,
            offered_schema=wire(state={"regression_proof": {"changed_code_evidence": self.assessment}}),
            verified_refs=self.refs,
        )

    def test_each_gap_carries_the_bounded_text_of_its_own_line(self):
        self.assertEqual(
            [
                "* The shared action corpus every client's next-command test reads.",
                'export const SURFACE = "typescript_client";',
                "surfaces: string[];",
                "export function repositoryRoot(): string {",
            ],
            [row["source_line"] for row in self.assessment["obligations"][:4]],
        )
        self.assertEqual(evidence.SOURCE_LINE_LIMIT, len(self.long["source_line"]))
        self.assertTrue(self.long["source_line"].startswith('return "segment0" + "segment1"'))
        # Other bytes than the inventoried file give no text: the handle alone still binds.
        stale = {self.PATH: evidence.line_texts(self.data + b"// edited\n", self.digest, self.LINES)}
        self.assertEqual({self.PATH: None}, stale)
        moved = {self.PATH: {**self.texts[self.PATH], "sha256": "b" * 64}}
        for texts in (stale, moved, None, {"other.ts": self.texts[self.PATH]}):
            rows = evidence.assess(
                self.inventory, None, None, source_revision="source-one", proof_context="proof-one", texts=texts
            )["obligations"]
            self.assertFalse(any("source_line" in row for row in rows))

    def test_the_retained_s6_reasons_that_describe_other_lines_are_refused(self):
        """The accepted S6 report put reasons for one line under the handle of another."""
        drifted = [
            # Bound to the corpus comment, the reason explains the SURFACE constant.
            self.decision(
                self.comment,
                'export const SURFACE = "typescript_client";',
                "SURFACE names this client's surface in the shared corpus; a different constant drops every case.",
            ),
            # Bound to SURFACE, the reason explains CorpusCase.surfaces typing.
            self.decision(
                self.surface,
                "surfaces: string[];",
                "CorpusCase.surfaces is a type annotation with no runtime behavior to mutate.",
            ),
            # Bound to repositoryRoot(), the reason explains an import in another file.
            self.decision(
                self.root,
                'import { applicableCases } from "./helpers/action-corpus";',
                "The import in next-command.test.ts is exercised by every corpus test.",
            ),
        ]
        with self.assertRaises(ValueError) as raised:
            self.bind(drifted)
        message = str(raised.exception)
        for gap in (self.comment, self.surface, self.root):
            self.assertIn(f"{gap['handle']} quotes ", message)
            self.assertIn(f"{self.PATH}:{gap['line']}, which is {json.dumps(gap['source_line'])}", message)
        self.assertIn("must justify that exact line", message)
        # One drifted row among correct ones still returns the whole report.
        correct = self.decision(self.typing, "surfaces: string[];", "A type annotation; tsc checks it.")
        with self.assertRaisesRegex(ValueError, f"{self.surface['handle']} quotes"):
            self.bind([correct, drifted[1]])

    def test_the_mismatch_returns_the_report_before_check_replay_even_after_a_repair(self):
        import autocode_changed_code_review_application as application

        state = {"regression_proof": {"changed_code_evidence": self.assessment, "path": "/run/proof.json"}}
        value = {
            "verdict": "PASS",
            "checks": [{"exit_code": 0, "evidence_ref": "check-output"}],
            review.FIELD: [self.decision(self.surface, "surfaces: string[];", "A type annotation.")],
        }
        for record in (self.record, {**self.record, "report_repaired": True}):
            with self.subTest(record=record), self.assertRaisesRegex(ValueError, "describes a different line"):
                application.preflight(state, value, record, passing_claim=True)
        # Another source revision is not this proof's review; the binder refuses it on its own terms.
        self.assertIsNone(application.require_exact_lines(state, value, {**self.record, "source_revision": "x"}))

    def test_exact_quotes_bind_comment_type_code_and_long_lines(self):
        """Positive controls: a reason that names its own line binds, whatever kind of line it is."""
        decisions = [
            self.decision(
                self.comment,
                " * The shared action corpus every client's next-command test reads.",
                "A documentation comment inside a block the inventory could not classify; it has no runtime behavior.",
                "defensive_unreachable",
            ),
            self.decision(
                self.surface,
                'export   const SURFACE = "typescript_client";',  # whitespace differences are not drift
                "next-command.test.ts filters the corpus by SURFACE and asserts the typescript_client cases run.",
            ),
            self.decision(self.typing, "\tsurfaces: string[];", "A type annotation erased at compile time."),
            self.decision(
                self.root,
                "export function repositoryRoot(): string {",
                "The function header; its body is exercised by every corpus load.",
            ),
            # The whole long line, or the bounded text the handoff showed, names the same line.
            self.decision(self.long, corpus_source().decode().splitlines()[40], "The returned path literal."),
        ]
        bound = self.bind(decisions)
        self.assertEqual(
            [gap["source_line"] for gap in self.assessment["obligations"]], [row["source_line"] for row in bound]
        )
        result = self.assess(bound)
        self.assertEqual("PASS", result["status"])
        self.assertEqual({"excepted"}, {row["status"] for row in result["obligations"]})
        decisions[-1]["source_line"] = self.long["source_line"]
        self.assertTrue(self.bind(decisions[-1:]))

    def test_the_assessor_never_excepts_a_line_with_another_lines_quote(self):
        bound = self.bind([self.decision(self.surface, self.surface["source_line"], "SURFACE is asserted.")])
        self.assertEqual("excepted", self.assess(bound)["obligations"][1]["status"])
        bound[0]["source_line"] = self.typing["source_line"]
        self.assertEqual("reason_required", self.assess(bound)["obligations"][1]["status"])

    def test_a_report_offered_the_legacy_fields_binds_without_a_quote(self):
        legacy = wire(state={"regression_proof": {"changed_code_evidence": self.assessment}})
        legacy["properties"][review.FIELD]["items"]["properties"].pop("source_line")
        row = self.decision(self.surface, "", "SURFACE is asserted.")
        del row["source_line"]
        bound = review.bind(self.assessment, [row], self.record, offered_schema=legacy, verified_refs=self.refs)
        self.assertEqual(self.surface["source_line"], bound[0]["source_line"])
        # A current offer requires the quote.
        with self.assertRaisesRegex(ValueError, "Malformed"):
            self.bind([row])

    def test_the_request_for_undecided_lines_quotes_each_line(self):
        """The repair handoff carries this error, not the proof's rows, so it names each line's text."""
        import autocode_changed_code_review_application as application

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        schema = Path(folder.name) / "tester.schema.json"
        schema.write_text(json.dumps(wire(state={"regression_proof": {"changed_code_evidence": self.assessment}})))
        state = {"regression_proof": {"changed_code_evidence": self.assessment, "path": "/run/proof.json"}}
        with self.assertRaises(ValueError) as raised:
            application.require_tester_decisions(
                state, {"verdict": "PASS", review.FIELD: []}, {**self.record, "schema": str(schema)}
            )
        self.assertIn(
            f"{self.surface['handle']} {self.PATH}:14 ({self.surface['gap_id']}) "
            + json.dumps('export const SURFACE = "typescript_client";'),
            str(raised.exception),
        )
        self.assertIn("as source_line", str(raised.exception))
