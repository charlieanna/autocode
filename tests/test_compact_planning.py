"""The compact planning path for an opted-in, reproduced, localized bug fix (#2021): the runner's draft goes to
the Plan Reviewer and then to the person; every other signal keeps the full path, with the reason recorded."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_bug_job as bug_job
import autocode_compact_planning as compact
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_resolver_human as human
import autocode_sensitive_paths as sensitive_paths
import autocode_stage_policy as stage_policy
import autopilot
from units import autoresolver

from .test_bug_job import CASE, diagnosis

TASK = "Occasionally we renew the same domain twice after a timeout. Fix it."
REQUIREMENT = {
    "id": "R1",
    "text": "Renew a domain once even when the registry times out",
    "source_quote": "Occasionally we renew the same domain twice after a timeout",
}
PRESERVE = {
    "id": "T2",
    "given": "no timeout",
    "when": "renew('example.com') runs",
    "then": "1 mutation",
    "kind": "preserve",
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
                "glm": {"model": "g"},
                "plan_reviewer": {"model": "p"},
                "astra": {"model": "a", "engine": "codex"},
                "terra": {"model": "t"},
                "sol": {"model": "s"},
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

    def investigated(self, state, *, probe="python3 -c 'raise SystemExit(0)'", shown=True, **overrides):
        """Apply the Investigator's report as the unit does, with the runner's probe result faked."""
        output = self.root / "investigation.json"
        counterexample = {"file": "pager.py", "find": "//", "replace": "/"}
        value = diagnosis(probe=probe, untestable="", counterexample=counterexample, **overrides)
        output.write_text(json.dumps(value))
        record = {"changed_files": [], "output": str(output), "source_revision": "rev-1"}
        bug_job.apply(
            state,
            value,
            record,
            str(self.root),
            run_probe=lambda command: {"exit_code": 0 if shown else 1, "tail": ""},
            run_counterexample=lambda command, edit: {"exit_code": 1, "tail": ""},
        )
        return state

    def reviewer_record(self, state, report):
        self.records += 1
        output = self.root / f"astra_challenge-{self.records}.json"
        output.write_text(json.dumps(report))
        events = self.root / f"astra_challenge-{self.records}.jsonl"
        events.write_text(
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report)}}) + "\n"
        )
        record = {
            "stage": "astra_challenge",
            "role": "astra",
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

    def test_an_eligible_reproduced_small_bug_takes_the_compact_path(self):
        state = self.investigated(self.state(), test_cases=[CASE, PRESERVE])
        self.assertEqual("astra_discovery", state["next_stage"], "the Investigator hands to the Planner by default")
        self.assertTrue(compact.start(state, str(self.root)))
        contract = state["goal_contract"]
        self.assertEqual(
            (compact.ORIGIN, "draft", None),
            (contract["origin"], contract["approval_status"], contract["approval_event"]),
        )
        self.assertFalse(goals.approved(state))
        self.assertEqual(
            ("RUNNING", "PLANNING", "astra_challenge"), (state["status"], state["phase"], state["next_stage"])
        )
        self.assertEqual({}, state["planning"]["reports"], "no Planner draft was paid for")
        body = contract["body"]
        criteria = {row["id"]: row["verification_method"] for row in body["acceptance_criteria"]}
        self.assertEqual(["C1", "C2", "C3"], list(criteria))
        self.assertEqual("test: test_t1_the_registry_records_exactly_1_renew", criteria["C2"])
        self.assertEqual("guard: test_t2_1_mutation", criteria["C3"])
        self.assertEqual(
            "The regression tests test_t1_the_registry_records_exactly_1_renew, test_t2_1_mutation (each restore case "
            "fails on the original code and passes after the fix), plus the existing test suite",
            criteria["C1"],
        )
        first = body["acceptance_criteria"][0]["criterion"]
        self.assertIn("(the fix in epp/client.py, proven by regression tests in tests/test_client.py)", first)
        self.assertNotIn(
            "keep one cl_trid", " ".join(body["technical_approach"]), "the fix_plan's own test names stay out"
        )
        self.assertIn("test_t1_the_registry_records_exactly_1_renew", body["technical_approach"][1])
        self.assertIn(REQUIREMENT["text"], body["required_behaviors"])
        self.assertIn("As requested: " + REQUIREMENT["source_quote"], body["constraints"])
        self.assertEqual("bugfix", body["task_kind"])
        self.assertEqual(
            ["epp/client.py", "tests/test_client.py", "docs/bugs/duplicate-renew.json"],
            body["initial_task"]["affected_paths"],
        )
        policy = stage_policy.view(state)
        self.assertEqual(("compact", []), (policy["path"], policy["reasons"]))
        decision = policy["decisions"][0]
        self.assertEqual(
            ("astra_discovery", "omitted", list(stage_policy.COVERED_BY)),
            (decision["stage"], decision["decision"], decision["covered_by"]),
        )
        self.assertEqual("rev-1", policy["basis"]["diagnosis_source_revision"])
        self.assertIsNotNone(policy["basis"]["diagnosis_output_hash"])
        self.assertIsNone(policy["fallback"])

    def test_a_clean_review_publishes_the_runners_draft_for_the_persons_approval(self):
        state = self.investigated(self.state())
        compact.start(state, str(self.root))
        report = self.review()
        autopilot.apply_planning(state, "astra_challenge", report, self.reviewer_record(state, report))
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
        self.assertEqual(["astra_challenge"], list(state["planning"]["reports"]))
        self.assertEqual("compact", stage_policy.view(state)["path"])
        self.assertIsNone(state.get("current_task"), "nothing is assigned before the person approves")
        self.assertIsNone(compact.fallback_context(state))
        self.assertEqual("", compact.fallback_rule(state))

    def test_a_blocking_review_returns_the_run_to_the_planners_draft(self):
        state = self.investigated(self.state())
        compact.start(state, str(self.root))
        concern = {
            "id": "K1",
            "concern": "The invariant ignores retries that succeed late",
            "evidence_refs": ["epp/client.py"],
            "requested_change": "Cover the late-success case",
            "acceptance_test": "A late success records one mutation",
            "blocking": True,
        }
        report = self.review([concern])
        state["planning"]["astra_calls"] = 1  # the review was charged at admission
        autopilot.apply_planning(state, "astra_challenge", report, self.reviewer_record(state, report))
        self.assertEqual(
            ("RUNNING", "PLANNING", "astra_discovery"), (state["status"], state["phase"], state["next_stage"])
        )
        self.assertEqual(0, state["planning"]["astra_calls"], "the compact review does not count against the full path")
        self.assertEqual(
            compact.ORIGIN, state["goal_contract"]["origin"], "the refused draft stays on record, unapproved"
        )
        self.assertFalse(goals.approved(state))
        self.assertIsNone(state.get("current_task"), "no Builder launches")
        self.assertIsNone(state["planning"]["final_token"])
        self.assertEqual(concern, state["planning"]["reports"]["astra_challenge"]["report"]["concerns"][0])
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
                "from": "astra_challenge",
                "reason": "the Plan Reviewer raised blocking concerns K1",
                "review_call_refunded": True,
            },
            policy["fallback"],
        )
        self.assertEqual("included", policy["decisions"][0]["decision"])
        self.assertEqual([], policy["decisions"][0]["covered_by"])

    def test_every_other_signal_keeps_the_full_path_and_says_why(self):
        cases = {
            "not opted in": (dict(opt_in=False), {}, "did not opt in (--compact-bugfix-planning)"),
            "adaptive planning off": (dict(adaptive=False), {}, "does not plan adaptively"),
            "pinned workflow": (dict(named_by=None), {}, "Requirements did not name the job"),
            "another job": (dict(kind="build"), {}, "not a bug fix"),
            "no sensitive-paths declaration": (dict(sensitive=None), {}, "declares no sensitive-paths"),
            "sensitive path": (dict(sensitive=["epp/**"]), {}, "sensitive paths: epp/client.py"),
            "sensitive note": (dict(sensitive=["docs/bugs/*"]), {}, "sensitive paths: docs/bugs/duplicate-renew.json"),
            "large fix": ({}, dict(fix_size="large"), "sized the fix large, not small"),
            "too many paths": (
                {},
                dict(affected_paths=["a.py", "b.py", "c.py"], test_paths=["tests/a.py", "tests/b.py"]),
                "touches 5 paths, more than 4",
            ),
        }
        for name, (state_options, diagnosis_options, reason) in cases.items():
            with self.subTest(name):
                state = self.investigated(self.state(**state_options), **diagnosis_options)
                before = copy.deepcopy(state)
                self.assertFalse(compact.start(state, str(self.root)))
                self.assertEqual(
                    "astra_discovery", state["next_stage"], "the Planner drafts, as the Investigator arranged"
                )
                self.assertNotIn("goal_contract", state)
                policy = stage_policy.view(state)
                self.assertEqual("full", policy["path"])
                self.assertTrue(any(reason in text for text in policy["reasons"]), (reason, policy["reasons"]))
                self.assertEqual(
                    ("astra_discovery", "included", []),
                    tuple(policy["decisions"][0][k] for k in ("stage", "decision", "covered_by")),
                )
                before["workflow"][stage_policy.RECORD] = state["workflow"][stage_policy.RECORD]
                self.assertEqual(before, state, "only the record changed")

    def test_a_reproduction_without_a_runner_probe_keeps_the_full_path(self):
        # Through the unit's hook: a diagnosis whose probe the runner did not run is a model's claim.
        state = self.state()
        autoresolver.apply_job(
            bug_job.STAGE,
            state,
            diagnosis(probe=""),
            {"changed_files": [], "output": str(self.root / "o.json")},
            str(self.root),
        )
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertNotIn("goal_contract", state)
        policy = stage_policy.view(state)
        self.assertEqual("full", policy["path"])
        self.assertIn("the reproduction was not shown by a probe the runner ran", policy["reasons"])

    def test_a_refused_draft_leaves_the_state_as_it_was_and_the_full_path(self):
        state = self.investigated(self.state())
        before = copy.deepcopy(state)
        with mock.patch.object(
            lifecycle, "install_draft", side_effect=ValueError("the draft cites a path outside the package")
        ):
            self.assertFalse(compact.start(state, str(self.root)))
        policy = stage_policy.view(state)
        self.assertEqual(
            ["the compact draft was refused: the draft cites a path outside the package"], policy["reasons"]
        )
        before["workflow"][stage_policy.RECORD] = state["workflow"][stage_policy.RECORD]
        self.assertEqual(before, state)
        self.assertEqual("astra_discovery", state["next_stage"])

    def test_the_requirement_trace_holds_the_runners_draft_to_the_planners_rule(self):
        state = self.investigated(self.state())
        body = compact.draft(state)
        report = compact.trace_report(state, body)
        self.assertEqual(
            [{"requirement_id": "R1", "disposition": "covered", "evidence": REQUIREMENT["text"]}],
            report["requirement_trace"],
        )
        goals.check_requirement_trace(state, report, body)
        # A requirement the draft dropped is refused, as a Planner's draft would be.
        dropped = {
            **body,
            "required_behaviors": [row for row in body["required_behaviors"] if row != REQUIREMENT["text"]],
        }
        with self.assertRaisesRegex(ValueError, "not covered"):
            goals.check_requirement_trace(state, compact.trace_report(state, dropped), dropped)

    def test_test_names_are_deterministic_and_distinct(self):
        self.assertEqual("test_t1_the_registry_records_exactly_1_renew", compact.test_name(CASE))
        twin = {**CASE, "id": "T3"}
        self.assertEqual("test_t3_the_registry_records_exactly_1_renew", compact.test_name(twin))
        self.assertEqual(
            "test_t1_the_registry_records_exactly_1_renew_2",
            compact.test_name(CASE, ["test_t1_the_registry_records_exactly_1_renew"]),
        )
        self.assertEqual("test_t9_holds", compact.test_name({"id": "T9", "then": "!!!"}))

    def test_the_setting_is_chosen_at_the_start_and_kept(self):
        self.assertTrue(compact.new_run_setting(None, True))
        self.assertFalse(compact.new_run_setting(None, False))
        self.assertFalse(compact.new_run_setting(False, True))
        self.assertTrue(compact.new_run_setting(True, True))
        with self.assertRaisesRegex(ValueError, "joint planning"):
            compact.new_run_setting(True, False)
        self.assertFalse(compact.resume_refused({}, None))
        self.assertFalse(compact.resume_refused({compact.SETTING: True}, True))
        self.assertTrue(compact.resume_refused({compact.SETTING: True}, False))
        self.assertTrue(compact.resume_refused({}, True))

    def test_the_view_is_a_copy_or_null(self):
        self.assertIsNone(stage_policy.view({}))
        self.assertIsNone(stage_policy.view({"workflow": {"kind": "build"}}))
        self.assertIsNone(stage_policy.view({"workflow": {"stage_policy": "not a record"}}))
        state = self.investigated(self.state())
        compact.start(state, str(self.root))
        shown = stage_policy.view(state)
        shown["path"] = "changed"
        self.assertEqual("compact", stage_policy.view(state)["path"])


class SensitivePathsTests(unittest.TestCase):
    def test_new_runs_save_shipped_patterns_without_overriding_project_choices(self):
        defaults = sensitive_paths.new_run_patterns(None)
        paths = [
            "src/auth/login.py",
            "authentication.py",
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
