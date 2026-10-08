"""Parallel quota and content-filter routing through the real CLI and offline concurrent Builders."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from . import test_subprocess
import autocode as runner
import autocode_dispatch as dispatch
import autocode_run_view as run_view
import autocode_stuck_job as stuck
import autocode_support as support
import autocode_util as util
from autocode_role_schema import role_schema
import fake_parallel_builder as offline_builder
from goal_fixtures import assert_operational_wait

QUOTA = {"type": "error", "error": {"message": "subscription usage limit reached"}}
# OpenCode's ContentFilterError, as tools/fixtures/opencode-content-filter-run.jsonl captured it (#441, #465).
REFUSAL = {"type": "error", "error": {"name": "ContentFilterError", "data": {
    "message": "The response was blocked by the provider's content filter"}}}
GLM, MIMO = "zai-coding-plan/glm-5.3", "xiaomi-token-plan-sgp/mimo-v2"


class ParallelFixtureReportRepairTests(unittest.TestCase):
    """Exercise the real offline repair entry point without replaying any work."""

    def common(self):
        return {"contract_revision": 1, "contract_hash": "a" * 64, "task_id": "T1",
                "deferred_backlog": [], "user_request": {
                    "kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                    "options": [], "proposed_delta": ""}}

    def schema(self, filename, role):
        path = Path(__file__).resolve().parents[1] / "tools/autocode-schemas/v2" / filename
        return role_schema(json.loads(path.read_text()), role)

    def repaired(self, stage, report, root_stage=None):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.json"
            output = Path(directory) / "repaired.json"
            original.write_text(json.dumps(report))
            original_bytes = original.read_bytes()
            data = {"report_repair": True, "original": {"stage": stage, "output": str(original)}}
            if root_stage is not None:
                data["stage"] = root_stage
            events = io.StringIO()
            with patch.object(sys, "argv", ["codex", "exec", "-o", str(output)]), \
                    patch.object(sys, "stdin", io.StringIO("CURRENT HANDOFF DATA\n" + json.dumps(data))), \
                    redirect_stdout(events), \
                    patch.object(offline_builder, "report", side_effect=AssertionError("Report repair replayed a stage")), \
                    patch.object(offline_builder.subprocess, "run", side_effect=AssertionError("Report repair executed a command")):
                offline_builder.main()
            self.assertEqual(original_bytes, original.read_bytes())
            self.assertEqual(["thread.started", "turn.completed"],
                             [json.loads(line)["type"] for line in events.getvalue().splitlines()])
            return json.loads(output.read_text())

    def test_tester_repair_preserves_the_original_report_and_schema(self):
        report = {**self.common(), "verdict": "BLOCKED", "checks_run": [], "findings": [],
                  "unverified_criteria": ["C1"], "checks": [],
                  "criterion_results": [{"id": "C1", "status": "NOT_VERIFIED", "evidence_refs": []}],
                  "end_to_end_result": {"status": "NOT_VERIFIED", "summary": "Evidence is missing",
                                        "evidence_refs": [], "technical_result": None,
                                        "pending_human_criteria": []}, "finding_dispositions": []}
        schema = self.schema("sol-report.schema.json", "sol")
        util.validate_schema(report, schema)
        # The old producer's Builder-only field is invalid on this valid Tester report.
        with self.assertRaisesRegex(ValueError, r"unexpected fields: summary"):
            util.validate_schema({**report, "summary": "Repaired report without replaying implementation"}, schema)
        for root_stage in (None, "sol_report_repair", "terra_report_repair"):
            with self.subTest(root_stage=root_stage):
                result = self.repaired("sol", report, root_stage)
                self.assertEqual(report, result)
                util.validate_schema(result, schema)

    def test_completion_repair_preserves_blocked_and_unverified_results(self):
        report = {**self.common(), "status": "BLOCKED",
                  "acceptance_criteria": [{"id": "C1", "criterion": "Output works",
                                           "status": "unverified", "evidence": ""}],
                  "evidence": [], "next_objective": "Collect missing evidence",
                  "blocker": "Checks are not verified", "plan": ["Run protected checks"],
                  "affected_paths": [], "next_task": {"kind": "none", "milestone_id": "",
                      "requirements": [], "acceptance_criteria": [], "validation_plan": [], "findings": []},
                  "findings": [], "agreed_limitations": [], "finding_dispositions": []}
        schema = self.schema("astra-decision.schema.json", "astra")
        util.validate_schema(report, schema)
        result = self.repaired("astra_review", report)
        self.assertEqual(report, result)
        util.validate_schema(result, schema)

    def test_builder_repair_restores_only_the_missing_required_summary(self):
        report = {**self.common(), "changed_files": ["a.txt"], "commands_run": ["python -m unittest"],
                  "results": ["A check failed"], "remaining_risks": ["Validation is incomplete"],
                  "evidence_refs": ["event:check"], "addressed_requirements": ["Produce output"],
                  "untested_behavior": ["Integration"], "recommended_checks": ["Run integration"]}
        schema = self.schema("terra-report.schema.json", "terra")
        with self.assertRaisesRegex(ValueError, r"missing summary"):
            util.validate_schema(report, schema)
        result = self.repaired("terra", report, "sol_report_repair")
        self.assertEqual({**report, "summary": "Repaired report without replaying implementation"}, result)
        util.validate_schema(result, schema)

    def test_existing_builder_summary_and_other_stage_fields_are_unchanged(self):
        for stage in ("terra", "astra_checkpoint", "requirements_gather", "investigate_stuck"):
            report = {"summary": "Original uncertainty", "status": "BLOCKED",
                      "evidence": ["event:failed-check"], "details": {"exit_code": 1, "verified": False}}
            with self.subTest(stage=stage):
                self.assertEqual(report, self.repaired(stage, report))


class ParallelQuotaTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ()

    def fixture(self, marker="AUTOCODE_BUILDER_QUOTA_FAIL_ONCE", error=QUOTA, members="M1"):
        source = Path(__file__).resolve().parents[1] / "tools"
        target = self.root / "fixture-bin"
        shutil.copy2(source / "fake_parallel_builder.py", target / "codex")
        code = (target / "codex").read_text()
        # The parallel Codex fixture predates joint planning. OpenCode always
        # enters that flow; supply its planning reports in the copied fixture
        # on both revisions so the test reaches the same parallel build batch.
        planning = ("    if data[\"stage\"] == \"requirements_gather\":\n"
                    "        draft = plan()\n"
                    "        return {\"summary\": \"Two outputs and their combination\", \"intended_outcome\": draft[\"intended_outcome\"],\n"
                    "                \"required_behaviors\": draft[\"required_behaviors\"], \"constraints\": draft[\"constraints\"],\n"
                    "                \"acceptance_tests\": [\"Read a.txt, b.txt and combined.txt\"], \"source_refs\": [],\n"
                    "                \"proposed_assumptions\": [], \"open_questions\": [], \"requirements\": [],\n"
                    "                \"ignored_statements\": [], \"conflicts\": []}\n"
                    "    if data[\"stage\"] == \"astra_challenge\":\n"
                    "        return {\"summary\": \"The three milestones cover the outputs\", \"concerns\": []}\n"
                    "    if data[\"stage\"] in (\"astra_discovery\", \"glm_revise\", \"astra_finalize\"):\n"
                    "        draft = plan()\n"
                    "        draft[\"initial_task\"] = {\"kind\": \"implement\", \"milestone_id\": \"M1\",\n"
                    "            \"objective\": \"Build first output\", \"affected_paths\": [\"a.txt\"],\n"
                    "            \"requirements\": [\"Produce output\"], \"acceptance_criteria\": [\"C1\"],\n"
                    "            \"validation_plan\": [\"Read all outputs\"]}\n"
                    "        result = {\"contract\": draft, \"summary\": \"Build independent outputs then combine\",\n"
                    "                  \"contract_changes\": [], \"conflict_resolutions\": [], \"requirement_trace\": []}\n"
                    "        if data[\"stage\"] in (\"astra_discovery\", \"glm_revise\"):\n"
                    "            result[\"code_refs\"] = [\"goal_contract.body\"]\n"
                    "        if data[\"stage\"] == \"astra_discovery\":\n"
                    "            result.update(alternatives=[], uncertainties=[])\n"
                    "        if data[\"stage\"] == \"glm_revise\":\n"
                    "            result[\"responses\"] = []\n"
                    "        if data[\"stage\"] == \"astra_finalize\":\n"
                    "            result[\"decisions\"] = []\n"
                    "        return result\n")
        stage = '    if data["stage"] == "astra_discovery":'
        self.assertIn(stage, code)
        code = code.replace(stage, planning + stage, 1)
        # The v3 recognize schema requires clarity; the stock fixture's report
        # predates it, so the copied sibling carries it from the start.
        code = code.replace(
            '"workflow": "build", "reason": "Fixture: every request is a build", "signals": [], "design_document": ""',
            '"workflow": "build", "reason": "Fixture: every request is a build", "signals": [], '
            '"design_document": "", "clarity": "clear"')
        # Inject the fake provider behavior into the copied fixture on the unfixed revision too.
        # This exercises the same pre-existing provider failure path without a fix-added API.
        if "AUTOCODE_BUILDER_QUOTA_FAIL_ONCE" not in code:
            injection = ("    if os.environ.get(\"AUTOCODE_BUILDER_QUOTA_FAIL_ONCE\") == task[\"milestone_id\"]:\n"
                         "        once = Path(data[\"state_file\"]).parent / \"quota-failed-once\"\n"
                         "        if not once.exists():\n            once.write_text(task[\"milestone_id\"])\n"
                         "            print(json.dumps({\"type\": \"error\", \"error\": {\"message\": \"subscription usage limit reached\"}}), flush=True)\n"
                         "            raise SystemExit(2)\n"
                         "    if os.environ.get(\"AUTOCODE_BUILDER_RESULT_STATUS\") == task[\"milestone_id\"]:\n"
                         "        print(json.dumps({\"type\": \"error\", \"error\": {\"message\": \"Selected model is at capacity\"}}), flush=True)\n"
                         "        raise SystemExit(2)\n")
            needle = "    if os.environ.get(\"AUTOCODE_BUILDER_FAIL\") == task[\"milestone_id\"]:"
            self.assertIn(needle, code)
            code = code.replace(needle, injection + needle, 1)
        # Each milestone in a comma-separated list stops once, on the quota error row or ``error`` in its place.
        once = 'os.environ.get("AUTOCODE_BUILDER_QUOTA_FAIL_ONCE") == task["milestone_id"]'
        quota = f"print(json.dumps({json.dumps(QUOTA)})"
        self.assertIn(once, code)
        self.assertIn(quota, code)
        code = code.replace(once, 'task["milestone_id"] in os.environ.get("AUTOCODE_BUILDER_QUOTA_FAIL_ONCE", "").split(",")', 1)
        code = code.replace(quota, f"print(json.dumps({json.dumps(error)})", 1)
        (target / "codex").write_text(code)
        (target / "codex").chmod(0o755)
        shutil.copy2(source / "fake_opencode.py", target / "opencode")
        opencode = (target / "opencode")
        opencode_text = opencode.read_text().replace("xiaomi-token-plan-sgp/mimo-v2.6-pro",
                                                     "xiaomi-token-plan-sgp/mimo-v2\\nxiaomi-token-plan-sgp/mimo-v2.6-pro")
        # Older fixtures compare an unset env against a repair's missing stage
        # (None == None). Correct that copy when needed; an already fixed stock
        # guard leaves this replacement as a no-op.
        guard = 'if os.environ.get("AUTOCODE_FIXTURE_TRUNCATE_STAGE") == data.get("stage"):'
        opencode.write_text(opencode_text.replace(
            guard, 'if os.environ.get("AUTOCODE_FIXTURE_TRUNCATE_STAGE") and '
                   'os.environ.get("AUTOCODE_FIXTURE_TRUNCATE_STAGE") == data.get("stage"):'))
        opencode.chmod(0o755)
        # OpenCode needs the explicit offline transport bootstrap, just as
        # OpenCodeFlow does. The copied Builder and model catalogue are
        # deliberately different from that bootstrap's standard fixture, so
        # authenticate these exact test-owned copies at each process launch.
        self.env["AUTOCODE_QUOTA_FIXTURE_HASHES"] = json.dumps({
            name: hashlib.sha256((target / name).read_bytes()).hexdigest()
            for name in ("codex", "opencode", "goal_fixtures.py")})
        self.env["AUTOCODE_QUOTA_SOURCE_ROOT"] = str(source.parent)
        bootstrap = target / "bootstrap.py"
        bootstrap.write_text(
            "import hashlib, json, os, shutil, subprocess, sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, os.environ[\"AUTOCODE_QUOTA_SOURCE_ROOT\"])\n"
            "from tests import opencode_fixture_cli as fixture\n"
            "expected = json.loads(os.environ[\"AUTOCODE_QUOTA_FIXTURE_HASHES\"])\n"
            "def checked_fixture(executable=\"opencode\", *, env=None):\n"
            "    selected = shutil.which(str(executable), path=(env or os.environ).get(\"PATH\", \"\"))\n"
            "    if not selected or Path(selected).name != \"opencode\":\n"
            "        raise RuntimeError(\"Expected the offline OpenCode fixture\")\n"
            "    selected = Path(selected).resolve()\n"
            "    for name, digest in expected.items():\n"
            "        path = selected.with_name(name)\n"
            "        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:\n"
            "            raise RuntimeError(\"Offline fixture changed: \" + name)\n"
            "    return selected\n"
            "fixture.checked_fixture = checked_fixture\n"
            "popen = subprocess.Popen\n"
            "def worker_popen(args, *positional, **kwargs):\n"
            "    if isinstance(args, (list, tuple)) and len(args) > 1 and Path(str(args[1])).name == \"autocode_builder_worker.py\":\n"
            "        args = [sys.executable, str(Path(__file__).resolve()), *args[1:]]\n"
            "    return popen(args, *positional, **kwargs)\n"
            "subprocess.Popen = worker_popen\n"
            "fixture.main()\n")
        self.entry = [sys.executable, str(bootstrap), str(source / "autocode.py")]
        self.env["AUTOCODE_BUILDER_BARRIER"] = str(self.root / "barrier")
        self.env[marker] = members

    def paused(self, marker="AUTOCODE_BUILDER_QUOTA_FAIL_ONCE", error=QUOTA, members="M1"):
        self.fixture(marker, error, members)
        result = self.launch(["Produce two outputs and combine", "--max-parallel-builders", "2", "--chat"],
                             2, answers="yes\n")
        self.assertTrue(list((self.project / ".autocode/runs").glob("*/state.json")), result.stdout + result.stderr)
        run, state = self.saved()
        self.assertNotEqual("PAUSED_INVALID_OUTPUT", state["status"], state.get("stop_reason"))
        return run, state

    def retry_actions(self, state):
        return [action["milestone_ids"] for action in run_view.view(state)["recovery"]["actions"]
                if action["kind"] == "retry_builder"]

    def answer(self, run, model, expected=0):
        return self.launch(["--run-dir", str(run), "--answer", "route-terra=" + model, "--no-chat"], expected)

    def resume(self, run):
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 0)
        return self.saved()[1]

    def workers(self, state):
        batch = state.get("orchestration_batch") or state["orchestration_history"][0]
        return {row["milestone_id"]: row for row in batch["workers"]}

    def attempts(self, row):
        child = json.loads((Path(row["run_dir"]) / "state.json").read_text())
        return [(r.get("launch_route") or {}).get("model") for r in child.get("stages", [])
                if r.get("stage") == "terra"] + [
                    (child["active_stage"].get("launch_route") or {}).get("model")] if child.get("active_stage") else [
                    (r.get("launch_route") or {}).get("model") for r in child.get("stages", []) if r.get("stage") == "terra"]

    def test_ac1_quota_stop_asks_the_milestone_named_route_question(self):
        _, state = self.paused()
        request = assert_operational_wait(self, state, "PAUSED_BUDGET")
        self.assertIn("--answer route-terra=MODEL --resolver-token TOKEN", state["stop_reason"])
        self.assertIn("M1", next(q for q in request["questions"] if q["id"] == "route-terra")["question"])

    def test_ac2_answered_question_retries_exactly_that_worker_on_the_named_model(self):
        run, _ = self.paused()
        self.answer(run, "xiaomi-token-plan-sgp/mimo-v2")
        rows = self.workers(self.resume(run))
        self.assertEqual(["zai-coding-plan/glm-5.3", "xiaomi-token-plan-sgp/mimo-v2"], self.attempts(rows["M1"]))
        self.assertEqual("M1", (self.project / "a.txt").read_text().strip())

    def test_ac3_cross_model_answer_is_refused_and_names_the_producing_model(self):
        run, _ = self.paused()
        result = self.answer(run, "zai-coding-plan/glm-5.3-flash", 2)
        self.assertIn("zai-coding-plan/glm-5.3", result.stderr)
        assert_operational_wait(self, self.saved()[1], "PAUSED_BUDGET")

    def test_ac4_sibling_worker_is_not_retried_and_keeps_its_result(self):
        run, _ = self.paused()
        self.answer(run, "xiaomi-token-plan-sgp/mimo-v2")
        rows = self.workers(self.resume(run))
        self.assertEqual("BUILT", rows["M2"]["status"])
        self.assertEqual("M2", (self.project / "b.txt").read_text().strip())
        self.assertEqual(["zai-coding-plan/glm-5.3"], self.attempts(rows["M2"]))

    def test_ac5_answered_retry_completes_the_run_to_task_complete(self):
        run, _ = self.paused()
        self.answer(run, "xiaomi-token-plan-sgp/mimo-v2")
        state = self.resume(run)
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual(["M1", "M2", "M3"], [
            (self.project / name).read_text().strip() for name in ("a.txt", "b.txt", "combined.txt")])

    def test_ac6_investigation_reraise_preserves_the_worker_quota_payload(self):
        state = {"status": "RUNNING", "next_stage": "orchestrator", "stages": []}
        error = support.Paused("PAUSED_BUDGET", "quota")
        error.quota_worker = {"role": "terra", "milestone_id": "M1"}
        def dispatch_stage(state, stage):
            state["stages"].append({"stage": stage})
            raise error
        with self.assertRaises(support.Paused) as caught:
            stuck.drive(state, dispatch_stage, active=lambda state: True, skip=object(),
                        paused=support.Paused, investigate=True)
        self.assertEqual("M1", caught.exception.quota_worker["milestone_id"])

    def test_ac7_worker_retry_guard_blocks_only_a_parent_stage_without_finished_at(self):
        # Exercise the public worker entry point with a retained stage in the parent checkpoint.
        run, _ = self.paused()
        self.answer(run, "xiaomi-token-plan-sgp/mimo-v2")
        row = self.workers(self.saved()[1])["M1"]
        parent_path = run / "state.json"
        parent = json.loads(parent_path.read_text())
        parent["stages"].append({"stage": "orchestrator"})
        parent_path.write_text(json.dumps(parent))
        from autocode_builder_worker import main
        import autocode_provider_launch as provider_launch
        original_prepare = provider_launch.prepare
        def simulated_prepare(**kwargs):
            # The offline bootstrap disables the native tool boundary for the
            # builtin engine; calling the worker main() directly needs the same.
            if kwargs.get("engine") == "opencode":
                kwargs["enforce_tool_boundary"] = False
            return original_prepare(**kwargs)
        with patch.dict(os.environ, self.env), patch.object(provider_launch, "prepare", simulated_prepare):
            self.assertEqual(2, main(row["run_dir"], "retry"))
        self.assertEqual("PAUSED_ORCHESTRATOR_WORKER", json.loads((Path(row["run_dir"]) / "result.json").read_text())["status"])
        parent["stages"][-1]["finished_at"] = "completed"
        parent_path.write_text(json.dumps(parent))
        with patch.dict(os.environ, self.env), patch.object(provider_launch, "prepare", simulated_prepare):
            self.assertEqual(0, main(row["run_dir"], "retry"))
        self.assertEqual("BUILT", json.loads((Path(row["run_dir"]) / "result.json").read_text())["status"])

    def test_ac8_non_quota_worker_failure_keeps_the_generic_inspection_pause(self):
        _, state = self.paused("AUTOCODE_BUILDER_FAIL")
        request = assert_operational_wait(self, state, "PAUSED_ORCHESTRATOR_WORKER")
        self.assertFalse(any(q.get("id") == "route-terra" for q in request.get("questions", [])))

    def test_ac9_invalid_answers_are_refused_and_launch_nothing(self):
        run, _ = self.paused()
        for model, message in (("gpt 6", "provider/model identifier"),
                               ("zai-coding-plan/glm-5.3", "already uses")):
            result = self.answer(run, model, 2)
            self.assertIn(message, result.stderr)
            state = self.saved()[1]
            assert_operational_wait(self, state, "PAUSED_BUDGET")
            self.assertFalse(any(r.get("retry_requested") for r in self.workers(state).values()))

    def test_ac10_live_builder_blocks_the_retry_with_the_existing_workers_pause(self):
        row = {"milestone_id": "M1", "run_dir": str(self.root), "workspace": str(self.project),
               "status": "RUNNING", "retry_requested": True, "processes": [{"pid": os.getpid()}]}
        state = {"orchestration_batch": {"workers": [row]}}
        with patch.object(dispatch.processes, "process_table", return_value={}), \
             patch.object(dispatch.processes, "live_processes", return_value=True), \
             patch.object(dispatch.subprocess, "Popen", side_effect=AssertionError("duplicate")):
            with self.assertRaises(support.Paused) as caught:
                dispatch.run_workers(state, self.root, state["orchestration_batch"])
        self.assertEqual("PAUSED_ORCHESTRATOR_WORKERS", caught.exception.status)

    def test_ac11_answered_retry_intent_survives_a_process_restart(self):
        run, _ = self.paused()
        self.answer(run, "xiaomi-token-plan-sgp/mimo-v2")
        state = json.loads((run / "state.json").read_text())
        rows = self.workers(state)
        self.assertTrue(rows["M1"]["retry_requested"])
        self.assertEqual("BUILT", rows["M2"]["status"])
        assignment = [e for e in state["user_events"] if e["kind"] == "route_assignment"][-1]
        self.assertEqual(("terra", "zai-coding-plan/glm-5.3", "xiaomi-token-plan-sgp/mimo-v2"),
                         (assignment["role"], assignment["from"], assignment["to"]))
        self.assertEqual("TASK_COMPLETE", self.resume(run)["status"])
        self.assertEqual(["zai-coding-plan/glm-5.3", "xiaomi-token-plan-sgp/mimo-v2"], self.attempts(rows["M1"]))
        self.assertEqual(["zai-coding-plan/glm-5.3"], self.attempts(rows["M2"]))

    def test_ac12_present_non_quota_paused_result_keeps_the_generic_inspection_pause(self):
        _, state = self.paused("AUTOCODE_BUILDER_RESULT_STATUS")
        request = assert_operational_wait(self, state, "PAUSED_ORCHESTRATOR_WORKER")
        self.assertFalse(any(q.get("id") == "route-terra" for q in request.get("questions", [])))

    def test_ac13_a_quota_stopped_member_is_asked_for_a_model_and_retried_only_when_named(self):
        # #465: the view offers no same-model retry; --retry-builder still runs it, the quota having reset.
        run, state = self.paused()
        self.assertEqual([], self.retry_actions(state))
        self.launch(["--run-dir", str(run), "--resume-paused", "--retry-builder", "M1", "--no-chat"], 0)
        state = self.saved()[1]
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual([GLM, GLM], self.attempts(self.workers(state)["M1"]))

    def refusal_asked(self, state, milestone):
        """A batch member its provider's content filter refused (#465): asked like a quota stop, never offered a retry."""
        request = assert_operational_wait(self, state, "PAUSED_CONTENT_FILTER")
        asked = next(q for q in request["questions"] if q["id"] == "route-terra")
        self.assertEqual((f"Builder (milestone {milestone})", "content_filter", GLM),
                         (asked["job"], asked["cause"], asked["stopped_model"]))
        self.assertIn(f"Builder (milestone {milestone})'s model was refused by its provider's content filter",
                      asked["question"])
        self.assertIn(f"Milestone {milestone}: Builder: the provider's content filter refused the response on {GLM} "
                      "(ContentFilterError: The response was blocked", state["stop_reason"])
        self.assertIn("--answer route-terra=MODEL --resolver-token TOKEN", state["stop_reason"])
        # Only commands the parent takes: the worker's own attempt is never named here (#288/#301).
        self.assertNotIn("--abandon-stage", state["stop_reason"])
        self.assertEqual([], self.retry_actions(state))
        return asked

    def test_a_refused_member_is_asked_for_another_model_and_only_it_reruns_there(self):
        run, state = self.paused(error=REFUSAL)
        self.assertEqual("PAUSED_CONTENT_FILTER", self.workers(state)["M1"]["status"])
        self.refusal_asked(state, "M1")
        self.answer(run, MIMO)
        state = self.resume(run)
        self.assertEqual("TASK_COMPLETE", state["status"])
        [assignment] = [e for e in state["user_events"] if e["kind"] == "route_assignment"]
        self.assertEqual((GLM, MIMO, "PAUSED_CONTENT_FILTER"),
                         (assignment["from"], assignment["to"], assignment["pause_status"]))
        rows = self.workers(state)
        self.assertEqual([GLM, MIMO], self.attempts(rows["M1"]))
        self.assertEqual([GLM], self.attempts(rows["M2"]))
        self.assertEqual(["M1", "M2", "M3"], [
            (self.project / name).read_text().strip() for name in ("a.txt", "b.txt", "combined.txt")])

    def test_the_refusing_model_is_never_rerun_by_a_resume_or_a_member_retry(self):
        run, _ = self.paused(error=REFUSAL)
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        result = self.launch(["--run-dir", str(run), "--resume-paused", "--retry-builder", "M1", "--no-chat"], 2)
        self.assertIn(f"Builder M1 was refused by its provider's content filter on {GLM}", result.stderr)
        state = self.saved()[1]
        self.refusal_asked(state, "M1")
        self.assertEqual([GLM], self.attempts(self.workers(state)["M1"]))
        self.assertFalse(any(e["kind"] == "builder_retry" for e in state.get("user_events", [])))

    def inform(self, run, response="provide_information"):
        request = self.saved()[1]["resolver_human_request"]
        return self.launch(["--run-dir", str(run), "--resolver-request", request["request_id"],
                            "--resolver-token", request["request_token"], "--resolver-response", response,
                            "--resolver-message", "The provider's policy for this repository was clarified", "--no-chat"], 0)

    def one_way_forward(self, state, milestone):
        """Once a person answered a member's model stop without a model, one command continues (#541).

        The stop reason, the status view's needs.action and the recovery card all name it, and no
        question is open."""
        command = f"--resume-paused --retry-builder {milestone}"
        self.assertEqual([], state.get("pending_questions"))
        self.assertEqual(command, run_view.view(state)["needs"]["action"])
        self.assertEqual([[milestone]], self.retry_actions(state))
        self.assertIn(command, state["stop_reason"])
        return command

    def test_after_information_a_refused_member_is_asked_again_by_its_one_named_retry(self):
        run, state = self.paused(error=REFUSAL)
        self.assertNotIn("then autocode resume", state["stop_reason"])
        self.assertIn("--resume-paused --retry-builder M1 asks this question again", state["stop_reason"])
        self.assertIn("--resume-paused --retry-builder M1", self.inform(run).stdout)
        command = self.one_way_forward(self.saved()[1], "M1")
        # A plain resume holds on AutoResolver's evaluation of the information and names the same command.
        held = self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        self.assertIn(f"Next command: autocode {command}", held.stdout)
        state = self.saved()[1]
        self.assertEqual("PAUSED_CONTENT_FILTER", state["status"])
        self.one_way_forward(state, "M1")
        # No question is open to answer; the refusal names the same command, not a relaunch that asks nothing.
        self.assertIn(command, self.answer(run, MIMO, 2).stderr)
        # That command collects M1's stop again and asks its question; nothing reruns the refused model.
        asked = self.launch(["--run-dir", str(run), *command.split(), "--no-chat"], 2)
        self.assertNotIn("Input rejected", asked.stderr)
        state = self.saved()[1]
        self.refusal_asked(state, "M1")
        self.assertEqual([GLM], self.attempts(self.workers(state)["M1"]))
        self.assertFalse(any(e["kind"] == "builder_retry" for e in state.get("user_events", [])))
        self.answer(run, MIMO)
        state = self.resume(run)
        self.assertEqual("TASK_COMPLETE", state["status"])
        rows = self.workers(state)
        self.assertEqual([[GLM, MIMO], [GLM]], [self.attempts(rows["M1"]), self.attempts(rows["M2"])])

    def test_after_information_a_quota_member_offers_only_its_unchanged_retry(self):
        run, state = self.paused()
        self.assertNotIn("then autocode resume", state["stop_reason"])
        self.assertIn("--resume-paused --retry-builder M1 retries it unchanged", state["stop_reason"])
        self.inform(run)
        command = self.one_way_forward(self.saved()[1], "M1")
        # A plain resume neither retries nor asks again: it names the retry the card offers.
        held = self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        self.assertIn(f"Next command: autocode {command}", held.stdout)
        state = self.saved()[1]
        self.assertEqual("PAUSED_BUDGET", state["status"])
        self.one_way_forward(state, "M1")
        self.launch(["--run-dir", str(run), *command.split(), "--no-chat"], 0)
        state = self.saved()[1]
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual([GLM, GLM], self.attempts(self.workers(state)["M1"]))

    def test_after_leaving_it_paused_a_refused_member_continues_only_by_its_named_retry(self):
        # leave_paused schedules no re-evaluation: a plain resume holds on the answered request, naming the command.
        run, _ = self.paused(error=REFUSAL)
        self.inform(run, "leave_paused")
        command = self.one_way_forward(self.saved()[1], "M1")
        held = self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        self.assertIn(command, held.stdout)
        self.one_way_forward(self.saved()[1], "M1")
        self.launch(["--run-dir", str(run), *command.split(), "--no-chat"], 2)
        state = self.saved()[1]
        self.refusal_asked(state, "M1")
        self.assertEqual([GLM], self.attempts(self.workers(state)["M1"]))

    def test_a_model_flag_after_information_asks_the_member_question_again(self):
        # The flag moves the run's route, not the stopped member's: AutoResolver asks M1's question again, never
        # a generic request that no answer can rerun M1 from.
        run, _ = self.paused(error=REFUSAL)
        self.inform(run)
        self.launch(["--run-dir", str(run), "--resume-paused", "--terra-model", MIMO, "--no-chat"], 2)
        state = self.saved()[1]
        self.refusal_asked(state, "M1")
        self.assertEqual([GLM], self.attempts(self.workers(state)["M1"]))

    def test_a_spent_recovery_allowance_still_advises_the_member_one_control(self):
        # With the run's automatic-recovery allowance spent the request also offers --grant-recovery N. Its advice
        # on corrective information still names the member's one control, the command needs.action names after it.
        run, _ = self.paused(error=REFUSAL)
        self.inform(run)
        path = run / "state.json"
        parent = json.loads(path.read_text())
        parent["automatic_recoveries_since_resume"] = 3
        path.write_text(json.dumps(parent))
        self.launch(["--run-dir", str(run), "--resume-paused", "--retry-builder", "M1", "--no-chat"], 2)
        state = self.saved()[1]
        self.refusal_asked(state, "M1")
        self.assertIn("--grant-recovery N", state["stop_reason"])
        self.assertIn("--resume-paused --retry-builder M1 asks this question again", state["stop_reason"])
        self.assertNotIn("then autocode resume", state["stop_reason"])
        self.inform(run)
        self.one_way_forward(self.saved()[1], "M1")

    def two_stopped_members(self, error, status):
        """M1 and M2 stop on their model; M1's answer moves the Builder route to MiMo before M2 is asked.

        M2 ran on GLM, so its question and answer are about GLM: MiMo is the natural answer and is accepted.
        A refusal's question lists it too, and M2's stop (collected again after a worker restart) names only
        commands the parent takes."""
        refused = status == "PAUSED_CONTENT_FILTER"
        run, state = self.paused(error=error, members="M1,M2")
        self.assertEqual({"M1": status, "M2": status}, {mid: row["status"] for mid, row in self.workers(state).items()})
        if refused:
            self.refusal_asked(state, "M1")
            # The open question is M1's: M2's refused retry never points at a question not asked yet (#541).
            result = self.launch(["--run-dir", str(run), "--resume-paused", "--retry-builder", "M2", "--no-chat"], 2)
            self.assertIn("Builder M2 was refused by its provider's content filter", result.stderr)
            self.assertIn("once the open request about Builder M1 is answered", result.stderr)
            self.refusal_asked(self.saved()[1], "M1")
        self.answer(run, MIMO)
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        state = self.saved()[1]
        request = assert_operational_wait(self, state, status)
        asked = next(q for q in request["questions"] if q["id"] == "route-terra")
        self.assertEqual(("Builder (milestone M2)", MIMO, GLM, GLM),
                         (asked["job"], state["settings"]["roles"]["terra"]["model"], asked["current_model"],
                          asked["stopped_model"]))
        if refused:
            self.assertIn(MIMO, self.refusal_asked(state, "M2")["candidates"])
        self.answer(run, MIMO)
        state = self.resume(run)
        self.assertEqual("TASK_COMPLETE", state["status"])
        rows = self.workers(state)
        self.assertEqual([[GLM, MIMO], [GLM, MIMO]], [self.attempts(rows["M1"]), self.attempts(rows["M2"])])
        self.assertEqual([(GLM, MIMO, status)] * 2, [
            (e["from"], e["to"], e["pause_status"]) for e in state["user_events"] if e["kind"] == "route_assignment"])

    def test_two_refused_members_are_asked_in_turn_about_the_model_each_ran_on(self):
        self.two_stopped_members(REFUSAL, "PAUSED_CONTENT_FILTER")

    def test_two_quota_stopped_members_are_asked_in_turn_about_the_model_each_ran_on(self):
        self.two_stopped_members(QUOTA, "PAUSED_BUDGET")
