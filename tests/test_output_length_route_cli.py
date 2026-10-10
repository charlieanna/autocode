"""Terminal length failures publish an explicit, authenticated model choice. No live provider."""

import json
import shutil
import unittest
from pathlib import Path

from autocode_taskrun import TaskRun, TaskRunError

from . import test_subprocess
from .opencode_fixture_cli import entrypoint


class OutputLengthRouteCliTests(unittest.TestCase):
    new_run_engine_args = ("--engine", "opencode")
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved

    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        source = Path(__file__).resolve().parents[1] / "tools" / "fake_opencode.py"
        provider = self.root / "fixture-bin" / "opencode"
        shutil.copy2(source, provider)
        provider.chmod(0o755)
        self.entry = entrypoint(self.entry)
        self.env.update(AUTOCODE_FIXTURE_MODE="no-human")

    def stopped(self, stage):
        self.env["AUTOCODE_FIXTURE_TRUNCATE_STAGE"] = stage
        self.launch(["Build a greeting tool", "--chat"], 2, answers="CLI\nyes\n")
        directory, state = self.saved()
        run = TaskRun(self.project, directory, command=tuple(self.entry), env=self.env, timeout=60)
        return run, state

    def test_builder_length_stop_accepts_only_an_explicit_valid_model_then_resumes(self):
        run, state = self.stopped("terra")
        view = run.status()
        self.assertFalse(view["done"])
        route = view["needs"]["route"]
        self.assertEqual(("terra", "Builder", "output_limit"), tuple(route[k] for k in ("role", "job", "cause")))
        self.assertIn("output", view["stop_reason"])
        record = state["active_stage"]
        event_path = Path(record["events"])
        original_events = event_path.read_bytes()
        original_goal = view["approved_contract"]
        original_usage = view["usage"]
        original_state = (run.run_dir / "state.json").read_bytes()
        for model, message in (
            (route["stopped_model"], "already uses"),
            ("gpt-6-luna", "provider/model"),
            ("openai/gpt-6-sol", "Cross-model"),
        ):
            with self.subTest(model=model):
                with self.assertRaisesRegex(TaskRunError, message):
                    run.assign_model("terra", model)
                self.assertEqual(original_state, (run.run_dir / "state.json").read_bytes())
        self.env.pop("AUTOCODE_FIXTURE_TRUNCATE_STAGE")
        answered = run.assign_model("terra", "openai/gpt-6-luna")
        self.assertFalse(answered["done"])
        self.assertEqual("output_limit", route["cause"])
        [assignment] = answered["route_assignments"]
        self.assertEqual(
            (route["stopped_model"], "openai/gpt-6-luna", "PAUSED_OUTPUT_CAP"),
            tuple(assignment[k] for k in ("from", "to", "pause_status")),
        )
        event_path = Path(assignment["events"])
        self.assertEqual(original_events, event_path.read_bytes())
        original = original_usage["accounting"]
        retained = answered["usage"]["accounting"]
        self.assertEqual(original["attempt_count"], retained["attempt_count"], "answering launches no new attempt")
        for key, quantity in original["tokens"].items():
            self.assertEqual(quantity["known"], retained["tokens"][key]["known"], key)
        for key in ("reported_usd", "historical_estimated_usd"):
            self.assertEqual(original["cost"][key]["known"], retained["cost"][key]["known"], key)
        self.assertEqual(original_goal, answered["approved_contract"])
        completed = run.resume_paused()
        self.assertTrue(completed["done"], completed["needs"])
        self.assertEqual(original_events, event_path.read_bytes())

    def test_requirements_length_stop_offers_a_route_without_replaying_on_plain_resume(self):
        run, state = self.stopped("astra_discovery")
        view = run.status()
        route = view["needs"]["route"]
        self.assertEqual("output_limit", route["cause"])
        self.assertEqual("Requirements", route["job"])
        before = (run.run_dir / "state.json").read_bytes()
        original_events = Path(state["active_stage"]["events"]).read_bytes()
        original_usage = view["usage"]
        result = self.launch(["--run-dir", str(run.run_dir), "--resume-paused", "--no-chat"], 2)
        self.assertIn("route-", result.stdout + result.stderr)
        self.assertEqual([], run.status()["route_assignments"])
        after = json.loads((run.run_dir / "state.json").read_bytes())
        self.assertEqual(json.loads(before)["active_stage"], after["active_stage"])
        self.assertEqual(json.loads(before)["stages"], after["stages"])
        self.assertEqual(original_events, Path(after["active_stage"]["events"]).read_bytes())
        self.assertEqual(original_usage, run.status()["usage"])
        self.env.pop("AUTOCODE_FIXTURE_TRUNCATE_STAGE")
        answered = run.assign_model(route["role"], "mimo-token-plan/mimo-v2.6-pro")
        self.assertEqual("PAUSED_STAGE_ABANDONED", answered["status"])
        self.assertEqual("mimo-token-plan/mimo-v2.6-pro", answered["routes"][route["role"]]["model"])


# Reuse only fixture services; this base contains no tests and dispatches no real provider.
from . import test_job_route as job_fixtures
from .test_job_failure_recovery import JobHarness


class OutputLengthJobCliTests(JobHarness):
    models = job_fixtures.JobModelRouteTests.models
    saved = job_fixtures.JobModelRouteTests.saved
    cli = job_fixtures.JobModelRouteTests.cli
    rejected = job_fixtures.JobModelRouteTests.rejected
    stopped = job_fixtures.JobModelRouteTests.stopped

    def setUp(self):
        super().setUp()
        provider = job_fixtures.REFUSING.replace(
            "print(json.dumps({'type':'error','error':error}),flush=True)",
            "print(json.dumps({'type':'turn.failed','error':{'code':'output_token_limit','message':'response output exhausted'},'usage':{'input_tokens':429,'output_tokens':32000}}),flush=True)",
        )
        (self.workspace / ".autocode" / "bin" / "codex").write_text(provider)
        self.env.update(REFUSE_MODEL="gpt-6-sol")

    def test_length_stopped_job_requires_fresh_answer_token_and_never_routes_back(self):
        run = self.start("exit42", "review")
        need = self.stopped(run)
        self.assertEqual("output_limit", need["route"]["cause"])
        self.assertEqual("--answer route-sol=MODEL --job-retry-token TOKEN", need["action"])
        self.assertEqual(["inspect", "feedback"], [action["kind"] for action in run.status()["recovery"]["actions"]])
        self.assertIn("output limit", run.status()["recovery"]["what_happened"])
        old_token = need["job_retry_token"]
        before_usage = run.status()["usage"]["accounting"]
        run.assign_model("sol", "gpt-6-luna")
        need = self.stopped(run)
        self.assertNotEqual(old_token, need["job_retry_token"])
        self.assertEqual(["gpt-6-sol"], self.models(), "answering never launches another provider")
        after_usage = run.status()["usage"]["accounting"]
        for key in ("attempt_count", "tokens", "provider_requests", "cost"):
            self.assertEqual(before_usage[key], after_usage[key], key)
        self.rejected(
            run, "--answer", "route-sol=gpt-6-sol", "--job-retry-token", need["job_retry_token"], message="output limit"
        )
        with self.assertRaisesRegex(TaskRunError, "token"):
            run.retry_job(old_token)
        self.assertEqual(["gpt-6-sol"], self.models())
        self.assertTrue(run.retry_job(need["job_retry_token"])["done"])
        self.assertEqual(["gpt-6-sol", "gpt-6-luna"], self.models())
