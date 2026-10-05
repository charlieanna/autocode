"""Parallel quota routing through the real CLI and offline concurrent Builders."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

from . import test_subprocess
import autocode as runner
import autocode_dispatch as dispatch
import autocode_stuck_job as stuck
import autocode_support as support
from goal_fixtures import assert_operational_wait


class ParallelQuotaTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ()

    def fixture(self, marker="AUTOCODE_BUILDER_QUOTA_FAIL_ONCE"):
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
        self.env[marker] = "M1"

    def paused(self, marker="AUTOCODE_BUILDER_QUOTA_FAIL_ONCE"):
        self.fixture(marker)
        result = self.launch(["Produce two outputs and combine", "--max-parallel-builders", "2", "--chat"],
                             2, answers="yes\n")
        self.assertTrue(list((self.project / ".autocode/runs").glob("*/state.json")), result.stdout + result.stderr)
        run, state = self.saved()
        self.assertNotEqual("PAUSED_INVALID_OUTPUT", state["status"], state.get("stop_reason"))
        return run, state

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
