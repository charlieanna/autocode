"""Saved inherited-test recovery through the public CLI; no provider or container.

The in-process TaskRun seam keeps real parsing, locks, state commits and status.
Only the independent test command is mocked: two-turn physical test/proof coverage
lives in the separate Linux regression. The existing unit handoff stops before a
Builder, and any attempted provider dispatch fails this test.
"""

from __future__ import annotations

import copy
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as core
import autocode_acceptance_tests_job as acceptance_job
import autocode_args as arguments
import autocode_contract_identity as identity
import autocode_source_scope as source_scope
import autocode_support as support
import autocode_taskrun as taskrun
import autocode_util as util
import autocode_verify as verify
import goal_fixtures
import inprocess_cli

from tests.test_acceptance_provenance import AcceptanceRecoveryFixture, seal

REAL_SNAPSHOT = source_scope.snapshot


class AcceptanceRecoveryCliTests(AcceptanceRecoveryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # The pure fixture uses a symbolic source revision. This persisted CLI
        # fixture uses a real Git tree and the ordinary snapshot implementation.
        self.enterContext(patch.object(source_scope, "snapshot", REAL_SNAPSHOT))
        previous_root = str(self.root)
        self.root, self.workspace, self.archive = self.root.resolve(), self.workspace.resolve(), self.archive.resolve()

        def canonical(value):
            if isinstance(value, dict):
                return {key: canonical(item) for key, item in value.items()}
            if isinstance(value, list):
                return [canonical(item) for item in value]
            return value.replace(previous_root, str(self.root)) if isinstance(value, str) else value

        self.state = canonical(self.state)
        self.run_dir = self.workspace / ".autocode/runs/inherited-recovery"
        self.run_dir.mkdir(parents=True)
        self.env = {
            "HOME": str(self.root / "home"),
            "PATH": os.environ["PATH"],
            "AUTOCODE_HOME": str(self.root / "registry"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "CODEX_HOME": str(self.root / "codex-home"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.enterContext(patch.dict(os.environ, self.env, clear=True))
        self.enterContext(patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}))
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        (self.workspace / "totals.py").write_text("def total(values):\n    return sum(v for v in values if v >= 0)\n")
        source = (self.workspace / "test_totals.py").read_bytes()
        (self.workspace / "test_totals.py").write_bytes(b"from totals import total\n\n" + source)
        self.archive.write_bytes(b"from totals import total\n\n" + self.old_code)
        self.state["acceptance_tests"]["receipts"]["old"]["files"]["test_totals.py"]["sha256"] = util.file_hash(
            self.archive
        )
        git = ["git", "-C", str(self.workspace), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test"]
        subprocess.run([*git, "add", "totals.py", "test_totals.py"], check=True)
        subprocess.run([*git, "commit", "-qm", "saved follow-up source"], check=True)

        # Complete public-compatible contracts while preserving the pure
        # fixture's exact inherited case and authoritative saved event stream.
        events = self.state["user_events"]
        feedback = events[1]
        body = goal_fixtures.body()
        body.update(copy.deepcopy(self.old["body"]))
        body["milestones"][0].update(acceptance_criteria=["AC1"], affected_paths=["totals.py", "test_totals.py"])
        old = seal(body, 6, [])
        events[0] = old["approval_event"]
        feedback["contract_token"] = identity.token(old)
        self.state["brief_feedback"] = [copy.deepcopy(feedback)]
        self.state["acceptance_tests"]["receipts"]["old"]["contract_hash"] = old["hash"]
        body.update(copy.deepcopy(self.approved["body"]))
        body["milestones"][0]["acceptance_criteria"] = ["AC1", "AC2"]
        events.pop()  # Replace the fixture r2 approval with the exact saved r12.
        self.approved = seal(body, 12, events)
        self.old = old
        names = {"AC1": "test_t1_negative"}
        candidate = {**self.state, "goal_contract": self.approved}
        draft = {
            "task_id": self.approved["task_id"],
            "revision": 13,
            "body": acceptance_job.corrected_body(candidate, names),
        }
        draft["hash"] = util.digest(draft)
        draft.update(
            origin=acceptance_job.ORIGIN,
            approval_status="draft",
            approval_event=None,
            declared_changes=copy.deepcopy(self.state["goal_contract"]["declared_changes"]),
        )
        self.state["goal_contract"] = draft
        self.state["contract_history"] = [old, copy.deepcopy(self.approved)]
        record = self.state["stages"][-1]
        record.update(contract_hash=self.approved["hash"], contract_revision=12)
        prior_events, prior_output = self.root / "prior-review.jsonl", self.root / "prior-review.json"
        prior_events.write_text(
            Path(record["events"]).read_text().replace("ses_fixture", "ses_prior").replace("msg_fixture", "msg_prior")
        )
        prior_output.write_text(Path(record["output"]).read_text())
        self.state["stages"][0] = {
            "stage": "astra_review",
            "engine": "opencode",
            "output_mode": "opencode_events",
            "events": str(prior_events),
            "output": str(prior_output),
            "exit_code": 0,
            "accounted": True,
            "finished_at": "2026-10-24T00:00:00+00:00",
            "duration_seconds": 7.5,
        }
        self.state["acceptance_tests"]["send_backs"][-1]["contract_hash"] = self.approved["hash"]
        self.state["task_archive"][0].update(contract_hash=self.approved["hash"], contract_revision=12)
        self.state["planning_history"][-1]["final_token"] = identity.token(self.approved)
        self.old_planning = copy.deepcopy(self.state["planning_history"][-1])
        # This is the historical broken send-back, saved before cap provenance
        # was carried with the same-turn spent count.
        self.state["planning"].pop("review_call_limit", None)
        self.state["planning"].pop("review_call_limit_origin", None)
        parsed = arguments.build_parser(None, arguments.DEFAULT_ROLE_MODELS).parse_args(
            [
                "--engine",
                "codex",
                "--joint-planning",
                "--spec-first",
                "--no-adaptive-planning",
                "--max-cost-usd",
                "3",
                "--evidence-provenance",
                "fake",
            ]
        )
        settings = core.configure(parsed, {"workspace": str(self.workspace), "iteration": 1})
        self.state.update(
            version=3,
            target="code",
            created_at="2026-10-25T00:00:00+00:00",
            iteration=1,
            sessions={},
            history=[],
            phase="PAUSED_OR_BLOCKED",
            settings=settings,
            no_progress_batches=0,
            active_seconds=12.5,
            resolver={"attempts": {"retained-incident": 2}, "lifetime_attempts": 4},
            report_repair_lifetime_attempts=6,
            report_repair_history=[{"attempts": 2, "reason": "retained completed repair"}],
        )
        record["source_revision"] = REAL_SNAPSHOT(self.workspace, {**self.state, "goal_contract": self.approved})[
            "revision"
        ]
        self.state["base_commit"] = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
        util.atomic_json(self.run_dir / "state.json", self.state)
        self.run = taskrun.TaskRun(self.workspace, self.run_dir, env=self.env, options=("--unit", "autoplanner"))
        self.enterContext(inprocess_cli.patched())
        self.checks = self.enterContext(patch.object(verify, "scratch_run", side_effect=self.check_current))
        self.providers = self.enterContext(
            patch.object(core, "run_role", side_effect=AssertionError("No provider may run"))
        )
        self.repair_resets = self.enterContext(
            patch.object(core, "reset_report_repair_for_resume", wraps=core.reset_report_repair_for_resume)
        )

    def check_current(self, workspace, directory, *, tests, **kwargs):
        self.assertEqual(self.workspace, Path(workspace))
        self.assertEqual(["test_totals.py"], tests)
        self.assertTrue(Path(directory).is_relative_to(self.run_dir))
        return {
            "command": "python -m pytest test_totals.py",
            "results": {"passed": ["test_t1_negative"], "failed": ["test_s1_start"], "complete": True},
        }

    def test_named_resume_restores_exact_approved_r12_without_new_authority_or_dispatch(self):
        checkpoint = (self.run_dir / "state.json").read_bytes()
        before = self.run.status()
        self.assertEqual(
            checkpoint, (self.run_dir / "state.json").read_bytes(), "Status must not reconcile saved state"
        )
        self.assertEqual("resume", before["needs"]["kind"])
        self.assertIn("--resume-paused", before["next_command"])
        after = self.run.resume_paused()
        self.assertEqual(identity.token(self.approved), after["approved_contract"]["token"])
        self.assertEqual("RUNNING", after["status"])
        self.assertIn(after["next_stage"], ("terra", "orchestrator"))
        self.assertFalse(after["done"])
        self.assertEqual(before["approvals"], after["approvals"])

        def recorded_usage(view):
            fields = (
                "stage",
                "events",
                "output",
                "engine",
                "model",
                "tokens",
                "requests",
                "reported_cost_usd",
                "historical_estimated_cost_usd",
                "duration_seconds",
            )
            return {
                row["identity"]: {key: row.get(key) for key in fields}
                for row in view["usage"]["accounting"]["attempts"]
            }

        self.assertEqual(2, len(recorded_usage(before)))
        self.assertTrue(
            any((row.get("tokens") or {}).get("input_tokens", 0) for row in recorded_usage(before).values())
        )
        self.assertEqual(recorded_usage(before), recorded_usage(after))
        self.assertEqual(3, before["cost"]["cap_usd"])
        self.assertEqual(before["cost"]["cap_usd"], after["cost"]["cap_usd"])
        self.assertEqual(1, self.checks.call_count)
        self.providers.assert_not_called()
        saved = util.read(self.run_dir / "state.json")
        self.assertEqual(self.approved, saved["goal_contract"])
        self.repair_resets.assert_not_called()
        for key in (
            "settings",
            "user_events",
            "stages",
            "resolver",
            "report_repair_lifetime_attempts",
            "report_repair_history",
        ):
            self.assertEqual(self.state[key], saved[key], key)
        self.assertEqual(self.old_planning, saved["planning"])
        self.assertEqual(self.state["goal_contract"], saved["contract_history"][-1])
        # A repeated explicit Resume cannot run the retained report again.
        self.run.resume_paused()
        self.assertEqual(1, self.checks.call_count)
        self.providers.assert_not_called()

    def test_stale_build_token_refuses_before_recheck_and_preserves_saved_correction(self):
        before = self.run.status()
        self.run.options += ("--expected-goal-token", "r999:stale")
        with self.assertRaisesRegex(taskrun.TaskRunError, "approved plan changed"):
            self.run.resume_paused()
        after = self.run.status()
        self.assertEqual(before["status"], after["status"])
        self.assertEqual(before["approvals"], after["approvals"])
        saved = util.read(self.run_dir / "state.json")
        for key in ("goal_contract", "planning", "contract_history", "user_events", "stages", "settings", "resolver"):
            self.assertEqual(self.state[key], saved[key], key)
        self.checks.assert_not_called()
        self.providers.assert_not_called()
