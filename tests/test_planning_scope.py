"""Planner discoveries reach the existing grounded repository scope boundary."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_goals as goals
import autocode_planning_scope as planning_scope
import autocode_run_records as records
import autocode_run_view as run_view
import autocode_scope_question as scope
import autocode_workspace_scope as workspace_scope
from goal_fixtures import body
from units import autoplanner as planner

from tests import ROOT

from .test_autoplanner_step2 import EpisodeCase, clarification_only, discovery, question


class PlanningScopeTests(EpisodeCase):
    def setUp(self):
        super().setUp()
        self.part = {
            "target": "history-service",
            "work": "Preserve the command in stored history",
            "source_quote": "",
            "evidence": "config.py:1",
        }
        self.report = discovery(body(), workspace_scope={"spans_repositories": True, "external_parts": [self.part]})
        self.state.update(run_dir=str(self.workspace / "run"), iteration=1, stages=[], history=[])

    def prepare(self, report):
        return planning_scope.prepare_handoff(
            self.state,
            "astra_discovery",
            report,
            {"output": "report.json"},
            investigation_stages=planner.INVESTIGATION_STAGES,
            check_code_refs=lambda *args: None,
        )

    def test_handoff_preparation_defers_new_scope_without_installing_a_draft(self):
        value, deferred = self.prepare(self.report)
        self.assertTrue(deferred)
        self.assertEqual(self.report, value)
        self.assertEqual("PAUSED_SCOPE_DECISION", self.state["status"])
        self.assertNotIn("goal_contract", self.state)

    def test_handoff_preparation_retains_the_existing_investigation_boundary(self):
        report = discovery(clarification_only([question("Q1")]))
        value, deferred = self.prepare(report)
        self.assertTrue(deferred)
        self.assertEqual(report, value)
        self.assertEqual(["Q1"], self.state["investigation_request"]["question_ids"])
        self.assertNotIn("workspace_scope", self.state)

    def test_handoff_preparation_continues_a_local_report(self):
        report = discovery(body())
        value, deferred = self.prepare(report)
        self.assertFalse(deferred)
        self.assertEqual(report, value)
        self.assertNotIn("workspace_scope", self.state)

    def test_malformed_citations_are_rejected_before_saving_external_scope(self):
        for sources, message in (
            ([{"source_id": "task:0", "quote": self.state["task"]}] * 2, "twice with one quote"),
            ([{"source_id": "missing", "quote": self.state["task"]}], "without quoting it verbatim"),
        ):
            with self.subTest(message=message):
                report = copy.deepcopy(self.report)
                report["contract"]["acceptance_criteria"][0]["sources"] = sources
                before = copy.deepcopy(report)
                with self.assertRaisesRegex(ValueError, message):
                    self.prepare(report)
                self.assertEqual(before, report)
                self.assertNotIn("workspace_scope", self.state)
                self.assertNotIn("goal_contract", self.state)

    def test_optional_field_and_guidance_reach_both_planner_stages(self):
        for stage in ("astra_discovery", "glm_revise"):
            with self.subTest(stage=stage):
                schema = planner.schema_for(self.state, stage)
                self.assertEqual(workspace_scope.SCHEMA, schema["properties"].get("workspace_scope"))
                self.assertNotIn("workspace_scope", schema["required"])
                prompt, _ = planner.context(self.state, stage, Path(self.state["run_dir"]) / "state.json")
                self.assertIn("LATE REPOSITORY DISCOVERY", prompt)
                self.assertIn("does not assign a repository or approve implementation", prompt)

    def test_grounded_discovery_stops_before_review_and_publishes_exact_scope(self):
        original = copy.deepcopy(self.report)
        self.apply("astra_discovery", self.report)
        records.normalize_human_boundary(self.state, self.state["run_dir"])
        view = run_view.view(self.state)
        self.assertEqual("answer", view["needs"]["kind"])
        self.assertTrue(view["needs"]["questions"][0]["id"].startswith(scope.PREFIX))
        self.assertEqual([self.part], view["scope"]["external_parts"])
        self.assertIsNone(view["scope"]["decision"])
        self.assertEqual("astra_discovery", view["next_stage"])
        self.assertFalse(goals.approved(self.state))
        self.assertEqual(original, self.report)

    def test_revision_can_discover_scope_without_overwriting_the_current_contract(self):
        self.apply("astra_discovery", discovery(body()))
        before = copy.deepcopy(self.state["goal_contract"])
        revision = copy.deepcopy(self.report)
        for key in ("alternatives", "uncertainties"):
            revision.pop(key)
        revision.update(responses=[])
        self.apply("glm_revise", revision)
        self.assertEqual([self.part], scope.view(self.state)["external_parts"])
        self.assertEqual("glm_revise", self.state["next_stage"])
        self.assertEqual(before, self.state["goal_contract"])

    def test_uncited_or_outside_evidence_cannot_create_external_work(self):
        for evidence in ("missing.py:1", "../outside.py", "config.py:9"):
            with self.subTest(evidence=evidence):
                report = copy.deepcopy(self.report)
                report["workspace_scope"]["external_parts"][0]["evidence"] = evidence
                self.apply("astra_discovery", report)
                self.assertEqual([], scope.view(self.state)["external_parts"])
                self.assertEqual(["history-service"], scope.view(self.state)["dropped"])
                self.assertEqual("astra_challenge", self.state["next_stage"])

    def test_quoted_work_is_grounded_in_the_saved_request(self):
        report = copy.deepcopy(self.report)
        report["workspace_scope"]["external_parts"][0].update(source_quote=self.state["task"], evidence="")
        self.apply("astra_discovery", report)
        self.assertEqual(self.state["task"], scope.view(self.state)["external_parts"][0]["source_quote"])

    def test_empty_model_refresh_cannot_erase_discovered_work(self):
        workspace_scope.record(self.state, self.report["workspace_scope"], {}, stage="requirements_gather")
        report = copy.deepcopy(self.report)
        report["workspace_scope"] = {"spans_repositories": False, "external_parts": []}
        self.apply("astra_discovery", report)
        self.assertEqual([self.part], scope.view(self.state)["external_parts"])
        self.assertEqual("astra_discovery", self.state["next_stage"])

    def test_already_assigned_scope_continues_without_a_second_question(self):
        workspace_scope.record(self.state, self.report["workspace_scope"], {}, stage="requirements_gather")
        report = copy.deepcopy(self.report)
        report["contract"]["scope_exclusions"].append(scope.exclusion(self.part))
        with mock.patch.object(
            scope.assignment, "current", return_value={"identity": workspace_scope.identity(self.state)}
        ):
            self.apply("astra_discovery", report)
            self.assertIsNone(scope.question(self.state))
        self.assertEqual("astra_challenge", self.state["next_stage"])

    def test_legacy_report_keeps_the_existing_planning_path(self):
        self.apply("astra_discovery", discovery(body()))
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.assertNotIn("workspace_scope", self.state)


class RetainedPlanningScopeTests(EpisodeCase):
    prepare = PlanningScopeTests.prepare

    def setUp(self):
        super().setUp()
        self.part = {
            "target": "history-service",
            "work": "Preserve the command in stored history",
            "source_quote": "",
            "evidence": "config.py:1",
        }
        self.report = discovery(body(), workspace_scope={"spans_repositories": True, "external_parts": [self.part]})
        self.state.update(iteration=1, stages=[], history=[])
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        (self.workspace / ".gitignore").write_text(".autocode/\n")
        subprocess.run(["git", "-C", str(self.workspace), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "-qm",
                "source",
            ],
            check=True,
        )
        self.output = self.workspace / ".autocode" / "report.json"
        self.output.parent.mkdir()
        self.output.write_text(json.dumps(self.report))
        self.state["run_dir"] = str(self.output.parent / "run")
        self.record = {"stage": "astra_discovery", "output": str(self.output), "source_revision": "original"}

    def hold(self):
        import autopilot

        autopilot.apply_planning(self.state, "astra_discovery", self.report, self.record)
        return copy.deepcopy(self.state)

    def resume(self):
        import autopilot

        with mock.patch.object(
            scope.assignment, "current", return_value={"identity": workspace_scope.identity(self.state)}
        ):
            return planning_scope.resume(self.state, apply=autopilot.apply_planning_result)

    def test_exact_assignment_resumes_the_saved_draft_once_without_a_new_report(self):
        self.hold()
        self.assertTrue(self.resume())
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.assertFalse(goals.approved(self.state))
        self.assertEqual([], self.state["stages"])
        self.assertEqual([self.part], scope.parts(self.state))
        self.assertFalse(self.resume())

    def test_unassigned_and_legacy_reports_have_no_reuse(self):
        self.assertFalse(planning_scope.resume(self.state, apply=mock.Mock()))
        self.hold()
        before = copy.deepcopy(self.state)
        apply = mock.Mock()
        self.assertFalse(planning_scope.resume(self.state, apply=apply))
        apply.assert_not_called()
        self.assertEqual(before, self.state)

    def test_saved_revision_continues_to_review_without_replacing_the_report(self):
        import autopilot

        self.apply("astra_discovery", discovery(body()))
        self.state["planning"]["reports"]["astra_challenge"] = {"report": {"concerns": []}}
        revision = copy.deepcopy(self.report)
        for key in ("alternatives", "uncertainties"):
            revision.pop(key)
        revision["responses"] = []
        self.output.write_text(json.dumps(revision))
        record = {**self.record, "stage": "glm_revise"}
        autopilot.apply_planning(self.state, "glm_revise", revision, record)
        self.assertEqual("PAUSED_SCOPE_DECISION", self.state["status"])
        self.assertTrue(self.resume())
        self.assertEqual("astra_finalize", self.state["next_stage"])
        self.assertEqual(str(self.output), self.state["planning"]["reports"]["glm_revise"]["output"])
        self.assertFalse(goals.approved(self.state))

    def test_changed_source_report_or_authority_refuses_reuse(self):
        original = self.hold()
        mutations = {
            "task": lambda: self.state.update(task="A different task"),
            "settings": lambda: self.state["settings"].update(max_seconds=999),
            "feedback": lambda: self.state["user_events"].append({"actor": "user_cli", "text": "Change it"}),
            "contract": lambda: self.state.update(goal_contract={"hash": "changed"}),
            "turn": lambda: self.state.update(turns=[{}]),
            "obligations": lambda: self.state.update(
                deferred_obligations=[{"id": "new", "kind": "human_decision", "status": "open"}]
            ),
            "source": lambda: (self.workspace / "config.py").write_text("CHANGED = True\n"),
            "report": lambda: self.output.write_text("{}"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                self.state = copy.deepcopy(original)
                (self.workspace / "config.py").write_text("PROVIDER = 'opencode'\nROUTE = 'default'\n")
                self.output.write_text(json.dumps(self.report))
                mutate()
                before = copy.deepcopy(self.state)
                with self.assertRaisesRegex(ValueError, "changed"):
                    self.resume()
                self.assertEqual(before, self.state)

    def test_runtime_prompt_change_refuses_saved_handoff(self):
        self.hold()
        before = copy.deepcopy(self.state)
        with mock.patch.object(planning_scope.prompts, "HASH", "new-runtime-prompts"):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.resume()
        self.assertEqual(before, self.state)

    def test_assignment_keeps_resolved_questions_settled_when_peer_exclusions_expand(self):
        self.apply("astra_discovery", discovery(clarification_only([question("Q1")])))
        handoff_hash = self.state["investigation_request"]["handoff_hash"]
        self.report["machine_resolutions"] = [self.resolution("Q1", handoff_hash)]
        self.output.write_text(json.dumps(self.report))
        self.hold()
        self.assertNotIn("investigation_request", self.state)
        self.assertTrue(self.resume())
        self.assertEqual("astra_challenge", self.state["next_stage"])
        self.assertEqual(["Q1"], [row["question_id"] for row in self.state["machine_resolutions"]])
        self.assertIn(scope.exclusion(self.part), self.state["goal_contract"]["body"]["scope_exclusions"])
        self.assertFalse(goals.approved(self.state))

    def test_failed_draft_validation_keeps_the_saved_handoff_and_contract(self):
        self.hold()
        before = copy.deepcopy(self.state)
        apply = mock.Mock(side_effect=ValueError("invalid draft"))
        with mock.patch.object(
            scope.assignment, "current", return_value={"identity": workspace_scope.identity(self.state)}
        ):
            with self.assertRaisesRegex(ValueError, "invalid draft"):
                planning_scope.resume(self.state, apply=apply)
        self.assertEqual(before, self.state)

    def test_saved_clarification_resolution_is_not_reapplied(self):
        self.report["machine_resolutions"] = [{"question_id": "Q1", "resolution": "resolved"}]
        self.output.write_text(json.dumps(self.report))
        with mock.patch.object(planning_scope.clarification, "clarify_discoverable", return_value=(self.report, False)):
            self.prepare(self.report)
        # Use a real artifact-backed handoff, after clarification already consumed its episode.
        with mock.patch.object(planning_scope.clarification, "clarify_discoverable", return_value=(self.report, False)):
            planning_scope.prepare_handoff(
                self.state,
                "astra_discovery",
                self.report,
                self.record,
                investigation_stages=planner.INVESTIGATION_STAGES,
                check_code_refs=lambda *args: None,
            )
        with mock.patch.object(
            planning_scope.clarification, "clarify_discoverable", side_effect=AssertionError("repeated")
        ):

            def accept(state, stage, value, record):
                result, deferred = planning_scope.prepare_handoff(
                    state,
                    stage,
                    value,
                    record,
                    investigation_stages=planner.INVESTIGATION_STAGES,
                    check_code_refs=lambda *args: None,
                )
                self.assertEqual(self.report, result)
                self.assertFalse(deferred)

            with mock.patch.object(
                scope.assignment, "current", return_value={"identity": workspace_scope.identity(self.state)}
            ):
                self.assertTrue(planning_scope.resume(self.state, apply=accept))


class PlanningScopeProviderTests(unittest.TestCase):
    def test_scripted_planners_follow_new_and_saved_report_schemas(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            schema, output = root / "schema.json", root / "output.json"
            packet = {
                "stage": "glm_revise",
                "goal_contract": {"revision": 1, "hash": "saved", "body": body()},
                "joint_planning": True,
            }
            for provider in ("fake_codex.py", "live_fixture_provider.py"):
                for offered in (False, True):
                    with self.subTest(provider=provider, offered=offered):
                        schema.write_text(
                            json.dumps({"properties": {"workspace_scope": workspace_scope.SCHEMA} if offered else {}})
                        )
                        result = subprocess.run(
                            [
                                sys.executable,
                                str(ROOT / "tools" / provider),
                                "--output-schema",
                                str(schema),
                                "-o",
                                str(output),
                            ],
                            input="CURRENT HANDOFF DATA\n" + json.dumps(packet),
                            capture_output=True,
                            text=True,
                            cwd=root,
                            timeout=15,
                        )
                        self.assertEqual(0, result.returncode, result.stderr)
                        report = json.loads(output.read_text())
                        if offered:
                            self.assertEqual(
                                {"spans_repositories": False, "external_parts": []}, report.get("workspace_scope")
                            )
                        else:
                            self.assertNotIn("workspace_scope", report)
