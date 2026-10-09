"""Pure checkpoint-consumer tests; synthetic facts are not native evidence."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
import unittest

import autocode_contract_identity as contract
import autocode_milestones as milestones
import autocode_util as util
import autocode_visual_checkpoint as checkpoint
import autocode_visual_scope as visual_scope


class VisualCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"version": 1, "cases": [
            {"id": "home", "implementation_paths": ["ui/home.py"]},
            {"id": "detail", "implementation_paths": ["ui/detail.py"]}]}
        self.body = {"open_blocking_questions": [], "constraints": [
            visual_scope.MARKER + visual_scope.POLICY,
            visual_scope.CASE_MARKER + json.dumps({"home": ["C1"], "detail": ["C2"]})],
            "acceptance_criteria": [{"id": "C1"}, {"id": "C2"}],
            "milestones": [
                {"id": "M1", "acceptance_criteria": ["C1"], "depends_on": [],
                 "affected_paths": ["ui/home.py"]},
                {"id": "M2", "acceptance_criteria": ["C2"], "depends_on": ["M1"],
                 "affected_paths": ["ui/detail.py"]}],
            "initial_task": {"milestone_id": "M1"}}
        saved = {"task_id": "job", "revision": 1, "body": self.body,
                 "approval_status": "approved", "origin": "planning"}
        saved["hash"] = util.digest({key: saved[key] for key in ("task_id", "revision", "body")})
        saved["approval_event"] = {"actor": "user_cli", "token": contract.token(saved)}
        self.current = {"head": "h", "files": {"ui/home.py": "a" * 64, "ui/detail.py": "b" * 64}}
        self.current["revision"] = util.digest(self.current)
        self.state = {"goal_contract": saved, "user_events": [deepcopy(saved["approval_event"])],
            "settings": {"design_manifest": {"body": self.manifest},
                         "milestone_checkpoints": {"enabled": True},
                         "roles": {"sol": {"model": "independent", "reasoning_effort": "high"}},
                         "limits": {"max_seconds": 120}}, "milestone_progress": {}}
        self.set_task("M1")

    def set_task(self, mid):
        row = next(row for row in self.body["milestones"] if row["id"] == mid)
        self.state["current_task"] = {"id": "task-" + mid, "kind": "validate", "milestone_id": mid,
            "acceptance_criteria": list(row["acceptance_criteria"]),
            "affected_paths": list(row["affected_paths"]), "contract_revision": 1,
            "contract_hash": self.state["goal_contract"]["hash"]}

    def facts(self, *, accepted=True, historical=None, eligible=None, prepared_commit=False):
        task = self.state["current_task"]
        prior = [] if task["milestone_id"] == "M1" else ["M1"]
        inputs = {} if not prior else {"home": ["ui/home.py"]}
        scope = visual_scope.derive(self.body, self.manifest, task,
            accepted_milestones=prior, accepted_case_input_paths=inputs)
        # This deliberately models a trusted CODE result for pure consumers;
        # it authenticates no actual capture, stage, report or child launch.
        if historical is None:
            historical = tuple([*prior, task["milestone_id"]] if accepted else prior)
        if eligible is None:
            eligible = historical if accepted else ()
        return checkpoint.VerifiedCheckpoint(
            state_binding=checkpoint.binding(self.state, self.current),
            source_revision=self.current["revision"], runtime_hash="c" * 64,
            scope_json=json.dumps(scope, sort_keys=True, separators=(",", ":")),
            historical_milestone_ids=historical, eligible_milestone_ids=eligible,
            current_scope_accepted=accepted, receipt_sha256="d" * 64 if accepted else None,
            authorized_acceptance=checkpoint.binding(self.state, self.current) if prepared_commit else None)

    def test_first_checkpoint_readiness_does_not_manufacture_committed_history(self):
        proof = self.facts(historical=(), eligible=())
        self.assertTrue(checkpoint.current_ready(self.state, self.current, proof))
        self.assertEqual(set(), checkpoint.eligible_ids(self.state, self.current, proof))
        self.assertEqual(set(), checkpoint.recheckable(self.state, self.current, proof))
        with self.assertRaisesRegex(util.Paused, "prepared native Completion transaction"):
            checkpoint.require_acceptance(self.state, self.current, proof)
        prepared = replace(proof, authorized_acceptance=checkpoint.binding(self.state, self.current))
        checkpoint.require_acceptance(self.state, self.current, prepared)
        self.assertEqual(set(), checkpoint.eligible_ids(self.state, self.current, prepared))

    def initial_facts(self):
        self.state.pop("current_task", None)
        first = {"kind": "implement", "milestone_id": "M1", "objective": "Build the approved first screen",
                 "acceptance_criteria": ["C1"], "affected_paths": ["ui/home.py"],
                 "requirements": ["The home screen matches its complete case"],
                 "validation_plan": ["Capture and independently review home"]}
        self.body["initial_task"] = first
        saved = self.state["goal_contract"]
        saved["hash"] = util.digest({key: saved[key] for key in ("task_id", "revision", "body")})
        saved["approval_event"] = {"actor": "user_cli", "token": contract.token(saved)}
        self.state["user_events"] = [deepcopy(saved["approval_event"])]
        decision = {"status": "CONTINUE", "next_objective": first["objective"],
                    "affected_paths": list(first["affected_paths"]),
                    "next_task": {key: value for key, value in first.items()
                                  if key not in ("objective", "affected_paths")}}
        proof = checkpoint.VerifiedInitialAssignment(
            state_binding=checkpoint.binding(self.state, self.current, initial=True),
            source_revision=self.current["revision"], runtime_hash="c" * 64,
            authorized_assignment=checkpoint.assignment_identity(decision))
        return decision, proof

    def test_initial_assignment_uses_approval_without_prior_image_acceptance(self):
        decision, proof = self.initial_facts()
        before = deepcopy(self.state)
        checkpoint.require_assignment(self.state, self.current, proof, decision)
        milestones.require_prerequisites(self.state, "M1", current=self.current,
                                       visual_proof=proof, decision=decision)
        self.assertEqual(before, self.state)
        self.assertFalse(hasattr(proof, "eligible_milestone_ids"))
        self.assertFalse(hasattr(proof, "current_scope_accepted"))
        with self.assertRaises(util.Paused):
            checkpoint.require_launch(self.state, self.current, proof)

    def test_initial_authority_cannot_assign_a_later_changed_or_existing_task(self):
        decision, proof = self.initial_facts()
        for supplied in (None, True, asdict(proof)):
            with self.subTest(supplied=supplied), self.assertRaises(util.Paused):
                checkpoint.require_assignment(self.state, self.current, supplied, decision)
        changed = deepcopy(decision)
        changed["next_task"]["milestone_id"] = "M2"
        with self.assertRaises(util.Paused):
            checkpoint.require_assignment(self.state, self.current, proof, changed)
        with self.assertRaises(util.Paused):
            milestones.require_prerequisites(self.state, "M2", current=self.current,
                                           visual_proof=proof, decision=decision)
        self.set_task("M1")
        with self.assertRaises(util.Paused):
            checkpoint.require_assignment(self.state, self.current, proof, decision)

    def test_current_owner_needs_all_earlier_committed_cases_and_exact_commit_authority(self):
        self.set_task("M2")
        proof = self.facts(historical=("M1",), eligible=("M1",), prepared_commit=True)
        self.assertTrue(checkpoint.current_ready(self.state, self.current, proof))
        checkpoint.require_acceptance(self.state, self.current, proof)
        self.assertEqual({"M1"}, checkpoint.eligible_ids(self.state, self.current, proof))
        for broken in (replace(proof, eligible_milestone_ids=()),
                       replace(proof, authorized_acceptance="f" * 64)):
            with self.subTest(broken=broken), self.assertRaises(util.Paused):
                checkpoint.require_acceptance(self.state, self.current, broken)

    def test_absent_policy_leaves_legacy_gate_decisions_to_existing_owner(self):
        state = {"goal_contract": {"body": {"constraints": []}}}
        self.assertIsNone(checkpoint.check(state, None, None))
        self.assertTrue(checkpoint.current_ready(state, None, None))
        self.assertIsNone(checkpoint.eligible_ids(state, None, None))
        checkpoint.require_acceptance(state, None, None)

    def test_saved_flags_or_serialized_context_cannot_replace_code_context(self):
        proof = self.facts()
        self.state["milestone_progress"] = {"M1": {"accepted": True}}
        self.state["visual_checkpoint"] = asdict(proof)
        for supplied in (None, True, asdict(proof)):
            with self.subTest(value=type(supplied).__name__), self.assertRaisesRegex(
                    util.Paused, "Missing freshly authenticated"):
                checkpoint.require_acceptance(self.state, self.current, supplied)

    def test_context_is_bound_to_exact_settings_task_contract_and_source(self):
        proof = self.facts()
        mutations = (
            lambda state, current: state["settings"]["roles"]["sol"].update(model="self-review"),
            lambda state, current: state["settings"]["limits"].update(max_seconds=150),
            lambda state, current: state["current_task"].update(id="borrowed-task"),
            lambda state, current: state["current_task"]["affected_paths"].append("ui/extra.py"),
            lambda state, current: state["goal_contract"]["body"]["constraints"].append("changed"),
            lambda state, current: current["files"].update({"ui/home.py": "e" * 64}),
        )
        for mutate in mutations:
            state, current = deepcopy(self.state), deepcopy(self.current)
            mutate(state, current)
            with self.subTest(mutation=mutate), self.assertRaises(util.Paused):
                checkpoint.require_acceptance(state, current, proof)

    def test_missing_receipt_and_nonboolean_acceptance_fail_closed(self):
        proof = self.facts()
        for broken in (replace(proof, receipt_sha256=None),
                       replace(proof, current_scope_accepted=1),
                       replace(proof, receipt_sha256="invalid")):
            with self.subTest(broken=broken), self.assertRaisesRegex(util.Paused, "receipt identity"):
                checkpoint.require_acceptance(self.state, self.current, broken)

    def test_malformed_snapshot_and_contract_identity_are_technical_holds(self):
        for current in ({"revision": "x"}, {"head": "h", "revision": "x"}, None):
            with self.subTest(current=current), self.assertRaises(util.Paused):
                checkpoint.binding(self.state, current)
        for key in ("body", "hash", "revision"):
            state = deepcopy(self.state)
            state["goal_contract"].pop(key)
            with self.subTest(key=key), self.assertRaises(util.Paused):
                checkpoint.binding(state, self.current)
        for parts in ({"head": [], "files": {}}, {"head": "h", "files": []},
                      {"head": "h", "files": {"ui/home.py": False}}):
            current = {**parts, "revision": util.digest(parts)}
            with self.subTest(current=current), self.assertRaises(util.Paused):
                checkpoint.binding(self.state, current)
        state = deepcopy(self.state)
        state["current_task"]["id"] = True
        with self.assertRaises(util.Paused):
            checkpoint.binding(state, self.current)

    def test_unknown_prerequisite_owner_cannot_be_treated_as_dependency_free(self):
        with self.assertRaisesRegex(util.Paused, "Unknown visual milestone prerequisite"):
            milestones.require_prerequisites(self.state, "unknown", current=self.current,
                                           visual_proof=self.facts())

    def test_malformed_settings_status_preserves_unknown_without_claiming_acceptance(self):
        proof = self.facts()
        for settings in (None, [], {"milestone_checkpoints": None},
                         {"milestone_checkpoints": []}):
            state = deepcopy(self.state)
            state["settings"] = settings
            before = deepcopy(state)
            result = milestones.summary(state, current=self.current, visual_proof=proof)
            self.assertEqual('UNKNOWN', result['visual_checkpoint']['status'])
            self.assertFalse(result['visual_checkpoint']['current_scope_accepted'])
            self.assertIsNone(result['visual_checkpoint']['eligible_milestones'])
            self.assertEqual([], result['accepted_milestones'])
            self.assertEqual(before, state)

    def test_malformed_settings_policy_and_nested_context_are_technical_holds(self):
        proof = self.facts()
        for settings in (None, [], {"milestone_checkpoints": None},
                         {"milestone_checkpoints": {"enabled": True}, "design_manifest": None}):
            state = deepcopy(self.state)
            state["settings"] = settings
            with self.subTest(settings=settings), self.assertRaises(util.Paused):
                checkpoint.check(state, self.current, proof)
        for constraints in (None, True, {"unexpected": True}):
            state = deepcopy(self.state)
            state["goal_contract"]["body"]["constraints"] = constraints
            with self.subTest(constraints=constraints), self.assertRaises(util.Paused):
                checkpoint.enabled(state)

    def test_missing_contract_is_only_normal_before_any_assignment(self):
        self.assertFalse(checkpoint.enabled({"goal_contract": None, "current_task": None}))
        for saved in (None, [], "invalid contract"):
            state = deepcopy(self.state)
            state["goal_contract"] = saved
            with self.subTest(saved=saved), self.assertRaises(util.Paused):
                checkpoint.enabled(state)
        for saved in ([], "invalid contract"):
            with self.subTest(preplanning=saved), self.assertRaises(util.Paused):
                checkpoint.enabled({"goal_contract": saved, "current_task": None})

    def test_disabled_checkpoint_settings_cannot_bypass_opted_in_policy(self):
        self.state["settings"]["milestone_checkpoints"]["enabled"] = False
        with self.assertRaisesRegex(util.Paused, "requires milestone checkpoints"):
            checkpoint.check(self.state, self.current, self.facts())

    def test_stale_current_eligibility_keeps_only_authenticated_prior_recheck_cids(self):
        self.set_task("M2")
        proof = self.facts(accepted=False, historical=("M1",), eligible=())
        before = deepcopy(self.state)
        self.assertEqual(set(), checkpoint.eligible_ids(self.state, self.current, proof))
        self.assertEqual({"C1"}, checkpoint.recheckable(self.state, self.current, proof))
        self.assertFalse(checkpoint.current_ready(self.state, self.current, proof))
        with self.assertRaisesRegex(util.Paused, "independent passing complete-case"):
            checkpoint.require_acceptance(self.state, self.current, proof)
        self.assertEqual(before, self.state)

    def test_future_owners_and_eligibility_without_original_acceptance_are_refused(self):
        proof = self.facts()
        for broken in (replace(proof, historical_milestone_ids=("M2",), eligible_milestone_ids=()),
                       replace(proof, historical_milestone_ids=(), eligible_milestone_ids=("M1",))):
            with self.subTest(broken=broken), self.assertRaises(util.Paused):
                checkpoint.check(self.state, self.current, broken)

    def test_current_scope_pass_cannot_restore_stale_prerequisite_eligibility(self):
        self.set_task("M2")
        proof = self.facts(accepted=True, historical=("M1", "M2"), eligible=())
        # Owned-case image acceptance remains available for the one bounded
        # Completion handoff that can assign an explicit prior-case recheck.
        checkpoint.require_case_acceptance(self.state, self.current, proof)
        self.assertFalse(checkpoint.current_ready(self.state, self.current, proof))
        self.assertEqual({"C1", "C2"}, checkpoint.recheckable(self.state, self.current, proof))
        with self.assertRaisesRegex(util.Paused, "Fresh complete-case prerequisites"):
            checkpoint.require_acceptance(self.state, self.current, proof)
        with self.assertRaisesRegex(util.Paused, "not transitively closed"):
            checkpoint.check(self.state, self.current, replace(proof, eligible_milestone_ids=("M2",)))

    def test_internally_consistent_but_unapproved_ownership_cannot_replace_admission(self):
        proof = self.facts()
        scope = json.loads(proof.scope_json)
        scope["case_owners"]["detail"] = "M1"
        scope["current_case_ids"] = ["home", "detail"]
        scope["required_case_ids"] = ["home", "detail"]
        scope["out_of_scope_case_ids"] = []
        scope["task_criterion_ids"] = ["C1", "C2"]
        changed = replace(proof, scope_json=json.dumps(scope, sort_keys=True, separators=(",", ":")))
        with self.assertRaisesRegex(util.Paused, "ownership differs"):
            checkpoint.check(self.state, self.current, changed)

    def test_assignment_authority_is_for_one_exact_reviewed_handoff(self):
        decision = {"status": "REWORK", "next_task": {"kind": "implement", "milestone_id": "M1",
                    "acceptance_criteria": ["C1"]}, "affected_paths": ["ui/home.py"],
                    "next_objective": "Repair the rendered finding", "evidence": ["sealed finding"]}
        proof = replace(self.facts(accepted=False),
                        authorized_assignment=checkpoint.assignment_identity(decision))
        checkpoint.require_assignment(self.state, self.current, proof, decision)
        for field, value in (("affected_paths", ["ui/detail.py"]), ("status", "CONTINUE"),
                             ("next_objective", "A different task")):
            changed = deepcopy(decision)
            changed[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(util.Paused, "Assignment lacks"):
                checkpoint.require_assignment(self.state, self.current, proof, changed)

    def test_launch_authority_cannot_be_borrowed_for_another_task(self):
        proof = self.facts(accepted=False)
        with self.assertRaisesRegex(util.Paused, "Writer lacks"):
            checkpoint.require_launch(self.state, self.current, proof)
        proof = replace(proof, authorized_launch=checkpoint.launch_identity(self.state["current_task"]))
        checkpoint.require_launch(self.state, self.current, proof)
        changed = deepcopy(self.state)
        changed["current_task"]["id"] = "different"
        with self.assertRaises(util.Paused):
            checkpoint.require_launch(changed, self.current, proof)


if __name__ == "__main__":
    unittest.main()
