"""A stopped Investigator changes model through the public answer/retry transaction."""

import argparse
import copy
import subprocess
import sys
import unittest

import autocode_job_failure as job_failure
import autocode_job_route as job_route
import autocode_quota_route as quota_route
from autocode_taskrun import TaskRun, TaskRunError

from tests import test_failure_routing_cli as fixture


class InvestigatorRouteCLI(unittest.TestCase):
    # Borrow only fixture setup and public planning helpers, never another test.
    command = fixture.FailureRoutingCLI.command
    setUp = fixture.FailureRoutingCLI.setUp
    call = fixture.FailureRoutingCLI.call
    status = fixture.FailureRoutingCLI.status
    trace = fixture.FailureRoutingCLI.trace
    call_order = fixture.FailureRoutingCLI.call_order
    seed = fixture.FailureRoutingCLI.seed
    approve = fixture.FailureRoutingCLI.approve

    def stopped(self, *, equals_pin=False):
        self.seed(planner_extra=("--investigator-model", "gpt-6-sol"))
        self.env.update(
            BUILD_AUDIT_FAULT="no_change",
            FAILURE_ROUTING_QUOTA_MODEL="gpt-6-sol",
            FAILURE_ROUTING_DIAGNOSIS="execution",
        )
        run = TaskRun(
            self.project,
            self.run,
            command=(sys.executable, str(self.source / "autocode.py")),
            options=("--investigator-model=gpt-6-sol",) if equals_pin else ("--investigator-model", "gpt-6-sol"),
            env=self.env,
            cwd=self.root,
            timeout=180,
        )
        view = run.advance_until_input()
        self.assertEqual("PAUSED_JOB_FAILURE", view["status"])
        need = view["needs"]
        self.assertEqual(
            ("retry_job", "investigate_stuck", "stuck_investigator"),
            (need["kind"], need["stage"], need["route"]["role"]),
        )
        self.assertEqual("gpt-6-sol", need["route"]["stopped_model"])
        self.assertTrue(need["job_retry_token"].startswith("jr:"))
        self.assertEqual(1, len(self.trace("terra")))
        self.assertEqual(1, len(self.trace("investigate_stuck")))
        self.assertFalse((self.project / "main.py").exists())
        return run, view

    def unchanged(self, before, after):
        for field in ("needs", "current_task", "usage", "routes"):
            self.assertEqual(before[field], after[field], field)
        self.assertEqual(self.approved_token, self.status()["contract_token"])
        self.assertFalse((self.project / "main.py").exists())

    def test_answer_saves_pin_renews_token_and_completes_on_the_named_model(self):
        run, held = self.stopped()
        calls = self.call_order()
        first = self.trace("investigate_stuck")[0]["handoff"]["builder_failure"]
        assigned = run.assign_model("stuck_investigator", "gpt-6-luna")
        self.assertEqual(calls, self.call_order(), "A model answer launches no provider")
        self.assertEqual("gpt-6-luna", assigned["needs"]["route"]["current_model"])
        token = assigned["needs"]["job_retry_token"]
        self.assertNotEqual(held["needs"]["job_retry_token"], token)
        self.assertEqual(("--investigator-model", "gpt-6-luna"), run.options)
        with self.assertRaises(TaskRunError):
            run.retry_job(held["needs"]["job_retry_token"])
        self.assertEqual(calls, self.call_order())
        self.unchanged(assigned, run.status())
        self.env.pop("BUILD_AUDIT_FAULT")
        completed = run.retry_job(token)
        self.assertEqual("TASK_COMPLETE", completed["status"])
        self.assertTrue(completed["done"])
        self.assertTrue(self.status()["completion_current"])
        investigators = self.trace("investigate_stuck")
        self.assertEqual(["gpt-6-sol", "gpt-6-luna"], [row["model"] for row in investigators])
        for row in investigators:
            self.assertEqual("read-only", row["argv"][row["argv"].index("--sandbox") + 1])
            packet = row["handoff"]["builder_failure"]
            self.assertEqual(first["failure_id"], packet["failure_id"])
            self.assertEqual(first["binding"], packet["binding"])
        self.assertEqual(2, len(self.trace("terra")))
        oracle = subprocess.run(
            [sys.executable, "main.py"],
            cwd=self.project,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual("hello\n", oracle.stdout)

    def test_equals_pin_option_is_updated_before_a_fresh_exact_retry(self):
        run, held = self.stopped(equals_pin=True)
        calls = self.call_order()
        assigned = run.assign_model("stuck_investigator", "gpt-6-luna")
        self.assertEqual(calls, self.call_order())
        self.assertNotEqual(held["needs"]["job_retry_token"], assigned["needs"]["job_retry_token"])
        self.assertEqual(("--investigator-model=gpt-6-luna",), run.options)
        self.env["FAILURE_ROUTING_QUOTA_MODEL"] = "gpt-6-luna"
        retried = run.retry_job(assigned["needs"]["job_retry_token"])
        self.assertEqual("PAUSED_JOB_FAILURE", retried["status"])
        rows = self.trace("investigate_stuck")
        self.assertEqual(["gpt-6-sol", "gpt-6-luna"], [row["model"] for row in rows])
        self.assertEqual(
            rows[0]["handoff"]["builder_failure"]["binding"],
            rows[1]["handoff"]["builder_failure"]["binding"],
        )
        self.assertEqual(self.approved_token, self.status()["contract_token"])
        self.assertEqual(1, len(self.trace("terra")))

    def test_unchanged_exact_retry_keeps_original_model_and_incident(self):
        run, held = self.stopped()
        retried = run.retry_job(held["needs"]["job_retry_token"])
        self.assertEqual("PAUSED_JOB_FAILURE", retried["status"])
        rows = self.trace("investigate_stuck")
        self.assertEqual(["gpt-6-sol", "gpt-6-sol"], [row["model"] for row in rows])
        self.assertEqual(
            rows[0]["handoff"]["builder_failure"]["binding"],
            rows[1]["handoff"]["builder_failure"]["binding"],
        )
        self.assertEqual(1, len(self.trace("terra")))
        self.assertEqual(self.approved_token, self.status()["contract_token"])

    def test_pin_and_mixed_flags_are_refused_before_saving_or_launching(self):
        run, held = self.stopped()
        calls = self.call_order()
        token = held["needs"]["job_retry_token"]
        for extra, reason in (
            (
                (
                    "--resume-paused",
                    "--retry-failed-stage",
                    "--job-retry-token",
                    token,
                    "--investigator-model",
                    "gpt-6-luna",
                ),
                "model change is not saved",
            ),
            (
                (
                    "--answer",
                    "route-stuck_investigator=gpt-6-luna",
                    "--job-retry-token",
                    token,
                    "--investigator-reasoning-effort",
                    "low",
                ),
                "preparation pin is bound to its exact retry; nothing is saved",
            ),
            (
                (
                    "--answer",
                    "route-stuck_investigator=gpt-6-luna",
                    "--job-retry-token",
                    token,
                    "--sol-model",
                    "gpt-6-astra",
                ),
                "this invocation also changes the configuration its exact retry is bound to, so nothing is saved",
            ),
            (
                (
                    "--answer",
                    "route-stuck_investigator=openai/gpt-6-luna",
                    "--job-retry-token",
                    token,
                ),
                "uses a bare Codex model name",
            ),
        ):
            with self.subTest(extra=extra):
                response = self.call("autocode", ["--run-dir", str(self.run), *extra], codes=(2,))
                self.assertIn(reason, response.stderr)
                self.unchanged(held, run.status())
                self.assertEqual(calls, self.call_order())

    def test_source_drift_refuses_the_answer_and_keeps_the_exact_token(self):
        run, held = self.stopped()
        calls = self.call_order()
        (self.project / "foreign.txt").write_text("new source after the stop\n")
        with self.assertRaises(TaskRunError) as rejected:
            run.assign_model("stuck_investigator", "gpt-6-luna")
        self.assertIn("Source changed since the failed attempt", str(rejected.exception))
        self.unchanged(held, run.status())
        self.assertEqual(calls, self.call_order())
        self.assertEqual("new source after the stop\n", (self.project / "foreign.txt").read_text())


class InvestigatorRoutePolicyTests(unittest.TestCase):
    def state(self):
        route = {
            "engine": "codex",
            "provider": None,
            "model": "gpt-6-sol",
            "reasoning_effort": "high",
        }
        state = {
            "status": "PAUSED_JOB_FAILURE",
            "settings": {
                "engine": "codex",
                "roles": {
                    "stuck_investigator": copy.deepcopy(route),
                    "sol": {"model": "gpt-6-astra"},
                },
                "stuck_investigation": {"route": copy.deepcopy(route)},
            },
            "sessions": {"stuck_investigator": "old", "sol": "keep"},
            "stages": [],
        }
        config = job_failure.configuration(state)
        state["job_failure"] = {
            "stage": "investigate_stuck",
            "attempt_id": "002/investigator-01",
            "job_retry_token": "jr:bound",
            "pause_status": "PAUSED_BUDGET",
            "source_identity": "source",
            "configuration": config,
            "archive": "/run/iterations/002/archive",
        }
        state["stages"] = [
            {
                "stage": "investigate_stuck",
                "role": "astra",
                "route_role": "stuck_investigator",
                "iteration": 2,
                "output": "/run/iterations/002/archive/investigator-01.json",
                "events": "/run/iterations/002/archive/investigator-01.jsonl",
                "rejected": True,
                "launch_route": copy.deepcopy(route),
                "job_configuration": copy.deepcopy(config),
                "job_source": {"before_identity": "source"},
            }
        ]
        return state

    def test_assignment_survives_preparation_and_preserves_packet_normalization(self):
        state = self.state()
        before = copy.deepcopy(state["settings"])
        event = quota_route.assign(
            state,
            "stuck_investigator",
            "gpt-6-luna",
            at="now",
            via="answer",
            attempt={"stage": "investigate_stuck", "attempt_id": "002/investigator-01"},
        )
        self.assertEqual("gpt-6-luna", state["settings"]["stuck_investigation"]["route"]["model"])
        self.assertEqual(
            state["settings"]["roles"]["stuck_investigator"],
            state["settings"]["stuck_investigation"]["route"],
        )
        self.assertEqual({"sol": "keep"}, state["sessions"])
        self.assertEqual(before, quota_route.unassigned(state, state["settings"]))
        self.assertEqual(("gpt-6-sol", "gpt-6-luna"), (event["from"], event["to"]))
        state["settings"]["stuck_investigation"]["route"]["reasoning_effort"] = "low"
        self.assertEqual(
            "low",
            quota_route.unassigned(state, state["settings"])["stuck_investigation"]["route"]["reasoning_effort"],
        )

    def test_unpinned_assignment_keeps_a_pin_but_normalization_restores_absence(self):
        state = self.state()
        del state["settings"]["stuck_investigation"]
        before = copy.deepcopy(state["settings"])
        quota_route.assign(state, "stuck_investigator", "gpt-6-luna", at="now", via="answer")
        self.assertEqual("gpt-6-luna", state["settings"]["stuck_investigation"]["route"]["model"])
        self.assertEqual(before, quota_route.unassigned(state, state["settings"]))

    def test_effective_pin_is_scoped_to_the_physical_failed_investigator(self):
        state = self.state()
        state["settings"]["stuck_investigation"]["route"]["model"] = "gpt-6-luna"
        self.assertEqual(
            "gpt-6-luna",
            job_failure.configuration(state)["roles"]["stuck_investigator"]["model"],
        )
        for stage in ("review_change", None):
            with self.subTest(stage=stage):
                state["job_failure"]["stage"] = stage
                self.assertEqual(
                    "gpt-6-sol",
                    job_failure.configuration(state)["roles"]["stuck_investigator"]["model"],
                )
        state["job_failure"]["stage"] = "investigate_stuck"
        state["status"] = "PAUSED_BUILDER_CLASSIFICATION"
        self.assertEqual(
            "gpt-6-sol",
            job_failure.configuration(state)["roles"]["stuck_investigator"]["model"],
        )
        state["status"] = "RUNNING"
        state["job_retry_authorization"] = {
            "stage": "investigate_stuck",
            "token": "jr:bound",
        }
        self.assertEqual(
            "gpt-6-luna",
            job_failure.configuration(state)["roles"]["stuck_investigator"]["model"],
        )
        state["job_retry_authorization"]["token"] = "jr:foreign"
        self.assertEqual(
            "gpt-6-sol",
            job_failure.configuration(state)["roles"]["stuck_investigator"]["model"],
        )
        del state["job_failure"]
        state["builder_failure_hold"] = {"classification_retry": {"job_retry_token": "bc:retained"}}
        self.assertEqual(
            "gpt-6-sol",
            job_failure.configuration(state)["roles"]["stuck_investigator"]["model"],
        )

    def test_legacy_question_requires_the_same_physical_attempt_source_and_configuration(
        self,
    ):
        state = self.state()
        before = copy.deepcopy(state)
        asked = quota_route.job_question(state)
        self.assertEqual(
            ("route-stuck_investigator", "gpt-6-sol"),
            (asked["id"], asked["stopped_model"]),
        )
        self.assertEqual(before, state, "Public projection grants no persisted authority")
        for key, value in (
            ("stage", "review_change"),
            ("output", "/run/iterations/002/archive/investigator-02.json"),
            ("events", "/other/investigator-01.jsonl"),
            ("job_source", {"before_identity": "foreign"}),
            ("job_configuration", {}),
            ("launch_route", {"model": "foreign"}),
            ("worker_attempt", "foreign"),
            ("rejected", False),
        ):
            with self.subTest(key=key):
                altered = copy.deepcopy(state)
                altered["stages"][0][key] = value
                self.assertIsNone(quota_route.job_question(altered))
        for missing in ("stages", "source_identity"):
            altered = copy.deepcopy(state)
            if missing == "stages":
                altered["stages"] = []
            else:
                altered["job_failure"].pop(missing)
            self.assertIsNone(quota_route.job_question(altered))

    def test_effort_and_pin_changes_cannot_be_saved_beside_a_model_answer(self):
        state = self.state()
        args = argparse.Namespace(
            answer=["route-stuck_investigator=gpt-6-luna"],
            delegate=[],
            investigator_model=None,
            investigator_reasoning_effort="low",
        )
        selected = copy.deepcopy(state["settings"])
        selected["stuck_investigation"]["route"]["reasoning_effort"] = "low"
        self.assertIn("nothing is saved", job_route.settings_refusal(args, state, selected))
        self.assertIsNone(job_route.settings_refusal(args, state, copy.deepcopy(state["settings"])))
        # An old version may already have saved a changed pin. Restore its exact
        # bound configuration without an answer; the original token then works.
        original = copy.deepcopy(state["settings"])
        state["settings"]["stuck_investigation"]["route"]["model"] = "gpt-6-luna"
        args.answer = []
        args.investigator_model = "gpt-6-sol"
        args.investigator_reasoning_effort = None
        self.assertIsNone(job_route.settings_refusal(args, state, original))
        args.answer = ["route-stuck_investigator=gpt-6-astra"]
        self.assertIn("nothing is saved", job_route.settings_refusal(args, state, original))
