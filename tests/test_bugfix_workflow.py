"""A bug fix through the full AutoCode workflow, proven by the runner.

Every stage runs: requirements, planning, plan challenge, revision, final plan
review, approval, orchestrator, Builder, Validator and Completion Owner. For a
contract whose task_kind is "bugfix" the runner adds a model-free
regression_proof step before the Validator, and the completion gate refuses
the fix unless that proof passes for the exact source being completed.
These runs use the offline fake provider; they make no model calls.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import autocode  # noqa: E402
import autocode_goal_lifecycle as lifecycle
import autocode_run_view as run_view  # noqa: E402
import scenario_references as references  # noqa: E402
import task_scenarios as scenarios  # noqa: E402

from tests import test_bug_job, test_planning, test_subprocess  # noqa: E402

# Requirements runs first and names the job (#1860); this fixture names a build.
PLANNING = [
    "requirements_gather",
    "astra_discovery",
    "astra_discovery",
    "astra_challenge",
    "glm_revise",
    "astra_finalize",
]
EXECUTION = ["orchestrator", "terra", "regression_proof", "sol", "astra_review"]
# A failing proof of this source is replayed once before the Completion Owner reviews
# (#2065: a single flake must not drive REWORK). The replay is runner-owned.
FAILED_PROOF_EXECUTION = ["orchestrator", "terra", "regression_proof", "sol", "regression_proof", "astra_review"]


class BugfixWorkflow(unittest.TestCase):
    # These regression/protected-oracle flows exercise a fixed planning route,
    # not automatic model selection or its runner-owned access probes.
    launch = test_planning.JointFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    prepare = test_planning.JointFlow.prepare
    draft = test_planning.JointFlow.draft
    new_run_engine_args = ()

    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        references.write(scenarios.BUGFIX_SEED, self.project)
        subprocess.run(["git", "-C", str(self.project), "add", "-A"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.project),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-qm",
                "seed: greeting CLI with the blank-name bug",
            ],
            check=True,
        )
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
        self.builder_writes(
            {
                "greet.py": references.BUGFIX_REFERENCE["greet.py"],
                "test_greet.py": references.BUGFIX_REFERENCE["test_greet.py"],
            }
        )
        _, approved, final, _ = self.run_to_end(0)
        # The job type is part of the approved contract and shown at approval.
        self.assertEqual("bugfix", approved["goal_contract"]["body"]["task_kind"])
        self.assertIn("Job type: bug fix", lifecycle.render(approved))
        self.assertIn(
            "Job kind: build (Requirements understood:", lifecycle.render(approved)
        )  # the offline fixture's Requirements names every request a build
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
        # Small-job guard (#15): nine model calls (Requirements names the job, so no recognizer call, #1860),
        # no report-format repair, one runner proof.
        model_calls = [row for row in final["stages"] if not row.get("runner_owned")]
        self.assertEqual(9, len(model_calls))
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
        # Every stage still ran and the gate refused, including the once-only replay of a
        # failing proof of this source. A runner-owned proof refusal is not a failed model
        # attempt and must not spend an extra Investigator call merely to explain that the
        # proof is missing.
        self.assertEqual(PLANNING + FAILED_PROOF_EXECUTION, stages)
        self.assertIn("no passing regression proof", final["stop_reason"])
        self.assertFalse(final.get("stuck_investigations"))

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
        record = self.record(
            "astra_finalize", {"contract": {"type": "object", "properties": {"task_kind": {"type": "string"}}}}
        )
        value = autocode.default_missing_provenance({"contract": {"intended_outcome": "x"}}, record)
        self.assertEqual({"intended_outcome": "x", "task_kind": "build"}, value["contract"])
        self.assertEqual(["contract.task_kind"], record["defaulted_fields"])

    def test_a_report_field_written_inside_the_contract_is_moved_up(self):
        # Live today-pipeline Planner draft, 2026-10-02: contract_changes inside the contract.
        properties = {
            "contract": {"type": "object", "properties": {"intended_outcome": {"type": "string"}}},
            "contract_changes": {"type": "array"},
            "code_refs": {"type": "array"},
        }
        record = self.record("astra_discovery", properties)
        change = {"item": "AC2", "change": "reworded", "basis": "user_answer", "answer_id": "Q1", "replacement": "x"}
        value = autocode.default_missing_provenance(
            {"contract": {"intended_outcome": "x", "contract_changes": [change]}, "code_refs": ["a.py:1"]}, record
        )
        self.assertEqual({"intended_outcome": "x"}, value["contract"])
        self.assertEqual([change], value["contract_changes"])
        self.assertEqual(["contract_changes"], record["hoisted_fields"])
        record = self.record("astra_discovery", properties)
        kept = autocode.default_missing_provenance(
            {
                "contract": {"intended_outcome": "x", "contract_changes": [change]},
                "contract_changes": [change],
                "code_refs": [],
            },
            record,
        )
        self.assertIn("contract_changes", kept["contract"], "a report that has its own list is left to the schema")
        self.assertNotIn("hoisted_fields", record)

    def test_an_omitted_current_behavior_box_is_empty_not_a_repair_call(self):
        # The strict generation schema requires the box on every criterion (#2133); the offline fixture
        # providers and a provider without enforced structured output leave it out. Absent is empty:
        # the runner fills the box from the bound diagnosis or leaves it so, with no report-repair call.
        criterion = {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "criterion": {"type": "string"},
                "current_behavior": {"type": "string"},
            },
        }
        properties = {
            "contract": {"type": "object", "properties": {"acceptance_criteria": {"type": "array", "items": criterion}}}
        }
        record = self.record("astra_discovery", properties)
        rows = [{"id": "C1", "criterion": "a"}, {"id": "C2", "criterion": "b", "current_behavior": "prints 0"}]
        value = autocode.default_missing_provenance({"contract": {"acceptance_criteria": rows}}, record)
        self.assertEqual(
            [{"id": "C1", "criterion": "a", "current_behavior": ""}, rows[1]], value["contract"]["acceptance_criteria"]
        )
        self.assertEqual(["contract.acceptance_criteria.current_behavior"], record["defaulted_fields"])
        # A finalize delta's upsert rows (autocode_plan_retention.model_schema) are criteria too; a
        # retained reference row is not.
        delta = {"type": "object", "properties": {"upsert": {"type": "array", "items": criterion}}}
        offered = {
            "contract": {
                "type": "object",
                "properties": {"acceptance_criteria": {"anyOf": [{"type": "array", "items": criterion}, delta]}},
            }
        }
        record = self.record("astra_finalize", offered)
        value = autocode.default_missing_provenance(
            {"contract": {"acceptance_criteria": {"retain_from": "t", "upsert": [rows[0]], "remove": []}}}, record
        )
        self.assertEqual(
            {"retain_from": "t", "upsert": [{"id": "C1", "criterion": "a", "current_behavior": ""}], "remove": []},
            value["contract"]["acceptance_criteria"],
        )
        self.assertEqual(["contract.acceptance_criteria.upsert.current_behavior"], record["defaulted_fields"])
        record = self.record("astra_finalize", offered)
        kept = {"contract": {"acceptance_criteria": [{"retain_criterion": "C1", "sha256": "0" * 64}]}}
        self.assertEqual(kept, autocode.default_missing_provenance(kept, record))
        self.assertNotIn("defaulted_fields", record)
        # A complete report, and one against a schema saved before the box existed, are left alone.
        record = self.record("astra_discovery", properties)
        complete = {"contract": {"acceptance_criteria": [rows[1]]}}
        self.assertEqual(complete, autocode.default_missing_provenance(complete, record))
        self.assertNotIn("defaulted_fields", record)
        plain = {"type": "object", "properties": {"id": {"type": "string"}, "criterion": {"type": "string"}}}
        legacy = self.record(
            "astra_discovery",
            {"contract": {"type": "object", "properties": {"acceptance_criteria": {"type": "array", "items": plain}}}},
        )
        before = {"contract": {"acceptance_criteria": [rows[0]]}}
        self.assertEqual(before, autocode.default_missing_provenance(before, legacy))
        self.assertNotIn("defaulted_fields", legacy)

    def test_revision_reference_and_legacy_body_get_only_their_own_defaults(self):
        import autocode_plan_retention as retention
        from units import autoplanner

        state = {"goal_contract": {"hash": "h", "revision": 1, "body": {}}}
        schema = retention.model_schema(autoplanner.SCHEMAS["glm_revise"], state, "glm_revise")
        properties = schema["properties"]
        ref = {"revise_contract": "r1:h", "changes": {}}
        record = self.record("glm_revise", properties)
        result = autocode.default_missing_provenance({"contract": ref}, record)
        self.assertEqual(ref, result["contract"])
        row = {"id": "C1", "criterion": "Preserve greeting"}
        delta = {"retain_from": "r1:h", "upsert": [row], "remove": []}
        result = autocode.default_missing_provenance(
            {"contract": {**ref, "acceptance_criteria": delta}}, self.record("glm_revise", properties)
        )
        self.assertEqual("", result["contract"]["acceptance_criteria"]["upsert"][0]["current_behavior"])
        self.assertNotIn("task_kind", result["contract"])
        result = autocode.default_missing_provenance(
            {"contract": {"acceptance_criteria": [row]}}, self.record("glm_revise", properties)
        )
        self.assertEqual("build", result["contract"]["task_kind"])
        self.assertEqual("", result["contract"]["acceptance_criteria"][0]["current_behavior"])

    def test_the_resolver_accepts_a_bug_fix_contract(self):
        import autocode_resolver as resolver
        from goal_fixtures import body

        self.assertTrue(resolver._validate_body(body(task_kind="bugfix")))
        self.assertFalse(resolver._validate_body(body(task_kind="rewrite")))
        # A criterion's current_behavior box (#2133) is a known optional string, like sources.
        boxed = body(task_kind="bugfix")
        boxed["acceptance_criteria"][0]["current_behavior"] = "prints the total in cents"
        self.assertTrue(resolver._validate_body(boxed))
        boxed["acceptance_criteria"][0]["current_behavior"] = ""
        self.assertTrue(resolver._validate_body(boxed))
        boxed["acceptance_criteria"][0]["current_behavior"] = ["not", "text"]
        self.assertFalse(resolver._validate_body(boxed))

    def test_the_proof_uses_the_project_virtualenv_when_the_task_worktree_has_none(self):
        from unittest import mock

        import autocode_regression as regression
        import autocode_verify as verify

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        project, task = Path(temp.name) / "project", Path(temp.name) / "task"
        (project / ".venv" / "bin").mkdir(parents=True)
        (project / ".venv" / "bin" / "python").symlink_to(sys.executable)
        task.mkdir()
        subprocess.run(["git", "init", "-q", str(task)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(task),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "base",
            ],
            check=True,
        )
        base = subprocess.run(
            ["git", "-C", str(task), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        state = {
            "goal_contract": {"body": {"task_kind": "bugfix"}},
            "project_workspace": str(project),
            "base_commit": base,
            "settings": {},
        }
        result = {"verdict": "PASS", "failures": [], "unverified": [], "checks": {}}
        with (
            mock.patch.object(verify, "detect_framework", return_value=None) as detect,
            mock.patch.object(verify, "verify", return_value=result),
        ):
            regression.prove(state, task, Path(temp.name) / "run")
        detect.assert_called_once_with(task, python=str(project / ".venv" / "bin" / "python"), base=base)
        # An explicit interpreter setting still wins.
        state = {**state, "settings": {"regression": {"python": "/opt/python"}}}
        with (
            mock.patch.object(verify, "detect_framework", return_value=None) as detect,
            mock.patch.object(verify, "verify", return_value=result),
        ):
            regression.prove(state, task, Path(temp.name) / "run")
        detect.assert_called_once_with(task, python="/opt/python", base=base)

    def test_decision_lists_and_review_stages_are_never_defaulted(self):
        record = self.record("astra_challenge", {"concerns": {"type": "array"}})
        self.assertEqual({}, autocode.default_missing_provenance({}, record))
        record = self.record("sol", {"code_refs": {"type": "array"}})
        self.assertEqual({}, autocode.default_missing_provenance({}, record))


if __name__ == "__main__":
    unittest.main()


class CurrentBehaviorPlanning(unittest.TestCase):
    """#2133: what happens today lives in each regression criterion's current_behavior box. The runner fills
    it from the Investigator's bound diagnosis, the approval screen shows it as "Today:", and a Planner that
    corrects its own wrong note to that text costs no report repair and no question."""

    launch = test_planning.JointFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    prepare = test_planning.JointFlow.prepare
    new_run_engine_args = ()
    OBSERVED = "greet.py prints 'Hello, ' and exits 0 for a blank name"

    def setUp(self):
        BugfixWorkflow.setUp(self)
        self.env["AUTOCODE_FIXTURE_CURRENT_BEHAVIOR"] = self.OBSERVED

    def plan(self):
        """JointFlow.draft's planning flow, with a reproduced diagnosis bound to the run before the Planner
        drafts from the saved answer (the offline Requirements fixture names every job a build, so no
        Investigator stage runs here)."""
        self.prepare()
        self.launch(["Fix the blank-name bug", "--no-chat"], 2)
        run, state = self.saved()
        self.assertEqual("WAITING_FOR_USER", state["status"])
        report = {**test_bug_job.diagnosis(fix_size="large"), "observed": self.OBSERVED, "test_cases": []}
        output = run / "iterations" / "investigator-fixture.json"
        output.write_text(json.dumps(report))
        state["investigation"] = {
            **report,
            "output": str(output),
            "output_hash": hashlib.sha256(output.read_bytes()).hexdigest(),
            "source_revision": "seed",
            "probe_result": {"exit_code": 0, "counterexample": {"exit_code": 1}},
        }
        (run / "state.json").write_text(json.dumps(state))
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        return self.saved()

    def criterion(self, state):
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])
        # Every planning stage once: no report repair and no question to the person.
        self.assertEqual(PLANNING, [row["stage"] for row in state["stages"]])
        rows = state["goal_contract"]["body"]["acceptance_criteria"]
        self.assertEqual(["C1"], [row["id"] for row in rows])
        return rows[0]

    def test_the_runner_fills_the_box_from_the_bound_diagnosis_and_shows_it(self):
        run, state = self.plan()
        c1 = self.criterion(state)
        self.assertEqual(self.OBSERVED, c1["current_behavior"])
        self.assertIn(
            f"  [C1] {c1['criterion']}\n    Today: {self.OBSERVED}\n    Checked by: test: test_c1_blank_name_rejected",
            lifecycle.render(state),
        )
        shown = run_view.view(state)["displayed_plan"]["acceptance_criteria"][0]
        self.assertEqual(self.OBSERVED, shown["current_behavior"])

    def test_the_planners_wrong_note_is_corrected_to_the_evidence_with_a_receipt_and_no_question(self):
        self.env["AUTOCODE_FIXTURE_CURRENT_BEHAVIOR_DRAFT"] = "greet.py prints 'Hello, World' for a blank name"
        run, state = self.plan()
        c1 = self.criterion(state)
        self.assertEqual(self.OBSERVED, c1["current_behavior"])
        receipts = [
            row
            for revision in [*state["contract_history"], state["goal_contract"]]
            for row in revision.get("declared_changes") or []
            if row.get("change") == "current_behavior"
        ]
        self.assertEqual(["C1"], [row["item"] for row in receipts])
        self.assertEqual(
            {"output_hash": state["investigation"]["output_hash"], "source_revision": "seed"}, receipts[0]["evidence"]
        )
        self.assertIn(
            f"  - current_behavior: C1 -> {self.OBSERVED} (basis: agent_proposed; revision ", lifecycle.render(state)
        )
