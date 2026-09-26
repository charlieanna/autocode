import copy
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_goals as goals
import autocode_planning as planning
import autocode_planning_artifacts as artifacts
import autocode_planning_graph as planning_graph
import autocode_support as support
from goal_fixtures import body


def requirements():
    value = body()
    for field in goals.BRIEF_FIELDS:
        value.pop(field)
    return value


def plan_body(*, second_dependency=None):
    value = body()
    value["initial_task"] = {"objective": "Deliver greeting", "affected_paths": ["greet.py"],
                             "kind": "implement", "milestone_id": "M1", "requirements": ["Deliver CLI"],
                             "acceptance_criteria": ["C1"], "validation_plan": ["Run tests"]}
    value["milestones"][0]["boundaries"] = ["greet.py"]
    if second_dependency is not None:
        value["milestones"].append({"id": "M2", "objective": "Document greeting", "acceptance_criteria": ["C1"],
                                    "depends_on": second_dependency, "boundaries": ["README.md"]})
    return value


class PlanningArtifactTests(unittest.TestCase):
    def setUp(self):
        evidence = Path.cwd() / ".autocode" / "evidence" / "planning-artifact-test-workspaces"
        evidence.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="m2-", dir=evidence)).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.run = self.root / "run"
        self.run.mkdir()
        self.state = {"task_id": "task", "task": "Task", "workspace": str(self.root), "answers": {},
                      "user_events": [], "acceptance_criteria": [], "settings": {"joint_planning": True,
                      "planning_flow": "v2", "roles": {
                          "requirements_planner": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3"},
                          "technical_planner": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3"},
                          "plan_reviewer": {"engine": "opencode", "model": planning.PINNED_REVIEWER_MODEL,
                                              "model_pinned": True}}}}

    def apply(self, stage, value):
        planning.apply(self.state, stage, value, {"output": f"{stage}.json"}, run_dir=self.run)
        artifacts.flush_pending(self.state, self.run)

    def initialize_plan(self):
        self.apply("requirements", {"requirements": requirements(), "summary": "Requirements ready"})
        self.apply("plan", {"contract": plan_body(), "summary": "Plan ready"})

    def review(self):
        self.apply("plan_review", {"summary": "Need explicit test", "concerns": [{
            "id": "C1", "concern": "Test evidence is incomplete", "evidence_refs": ["tools/test_goals.py"],
            "requested_change": "Add a regression", "acceptance_test": "Focused suite passes", "blocking": True}]})

    def revise(self, *, second_dependency=[]):
        self.apply("plan_revise", {"contract": plan_body(second_dependency=second_dependency), "summary": "Revision ready",
                                    "responses": [{"concern_id": "C1", "response": "Added regression",
                                                   "evidence_refs": ["tools/test_planning_artifacts.py"],
                                                   "change": "Added persistence coverage",
                                                   "acceptance_test": "Focused suite passes"}]})

    def test_all_v2_handoffs_have_stable_hashed_artifact_and_delta_files(self):
        self.initialize_plan()
        self.review()
        self.revise(second_dependency=[])
        self.apply("plan_finalize", {"contract": plan_body(second_dependency=["M1"]), "summary": "Final plan",
                                      "decisions": [{"concern_id": "C1", "decision": "accepted", "rationale": "Regression added",
                                                     "acceptance_test": "Focused suite passes", "resolved": True}]})
        expected = {"requirements": "requirements-1.json", "plan": "plan-1.json", "plan_review": "review-1.json",
                    "plan_revise": "revision-1.json", "plan_finalize": "final-plan-1.json"}
        predecessors = {"plan": "requirements", "plan_review": "plan", "plan_revise": "plan_review",
                        "plan_finalize": "plan_revise"}
        for stage, filename in expected.items():
            with self.subTest(stage=stage):
                entry = self.state["planning_artifacts"][stage]
                self.assertEqual("planning/" + filename, entry["artifact"]["path"])
                self.assertEqual("planning/" + filename.removesuffix(".json") + ".delta.json", entry["delta"]["path"])
                for kind in ("artifact", "delta"):
                    path = self.run / entry[kind]["path"]
                    self.assertEqual(entry[kind]["sha256"], support.file_hash(path))
                delta = json.loads((self.run / entry["delta"]["path"]).read_text())
                support.validate_schema(delta, goals.DELTA_SCHEMA)
                artifact = json.loads((self.run / entry["artifact"]["path"]).read_text())
                self.assertEqual(stage, artifact["stage"])
                if stage in predecessors:
                    predecessor = self.state["planning_artifacts"][predecessors[stage]]["artifact"]
                    self.assertEqual(predecessor["path"], delta["input_path"])
                    self.assertEqual(predecessor["sha256"], delta["input_sha256"])
                else:
                    self.assertEqual("", delta["input_path"])
                    self.assertEqual("", delta["input_sha256"])
        final_delta = json.loads((self.run / self.state["planning_artifacts"]["plan_finalize"]["delta"]["path"]).read_text())
        self.assertIn({"from": "M2", "to": "M1", "change": "added"}, final_delta["graph_edge_diffs"])

    def test_final_outputs_are_sealed_consumable_and_invalidated_without_launching(self):
        self.initialize_plan()
        self.review()
        self.revise(second_dependency=[])
        decision = {"concern_id": "C1", "decision": "accepted", "rationale": "Regression added",
                    "acceptance_test": "Focused suite passes", "resolved": True}
        self.apply("plan_finalize", {"contract": plan_body(second_dependency=["M1"]), "summary": "Final plan",
                                      "decisions": [decision]})
        final = self.state["planning_final"]
        self.assertEqual(self.state["planning"]["final_token"], final["final_token"])
        for kind, relative in (("artifact", "planning/final-plan.json"),
                               ("delta", "planning/final-plan.delta.json"),
                               ("graph", "planning/graph.json")):
            self.assertEqual(relative, final[kind]["path"])
            self.assertEqual(final[kind]["sha256"], support.file_hash(self.run / relative))
        payload = json.loads((self.run / "planning/graph.json").read_text())
        self.assertEqual(final["final_token"], payload["final_token"])
        self.assertFalse(payload["automatic_execution"])
        self.assertEqual("not-approved", planning_graph.consume(self.state, self.run)["status"])
        self.assertIn("[C1] accepted", goals.render(self.state))

        graph_path = self.run / "planning/graph.json"
        original = graph_path.read_bytes()
        graph_path.write_text("{}\n")
        self.assertEqual("tampered", planning_graph.consume(self.state, self.run)["status"])
        graph_path.write_bytes(original)

        pending_feedback = copy.deepcopy(self.state)
        goals.feedback(pending_feedback, "Reconsider the dependency")
        self.assertEqual("stale", planning_graph.consume(pending_feedback, self.run)["status"])
        self.assertNotIn("planning_final", pending_feedback)
        self.assertEqual(final["final_token"], pending_feedback["planning_final_archive"][-1]["final_token"])

        self.state["iteration"] = 1
        goals.present(self.state)
        with patch.object(support, "snapshot", return_value={"revision": "fixture"}):
            goals.approve(self.state, final["final_token"])
        self.assertEqual("ready", planning_graph.consume(self.state, self.run)["status"])

    def test_verify_predecessor_rejects_missing_altered_unrecorded_and_planted_files(self):
        self.initialize_plan()
        entry = self.state["planning_artifacts"]["requirements"]
        verified = artifacts.verify_predecessor(self.state, "plan", self.run)
        self.assertEqual(entry["artifact"]["path"], verified["artifact"]["path"])
        artifact = self.run / entry["artifact"]["path"]
        original = artifact.read_bytes()
        artifact.unlink()
        with self.assertRaisesRegex(ValueError, "artifact is missing"):
            artifacts.verify_predecessor(self.state, "plan", self.run)
        artifact.write_bytes(original)
        artifact.write_text("altered")
        with self.assertRaisesRegex(ValueError, "hash does not match"):
            artifacts.verify_predecessor(self.state, "plan", self.run)
        artifact.write_bytes(original)
        delta = self.run / entry["delta"]["path"]
        original_delta = delta.read_bytes()
        delta.write_text("altered")
        with self.assertRaisesRegex(ValueError, "delta hash does not match"):
            artifacts.verify_predecessor(self.state, "plan", self.run)
        delta.write_bytes(original_delta)
        recorded_hash = entry["artifact"]["sha256"]
        entry["artifact"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash does not match"):
            artifacts.verify_predecessor(self.state, "plan", self.run)
        entry["artifact"]["sha256"] = recorded_hash
        (self.run / "planning" / "requirements-999.json").write_text("planted")
        self.state["planning_artifacts"].pop("requirements")
        with self.assertRaisesRegex(ValueError, "not state-recorded"):
            artifacts.verify_predecessor(self.state, "plan", self.run)

    def test_orphan_reconciliation_never_promotes_artifact_written_before_state_persistence(self):
        support.atomic_json(self.run / "state.json", self.state)
        with patch.object(runner, "write_json", side_effect=OSError("state persistence failed")):
            with self.assertRaisesRegex(OSError, "state persistence failed"):
                runner.commit_stage_result(self.state, "requirements",
                                           {"requirements": requirements(), "summary": "ready"},
                                           {"output": "requirements.json"}, self.root, self.run)
        persisted = support.read(self.run / "state.json")
        self.assertTrue((self.run / "planning" / "requirements-1.json").is_file())
        reconciled = artifacts.reconcile_orphans(persisted, self.run)
        self.assertEqual(2, len(reconciled))
        self.assertFalse((self.run / "planning" / "requirements-1.json").exists())
        self.assertTrue((self.run / reconciled[0]["archive"]).is_file())
        with self.assertRaisesRegex(ValueError, "not state-recorded"):
            artifacts.verify_predecessor(persisted, "plan", self.run)

    def test_rejected_revision_leaves_persisted_state_and_files_unchanged(self):
        self.initialize_plan()
        self.review()
        before_state = json.dumps(self.state, sort_keys=True)
        before_paths = {str(path.relative_to(self.run)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in self.run.glob("planning/*.json")}
        invalid = {"contract": plan_body(), "summary": "Incomplete", "responses": []}
        with self.assertRaisesRegex(ValueError, "Every plan-review concern"):
            runner.apply_result(self.state, "plan_revise", invalid, {"output": "revision.json"}, self.root, self.run)
        after_paths = {str(path.relative_to(self.run)): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in self.run.glob("planning/*.json")}
        self.assertEqual(before_state, json.dumps(self.state, sort_keys=True))
        self.assertEqual(before_paths, after_paths)

    def test_context_references_the_exact_verified_predecessor_paths(self):
        self.apply("requirements", {"requirements": requirements(), "summary": "ready"})
        self.initialize_plan()
        self.review()
        self.revise()
        self.apply("plan_finalize", {"contract": plan_body(), "summary": "Final plan",
                                      "decisions": [{"concern_id": "C1", "decision": "accepted",
                                                     "rationale": "Regression added",
                                                     "acceptance_test": "Focused suite passes", "resolved": True}]})
        predecessors = {"plan": "requirements", "plan_review": "plan", "plan_revise": "plan_review",
                        "plan_finalize": "plan_revise"}
        for stage, previous_stage in predecessors.items():
            with self.subTest(stage=stage):
                prompt, _ = planning.context(self.state, stage, self.run / "state.json")
                handoff = self.state["planning_artifacts"][previous_stage]
                self.assertIn(handoff["artifact"]["path"], prompt)
                self.assertIn(handoff["delta"]["path"], prompt)

    def test_v2_edit_goal_writes_plan_delta_from_recorded_prior_artifact_or_changes_nothing(self):
        self.initialize_plan()
        original = self.state["planning_artifacts"]["plan"]["artifact"]
        candidate = copy.deepcopy(self.state)
        edited = plan_body()
        edited["required_behaviors"].append("Document the command")
        goals.install_draft(candidate, edited, origin="user_cli_edit")
        artifacts.prepare_user_cli_edit(candidate, run_dir=self.run)
        runner.commit_user_action(self.state, candidate, self.run)
        entry = self.state["planning_artifacts"]["plan"]
        delta = json.loads((self.run / entry["delta"]["path"]).read_text())
        self.assertEqual(original["sha256"], delta["input_sha256"])
        self.assertEqual("plan_review", self.state["next_stage"])
        self.assertFalse(self.state["planning"]["derived_graph"]["automatic_execution"])
        self.assertEqual(entry["artifact"]["path"], artifacts.verify_predecessor(self.state, "plan_review", self.run)["artifact"]["path"])

        (self.run / entry["artifact"]["path"]).unlink()
        before_state = json.dumps(self.state, sort_keys=True)
        before_files = {str(path.relative_to(self.run)): path.read_bytes() for path in self.run.glob("planning/*.json")}
        candidate = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "prior plan artifact hash"):
            goals.install_draft(candidate, edited, origin="user_cli_edit")
            artifacts.prepare_user_cli_edit(candidate, run_dir=self.run)
        self.assertEqual(before_state, json.dumps(self.state, sort_keys=True))
        self.assertEqual(before_files, {str(path.relative_to(self.run)): path.read_bytes()
                                        for path in self.run.glob("planning/*.json")})

    def test_v2_edit_goal_state_failure_leaves_state_and_planning_files_byte_identical(self):
        self.initialize_plan()
        support.atomic_json(self.run / "state.json", self.state)
        before_state = copy.deepcopy(self.state)
        before_state_file = (self.run / "state.json").read_bytes()
        before_files = {str(path.relative_to(self.run)): path.read_bytes()
                        for path in self.run.glob("planning/*.json")}
        candidate = copy.deepcopy(self.state)
        edited = plan_body()
        edited["required_behaviors"].append("Document the command")
        goals.install_draft(candidate, edited, origin="user_cli_edit")
        artifacts.prepare_user_cli_edit(candidate, run_dir=self.run)

        with patch.object(runner, "write_json", side_effect=OSError("state persistence failed")):
            with self.assertRaisesRegex(OSError, "state persistence failed"):
                runner.commit_user_action(self.state, candidate, self.run)

        self.assertEqual(before_state, self.state)
        self.assertEqual(before_state_file, (self.run / "state.json").read_bytes())
        self.assertEqual(before_files, {str(path.relative_to(self.run)): path.read_bytes()
                                        for path in self.run.glob("planning/*.json")})
