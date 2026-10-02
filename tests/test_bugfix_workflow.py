"""A bug fix through the full AutoCode workflow, proven by the runner.

Every stage runs: requirements, planning, plan challenge, revision, final plan
review, approval, orchestrator, Builder, Validator and Completion Owner. For a
contract whose task_kind is "bugfix" the runner adds a model-free
regression_proof step before the Validator, and the completion gate refuses
the fix unless that proof passes for the exact source being completed.
These runs use the offline fake provider; they make no model calls.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import autocode  # noqa: E402
import autocode_goals as goals  # noqa: E402
import autocode_goal_lifecycle as lifecycle
import scenario_references as references  # noqa: E402
import task_scenarios as scenarios  # noqa: E402
from tests import test_planning, test_subprocess  # noqa: E402

# The job recognizer runs first (autocode_workflows); this fixture recognizes a build request.
PLANNING = ["recognize_workflow", "requirements_gather", "astra_discovery", "astra_discovery", "astra_challenge",
            "glm_revise", "astra_finalize"]
EXECUTION = ["orchestrator", "terra", "regression_proof", "sol", "astra_review"]


class BugfixWorkflow(unittest.TestCase):
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    prepare = test_planning.JointFlow.prepare
    draft = test_planning.JointFlow.draft
    new_run_engine_args = ()

    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        references.write(scenarios.BUGFIX_SEED, self.project)
        subprocess.run(["git", "-C", str(self.project), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=t", "-c", "user.email=t@example.test",
                        "commit", "-qm", "seed: greeting CLI with the blank-name bug"], check=True)
        self.env["AUTOCODE_FIXTURE_TASK_KIND"] = "bugfix"

    def builder_writes(self, files):
        path = self.root / "builder-files.json"
        path.write_text(json.dumps(files))
        self.env["AUTOCODE_FIXTURE_FILES"] = str(path)

    def run_to_end(self, expected):
        run, state = self.draft()
        self.launch(["--run-dir", str(run), "--approve-goal", state["displayed_goal"]], 0)
        result = self.launch(["--run-dir", str(run), "--no-chat"], expected)
        return run, state, self.saved()[1], result

    def test_bug_fix_runs_every_stage_and_completes_only_with_the_runner_proof(self):
        self.builder_writes({"greet.py": references.BUGFIX_REFERENCE["greet.py"],
                             "test_greet.py": references.BUGFIX_REFERENCE["test_greet.py"]})
        _, approved, final, _ = self.run_to_end(0)
        # The job type is part of the approved contract and shown at approval.
        self.assertEqual("bugfix", approved["goal_contract"]["body"]["task_kind"])
        self.assertIn("Job type: bug fix", lifecycle.render(approved))
        self.assertIn("Job kind: build (recognized:", lifecycle.render(approved))  # the offline fixture recognizes every request as a build
        self.assertEqual("TASK_COMPLETE", final["status"])
        stages = [row["stage"] for row in final["stages"]]
        self.assertEqual(PLANNING + EXECUTION, stages)
        proof = final["regression_proof"]
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertEqual(["test_greet.TestGreet.test_blank_name_rejected"], proof["fail_to_pass"])
        self.assertEqual(final["validation"]["source_revision"], proof["source_revision"])
        runner = next(row for row in final["stages"] if row["stage"] == "regression_proof")
        self.assertTrue(runner["runner_owned"])
        self.assertEqual(0, runner["metrics"]["provider_tokens"]["input_tokens"])
        # Small-job guard (#15): ten model calls (nine plus the job recognizer), no report-format
        # repair, one runner proof.
        model_calls = [row for row in final["stages"] if not row.get("runner_owned")]
        self.assertEqual(10, len(model_calls))
        self.assertFalse([row for row in final["stages"] if row["stage"].endswith("_report_repair")])
        # The independent scenario oracle agrees with the runner.
        self.assertEqual("PASS", scenarios.bugfix01_oracle(self.project).status)

    def test_a_fix_without_a_regression_test_cannot_complete(self):
        self.builder_writes({"greet.py": references.BUGFIX_REFERENCE["greet.py"]})
        _, _, final, result = self.run_to_end(2)
        self.assertNotEqual("TASK_COMPLETE", final["status"], result.stdout)
        proof = final["regression_proof"]
        self.assertEqual("FAIL", proof["verdict"])
        self.assertTrue(any("No regression test" in reason for reason in proof["failures"]))
        stages = [row["stage"] for row in final["stages"]]
        # Every stage still ran and the gate refused; the refusal was investigated once
        # (autocode_stuck_job) and the fixture's Investigator left the pause standing.
        self.assertEqual(PLANNING + EXECUTION + ["investigate_stuck"], stages)
        self.assertIn("no passing regression proof", final["stop_reason"])
        self.assertEqual(["paused"], [row["outcome"] for row in final["stuck_investigations"]])

    def test_build_tasks_get_no_proof_step(self):
        self.env["AUTOCODE_FIXTURE_TASK_KIND"] = "build"
        self.builder_writes({"greet.py": references.BUGFIX_REFERENCE["greet.py"]})
        _, _, final, _ = self.run_to_end(0)
        self.assertEqual("TASK_COMPLETE", final["status"])
        self.assertNotIn("regression_proof", [row["stage"] for row in final["stages"]])
        self.assertNotIn("regression_proof", final)


class ProvenanceDefaults(unittest.TestCase):
    def record(self, stage, properties):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        schema = Path(temp.name) / "schema.json"
        schema.write_text(json.dumps({"type": "object", "properties": properties}))
        return {"stage": stage, "schema": str(schema)}

    def test_missing_provenance_lists_are_defaulted_instead_of_repaired(self):
        record = self.record("astra_discovery", {"code_refs": {"type": "array"}, "summary": {"type": "string"}})
        value = autocode.default_missing_provenance({"summary": "Plan"}, record)
        self.assertEqual({"summary": "Plan", "code_refs": []}, value)
        self.assertEqual(["code_refs"], record["defaulted_fields"])

    def test_an_omitted_job_type_is_build_not_a_repair_call(self):
        record = self.record("requirements_gather", {"task_kind": {"type": "string"}})
        self.assertEqual({"task_kind": "build"}, autocode.default_missing_provenance({}, record))
        record = self.record("astra_finalize", {"contract": {"type": "object", "properties": {
            "task_kind": {"type": "string"}}}})
        value = autocode.default_missing_provenance({"contract": {"intended_outcome": "x"}}, record)
        self.assertEqual({"intended_outcome": "x", "task_kind": "build"}, value["contract"])
        self.assertEqual(["contract.task_kind"], record["defaulted_fields"])

    def test_a_report_field_written_inside_the_contract_is_moved_up(self):
        # Live today-pipeline Planner draft, 2026-10-02: contract_changes inside the contract.
        properties = {"contract": {"type": "object", "properties": {"intended_outcome": {"type": "string"}}},
                      "contract_changes": {"type": "array"}, "code_refs": {"type": "array"}}
        record = self.record("astra_discovery", properties)
        change = {"item": "AC2", "change": "reworded", "basis": "user_answer", "answer_id": "Q1", "replacement": "x"}
        value = autocode.default_missing_provenance(
            {"contract": {"intended_outcome": "x", "contract_changes": [change]}, "code_refs": ["a.py:1"]}, record)
        self.assertEqual({"intended_outcome": "x"}, value["contract"])
        self.assertEqual([change], value["contract_changes"])
        self.assertEqual(["contract_changes"], record["hoisted_fields"])
        record = self.record("astra_discovery", properties)
        kept = autocode.default_missing_provenance(
            {"contract": {"intended_outcome": "x", "contract_changes": [change]}, "contract_changes": [change],
             "code_refs": []}, record)
        self.assertIn("contract_changes", kept["contract"], "a report that has its own list is left to the schema")
        self.assertNotIn("hoisted_fields", record)

    def test_an_optional_field_written_as_null_is_left_out(self):
        item = {"type": "object", "required": ["item", "change"], "properties": {
            "item": {"type": "string"}, "change": {"type": "string"}, "example_correction": {"type": "object"}}}
        record = self.record("glm_revise", {"contract_changes": {"type": "array", "items": item},
                                           "code_refs": {"type": "array"}})
        value = autocode.default_missing_provenance({"code_refs": [], "contract_changes": [
            {"item": "AC6", "change": "reworded", "example_correction": None}]}, record)
        self.assertEqual([{"item": "AC6", "change": "reworded"}], value["contract_changes"])
        self.assertEqual(["contract_changes"], record["dropped_null_fields"])
        record = self.record("glm_revise", {"contract_changes": {"type": "array", "items": item}})
        required_null = autocode.default_missing_provenance({"contract_changes": [{"item": None, "change": "x"}]}, record)
        self.assertEqual([{"item": None, "change": "x"}], required_null["contract_changes"], "required fields stay")

    def test_the_resolver_accepts_a_bug_fix_contract(self):
        import autocode_resolver as resolver
        from goal_fixtures import body
        self.assertTrue(resolver._validate_body(body(task_kind="bugfix")))
        self.assertFalse(resolver._validate_body(body(task_kind="rewrite")))

    def test_the_proof_uses_the_project_virtualenv_when_the_task_worktree_has_none(self):
        import autocode_regression as regression
        import autocode_verify as verify
        from unittest import mock
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        project, task = Path(temp.name) / "project", Path(temp.name) / "task"
        (project / ".venv" / "bin").mkdir(parents=True)
        (project / ".venv" / "bin" / "python").symlink_to(sys.executable)
        task.mkdir()
        subprocess.run(["git", "init", "-q", str(task)], check=True)
        subprocess.run(["git", "-C", str(task), "-c", "user.name=t", "-c", "user.email=t@example.test",
                        "commit", "--allow-empty", "-qm", "base"], check=True)
        state = {"goal_contract": {"body": {"task_kind": "bugfix"}}, "project_workspace": str(project),
                 "base_commit": "abc", "settings": {}}
        seen = []
        result = {"verdict": "PASS", "failures": [], "unverified": [], "checks": {}}
        with mock.patch.object(verify, "detect_framework", side_effect=lambda root, python: seen.append(python)), \
                mock.patch.object(verify, "verify", return_value=result):
            regression.prove(state, task, Path(temp.name) / "run")
        self.assertEqual([str(project / ".venv" / "bin" / "python")], seen)
        # An explicit interpreter setting still wins.
        state = {**state, "settings": {"regression": {"python": "/opt/python"}}}
        seen.clear()
        with mock.patch.object(verify, "detect_framework", side_effect=lambda root, python: seen.append(python)), \
                mock.patch.object(verify, "verify", return_value=result):
            regression.prove(state, task, Path(temp.name) / "run")
        self.assertEqual(["/opt/python"], seen)

    def test_decision_lists_and_review_stages_are_never_defaulted(self):
        record = self.record("astra_challenge", {"concerns": {"type": "array"}})
        self.assertEqual({}, autocode.default_missing_provenance({}, record))
        record = self.record("sol", {"code_refs": {"type": "array"}})
        self.assertEqual({}, autocode.default_missing_provenance({}, record))


if __name__ == "__main__":
    unittest.main()
