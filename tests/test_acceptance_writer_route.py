"""Acceptance-writer recovery keeps its own route and exact retry authority (#2158)."""

import copy
import json
import subprocess
import sys
import unittest

import autocode_job_failure as job_failure
import autocode_quota_route as quota_route
import autocode_run_view as run_view
from autocode_taskrun import TaskRun, TaskRunError

from . import test_acceptance_tests as acceptance_fixture
from . import test_investigator_route as investigator_fixture


class AcceptanceWriterProjectionTests(unittest.TestCase):
    def state(self):
        state = investigator_fixture.InvestigatorRoutePolicyTests().state()
        route = state["settings"]["roles"].pop("stuck_investigator")
        state["settings"]["roles"]["acceptance_tester"] = route
        state["settings"].pop("stuck_investigation")
        config = job_failure.configuration(state)
        state["job_failure"].update(
            stage="write_acceptance_tests",
            kind="timeout",
            pause_status=quota_route.TIMEOUT_STATUS,
            configuration=config,
            reason="Stage exceeded 600 seconds",
            write_diagnosis={},
            unrestored=[],
        )
        state["stages"][0].update(
            stage="write_acceptance_tests",
            role="sol",
            route_role="acceptance_tester",
            timed_out=True,
            job_configuration=copy.deepcopy(config),
        )
        return state

    def test_saved_timeout_projects_exact_writer_question_without_mutating_evidence(self):
        state = self.state()
        before = copy.deepcopy(state)
        shown = run_view.view(state)
        self.assertEqual("acceptance_tester", shown["needs"]["route"]["role"])
        self.assertEqual("timeout", shown["needs"]["route"]["cause"])
        self.assertIn("--answer route-acceptance_tester=MODEL --job-retry-token -", shown["next_command"])
        self.assertEqual(before, state)

    def test_saved_timeout_needs_matching_physical_attempt_and_no_active_work(self):
        original = self.state()
        for key, value in (
            ("stage", "sol"),
            ("route_role", "sol"),
            ("timed_out", False),
            ("rejected", False),
            ("worker_attempt", "foreign"),
            ("job_configuration", {}),
            ("job_source", {"before_identity": "foreign"}),
            ("launch_route", {"model": "foreign"}),
            ("events", "/foreign/attempt.jsonl"),
            ("output", "/run/iterations/002/archive/other.json"),
        ):
            with self.subTest(key=key):
                state = copy.deepcopy(original)
                state["stages"][0][key] = value
                self.assertIsNone(quota_route.job_question(state))
        for key in ("active_stage", "active_runner_check"):
            state = copy.deepcopy(original)
            state[key] = {"stage": "write_acceptance_tests"}
            self.assertIsNone(quota_route.job_question(state))
        for key in ("source_identity", "configuration", "archive"):
            state = copy.deepcopy(original)
            state["job_failure"].pop(key)
            self.assertIsNone(quota_route.job_question(state))
        state = copy.deepcopy(original)
        state["job_failure"].update(kind="exit", pause_status="PAUSED_JOB_FAILURE")
        self.assertIsNone(quota_route.job_question(state), "do not invent a timeout from prose")


WRAPPER = """import os, sys
sys.path.insert(0, os.environ['WRITER_RUNTIME'] + '/tools')
import autocode
from unittest.mock import patch
original = autocode.run_role
def run_role(**kw):
 if kw['state'].get('next_stage') != 'write_acceptance_tests' or not os.environ.get('WRITER_TIMEOUT'):
  return original(**kw)
 def deadline(child, seconds, checkpoint, *, activity=None, **kwargs):
  code=child.wait(timeout=10); checkpoint([])
  activity.timeout={'kind':'stage','reason':'Stage exceeded its hard runtime limit (injected clock)'}
  return code, True
 with patch.object(autocode.processes, 'wait_for_stage', deadline):
  return original(**kw)
autocode.run_role=run_role
raise SystemExit(autocode.main())
"""


class AcceptanceWriterRouteCLI(unittest.TestCase):
    setUp = acceptance_fixture.AcceptanceTestsCliTests.setUp

    def start(self):
        from pathlib import Path

        project = acceptance_fixture.without_maintenance(
            acceptance_fixture.materialize(self.scenario.seed, self.root / "project")
        )
        flags, provider_env = acceptance_fixture.fake_setup(self.scenario, self.root, self.scenario.dir / "reference")
        provider = self.root / "bin" / "codex"
        marker = "def report_for(stage: str, data: dict) -> dict:\n"
        injected = (
            '    if stage == "write_acceptance_tests":\n'
            '        with open(os.environ["WRITER_CALLS"], "a") as log:\n'
            '            log.write(json.dumps({"model": sys.argv[sys.argv.index("--model") + 1], "effort": sys.argv}) + "\\n")\n'
            '        if os.environ.get("WRITER_TIMEOUT"): raise SystemExit(1)\n'
        )
        source = provider.read_text()
        self.assertIn(marker, source)
        provider.write_text(source.replace(marker, marker + injected))
        wrapper = self.root / "writer_cli.py"
        wrapper.write_text(WRAPPER)
        self.calls = self.root / "writer-calls.jsonl"
        self.env = {
            **provider_env,
            "AUTOCODE_HOME": str(self.root / "registry"),
            "AUTOCODE_ATTENTION_HOME": str(self.root / "private-attention"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "WRITER_RUNTIME": str(Path(__file__).resolve().parents[1]),
            "WRITER_CALLS": str(self.calls),
            "WRITER_TIMEOUT": "1",
        }
        run = TaskRun.start(
            project,
            self.scenario.brief,
            command=(sys.executable, str(wrapper)),
            options=(*flags, "--joint-planning", "--no-adaptive-planning", "--spec-first"),
            env=self.env,
            timeout=180,
            cwd=self.root,
        )
        for _ in range(12):
            shown = run.advance_until_input()
            need = shown.get("needs") or {}
            if need.get("kind") == "approve_plan":
                run.approve_plan(need["token"])
            elif need.get("kind") == "answer":
                q = need["questions"][0]
                run.answer(q["id"], q.get("proposed_default") or "plan", resolver_token=need["resolver_token"])
            else:
                return run, shown
        self.fail("fixture did not reach writer")

    def snapshot(self, run, *args):
        result = subprocess.run(
            [*run.command, "--run-dir", str(run.run_dir), *args, "--status", "--json"],
            env=run.env,
            cwd=run.cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def assert_deadline_extended_before_pause(self, run):
        """#2605: AutoCode settled the hard deadline itself, twice on the same model, before it paused."""
        rows = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(3, len(rows))
        self.assertEqual(1, len({row["model"] for row in rows}))
        return self.snapshot(run)["settings"]["limits"]["stage_timeout_seconds"]

    def test_a_writer_past_its_deadline_extensions_retries_exactly_without_a_model_question(self):
        # #2605: after AutoCode's own extensions the writer waits for its exact retry on the same model.
        run, held = self.start()
        self.assertEqual("PAUSED_JOB_FAILURE", held["status"])
        self.assert_deadline_extended_before_pause(run)
        self.assertEqual("write_acceptance_tests", held["next_stage"])
        self.assertEqual("retry_job", held["needs"]["kind"])
        self.assertNotIn("route", held["needs"])
        self.assertIn("AutoCode already retried", held["stop_reason"])
        self.assertNotIn("--", held["stop_reason"])
        original = self.calls.read_bytes()
        with self.assertRaises(TaskRunError):
            run.assign_model("acceptance_tester", "gpt-6-luna")
        self.assertEqual(original, self.calls.read_bytes(), "a refused model answer launches nothing")
        run.env.pop("WRITER_TIMEOUT")
        completed = run.retry_job(held["needs"]["job_retry_token"])
        self.assertTrue(completed["done"], {k: completed.get(k) for k in ("status", "needs")})
        rows = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(4, len(rows))
        self.assertEqual(1, len({row["model"] for row in rows}))
        for role in ("sol", "completion", "terra"):
            self.assertEqual(held["routes"][role]["model"], completed["routes"][role]["model"])
