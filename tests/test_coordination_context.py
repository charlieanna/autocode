"""Automatic members know where real joint proof belongs, without gaining authority."""

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_changeset_store as store
import autocode_coordination_context as coordination
import autocode_coordination_store as coordination_store
import autocode_report_repair_context as repair
import autocode_requirements_review as requirements_review
import autocode_scope_question as scope_question
import autocode_workspace_scope_state as workspace_scope
import autocode_stage_context as runtime
import autocode_trace_coverage as coverage
from units import autoplanner


class CoordinationContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "catalog")})
        environment.start()
        self.addCleanup(environment.stop)
        self.workspace = self.root / "checkout"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--allow-empty",
                "-qm",
                "baseline",
            ],
            cwd=self.workspace,
            check=True,
        )
        self.run = self.workspace / ".autocode/runs/fix"
        self.run.mkdir(parents=True)
        self.state = {
            "task": "Fix the checkout total across repositories",
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "settings": {"joint_planning": True, "spec_first": True, "roles": {}},
            "iteration": 1,
            "stages": [],
            "acceptance_criteria": [],
        }
        self.record = store.reserve(
            source_workspace=self.workspace,
            source_run_dir=self.run,
            target="history",
            workspace=self.root / "history",
            brief="Fix receipt serialization",
        )
        store.bind(self.record["id"], "history", self.root / "history/.autocode/runs/fix")

    def automatic(self):
        store.set_fields(self.record["id"], {"automatic_anchor": str(self.root / "anchor")})

    def assigned_scope(self):
        self.automatic()
        self.state["workspace_scope"] = {
            "turn": 0,
            "spans_repositories": True,
            "external_parts": [{"target": "history", "work": "Fix receipt serialization"}],
        }
        receipt = {
            "identity": workspace_scope.identity(self.state),
            "members": {"history": str(self.root / "history/.autocode/runs/fix")},
            "aliases": {},
        }
        store.set_fields(self.record["id"], {"scope_assignments": {str(self.run): receipt}})
        return receipt

    def test_current_scope_assignment_is_core_context_without_new_requirements_or_authority(self):
        receipt = self.assigned_scope()
        before = copy.deepcopy(self.state)
        found = coordination.context(self.state)
        self.assertEqual(receipt, found["scope_assignment"])
        self.assertIn("already settled by the core", found["instruction"])
        self.assertIn("Do not turn this operational receipt into an acceptance criterion", found["instruction"])
        self.assertIn("Plan approval and independent verification remain required", found["instruction"])
        self.assertEqual(before, self.state)
        found["scope_assignment"]["members"]["history"] = "/forged/run"
        self.assertEqual(receipt, coordination.context(self.state)["scope_assignment"])

    def test_stale_incomplete_and_wrong_run_assignments_never_claim_scope_is_settled(self):
        for invalid in ("stale", "incomplete", "wrong-run", "new-scope", "new-turn"):
            with self.subTest(invalid=invalid):
                receipt = self.assigned_scope()
                state = copy.deepcopy(self.state)
                if invalid == "stale":
                    receipt["identity"] = "old-scope"
                elif invalid == "incomplete":
                    receipt["members"] = {}
                elif invalid == "wrong-run":
                    receipt["members"]["history"] = "/unregistered/run"
                elif invalid == "new-scope":
                    state["workspace_scope"]["external_parts"][0]["work"] = "Different work"
                else:
                    state["turns"] = [{"task": "Different request"}]
                store.set_fields(self.record["id"], {"scope_assignments": {str(self.run): receipt}})
                found = coordination.context(state)
                self.assertNotIn("scope_assignment", found)
                self.assertNotIn("already settled by the core", found["instruction"])

    def test_an_explicit_personal_scope_decision_takes_precedence_over_an_assignment(self):
        self.assigned_scope()
        store.set_fields(self.record["id"], {"scope_assignments": {}})
        question = scope_question.question(self.state)
        answer = {"actor": "user_cli", "question": question, "text": scope_question.THIS_REPOSITORY}
        self.state.update(answers={question["id"]: answer}, user_events=[answer])
        self.assigned_scope()
        self.assertEqual("this_repository", scope_question.decision(self.state))
        self.assertNotIn("scope_assignment", coordination.context(self.state))

    def test_planning_and_report_repair_receive_exact_scope_assignment_without_catalog_rediscovery(self):
        receipt = self.assigned_scope()
        for stage in ("requirements_gather", "astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                prompt, _ = autoplanner.context(self.state, stage, self.run / "state.json")
                packets = [json.loads(line) for line in prompt.splitlines() if line.startswith('{"task":')]
                self.assertEqual(receipt, packets[-1]["coordinated_member"]["scope_assignment"])
                repaired = repair.clarification_context(self.state, stage)
                self.assertEqual(receipt, repaired["coordinated_member"]["scope_assignment"])
                self.assertFalse(any((repaired["protected_contract"] or {}).values()))

    def joint_intent(self):
        anchor = str(self.root / "intake/.autocode/runs/fix")
        workspace = coordination_store.root(anchor) / "joint-1"
        state = {
            **self.state,
            "workspace": str(workspace),
            "run_dir": str(workspace / ".autocode/runs/joint-regression"),
            "workflow": {"kind": "build"},
            "requirements_handoff": {
                "report": {
                    "intended_outcome": "Prove the actual combined bug fix",
                    "required_behaviors": ["Run the actual components together"],
                    "acceptance_tests": ["The original fails and the candidate passes"],
                    "open_questions": [],
                }
            },
        }
        document = {
            "version": 1,
            "anchor": anchor,
            "members": [],
            "joint": {"workspace": state["workspace"], "run_dir": state["run_dir"], "started": False},
            "pending": {"operation": "joint_plan_start"},
        }
        coordination_store.save(document)
        return state, document

    def test_joint_planning_identity_requires_current_bound_start_intent_or_started_run(self):
        state, document = self.joint_intent()
        self.assertTrue(coordination.joint_planning(state))
        for replacement in ({"run_dir": state["run_dir"] + "-other"}, {"workspace": str(self.root)}):
            with self.subTest(replacement=replacement):
                self.assertFalse(coordination.joint_planning({**state, **replacement}))
        document["pending"] = None
        coordination_store.save(document)
        self.assertFalse(coordination.joint_planning(state))
        document["joint"]["started"] = True
        coordination_store.save(document)
        self.assertTrue(coordination.joint_planning(state))
        document["joint"]["run_dir"] += "-replacement"
        coordination_store.save(document)
        self.assertFalse(coordination.joint_planning(state), "A retired joint task gets no exemption")
        self.assertFalse(coordination.joint_planning({}))

    def test_joint_requirements_flow_to_one_plan_approval_but_business_questions_remain(self):
        state, document = self.joint_intent()
        self.assertEqual([], requirements_review.questions(state), "Joint machinery needs no separate confirmation")
        question = {"id": "Q1", "question": "Which business outcome is intended?"}
        state["requirements_handoff"]["report"]["open_questions"] = [question]
        self.assertEqual([question], requirements_review.questions(state))
        state["answers"] = {"Q1": {"text": "The original outcome"}}
        self.assertEqual([], requirements_review.questions(state))
        document["joint"]["run_dir"] += "-replacement"
        coordination_store.save(document)
        self.assertEqual(
            [requirements_review.review_question(state)],
            requirements_review.questions(state),
            "An unauthenticated build retains its ordinary spec-first confirmation",
        )

    def test_joint_identity_rejects_unreadable_symlinked_and_foreign_coordinator_records(self):
        state, document = self.joint_intent()
        path = coordination_store.root(document["anchor"]) / "coordination.json"
        for content in ("{broken", json.dumps({**document, "anchor": str(self.root / "foreign")})):
            path.write_text(content)
            self.assertFalse(coordination.joint_planning(state))
        path.unlink()
        other = self.root / "copied-record.json"
        other.write_text(json.dumps(document))
        path.symlink_to(other)
        self.assertFalse(coordination.joint_planning(state))
        path.unlink()
        self.assertFalse(coordination.joint_planning(state))

    def test_first_reserved_member_has_context_before_a_second_repository_is_known(self):
        anchor = str(self.root / "intake/.autocode/runs/fix")
        workspace = coordination_store.root(anchor) / "worktrees/first"
        run = workspace / ".autocode/runs/fix"
        member = {
            "target": "first",
            "workspace": str(workspace),
            "run_dir": str(run),
            "started": False,
            "reported_work": "Inspect the producer boundary",
        }
        document = {
            "version": 1,
            "anchor": anchor,
            "members": [member],
            "pending": {"operation": "start", "target": "first"},
        }
        coordination_store.save(document)
        state = {**self.state, "workspace": str(workspace), "run_dir": str(run)}
        found = coordination.context(state)
        self.assertIsNotNone(found)
        self.assertEqual(["first"], found["members"])
        self.assertEqual("Inspect the producer boundary", found["reported_work"])
        self.assertIsNone(coordination.context({**state, "run_dir": str(run) + "-other"}))
        document["pending"] = None
        coordination_store.save(document)
        self.assertIsNone(coordination.context(state), "An unstarted reservation grants no context")
        member["started"] = True
        coordination_store.save(document)
        self.assertIsNotNone(coordination.context(state))

    def test_manual_sets_and_unbound_or_wrong_workspace_runs_get_no_orchestration_claim(self):
        self.assertIsNone(coordination.context(self.state))
        self.automatic()
        for replacement in ({"run_dir": str(self.root / "unrelated")}, {"workspace": str(self.root / "wrong")}):
            with self.subTest(replacement=replacement):
                self.assertIsNone(coordination.context({**self.state, **replacement}))

    def test_leaf_and_source_members_share_joint_proof_and_publication_responsibilities(self):
        self.automatic()
        leaf = {
            **self.state,
            "workspace": str(self.root / "history"),
            "run_dir": str(self.root / "history/.autocode/runs/fix"),
        }
        for state in (self.state, leaf):
            with self.subTest(workspace=state["workspace"]):
                context = coordination.context(state)
                self.assertEqual(["checkout", "history"], context["members"])
                self.assertIn("SEPARATE isolated integration workspace", context["instruction"])
                self.assertIn("fail-before/pass-after", context["instruction"])
                self.assertIn("never discharges the real joint obligation", context["instruction"])
                self.assertIn("not exclusions or proxy acceptance", context["instruction"])
                self.assertIn("only after ALL member gates and independent joint proof pass", context["instruction"])
                self.assertIn("end_to_end_flow", context["instruction"])
                self.assertIn("only this member's executable local path", context["instruction"])
                self.assertIn("request a plan revision", context["instruction"])

    def bind_participant(self, target, *, role):
        workspace = self.root / target
        shutil.copytree(self.workspace, workspace)
        run = workspace / ".autocode/runs/fix"
        store.reserve(
            source_workspace=self.workspace,
            source_run_dir=self.run,
            target=target,
            workspace=workspace,
            brief="Prove the real composed outcome",
            role=role,
        )
        store.bind(self.record["id"], target, run)
        return {**self.state, "workspace": str(workspace), "run_dir": str(run)}

    def test_joint_participant_never_receives_circular_member_prerequisites(self):
        self.automatic()
        joint = self.bind_participant("Joint regression (2)", role="joint")
        self.assertIsNone(coordination.context(joint))
        self.assertEqual("ordinary proof rule", coordination.proxy_rule(joint, "ordinary proof rule"))
        for stage in ("requirements_gather", "astra_discovery", "astra_challenge", "astra_finalize"):
            with self.subTest(stage=stage):
                prompt, _ = autoplanner.context(joint, stage, self.run / "state.json")
                self.assertNotIn("REAL JOINT PROOF IS NOT A LOCAL PROXY", prompt)
                self.assertNotIn("coordinated_member", repair.clarification_context(joint, stage))
        prompt, _ = runtime.context_packet(joint, "terra", self.run / "state.json")
        packets = [json.loads(line) for line in prompt.splitlines() if line.startswith('{"task":')]
        self.assertNotIn("coordinated_member", packets[-1])

    def test_member_context_lists_only_producers_by_authenticated_role_not_name(self):
        self.automatic()
        self.bind_participant("Joint regression (2)", role="joint")
        ordinary = self.bind_participant("Joint regression", role="member")
        for state in (self.state, ordinary):
            with self.subTest(workspace=state["workspace"]):
                found = coordination.context(state)
                self.assertEqual(["checkout", "history", "Joint regression"], found["members"])
                self.assertIn("only this member", found["instruction"])
                self.assertIn("independent joint proof pass", found["instruction"])

    def test_discovery_hint_requires_the_same_authenticated_member_identity(self):
        self.automatic()
        leaf = {
            **self.state,
            "workspace": str(self.root / "history"),
            "run_dir": str(self.root / "history/.autocode/runs/fix"),
        }
        hint = "Apply tax here unless Q1 assigns it to checkout."
        document = {
            "version": 1,
            "anchor": str(self.root / "anchor"),
            "members": [
                {"target": "history", "workspace": leaf["workspace"], "run_dir": leaf["run_dir"], "reported_work": hint}
            ],
        }
        coordination_store.save(document)
        context = coordination.context(leaf)
        self.assertEqual(hint, context["reported_work"])
        self.assertIn("not a user requirement or an answered question", context["instruction"])
        self.assertIn("Never copy reported_work into a requirement source_quote", context["instruction"])
        self.assertNotIn("reported_work", coordination.context(self.state))
        for field in ("target", "workspace", "run_dir"):
            with self.subTest(field=field):
                value = document["members"][0][field]
                document["members"][0][field] = str(self.root / "unrelated")
                coordination_store.save(document)
                self.assertNotIn("reported_work", coordination.context(leaf))
                document["members"][0][field] = value

    def test_requirements_planning_runtime_and_report_repair_receive_the_same_bound_context(self):
        self.automatic()
        expected = coordination.context(self.state)
        self.assertIsNotNone(expected)
        instruction = expected["instruction"]
        self.assertIn("Source citations establish provenance", instruction)
        self.assertIn("not fulfillment", instruction)
        self.assertIn("LOCAL test or guard", instruction)
        self.assertIn("verbatim in required_behaviors/constraints", instruction)
        self.assertIn("do not create separate global coordinator-only", instruction.lower())
        self.assertIn("Observable boundary preservation", instruction)
        for stage in ("requirements_gather", "astra_discovery", "astra_challenge"):
            with self.subTest(stage=stage):
                prompt, _ = autoplanner.context(self.state, stage, self.run / "state.json")
                packets = [json.loads(line) for line in prompt.splitlines() if line.startswith('{"task":')]
                self.assertEqual(expected, packets[-1]["coordinated_member"])
        prompt, _ = runtime.context_packet(self.state, "terra", self.run / "state.json")
        packets = [json.loads(line) for line in prompt.splitlines() if line.startswith('{"task":')]
        self.assertEqual(expected, packets[-1]["coordinated_member"])
        repaired = repair.clarification_context(self.state, "requirements_gather")
        self.assertEqual(expected, repaired["coordinated_member"])
        self.assertIsNone(repaired["protected_contract"], "Context does not manufacture an approved goal")

    def test_member_context_demands_exact_external_owner_names(self):
        self.automatic()
        instruction = coordination.context(self.state)["instruction"]
        self.assertIn("Name an external owner exactly as the members list in this context names it", instruction)
        self.assertIn("never append role words such as 'member' to a target", instruction)
        self.assertIn("genuinely named that way keeps its exact name", instruction)

    def test_the_investigator_and_its_report_repair_receive_the_member_context(self):
        self.automatic()
        import autocode_bug_job as bug_job

        expected = coordination.context(self.state)
        prompt, _ = bug_job.prompt(self.state)
        data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(expected, data["coordinated_member"])
        repaired = repair.clarification_context(self.state, "investigate_bug")
        self.assertEqual(expected, repaired["coordinated_member"])
        source = {"path": "/run/x.json", "sha256": "h", "format": "json", "truncated": False, "content": {}}
        pending = {"original": {"stage": "investigate_bug"}}
        _, handoff = repair.prompt_parts(self.state, pending, source, source, ("", {}))
        self.assertEqual(expected, handoff["header"]["coordinated_member"], "The repair payload carries it")

    def test_only_bound_members_distinguish_real_joint_proof_from_a_local_proxy(self):
        for automatic in (False, True):
            if automatic:
                self.automatic()
            with self.subTest(automatic=automatic):
                prompt, _ = autoplanner.context(self.state, "astra_challenge", self.run / "state.json")
                self.assertIn("OUT-OF-CHECKOUT PROXY", prompt)
                self.assertEqual(automatic, "REAL JOINT PROOF IS NOT A LOCAL PROXY" in prompt)
                repaired = repair.clarification_context(self.state, "astra_finalize")
                if automatic:
                    self.assertIn(
                        "REAL JOINT PROOF IS NOT A LOCAL PROXY", repaired["coordinated_member"]["instruction"]
                    )

        requirement = {
            "id": "R1",
            "text": "The real checkout must show `$110.00`.",
            "source_quote": "The real checkout must show `$110.00`.",
        }
        joint = "The coordinator's separate real-code joint proof must establish `$110.00`; local evidence cannot discharge it."
        contract = {
            "constraints": ["The receipt view is outside this checkout."],
            "required_behaviors": [joint],
            "acceptance_criteria": [{"id": "AC1", "criterion": "The receipt amount is numeric 110."}],
        }
        # No validator exemption: a local stand-in still raises the ordinary gap.
        local = [{"requirement_id": "R1", "disposition": "covered", "evidence": "AC1"}]
        self.assertEqual(["R1"], [row["requirement_id"] for row in coverage.proxy_gaps([requirement], local, contract)])
        retained = [{"requirement_id": "R1", "disposition": "covered", "evidence": joint}]
        self.assertEqual([], coverage.proxy_gaps([requirement], retained, contract))


if __name__ == "__main__":
    unittest.main()
