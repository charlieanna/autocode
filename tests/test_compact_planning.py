"""The compact planning path an earlier runtime's bug fixes could take (#2021): the runner's draft went to the
Plan Reviewer and then to the person. No run starts it now; a run saved with that draft under review finishes
its review, and a blocking concern sends it to the Planner's draft."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_compact_planning as compact
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_planning_cycle as planning_cycle
import autocode_resolver_human as human
import autocode_sensitive_paths as sensitive_paths
import autocode_stage_policy as stage_policy
import autopilot
from goal_fixtures import body

TASK = "Occasionally we renew the same domain twice after a timeout. Fix it."
REQUIREMENT = {
    "id": "R1",
    "text": "Renew a domain once even when the registry times out",
    "source_quote": "Occasionally we renew the same domain twice after a timeout",
}


class CompactPlanningTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="compact-"))
        (self.root / "epp").mkdir()
        (self.root / "epp" / "client.py").write_text("x = 1\n")
        for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *command], cwd=self.root, check=True
            )
        self.records = 0

    def state(self, *, opt_in=True, adaptive=True, sensitive=(), named_by="requirements_gather", kind="bugfix"):
        settings = {
            "joint_planning": True,
            "adaptive_planning": adaptive,
            "roles": {
                "requirements": {"model": "r"},
                "planner": {"model": "g"},
                "plan_reviewer": {"model": "p"},
                "task_planner": {"model": "a", "engine": "codex"},
                "builder": {"model": "t"},
                "tester": {"model": "s"},
            },
        }
        if opt_in:
            settings[compact.SETTING] = True
        if sensitive is not None:
            settings[sensitive_paths.SETTING] = list(sensitive)
        return {
            "version": 3,
            "task_id": "task",
            "task": TASK,
            "workspace": str(self.root),
            "status": "RUNNING",
            "phase": "INVESTIGATING",
            "stages": [],
            "answers": {},
            "user_events": [],
            "history": [],
            "sessions": {},
            "acceptance_criteria": [],
            "workflow": {"kind": kind, "then": "requirements_gather", "source": "model", "named_by": named_by},
            "requirements_handoff": {
                "report": {
                    "intended_outcome": "A domain is renewed once even when the registry times out.",
                    "requirements": [REQUIREMENT],
                    "required_behaviors": [REQUIREMENT["text"]],
                    "acceptance_tests": ["renew after a timeout records one mutation"],
                    "open_questions": [],
                    "summary": "Fix the duplicate renew.",
                    "source_refs": [],
                },
                "output": str(self.root / "requirements.json"),
            },
            "settings": settings,
        }

    def saved(self):
        """A run an earlier runtime saved with the runner's compact draft under the Plan Reviewer's review."""
        state = self.state()
        draft = body()
        draft["initial_task"] = {
            "objective": "Fix the root cause: retries after an uncertain timeout",
            "affected_paths": ["epp/client.py"],
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": [REQUIREMENT["text"]],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["python3 -m unittest -v"],
        }
        lifecycle.install_draft(state, draft, origin=compact.ORIGIN, queue_human=False)
        planning_cycle.start(state)
        stage_policy.record(
            state,
            path="compact",
            reasons=[],
            basis={"diagnosis_output_hash": "h", "diagnosis_source_revision": "rev-1", "requirements_output": None},
            decision="omitted",
            reason="The runner built the draft from the reproduced diagnosis and the Requirements record",
        )
        return state

    def reviewer_record(self, state, report):
        self.records += 1
        output = self.root / f"review_plan-{self.records}.json"
        output.write_text(json.dumps(report))
        events = self.root / f"review_plan-{self.records}.jsonl"
        events.write_text(
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report)}}) + "\n"
        )
        record = {
            "stage": "review_plan",
            "role": "task_planner",
            "route_role": "plan_reviewer",
            "engine": "fixture",
            "task_id": state["task_id"],
            "exit_code": 0,
            "output": str(output),
            "events": str(events),
            "source_revision": "rev-1",
        }
        state["stages"].append(record)
        return record

    @staticmethod
    def review(concerns=()):
        return {
            "summary": "Reviewed the runner's draft",
            "concerns": list(concerns),
            "brief_observations": [],
            "brief_observation_changes": [],
            "risk_observations": [],
            "risk_observation_changes": [],
            "source_trace": {"out_of_scope": [], "negation_waivers": []},
        }

    def test_a_clean_review_publishes_the_runners_draft_for_the_persons_approval(self):
        state = self.saved()
        report = self.review()
        autopilot.apply_planning(state, "review_plan", report, self.reviewer_record(state, report))
        contract = state["goal_contract"]
        self.assertEqual("adaptive_review_approval", contract["origin"])
        self.assertEqual("draft", contract["approval_status"], "the person approves; no policy does")
        self.assertEqual(goals.token(contract), state["planning"]["final_token"])
        # The approval request is published once the source is seen unchanged, as for a Planner's draft.
        with mock.patch.object(human.source_scope, "snapshot", return_value={"revision": "rev-1"}):
            self.assertEqual("escalate", human.evaluate(state))
            published = human.current(state)
        self.assertEqual("goal_approval", published["scope"])
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])
        self.assertEqual(["review_plan"], list(state["planning"]["reports"]))
        self.assertEqual("compact", stage_policy.view(state)["path"])
        self.assertIsNone(state.get("current_task"), "nothing is assigned before the person approves")
        self.assertIsNone(compact.fallback_context(state))
        self.assertEqual("", compact.fallback_rule(state))

    def test_a_blocking_review_returns_the_run_to_the_planners_draft(self):
        state = self.saved()
        concern = {
            "id": "K1",
            "concern": "The invariant ignores retries that succeed late",
            "evidence_refs": ["epp/client.py"],
            "requested_change": "Cover the late-success case",
            "acceptance_test": "A late success records one mutation",
            "blocking": True,
        }
        report = self.review([concern])
        state["planning"]["plan_review_calls"] = 1  # the review was charged at admission
        autopilot.apply_planning(state, "review_plan", report, self.reviewer_record(state, report))
        self.assertEqual(("RUNNING", "PLANNING", "draft_plan"), (state["status"], state["phase"], state["next_stage"]))
        self.assertEqual(
            0, state["planning"]["plan_review_calls"], "the compact review does not count against the full path"
        )
        self.assertEqual(
            compact.ORIGIN, state["goal_contract"]["origin"], "the refused draft stays on record, unapproved"
        )
        self.assertFalse(goals.approved(state))
        self.assertIsNone(state.get("current_task"), "no Builder launches")
        self.assertIsNone(state["planning"]["final_token"])
        self.assertEqual(concern, state["planning"]["reports"]["review_plan"]["report"]["concerns"][0])
        # The Planner's packet says what happened, and its rule tells it to draft afresh.
        self.assertEqual(
            {"reason": "the Plan Reviewer raised blocking concerns K1", "blocking_concerns": ["K1"]},
            compact.fallback_context(state),
        )
        self.assertIn("COMPACT DRAFT FALLBACK", compact.fallback_rule(state))
        policy = stage_policy.view(state)
        self.assertEqual("full", policy["path"])
        self.assertEqual(
            {
                "from": "review_plan",
                "reason": "the Plan Reviewer raised blocking concerns K1",
                "review_call_refunded": True,
            },
            policy["fallback"],
        )
        self.assertEqual("included", policy["decisions"][0]["decision"])
        self.assertEqual([], policy["decisions"][0]["covered_by"])

    def test_the_setting_is_chosen_at_the_start_and_kept(self):
        self.assertFalse(compact.new_run_setting(None, True))
        self.assertFalse(compact.new_run_setting(None, False))
        self.assertFalse(compact.new_run_setting(False, True))
        for joint in (False, True):
            with self.subTest(joint=joint), self.assertRaisesRegex(ValueError, "retired"):
                compact.new_run_setting(True, joint)
        self.assertFalse(compact.resume_refused({}, None))
        self.assertFalse(compact.resume_refused({compact.SETTING: True}, True))
        self.assertTrue(compact.resume_refused({compact.SETTING: True}, False))
        self.assertTrue(compact.resume_refused({}, True))

    def test_standard_runs_never_count_as_compact_even_with_a_retained_setting(self):
        state = self.state()
        self.assertTrue(compact.enabled(state))
        state["settings"]["implementation_flow"] = "standard"
        self.assertFalse(compact.enabled(state))

    def test_the_view_is_a_copy_or_null(self):
        self.assertIsNone(stage_policy.view({}))
        self.assertIsNone(stage_policy.view({"workflow": {"kind": "build"}}))
        self.assertIsNone(stage_policy.view({"workflow": {"stage_policy": "not a record"}}))
        state = self.saved()
        shown = stage_policy.view(state)
        shown["path"] = "changed"
        self.assertEqual("compact", stage_policy.view(state)["path"])


class SensitivePathsTests(unittest.TestCase):
    def test_new_runs_save_shipped_patterns_without_overriding_project_choices(self):
        defaults = sensitive_paths.new_run_patterns(None)
        paths = [
            "src/auth/login.py",
            "authentication.py",
            "src/security.py",
            "payment.py",
            "billing.py",
            "migration.py",
            "secrets.py",
            "certificate.py",
            "src/payments/refund.py",
            "billing/charge.py",
            "db/migrations/001.sql",
            ".github/workflows/ci.yml",
            "package.json",
            "sub/pyproject.toml",
            "poetry.lock",
            "requirements-dev.txt",
            "Dockerfile",
            "infra/deploy.tf",
            "certificates/server.pem",
            ".env.local",
            "src/summing.py",
            "tests/test_summing.py",
        ]
        self.assertEqual(paths[:-2], sensitive_paths.matched(paths, defaults))
        defaults.clear()
        self.assertTrue(sensitive_paths.new_run_patterns(None), "a caller cannot mutate the shipped list")
        self.assertEqual([], sensitive_paths.new_run_patterns([]))
        custom = ["src/registry/**"]
        self.assertEqual(custom, sensitive_paths.new_run_patterns(custom))
        self.assertIsNot(custom, sensitive_paths.new_run_patterns(custom))

    def test_a_project_declares_patterns_or_nothing_sensitive(self):
        self.assertEqual(
            ["src/billing/**", "*.lock"], sensitive_paths.checked([" src/billing/** ", "*.lock", "*.lock"], "f")
        )
        self.assertEqual([], sensitive_paths.checked([], "f"))
        for bad in ("src", ["src", 3], [""], ["a\tb"]):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "sensitive-paths"):
                sensitive_paths.checked(bad, "f")

    def test_patterns_match_files_directories_and_names(self):
        paths = [
            "src/billing/rounding.py",
            "src/billing/deep/x.py",
            "src/billingx.py",
            "poetry.lock",
            "migrations/0001.sql",
            "app.py",
        ]
        self.assertEqual(["src/billing/rounding.py"], sensitive_paths.matched(paths, ["src/billing/*"]))
        self.assertEqual(
            ["src/billing/rounding.py", "src/billing/deep/x.py"], sensitive_paths.matched(paths, ["src/billing/**"])
        )
        self.assertEqual(
            ["src/billing/rounding.py", "src/billing/deep/x.py"], sensitive_paths.matched(paths, ["src/billing/"])
        )
        self.assertEqual(["poetry.lock"], sensitive_paths.matched(paths, ["*.lock"]))
        self.assertEqual(["migrations/0001.sql"], sensitive_paths.matched(paths, ["migrations/**", ""]))
        self.assertEqual([], sensitive_paths.matched(paths, []))
        self.assertEqual([], sensitive_paths.matched([], ["*"]))
