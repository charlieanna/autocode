"""Deterministic complete-case policy tests; no providers or image authority."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import autocode_design_plan as design_plan
import autocode_util as util
import autocode_visual_scope as visual_scope


class VisualScopeTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"version": 1, "cases": [
            {"id": "home.empty", "implementation_paths": ["ui/home.py", "ui/shared.css"]},
            {"id": "home.filled", "implementation_paths": ["ui/home.py", "ui/shared.css"]},
            {"id": "detail", "implementation_paths": ["ui/detail.py", "ui/shared.css"]},
            {"id": "final", "implementation_paths": ["ui/final.py"]}]}
        self.mapping = {"home.empty": ["C1"], "home.filled": ["C1"], "detail": ["C2"], "final": ["C3"]}
        self.body = {"constraints": [visual_scope.MARKER + visual_scope.POLICY,
                                    visual_scope.CASE_MARKER + json.dumps(self.mapping)],
                     "acceptance_criteria": [{"id": "C1"}, {"id": "C2"}, {"id": "C3"}],
                     "milestones": [
                         {"id": "M1", "acceptance_criteria": ["C1"], "depends_on": [],
                          "affected_paths": ["ui/home.py", "ui/shared.css"]},
                         {"id": "M2", "acceptance_criteria": ["C2"], "depends_on": ["M1"],
                          "affected_paths": ["ui/detail.py", "ui/shared.css"]},
                         {"id": "M3", "acceptance_criteria": ["C3"], "depends_on": ["M2"],
                          "affected_paths": ["ui/final.py"]}],
                     "initial_task": {"milestone_id": "M1"}}
        self.inputs = {"home.empty": ["ui/home.py", "ui/shared.css", "fixtures/empty.json"],
                       "home.filled": ["ui/home.py", "ui/shared.css", "fixtures/filled.json"]}

    def task(self, milestone="M1", kind="implement", criteria=None, paths=None):
        row = next(row for row in self.body["milestones"] if row["id"] == milestone)
        return {"milestone_id": milestone, "kind": kind,
                "acceptance_criteria": list(criteria or row["acceptance_criteria"]),
                "affected_paths": list(paths or row["affected_paths"])}

    def scope(self, task=None, **options):
        return visual_scope.derive(self.body, self.manifest, task or self.task(), **options)

    def test_opt_out_preserves_absent_and_legacy_plan_behavior(self):
        for body in ({}, {"constraints": ["Ordinary review requirement"]}):
            self.assertIsNone(visual_scope.admit(body, None))
            self.assertIsNone(visual_scope.derive(body, None, {}))
            design_plan.validate(None, body, ready=True)
            design_plan.validate({"body": {"version": 1}}, body, ready=True)

    def test_policy_requires_one_exact_supported_declaration(self):
        for values in ([], ["other"], [visual_scope.POLICY, visual_scope.POLICY],
                       [visual_scope.POLICY + " "]):
            body = {"constraints": [visual_scope.MARKER + value for value in values]}
            if not values:
                self.assertIsNone(visual_scope.policy(body))
            else:
                with self.subTest(values=values), self.assertRaisesRegex(ValueError, "one exact"):
                    visual_scope.policy(body)

    def test_serial_admission_rejects_two_ready_owners_and_progressive_disclosure(self):
        broken = deepcopy(self.body)
        broken["milestones"][1]["depends_on"] = []
        with self.assertRaisesRegex(ValueError, "serial milestone dependency chain"):
            visual_scope.admit(broken, self.manifest)
        broken = deepcopy(self.body)
        broken["milestones"][0]["depends_on"] = ["M3"]
        with self.assertRaisesRegex(ValueError, "serial milestone dependency chain"):
            visual_scope.admit(broken, self.manifest)

    def test_progressive_disclosure_in_either_plan_field_refuses_all_existing_prefixes(self):
        prefixes = (visual_scope.progressive.DISCLOSURE_DELEGATION,
                    visual_scope.progressive.DISCLOSURE_SLICE,
                    visual_scope.progressive.DISCLOSURE_OUTSTANDING)
        for field in ("constraints", "technical_approach"):
            for prefix in prefixes:
                body = deepcopy(self.body)
                body.setdefault(field, []).append(prefix + " retained runner disclosure")
                before = deepcopy((body, self.manifest))
                with self.subTest(field=field, prefix=prefix), self.assertRaisesRegex(
                        ValueError, "progressive disclosure"):
                    visual_scope.admit(body, self.manifest)
                self.assertEqual(before, (body, self.manifest))

    def test_v1_requires_full_mapping_unique_cid_owner_and_complete_paths(self):
        changes = (
            lambda body, manifest: body["constraints"].pop(),
            lambda body, manifest: body["constraints"].__setitem__(1, visual_scope.CASE_MARKER +
                '{"home.empty":["C1"],"home.empty":["C1"],"detail":["C2"],"final":["C3"]}'),
            lambda body, manifest: body["milestones"][1]["acceptance_criteria"].append("C1"),
            lambda body, manifest: manifest["cases"][0]["implementation_paths"].append("outside.py"),
            lambda body, manifest: manifest["cases"][0].__setitem__("implementation_paths", ["ui"]),
            lambda body, manifest: manifest["cases"][0].__setitem__("implementation_paths", []))
        for change in changes:
            body, manifest = deepcopy(self.body), deepcopy(self.manifest)
            change(body, manifest)
            with self.subTest(change=change), self.assertRaises(ValueError):
                visual_scope.admit(body, manifest)

    def v2(self):
        body, manifest = deepcopy(self.body), deepcopy(self.manifest)
        manifest["version"] = 2
        body["design_coverage"] = {"manifest_hash": util.digest(manifest),
            "cases": [{"id": identity, "criterion_ids": list(criteria),
                       "milestone_ids": ["M1" if identity.startswith("home") else "M2" if identity == "detail" else "M3"]}
                      for identity, criteria in self.mapping.items()], "responsive_derivations": []}
        return body, manifest

    def test_v2_keeps_exact_structured_owner_mapping_and_reference_identity(self):
        body, manifest = self.v2()
        self.assertEqual(["M1", "M2", "M3"], visual_scope.admit(body, manifest)["milestone_ids"])
        mutations = (
            lambda body: body["design_coverage"]["cases"][0]["milestone_ids"].append("M2"),
            lambda body: body["design_coverage"]["cases"][0].__setitem__("criterion_ids", ["C2"]),
            lambda body: body["design_coverage"].__setitem__("manifest_hash", "wrong"),
            lambda body: body["design_coverage"]["cases"].pop())
        for mutate in mutations:
            broken = deepcopy(body)
            mutate(broken)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                visual_scope.admit(broken, manifest)

    def test_existing_design_plan_validator_applies_opt_in_to_both_versions(self):
        # Verified manifest facts are stubbed only at this domain seam. This
        # test creates no screenshot, capture, delivery or acceptance authority.
        for body, manifest in ((self.body, self.manifest), self.v2()):
            record = {"body": manifest, "manifest_hash": util.digest(manifest)}
            with patch.object(design_plan.manifest, "verify", return_value=None):
                design_plan.validate(record, body, ready=True)
                broken = deepcopy(body)
                broken["milestones"][1]["depends_on"] = []
                with self.assertRaisesRegex(ValueError, "serial milestone dependency chain"):
                    design_plan.validate(record, broken, ready=True)
        with self.assertRaisesRegex(ValueError, "retained design inventory"):
            design_plan.validate(None, self.body, ready=True)

    def test_clarification_can_precede_complete_opt_in_plan(self):
        body = {"constraints": [visual_scope.MARKER + visual_scope.POLICY],
                "open_blocking_questions": [{"id": "Q1"}]}
        design_plan.validate(None, body)
        with self.assertRaises(ValueError):
            design_plan.validate(None, body, ready=True)

    def test_m1_narrow_paths_still_require_both_complete_owned_cases(self):
        before = deepcopy((self.body, self.manifest))
        result = self.scope(self.task(paths=["ui/home.py"]))
        self.assertEqual(["home.empty", "home.filled"], result["required_case_ids"])
        self.assertEqual(["detail", "final"], result["out_of_scope_case_ids"])
        self.assertEqual(self.mapping, result["case_criteria"])
        self.assertEqual(before, (self.body, self.manifest))

    def test_unrelated_m2_scope_does_not_claim_current_m1_images(self):
        result = self.scope(self.task("M2", paths=["ui/detail.py"]), accepted_milestones={"M1"},
                            accepted_case_input_paths=self.inputs, changed_paths=["ui/detail.py"])
        self.assertEqual(["detail"], result["required_case_ids"])
        self.assertEqual(["home.empty", "home.filled", "final"], result["out_of_scope_case_ids"])
        self.assertEqual("slice", result["mode"])

    def test_shared_path_requires_explicit_validate_task_without_mutating_implementation(self):
        implement = self.task("M2")
        saved = deepcopy(implement)
        options = {"accepted_milestones": ["M1"], "accepted_case_input_paths": self.inputs,
                   "changed_paths": ["ui/shared.css"]}
        current_only = self.scope(implement, **options)
        self.assertEqual(["detail"], current_only["required_case_ids"])
        self.assertEqual([], current_only["recheck_case_ids"])
        self.assertEqual(["home.empty", "home.filled", "final"], current_only["out_of_scope_case_ids"])
        self.assertEqual(["C2"], current_only["task_criterion_ids"])
        self.assertEqual(saved, implement)
        with self.assertRaisesRegex(ValueError, "explicit approved task criteria"):
            self.scope(self.task("M2", "validate"), **options)
        result = self.scope(self.task("M2", "validate", ["C1", "C2"]), **options)
        self.assertEqual(["home.empty", "home.filled", "detail"], result["required_case_ids"])
        self.assertEqual(["home.empty", "home.filled"], result["recheck_case_ids"])
        self.assertEqual(["final"], result["out_of_scope_case_ids"])
        self.assertEqual("slice", result["mode"])

    def test_shared_current_case_pass_does_not_release_a_stale_prerequisite_chain(self):
        # Native history/current identities are controlled caller facts only.
        # Real receipt/footprint and Builder admission authentication remain owed.
        before = deepcopy((self.body, self.manifest))
        self.assertEqual([], visual_scope.eligible_milestones(self.body, self.manifest,
            authenticated_milestone_ids=["M1", "M2"], current_case_ids=["detail"]))
        self.assertEqual(["M1"], visual_scope.eligible_milestones(self.body, self.manifest,
            authenticated_milestone_ids=["M1", "M2"], current_case_ids=["home.empty", "home.filled"]))
        self.assertEqual(["M1", "M2"], visual_scope.eligible_milestones(self.body, self.manifest,
            authenticated_milestone_ids=["M1", "M2"], current_case_ids=["home.empty", "home.filled", "detail"]))
        self.assertEqual(before, (self.body, self.manifest))

    def test_current_images_cannot_replace_original_owner_acceptance(self):
        self.assertEqual([], visual_scope.eligible_milestones(self.body, self.manifest,
            authenticated_milestone_ids=[], current_case_ids=list(self.mapping)))
        for history, current in ((["unknown"], []), ([], ["unknown"]), (["M1", "M1"], [])):
            with self.subTest(history=history, current=current), self.assertRaises(ValueError):
                visual_scope.eligible_milestones(self.body, self.manifest,
                    authenticated_milestone_ids=history, current_case_ids=current)

    def test_explicit_validate_recheck_and_existing_glob_scope_are_preserved(self):
        self.body["milestones"][1]["affected_paths"] = ["ui/**"]
        result = self.scope(self.task("M2", "validate", ["C2", "C1"], ["ui/detail.py"]),
            accepted_milestones=["M1"], accepted_case_input_paths=self.inputs)
        self.assertEqual(["C2", "C1"], result["task_criterion_ids"])
        self.assertEqual(["home.empty", "home.filled"], result["recheck_case_ids"])

    def test_missing_prior_inputs_unaccounted_writes_future_history_and_batches_refuse(self):
        with self.assertRaisesRegex(ValueError, "authenticated earlier owner acceptance"):
            self.scope(self.task("M2", paths=["ui/detail.py"]))
        with self.assertRaisesRegex(ValueError, "authenticated prior case input"):
            self.scope(self.task("M2", paths=["ui/detail.py"]), accepted_milestones=["M1"])
        with self.assertRaisesRegex(ValueError, "unaccounted source change"):
            self.scope(self.task(paths=["ui/home.py"]), changed_paths=["outside.py"])
        with self.assertRaisesRegex(ValueError, "future milestone acceptance"):
            self.scope(accepted_milestones=["M3"])
        task = self.task()
        task["milestone_ids"] = ["M1", "M2"]
        with self.assertRaisesRegex(ValueError, "serial implement or validate"):
            self.scope(task)

    def test_integration_selects_all_cases_only_on_explicit_all_criteria_validate(self):
        inputs = {**self.inputs, "detail": ["ui/detail.py", "ui/shared.css"]}
        result = self.scope(self.task("M3", "validate", ["C1", "C2", "C3"]),
            accepted_milestones=["M1", "M2"], accepted_case_input_paths=inputs)
        self.assertEqual("integration", result["mode"])
        self.assertEqual(list(self.mapping), result["required_case_ids"])
        self.assertEqual([], result["out_of_scope_case_ids"])
        # This result is only a requirement, not proof or a completion verdict.
        self.assertNotIn("passed", result)

    def test_bound_scope_cannot_borrow_only_prior_cases_for_an_unknown_current_owner(self):
        # Every other partition, mapping, prior footprint and explicit CID is
        # internally consistent. Binding shape alone still grants no authority.
        broken = self.scope()
        broken["milestone_ids"] = ["unknown"]
        broken["current_case_ids"] = []
        broken["recheck_case_ids"] = ["home.empty", "home.filled"]
        broken["accepted_milestone_ids"] = ["M1"]
        broken["case_input_paths"] = deepcopy(self.inputs)
        before = deepcopy((broken, self.mapping))
        with self.assertRaisesRegex(ValueError, "current owner must own a complete case"):
            visual_scope.check_bound_scope(broken, case_ids=list(self.mapping),
                case_criteria=self.mapping, criterion_ids=["C1", "C2", "C3"])
        self.assertEqual(before, (broken, self.mapping))

    def test_bound_scope_requires_nonempty_current_cases_for_a_known_owner(self):
        broken = self.scope()
        broken["current_case_ids"] = []
        before = deepcopy((broken, self.mapping))
        with self.assertRaisesRegex(ValueError, "current_case_ids needs unique declared IDs"):
            visual_scope.check_bound_scope(broken, case_ids=list(self.mapping),
                case_criteria=self.mapping, criterion_ids=["C1", "C2", "C3"])
        self.assertEqual(before, (broken, self.mapping))

    def test_binding_check_refuses_tampered_roster_partitions_mapping_and_claims(self):
        expected = self.scope()
        mutations = (
            lambda scope: scope.__setitem__("extra", True),
            lambda scope: scope.__setitem__("mode", "integration"),
            lambda scope: scope["required_case_ids"].reverse(),
            lambda scope: scope["required_case_ids"].pop(),
            lambda scope: scope["out_of_scope_case_ids"].append("home.empty"),
            lambda scope: scope["case_criteria"]["home.empty"].append("C2"),
            lambda scope: scope["case_owners"].__setitem__("home.filled", "M2"),
            lambda scope: scope["task_criterion_ids"].append("unknown"),
            lambda scope: scope["changed_paths"].append("outside.py"))
        for mutate in mutations:
            broken = deepcopy(expected)
            mutate(broken)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                visual_scope.check_bound_scope(broken, case_ids=list(self.mapping),
                    case_criteria=self.mapping, criterion_ids=["C1", "C2", "C3"])


if __name__ == "__main__":
    unittest.main()
