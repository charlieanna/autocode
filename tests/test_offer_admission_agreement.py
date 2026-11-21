"""Each named recovery offer is one its executor admits, on the same saved state (#3852, #3880, #3869 survey).

The status view's recovery card names an action with the exact CLI flags a client runs
(autocode_recovery_view._args). This guard builds complete saved states, projects the card the
way a client reads it (autocode_run_view.view) and gives the CLI's own action handling
(autocode_run_actions.handle, the steps one ``autocode --run-dir RUN`` invocation applies before
it builds) exactly those flags after the card's recovery token. No provider is launched: a
launch fails the test. Each refused variant changes one field of its admitted state, so the
refusal follows from that field. docs/bugs/offer-admission-agreement.md records the survey,
including the variants where the card and the executor disagree on the unchanged code.
"""

import contextlib
import copy
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_args
import autocode_builder_policy as builder_policy
import autocode_goals as goals
import autocode_job_failure as job_failure
import autocode_job_source as job_source
import autocode_recovery_view as recovery
import autocode_run_actions as actions
import autocode_run_view as run_view
import autocode_support as support
from goal_fixtures import body

SOURCE = "unchanged source\n"
REPORT_ERROR = "Check is not supported by an exact executed Validator event"


class SavedRunFixtures:
    """Complete saved runs for each surveyed offer, in a committed Git workspace with its run directory."""

    def open_run(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / "project"
        self.root.mkdir()
        (self.root / "README.md").write_text(SOURCE)
        for argv in (
            ["init", "-q"],
            ["config", "maintenance.auto", "false"],
            ["config", "gc.auto", "0"],
            ["add", "-A"],
            ["-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed"],
        ):
            subprocess.run(["git", "-C", str(self.root), *argv], check=True, capture_output=True)
        self.run = self.root / ".autocode/runs/survey"
        self.run.mkdir(parents=True)

    def state(self, status, **fields):
        contract = {"task_id": "task-fixture", "revision": 1, "body": body()}
        contract["hash"] = support.digest(contract)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": goals.token(contract)}
        contract.update(approval_status="approved", approval_event=approval)
        saved = {
            "version": 3,
            "task_id": "task-fixture",
            "task": "Keep the saved work",
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "status": status,
            "stop_reason": "Saved pause",
            "iteration": 4,
            "next_stage": "builder",
            "phase": "EXECUTING",
            "current_task": {"id": "t", "milestone_id": "M1", "kind": "implement", "acceptance_criteria": ["C1"]},
            "settings": {
                "builder_retry": {"enabled": True},
                "roles": {"builder": {"model": "p/builder", "reasoning_effort": "high", "model_pinned": True}},
            },
            "goal_contract": contract,
            "user_events": [approval],
            "sessions": {},
            "stages": [],
            "history": [],
            "answers": {},
            "active_seconds": 17,
            "recovery_context": {},
        }
        saved.update(fields)
        return saved

    def interrupted(self, **fields):
        """PAUSED_PROVIDER_UNCERTAIN with the stopped Builder attempt 004/builder-02 and its files."""
        base = self.run / "iterations/004/builder-02"
        base.parent.mkdir(parents=True, exist_ok=True)
        support.atomic_json(base.with_suffix(".before.json"), support.snapshot(self.root))
        base.with_suffix(".jsonl").write_text('{"type":"thread.started","thread_id":"t-session"}\n')
        attempt = {
            "role": "builder",
            "stage": "builder",
            "iteration": 4,
            "duration_seconds": 8,
            "output": str(base.with_suffix(".json")),
            "events": str(base.with_suffix(".jsonl")),
            "before_ref": str(base.with_suffix(".before.json")),
            "exit_code": -15,
        }
        return self.state("PAUSED_PROVIDER_UNCERTAIN", **{"active_stage": attempt, **fields})

    def job(self, **fields):
        """PAUSED_JOB_FAILURE of the Investigator job with retry token job-1, bound to this source and settings."""
        attempt = "001/stuck-investigation-03"
        state = self.state(
            "PAUSED_JOB_FAILURE",
            next_stage="investigate_stuck",
            recovery_context={"kind": "job_failure", "stage": "investigate_stuck", "attempt_id": attempt},
        )
        state["job_failure"] = {
            "stage": "investigate_stuck",
            "attempt_id": attempt,
            "job_retry_token": "job-1",
            "reason": "Investigator interrupted",
            "kind": "timeout",
            "source_paths": [],
            "archive": str(self.run / "archive"),
            "write_diagnosis": {},
            "unrestored": [],
            "source_identity": support.digest(job_source.identity(self.root)),
            "configuration": job_failure.configuration(state),
        }
        state["job_failure"].update(fields)
        return state

    def report(self, **fields):
        """PAUSED_REPEATED_FAILURE after both bounded repairs of a Tester report; the last is 004/validator-01."""
        iterations = self.run / "iterations/004"
        iterations.mkdir(parents=True, exist_ok=True)
        schema = iterations / "validator.schema.json"
        schema.write_text('{"type": "object"}')
        revision = support.snapshot(self.root)["revision"]
        original = {
            "stage": "tester",
            "role": "tester",
            "iteration": 3,
            "output": str(self.run / "iterations/003/validator-01.json"),
            "schema": str(schema),
            "source_revision": revision,
        }
        repair = {
            "stage": "tester_report_repair",
            "original_stage": "tester",
            "role": "tester",
            "iteration": 4,
            "output": str(iterations / "validator-01.json"),
            "schema": str(schema),
            "source_revision": revision,
            "report_only": True,
            "rejected": True,
        }
        state = self.state("PAUSED_REPEATED_FAILURE", next_stage="tester", stages=[repair])
        state["settings"]["report_repair"] = {"max_attempts": 2}
        state["pending_report_repair"] = {
            "error": REPORT_ERROR,
            "original": original,
            "attempts": 2,
            "latest_rejected": repair,
            "contract_hash": state["goal_contract"]["hash"],
            "pins": {},
            **fields,
        }
        return state

    def builder(self, **lane):
        """PAUSED_BUILDER_RETRY_LIMIT of the serial Builder on milestone M1, its retry lane paused."""
        state = self.state("PAUSED_BUILDER_RETRY_LIMIT")
        state["builder_retries"] = {
            builder_policy.key(state): {"action": "pause", "failures": ["failure-a", "failure-b"], **lane}
        }
        return state

    def repeated(self, **fields):
        """PAUSED_REPEATED_FAILURE of the Builder, three identical failures recorded at this source."""
        state = self.state("PAUSED_REPEATED_FAILURE", failure_history={}, **fields)
        revision = support.snapshot(self.root)["revision"]
        for stage, count in (("tester", 1), ("builder", 3)):
            for index in range(count):
                row = {
                    "stage": stage,
                    "iteration": 1,
                    "role": stage,
                    "output": str(self.run / f"{stage}-{index}.json"),
                    "source_revision": revision,
                    "rejected": True,
                }
                runner.failures.record(state, row, ValueError("invalid report"), runner.now())
                state["stages"].append(row)
        return state

    def card(self, state):
        before = copy.deepcopy(state)
        card = run_view.view(state, workspace=self.root, run_dir=self.run)["recovery"]
        assert before == state, "projecting the card changed the saved state"
        return card

    def execute(self, state, flags, token=None):
        """Apply one invocation's actions to the saved state as the CLI does; return (code, output).

        ``code`` is the step's exit code, None when the invocation goes on to build, or the
        status of a pause the actions raised. No provider may launch.
        """
        argv = ["--run-dir", str(self.run), "--no-chat"]
        if token is not None:
            recovery.require_token(state, token)  # The client's --expected-recovery-token check.
            argv += ["--expected-recovery-token", token]
        with contextlib.redirect_stderr(io.StringIO()):
            args, parser = autocode_args.parse(None, [*argv, *flags], runner.DEFAULT_ROLE_MODELS)
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            patch.object(runner, "run_role", side_effect=AssertionError("No provider is permitted")),
        ):
            try:
                code = actions.handle(runner, args, parser, state, self.run / "state.json", self.run, self.root)
            except support.Paused as error:
                code = error.status
        return code, out.getvalue() + err.getvalue()


class OfferAdmissionAgreementTests(SavedRunFixtures, unittest.TestCase):
    def setUp(self):
        self.open_run()
        self.addCleanup(self.temp.cleanup)

    def offered(self, card, kind):
        return [row for row in card["actions"] if row["kind"] == kind]

    def test_a_live_provider_is_not_offered_for_abandonment(self):
        state = self.interrupted()
        state["active_stage"].update(pid=os.getpid(), exit_code=None)
        view = run_view.view(state)
        self.assertFalse(self.offered(view["recovery"], "abandon"))
        self.assertIsNone(view["next_command"])
        self.assertTrue(view["needs"]["resume_unavailable"])
        code, _ = self.execute(state, ["--abandon-stage", "004/builder-02"])
        self.assertEqual("PAUSED_WORKSPACE_BUSY", code)
        self.assertIn("still exists", view["needs"]["resume_unavailable"])

    def test_an_uninspectable_provider_stays_on_hold(self):
        state = self.interrupted()
        state["active_stage"].update(pid=os.getpid(), exit_code=None)
        with patch("autocode_process.os.kill", side_effect=PermissionError):
            view = run_view.view(state)
            code, _ = self.execute(state, ["--abandon-stage", "004/builder-02"])
        self.assertFalse(self.offered(view["recovery"], "abandon"))
        self.assertIsNone(view["next_command"])
        self.assertIn("Cannot verify", view["needs"]["resume_unavailable"])
        self.assertEqual("PAUSED_WORKSPACE_BUSY", code)

    def test_job_retry_offer_rechecks_the_executor_source_and_settings(self):
        for change in ("source", "settings"):
            with self.subTest(change=change):
                state = self.job()
                if change == "source":
                    (self.root / "README.md").write_text("changed source\n")
                else:
                    state["settings"]["roles"]["builder"]["model"] = "another-model"
                view = run_view.view(state)
                self.assertFalse(self.offered(view["recovery"], "retry_job"))
                self.assertNotIn("--retry-failed-stage", view["next_command"] or "")
                code, _ = self.execute(state, ["--resume-paused", "--retry-failed-stage", "--job-retry-token", "job-1"])
                self.assertNotEqual(0, code)
                (self.root / "README.md").write_text(SOURCE)

    def test_repeated_failure_retry_offer_requires_the_same_failure_frontier(self):
        for change in ("source", "stage", "history"):
            with self.subTest(change=change):
                state = self.repeated()
                if change == "source":
                    (self.root / "README.md").write_text("changed source\n")
                elif change == "stage":
                    state["next_stage"] = "tester"
                else:
                    state["failure_history"] = {}
                view = run_view.view(state)
                self.assertFalse(self.offered(view["recovery"], "retry_failed_stage"))
                code, _ = self.execute(state, ["--resume-paused", "--retry-failed-stage"])
                self.assertNotEqual(0, code)
                (self.root / "README.md").write_text(SOURCE)

    def test_ac22_surveyed_offers_agree_with_their_executor_admission(self):
        # (kind, status, args, admitted state, the one-field variant its executor refuses, refusal text)
        cases = (
            (
                "abandon",
                "PAUSED_PROVIDER_UNCERTAIN",
                ["--abandon-stage", "004/builder-02"],
                self.interrupted,
                lambda: self.interrupted(active_stage=None),
                "--abandon-stage must match the active attempt_id",
            ),
            (
                "retry_job",
                "PAUSED_JOB_FAILURE",
                ["--resume-paused", "--retry-failed-stage", "--job-retry-token", "job-1"],
                self.job,
                lambda: self.job(source_identity=None),
                "Exact retry is unavailable",
            ),
            (
                "retry_report",
                "PAUSED_REPEATED_FAILURE",
                ["--resume-paused", "--retry-report", "004/validator-01"],
                self.report,
                lambda: self.report(error="Validator report failed for another reason"),
                "--retry-report must match the bounded rejected report-only attempt",
            ),
            (
                "retry_builder",
                "PAUSED_BUILDER_RETRY_LIMIT",
                ["--resume-paused", "--retry-builder", "M1"],
                self.builder,
                lambda: self.builder(action="retry"),
                "Serial Builder retry requires an exhausted, stopped approved milestone",
            ),
            (
                "retry_failed_stage",
                "PAUSED_REPEATED_FAILURE",
                ["--resume-paused", "--retry-failed-stage"],
                self.repeated,
                lambda: self.repeated(uncertain_artifacts=["partial.py"]),
                "Reconcile the active attempt or pending report repair",
            ),
        )
        for kind, status, flags, admitted, refused, refusal in cases:
            with self.subTest(offer=kind, state="admitted"):
                state = admitted()
                self.assertEqual(status, state["status"])
                card = self.card(state)
                offers = self.offered(card, kind)
                self.assertEqual([flags], [row["args"] for row in offers])
                code, err = self.execute(state, flags, token=card["token"])
                if kind == "abandon":
                    self.assertEqual(0, code, err)
                    self.assertEqual("PAUSED_STAGE_ABANDONED", state["status"])
                    self.assertNotIn("active_stage", state)
                else:
                    self.assertIsNone(code, err)
                    self.assertEqual("RUNNING", state["status"])
                self.assertNotIn("Input rejected", err)
            with self.subTest(offer=kind, state="refused"):
                state = refused()
                self.assertEqual(status, state["status"])
                self.assertEqual([], self.offered(self.card(state), kind))
                before = copy.deepcopy(state)
                code, err = self.execute(state, flags)
                self.assertEqual(2, code)
                self.assertIn(refusal, err)
                self.assertEqual(before["status"], state["status"])


if __name__ == "__main__":
    unittest.main()
