"""The task-run interface: the status view and the CLI client. See docs/task-run.md."""

import copy
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_action_authority as action_authority
import autocode_captured_process as captured_process
import autocode_goal_lifecycle as lifecycle
import autocode_plan_file as plan_file
import autocode_process as processes
import autocode_prompts as prompts
import autocode_run_view as run_view
import autocode_source_snapshot as source_snapshot
import autocode_taskrun as taskrun
import autocode_util as util
import goal_fixtures
from units import autoplanner as planner

HERE = Path(__file__).resolve().parents[1] / "tools"  # its fixtures stay beside the runtime
# The offline fixture provider plans and builds exactly this greeting task.
BRIEF = (
    "Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
    "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
    "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
    "Python standard library only."
)
FIXTURE_OPTIONS = (
    "--evidence-provenance",
    "fake",
    "--engine",
    "codex",
    "--joint-planning",
    "--astra-model",
    "gpt-6-astra",
    "--terra-model",
    "gpt-5.6-terra",
    "--sol-model",
    "gpt-5.6-sol",
    "--completion-model",
    "gpt-6-astra",
    "--glm-model",
    "gpt-5.6-sol",
    "--plan-reviewer-model",
    "gpt-6-astra",
)


class RunViewTests(unittest.TestCase):
    def test_ready_boundary_names_advance_without_inventing_a_pause_category(self):
        for status in ("RUNNING", "DISCOVERING"):
            with self.subTest(status=status):
                shown = run_view.view(
                    {"status": status, "next_stage": "requirements_gather", "phase": "DISCOVERING"},
                    workspace=Path("/project"),
                    run_dir=Path("/runs/r1"),
                )
                self.assertEqual("continue", shown["needs"]["kind"])
                self.assertIsNone(shown["pause_category"])
                self.assertEqual("autocode --run-dir /runs/r1 --workspace /project --no-chat", shown["next_command"])

    def test_continue_with_a_retained_attempt_or_runner_check_names_no_advance(self):
        for retained in (
            {"active_stage": {"stage": "terra"}},
            {"active_stage": {}},
            {"active_runner_check": {"stage": "regression"}},
            {"active_runner_check": {}},
        ):
            with self.subTest(retained=retained):
                shown = run_view.action({"status": "RUNNING", **retained})
                self.assertEqual("continue", shown["needs"]["kind"])
                self.assertIsNone(shown["next_command"])

    def test_source_inventory_pause_names_reconciliation_without_discarding_the_retained_result(self):
        state = {
            "status": "PAUSED_SOURCE_UNAVAILABLE",
            "stop_reason": "Git could not inventory source",
            "active_stage": {"iteration": 1, "output": "/runs/r1/iterations/001/plan-challenge-01.json"},
        }
        shown = run_view.view(state, workspace=Path("/project"), run_dir=Path("/runs/r1"))
        self.assertEqual("resume", shown["needs"]["kind"])
        self.assertNotIn("abandon_stage", shown["needs"])
        self.assertIn("--resume-paused", shown["next_command"])
        self.assertNotIn("--abandon-stage", shown["next_command"])
        self.assertFalse(shown["done"])
        self.assertIsNone(shown.get("displayed_plan"))

    def test_explicit_report_retry_bypasses_automatic_escalation(self):
        self.assertTrue(
            action_authority.explicit_recovery_requested(SimpleNamespace(retry_report="001/sol_report_repair-02"), None)
        )
        self.assertFalse(action_authority.explicit_recovery_requested(SimpleNamespace(retry_report=None), None))

    def test_contract_fields(self):
        self.assertEqual(
            {
                "schema",
                "workspace",
                "run_dir",
                "pause_category",
                "pause_category_label",
                "next_command",
                "chat",
                "attention",
                "status",
                "done",
                "needs",
                "phase",
                "next_stage",
                "iteration",
                "stop_reason",
                "runner_check",
                "current_task",
                "workflow",
                "workflow_source",
                "workflow_reason",
                "turn",
                "evidence",
                "approvals",
                "dependency",
                "usage",
                "cost",
                "request_context",
                "output_transport",
                "direct_rework_assignments",
                "efficiency",
                "recovery",
                "verification",
                "code_checkpoints",
                "routes",
                "provider",
                "route_assignments",
                "liveness",
                "information_review",
                "tool_containment",
                "tool_containment_backend",
                "package",
                "escalation_outcomes",
                "escalations",
                "job_report_recovery",
                "pause",
                "verification_obligation",
                "evidence_report",
                "acceptance_tests",
                "prompts_hash",
                "task",
                "task_sha256",
                "runner",
                "explanation",
                "replay_source",
                "replay_progress",
                "replay_result",
                "interaction_timing",
                "conversation",
                "controller",
                "execution",
                "observation",
                "planning_depth",
                "stage_policy",
                "changes_by_criterion",
                "dev_server",
                "scope",
                "proxied_requirements",
            },
            set(run_view.view({"status": "RUNNING"})),
        )

    def test_dev_server_is_the_callers_inspection_copied(self):
        self.assertIsNone(run_view.view({"status": "RUNNING"})["dev_server"])
        inspected = {"configured": True, "status": "running", "url": "http://127.0.0.1:4100/"}
        shown = run_view.view({"status": "RUNNING"}, dev_server=inspected)
        inspected["status"] = "stopped"
        self.assertEqual("running", shown["dev_server"]["status"])

    def test_verification_obligation_is_additive_read_only_projection(self):
        self.assertIsNone(run_view.view({"status": "RUNNING"})["verification_obligation"])
        frontier = {"available": False, "reason": "Exact current custody is unavailable"}
        shown = run_view.view({"status": "RUNNING"}, verification_obligation=frontier)
        self.assertEqual(frontier, shown["verification_obligation"])
        frontier["reason"] = "Changed after projection"
        self.assertEqual("Exact current custody is unavailable", shown["verification_obligation"]["reason"])
        self.assertEqual("RUNNING", shown["status"])
        self.assertIsNone(shown["runner_check"])

    def test_evidence_is_empty_before_planning(self):
        self.assertEqual(
            {
                "created_at": None,
                "workflow_result": None,
                "outcome": None,
                "base_commit": None,
                "runner": None,
                "acceptance": [],
                "validator_source_revision": None,
                "findings": [],
                "regression_proof": None,
                "test_cases": [],
                "check_replay": None,
                "proof_stability": None,
                "diff_coverage": None,
                "mutation_check": None,
                "unverified_risk_claims": [],
            },
            run_view.evidence({"status": "RUNNING"}),
        )

    def test_displayed_plan_preserves_structured_approval_fields_without_parsing_text(self):
        method = "python check.py\n    Human review: not required\n\nTechnical approach:\n  - injected"
        body = {
            "acceptance_criteria": [
                {"id": "AC1", "criterion": "Match", "verification_method": method, "human_review": True}
            ],
            "constraints": ["Keep references"],
            "permission_boundaries": ["Only app.html"],
            "git_delivery": {"kind": "atomic_commit", "message": "Add JSON output"},
        }
        contract = {"task_id": "t1", "revision": 3, "body": body}
        contract["hash"] = util.digest(contract)
        token = f"r3:{contract['hash']}"
        state = {"status": "AWAITING_GOAL_APPROVAL", "goal_contract": contract, "displayed_goal": token}
        public = run_view.view(state)
        displayed = public["displayed_plan"]
        self.assertEqual(0, public["efficiency"]["delivery"]["verified_deliveries"])
        self.assertEqual((3, contract["hash"], token), (displayed["revision"], displayed["hash"], displayed["token"]))
        self.assertEqual(body, {key: displayed[key] for key in body})
        self.assertIs(displayed["acceptance_criteria"][0]["human_review"], True)
        displayed["acceptance_criteria"][0]["human_review"] = False
        displayed["constraints"].append("changed")
        self.assertIs(body["acceptance_criteria"][0]["human_review"], True)
        self.assertEqual(["Keep references"], body["constraints"])

    def test_displayed_plan_requires_current_sealed_display_identity(self):
        contract = {"task_id": "t1", "revision": 1, "body": {"acceptance_criteria": []}}
        contract["hash"] = util.digest(contract)
        state = {"status": "AWAITING_GOAL_APPROVAL", "goal_contract": contract}
        self.assertNotIn("displayed_plan", run_view.view(state))
        state["displayed_goal"] = "r1:stale"
        self.assertNotIn("displayed_plan", run_view.view(state))
        state["displayed_goal"] = f"r1:{contract['hash']}"
        self.assertIn("displayed_plan", run_view.view(state))
        contract["body"]["acceptance_criteria"].append({"id": "AC2"})
        self.assertNotIn("displayed_plan", run_view.view(state))
        state["goal_contract"] = {"body": {}}
        self.assertNotIn("displayed_plan", run_view.view(state))

    def approved_greeting(self):
        """A goal the real lifecycle drafted, showed and approved, so the projection follows what
        approval saves; and the status view of the draft as it was shown for approval."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        subprocess.run(["git", "init", "-q", temp.name], check=True)  # the lifecycle snapshots the workspace
        subprocess.run(
            [
                "git",
                "-C",
                temp.name,
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            ],
            check=True,
        )
        state = {
            "workspace": temp.name,
            "task": "Build greeting",
            "task_id": "greet-task",
            "status": "RUNNING",
            "settings": {},
        }
        lifecycle.migrate(state)
        lifecycle.install_draft(state, goal_fixtures.body(), origin="test")
        lifecycle.human.evaluate(state)
        lifecycle.present(state)
        draft = run_view.view(state)
        lifecycle.approve(state, draft["needs"]["token"])
        return state, draft

    def test_approved_contract_is_an_additive_copy_of_the_approved_plan(self):
        state, draft = self.approved_greeting()
        self.assertNotIn("approved_contract", draft)
        approved = run_view.view(state)
        shown = approved["approved_contract"]
        self.assertEqual(
            {
                "revision",
                "hash",
                "token",
                "task_id",
                "approved_at",
                "approval_source",
                "body",
                "goal_hash",
                "plan_file",
            },
            set(shown),
        )
        self.assertEqual("person", shown["approval_source"])  # not --skip-to-build (#1731)
        # Expected values are public and independent of the saved contract: what the draft view
        # showed for approval, the token it asked for, and the inputs the run was given.
        self.assertEqual(
            (draft["displayed_plan"]["revision"], draft["displayed_plan"]["hash"], draft["needs"]["token"]),
            (shown["revision"], shown["hash"], shown["token"]),
        )
        self.assertEqual("greet-task", shown["task_id"])
        self.assertEqual(goal_fixtures.body(), shown["body"])
        # What `autocode plan --check FILE` prints for a file holding this plan; none was loaded from one (#720).
        self.assertEqual(plan_file.goal_hash(goal_fixtures.body()), shown["goal_hash"])
        self.assertIsNone(shown["plan_file"])
        self.assertIsInstance(shown["approved_at"], str)
        self.assertTrue(shown["approved_at"])
        self.assertEqual({"approved_contract"}, set(approved) - set(draft))
        shown["body"]["acceptance_criteria"].append({"id": "changed"})
        shown["body"]["acceptance_criteria"][0]["criterion"] = "changed"
        self.assertEqual(goal_fixtures.body(), run_view.view(state)["approved_contract"]["body"])

    def test_approved_contract_appears_only_for_an_authenticated_current_approval(self):
        approved, _ = self.approved_greeting()
        token = run_view.view(approved)["approved_contract"]["token"]
        newer = goal_fixtures.body()
        newer["constraints"].append("Keep the greeting on one line")

        def approval(state, **fields):
            # approval_event is the very record in user_events (deepcopy keeps that), so changing it
            # in place breaks only the condition named, never the user-event check as well.
            state["goal_contract"]["approval_event"].update(fields)

        refused = {
            "approval of an older token": lambda state: approval(state, token="r1:old"),
            "approval not among the user's events": lambda state: state.update(user_events=[]),
            "approved by the model": lambda state: approval(state, actor="model"),
            "workflow policy outside a policy origin": lambda state: (
                approval(state, actor="workflow_policy"),
                state["goal_contract"].update(origin="test"),
            ),
            "body edited after approval": lambda state: state["goal_contract"]["body"]["acceptance_criteria"].append(
                {
                    "id": "C9",
                    "criterion": "Also greets twice",
                    "verification_method": "python -m unittest",
                    "human_review": False,
                }
            ),
            "newer draft installed after approval": lambda state: lifecycle.install_draft(state, newer, origin="test"),
        }
        for name, change in refused.items():
            with self.subTest(name):
                state = copy.deepcopy(approved)
                change(state)
                self.assertNotIn("approved_contract", run_view.view(state))
                self.assertIsNone(run_view.approved_contract(state))
        # A bug fix's small correction approved under the workflow policy the user agreed to.
        state = copy.deepcopy(approved)
        approval(state, actor="workflow_policy")
        state["goal_contract"]["origin"] = "bugfix_small_correction"  # autocode_workflows.POLICY_ORIGINS
        self.assertEqual(token, run_view.view(state)["approved_contract"]["token"])
        self.assertEqual(token, run_view.approved_contract(state)["token"])

    def test_direct_rework_provenance_is_not_completion_proof_and_is_copied(self):
        receipt = {
            "source_task_id": "task-old",
            "assigned_task_id": "task-repair",
            "evidence_hashes": {"review.json": "digest"},
            "provenance": "completion_direct_assignment",
        }
        state = {"status": "RUNNING", "direct_rework_assignments": [receipt]}
        view = run_view.view(state)
        self.assertEqual([receipt], view["direct_rework_assignments"])
        self.assertFalse(view["done"])
        self.assertIsNone(view["evidence"]["check_replay"])
        view["direct_rework_assignments"][0]["evidence_hashes"]["review.json"] = "changed"
        self.assertEqual("digest", receipt["evidence_hashes"]["review.json"])
        self.assertEqual([], run_view.view({"status": "RUNNING"})["direct_rework_assignments"])

    def test_evidence_carries_the_english_test_cases_and_what_proves_them(self):
        case = {"id": "T1", "given": "a timeout", "when": "renew()", "then": "one mutation"}
        state = {
            "status": "TASK_COMPLETE",
            "investigation": {"outcome": "reproduced", "test_cases": [case]},
            "regression_proof": {"verdict": "PASS", "case_tests": {"T1": ["test_t1_one_mutation"]}},
        }
        evidence = run_view.evidence(state)
        self.assertEqual([case], evidence["test_cases"])
        self.assertEqual({"T1": ["test_t1_one_mutation"]}, evidence["regression_proof"]["case_tests"])
        state["investigation"]["outcome"] = "not_reproduced"
        self.assertEqual([], run_view.evidence(state)["test_cases"])

    def test_evidence_pairs_criteria_with_their_latest_outcome(self):
        state = {
            "status": "TASK_COMPLETE",
            "base_commit": "abc",
            "goal_contract": {
                "body": {
                    "intended_outcome": "Fix it",
                    "acceptance_criteria": [
                        {"id": "AC1", "criterion": "Parses dates"},
                        {"id": "AC2", "criterion": "Documents it"},
                        {"id": "AC3", "criterion": "Ships it"},
                    ],
                }
            },
            "last_decision": {
                "report": {
                    "acceptance_criteria": [{"id": "AC1", "status": "passed", "evidence": "pytest -k dates: 3 passed"}]
                }
            },
            "validation": {
                "source_revision": "r7",
                "criterion_results": [{"id": "AC1", "status": "FAIL"}, {"id": "AC3", "status": "NOT_VERIFIED"}],
            },
            "human_reviews": {"AC2": {"token": "r1"}},
            "findings_ledger": [
                {
                    "id": "F1",
                    "status": "open",
                    "severity": "low",
                    "finding": "Prefer a clearer name",
                    "source": "sol",
                    "blocking": False,
                    "evidence": "parser.py:14",
                    "times_reported": 2,
                }
            ],
            "regression_proof": {
                "verdict": "PASS",
                "fail_to_pass": ["test_dates"],
                "failures": [],
                "unverified": [],
                "commands": {"suite": "pytest"},
                "source_revision": "r9",
                "checks": {"large": "output"},
            },
        }
        evidence = run_view.evidence(state)
        self.assertEqual(("Fix it", "abc"), (evidence["outcome"], evidence["base_commit"]))
        self.assertEqual("r7", evidence["validator_source_revision"])
        # validator_status keeps a Validator FAIL distinct from NOT_VERIFIED and from a
        # criterion the latest validation never listed (None).
        self.assertEqual(
            [
                {
                    "id": "AC1",
                    "criterion": "Parses dates",
                    "status": "passed",
                    "evidence": "pytest -k dates: 3 passed",
                    "validator_status": "FAIL",
                    "human_reviewed": False,
                },
                {
                    "id": "AC2",
                    "criterion": "Documents it",
                    "status": None,
                    "evidence": None,
                    "validator_status": None,
                    "human_reviewed": True,
                },
                {
                    "id": "AC3",
                    "criterion": "Ships it",
                    "status": None,
                    "evidence": None,
                    "validator_status": "NOT_VERIFIED",
                    "human_reviewed": False,
                },
            ],
            evidence["acceptance"],
        )
        self.assertEqual(
            [
                {
                    "id": "F1",
                    "status": "open",
                    "severity": "low",
                    "finding": "Prefer a clearer name",
                    "source": "sol",
                    "blocking": False,
                    "evidence": "parser.py:14",
                }
            ],
            evidence["findings"],
        )
        self.assertEqual(
            {
                "verdict": "PASS",
                "fail_to_pass": ["test_dates"],
                "failures": [],
                "unverified": [],
                "commands": {"suite": "pytest"},
                "source_revision": "r9",
                "case_tests": None,
                "unstable": None,
                "follow_up_baseline": None,
            },
            evidence["regression_proof"],
        )

    def test_workflow_is_none_until_recognized(self):
        self.assertIsNone(run_view.view({"status": "RUNNING"})["workflow"])
        self.assertIsNone(run_view.view({"status": "RUNNING", "workflow": {"kind": None, "then": "x"}})["workflow"])
        self.assertEqual("review", run_view.view({"status": "RUNNING", "workflow": {"kind": "review"}})["workflow"])

    def test_complete_needs_nothing(self):
        view = run_view.view({"status": "TASK_COMPLETE"})
        self.assertTrue(view["done"])
        self.assertIsNone(view["needs"])

    def test_human_review_comes_before_other_questions(self):
        state = {
            "status": "WAITING_FOR_USER",
            "pending_questions": [
                {"id": "d-1", "question": "Accept C2?", "review_criteria": ["C2"], "review_token": "r-9"}
            ],
        }
        self.assertEqual(
            {"kind": "review", "criteria": ["C2"], "token": "r-9", "question": "Accept C2?"}, run_view.needs(state)
        )

    def test_human_review_after_validation_passes_with_no_pending_question(self):
        # autopilot.apply_review_result sets user_request directly, with pending_questions
        # left empty — no question object to read review_criteria/review_token from. Shape
        # matches a real run captured live (2026-09-26): sol PASSED, AC11/AC13 need a person.
        state = {
            "status": "WAITING_FOR_USER",
            "pending_questions": [],
            "displayed_review": "r5:abc@def:ghi",
            "user_request": {
                "kind": "human_review",
                "criteria": ["AC11", "AC13"],
                "decision_needed": "Review the current artifact and explicitly approve the listed criteria",
            },
        }
        self.assertEqual(
            {
                "kind": "review",
                "criteria": ["AC11", "AC13"],
                "token": "r5:abc@def:ghi",
                "question": "Review the current artifact and explicitly approve the listed criteria",
            },
            run_view.needs(state),
        )

    def test_questions_carry_their_defaults(self):
        state = {
            "status": "WAITING_FOR_USER",
            "user_request": {"kind": "permission"},
            "pending_questions": [
                {
                    "id": "q1",
                    "question": "Use a network?",
                    "why": "tests",
                    "options": ["no"],
                    "proposed_default": "no",
                    "internal": "x",
                }
            ],
        }
        need = run_view.needs(state)
        self.assertEqual(("answer", "permission"), (need["kind"], need["request_kind"]))
        self.assertEqual(
            [{"id": "q1", "question": "Use a network?", "why": "tests", "options": ["no"], "proposed_default": "no"}],
            need["questions"],
        )

    def test_a_pending_resolver_request_carries_its_token_and_scope(self):
        public = {"request_id": "r1", "request_token": "t1", "scope": "operational_exhaustion"}
        state = {
            "status": "WAITING_FOR_USER",
            "user_request": {"kind": "blocker"},
            "pending_questions": [{"id": "q1", "question": "Raise the cap?"}],
            "resolver_human_request": public,
            "resolver": {"human_escalations": {"r1": {"status": "pending"}}},
        }
        need = run_view.needs(state)
        self.assertEqual(
            ("r1", "t1", "operational_exhaustion"),
            (need["resolver_request_id"], need["resolver_token"], need["resolver_scope"]),
        )
        # A consumed request is not answerable, so it carries none of them.
        state["resolver"]["human_escalations"]["r1"]["status"] = "consumed"
        self.assertFalse({"resolver_request_id", "resolver_token", "resolver_scope"} & set(run_view.needs(state)))

    def test_plan_approval_needs_the_displayed_token(self):
        self.assertEqual(
            {"kind": "approve_plan", "token": "g-1"},
            run_view.needs({"status": "AWAITING_GOAL_APPROVAL", "displayed_goal": "g-1"}),
        )
        self.assertEqual({"kind": "continue"}, run_view.needs({"status": "AWAITING_GOAL_APPROVAL"}))

    def test_pauses(self):
        self.assertEqual(
            {"kind": "planning_budget", "reason": "two calls"},
            run_view.needs({"status": "PAUSED_PLANNING_BUDGET", "stop_reason": "two calls"}),
        )
        self.assertEqual(
            {"kind": "resume", "reason": "quota"}, run_view.needs({"status": "PAUSED_BUDGET", "stop_reason": "quota"})
        )
        self.assertEqual("resume", run_view.needs({"status": "PLAN_REWORK_REQUIRED"})["kind"])

    def test_quota_pause_names_the_abandon_step_with_the_attempt_id(self):
        need = run_view.needs(
            {
                "status": "PAUSED_BUDGET",
                "stop_reason": "quota restored; set the attempt aside",
                "active_stage": {"iteration": 1, "output": "/run/terra-01.json", "stage": "terra"},
            }
        )
        self.assertEqual("001/terra-01", need["abandon_stage"])
        self.assertIn("--abandon-stage 001/terra-01 then --resume-paused", need["action"])

    def test_rejected_validator_report_exposes_exact_retry_attempt(self):
        state = {
            "status": "PAUSED_REPEATED_FAILURE",
            "stop_reason": "report rejected",
            "settings": {"report_repair": {"max_attempts": 2}},
            "pending_report_repair": {
                "error": "Check is not supported by an exact executed Validator event",
                "original": {"stage": "sol"},
                "attempts": 2,
                "latest_rejected": {"iteration": 1, "output": "/run/sol_report_repair-02.json"},
            },
        }
        self.assertEqual("001/sol_report_repair-02", run_view.needs(state)["retry_report_attempt"])

    def test_legacy_job_without_source_identity_exposes_recovery_not_retry(self):
        failure = {
            "reason": "Provider stopped",
            "stage": "investigate_bug",
            "attempt_id": "001/bug-investigation-01",
            "job_retry_token": "jr:old",
            "archive": "/run/archive",
            "source_identity": None,
            "write_diagnosis": {"unrestored": ["original source capture"]},
            "unrestored": ["original source capture"],
        }
        for status in ("PAUSED_JOB_FAILURE", "PAUSED_STAGE_ABANDONED"):
            need = run_view.needs({"status": status, "job_failure": failure})
            self.assertEqual("recover_source", need["kind"])
            self.assertIsNone(need["action"])
            self.assertIn("original source identity", need["recovery_hint"])
            self.assertEqual("jr:old", need["job_retry_token"])
            self.assertEqual("/run/archive", need["archive"])
            self.assertEqual(failure["write_diagnosis"], need["write_diagnosis"])

    def test_running_continues(self):
        self.assertEqual({"kind": "continue"}, run_view.needs({"status": "RUNNING", "pending_questions": []}))


class TaskRunTests(unittest.TestCase):
    """End to end through the real CLI with the offline fixture provider (a few seconds)."""

    def setUp(self):
        if artifacts := os.environ.get("BUILD_AUDIT_ARTIFACTS"):
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            root = Path(tempfile.mkdtemp(prefix="taskrun-", dir=artifacts))
        else:
            temp = tempfile.TemporaryDirectory(prefix="taskrun-")
            self.addCleanup(temp.cleanup)
            root = Path(temp.name)
        self.workspace = root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            ],
            check=True,
        )
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(HERE / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def test_retired_token_cap_option_is_rejected_before_a_run_starts(self):
        for cap in ("0", "1", "2000000"):
            with self.subTest(cap=cap), self.assertRaisesRegex(taskrun.TaskRunError, "unrecognized arguments"):
                taskrun.TaskRun.start(
                    self.workspace, BRIEF, options=[*FIXTURE_OPTIONS, "--max-reported-tokens", cap], env=self.env
                )
        self.assertFalse((self.workspace / ".autocode").exists())

    def test_status_shows_the_planning_depth_decisions(self):
        # #697: the clarity judgment, the plan's size, the review allowance and whether the Plan Reviewer
        # approved the draft at its first review reach --status, not only state.json. Requirements runs first
        # on every run and makes the judgment itself (#1860), so no run skips it.
        for clarity in ("clear", "vague"):
            with self.subTest(clarity=clarity):
                workspace = self.workspace.parent / f"project-{clarity}"
                shutil.copytree(self.workspace, workspace)
                env = {**self.env, "LIVE_FIXTURE_CLARITY": clarity}
                run = taskrun.TaskRun.start(workspace, BRIEF, options=FIXTURE_OPTIONS, env=env, timeout=300)
                view = run.status()
                self.assertEqual("approve_plan", (view["needs"] or {}).get("kind"), view["stop_reason"])
                depth = view["planning_depth"]
                self.assertEqual(
                    {
                        "adaptive": True,
                        "clarity": clarity,
                        "requirements_skipped": None,
                        "plan_size": "small",
                        "review_limit": 2,
                        "challenges": 1,
                        "approved_early": True,
                        "uncited_sentences": 0,
                    },
                    {key: value for key, value in depth.items() if key != "size_signals"},
                )
                self.assertEqual(1, depth["size_signals"]["milestones"])
                stages = [row["stage"] for row in util.read(run.run_dir / "state.json")["stages"]]
                self.assertEqual("requirements_gather", stages[0], stages)
                self.assertNotIn("recognize_workflow", stages)
                self.assertNotIn("astra_finalize", stages, "approved at the first review")

    def test_start_approve_and_complete(self):
        before = self.footprint()
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertEqual(prompts.HASH, view["prompts_hash"])
        self.assertTrue(view["usage"]["accounting"]["attempts"])
        model_attempts = [row for row in view["usage"]["accounting"]["attempts"] if not row["runner_owned"]]
        self.assertTrue(model_attempts)
        self.assertTrue(all(row.get("prompts_hash") == prompts.HASH for row in model_attempts))
        self.assertEqual("Waiting for you", view["progress"]["headline"], view["progress"])
        self.assertEqual("plan approval needed", view["progress"]["needs_you"])
        self.assertNotIn("approved_contract", view)  # shown for approval, not yet the plan in force
        timing = view["interaction_timing"]
        self.assertIsNotNone(timing["launched_at"])
        self.assertIsNotNone(timing["first_plan_at"])
        self.assertIsNotNone(timing["first_plan_seconds"])
        self.assertIsNone(timing["first_builder_at"])
        self.assertEqual(timing, run.status()["interaction_timing"])  # reads never stamp displays
        with self.assertRaisesRegex(taskrun.TaskRunError, "approve plan exited"):
            run.approve_plan("not-the-displayed-token")
        token = view["needs"]["token"]
        run.approve_plan(token)
        view = run.advance_until_input()
        self.assertTrue(view["done"], {key: view.get(key) for key in ("status", "needs", "runner_check")})
        evidence = run.evidence_report(require_current=False)["document"]
        self.assertEqual(prompts.HASH, evidence["run"]["prompts_hash"])
        model_attempts = [row for row in evidence["attempts"] if not row["runner_owned"]]
        self.assertTrue(model_attempts)
        self.assertTrue(all(row.get("prompts_hash") == prompts.HASH for row in model_attempts))
        finished_timing = view["interaction_timing"]
        self.assertEqual(timing["launched_at"], finished_timing["launched_at"])
        self.assertEqual(timing["first_plan_at"], finished_timing["first_plan_at"])
        self.assertIsNotNone(finished_timing["first_builder_at"])
        self.assertGreaterEqual(finished_timing["first_builder_seconds"], finished_timing["first_plan_seconds"])
        self.assertEqual(finished_timing, run.evidence_report()["document"]["run"]["interaction_timing"])
        self.assertEqual(token, view["approved_contract"]["token"])  # a completed run keeps the plan it approved
        self.assertEqual(0, view["efficiency"]["by_category"].get("report_repair", {}).get("attempts", 0))
        self.assertTrue((self.workspace / "greet.py").is_file())
        progress = view["progress"]
        self.assertEqual("Complete", progress["headline"], progress)
        self.assertEqual(progress["tasks"]["total"], progress["tasks"]["done"], progress)
        self.assertEqual(progress["requirements"]["total"], progress["requirements"]["checked"], progress)
        self.assertGreater(progress["tasks"]["total"], 0, progress)
        self.assertGreater(progress["requirements"]["total"], 0, progress)
        # A new caller can reattach to the saved run.
        again = taskrun.TaskRun(self.workspace, run.run_dir, options=FIXTURE_OPTIONS, env=self.env)
        self.assertEqual("TASK_COMPLETE", again.status()["status"])
        self.assertEqual(1, view["efficiency"]["delivery"]["verified_deliveries"])
        # Historical completion cannot supply a current accepted-outcome denominator.
        (self.workspace / "greet.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
        stale = again.status()
        self.assertEqual(0, stale["efficiency"]["delivery"]["verified_deliveries"])
        self.assertIsNone(stale["efficiency"]["unit_metrics"]["wall_seconds"]["value"])
        # The task-run interface works in place (--in-place): the job delivers into the project itself.
        self.assert_no_project_level_files(before, run, stale, delivered=("greet.py", "test_greet.py", "README.md"))

    def test_a_first_task_that_only_validates_needs_no_paths_to_be_approved(self):
        # A live program's final check planned one validate task naming no affected paths, in a milestone that
        # owned none; the Plan Reviewer passed it and every approval failed with "Input rejected" (#615).
        env = {**self.env, "LIVE_FIXTURE_FIRST_TASK": "validate"}
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=env, timeout=300)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        view = run.approve_plan(view["needs"]["token"])
        first = view["approved_contract"]["body"]["initial_task"]
        self.assertEqual(("validate", []), (first["kind"], first["affected_paths"]))
        self.assertNotEqual("approve_plan", (view.get("needs") or {}).get("kind"), view)

    def test_a_first_task_that_writes_must_name_its_paths_before_anyone_approves_it(self):
        # The same plan with an implement task cannot be assigned: it is refused as a draft and repaired, never
        # shown for approval with a task approval would refuse (#615).
        env = {**self.env, "LIVE_FIXTURE_FIRST_TASK": "implement"}
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=env, timeout=300)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertEqual(1, view["efficiency"]["by_category"]["report_repair"]["attempts"], view["efficiency"])
        view = run.approve_plan(view["needs"]["token"])
        first = view["approved_contract"]["body"]["initial_task"]
        self.assertEqual(
            ("implement", ["greet.py", "test_greet.py", "README.md"]), (first["kind"], first["affected_paths"])
        )  # the repaired plan names what it writes

    def saved_unassignable_first_task(self, *, activate_later=False, adaptive=True, repaired_planner=False):
        # Seed a sealed older checkpoint; modifying it here models the pre-#620 draft format.
        options = FIXTURE_OPTIONS if adaptive else (*FIXTURE_OPTIONS, "--no-adaptive-planning")
        probe = self.workspace.parent / "launches.jsonl"
        env = {**self.env, "AUTOCODE_REGISTRY_LAUNCH_PROBE": str(probe)}
        if repaired_planner:
            env["LIVE_FIXTURE_FIRST_TASK"] = "implement"
        provider = Path(self.env["PATH"].split(os.pathsep)[0]) / "codex"
        script = provider.read_text()
        anchor = '    repairing = bool(data.get("report_repair"))'
        self.assertIn(anchor, script)
        provider.write_text(
            script.replace(
                anchor,
                '    with Path(os.environ["AUTOCODE_REGISTRY_LAUNCH_PROBE"]).open("a") as probe:\n        probe.write(stage + "\\n")\n'
                + anchor,
                1,
            )
        )
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=options, env=env, timeout=300)
        checkpoint = run.run_dir / "state.json"
        state = util.read(checkpoint)
        contract = state["goal_contract"]
        contract["body"]["initial_task"]["affected_paths"] = []
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        state["planning"]["final_token"] = "r" + str(contract["revision"]) + ":" + contract["hash"]
        state["displayed_goal"] = state["planning"]["final_token"]
        final = next((row for row in state["stages"] if row.get("stage") == "astra_finalize"), None)
        if final:
            # The checkpoint predates retained final reviews (#1858) too: its final review was asked for
            # the full contract, so its saved schema, which the report repair reuses, asks for one.
            schema = Path(final["schema"])
            schema.write_text(
                json.dumps(planner._model_report_schema(planner.schema_for(state, final["stage"]), state))
            )
        if activate_later:
            state["settings"]["milestone_checkpoints"]["enabled"] = False
            run = taskrun.TaskRun(
                self.workspace,
                run.run_dir,
                options=options,
                command=(*run.command, "--milestone-checkpoints"),
                env=env,
                timeout=300,
            )
        util.atomic_json(checkpoint, state)
        before = probe.read_bytes()
        if activate_later:
            with self.assertRaises(taskrun.TaskRunError):
                run.approve_plan(state["planning"]["final_token"])
            run.command = run.command[:-1]  # activation belongs to that one invocation
        else:
            self.assertIn("queued report repair before approval", run.show_goal())
        self.assertEqual(before, probe.read_bytes(), "Rechecking never launches a provider or approves work")
        self.assertNotEqual("approve_plan", (run.status().get("needs") or {}).get("kind"))
        self.assertFalse((self.workspace / "greet.py").exists())
        repaired = run.advance_until_input()
        self.assertEqual("approve_plan", repaired["needs"]["kind"], (repaired["status"], repaired["stop_reason"]))
        self.assertNotEqual(state["planning"]["final_token"], repaired["needs"]["token"])
        calls = util.read(checkpoint)["planning"]["astra_calls"]
        self.assertEqual(state["planning"]["astra_calls"] + int(adaptive), calls)
        accepted = run.approve_plan(repaired["needs"]["token"])
        self.assertTrue(accepted["approved_contract"]["body"]["initial_task"]["affected_paths"])

    def test_saved_unassignable_first_task_returns_to_repair_before_approval(self):
        self.saved_unassignable_first_task()

    def test_saved_finalized_first_task_returns_to_its_final_report_repair(self):
        self.saved_unassignable_first_task(adaptive=False)

    def test_saved_task_from_a_repaired_planner_can_be_repaired_again(self):
        self.saved_unassignable_first_task(repaired_planner=True)

    def test_checkpoints_enabled_after_drafting_repair_before_approval(self):
        self.saved_unassignable_first_task(activate_later=True)

    def test_a_default_launch_keeps_its_job_in_its_own_task_worktree(self):
        # The CLI without --in-place, as a person launches it: the run works in a task worktree.
        before = self.footprint()
        launch = captured_process.run(
            [*taskrun.AUTOCODE, BRIEF, "--no-chat", *FIXTURE_OPTIONS, "--workspace", str(self.workspace)],
            env={**os.environ, **self.env},
            timeout=300,
        )
        self.assertEqual(2, launch.returncode, (launch.stdout + launch.stderr)[-2000:])  # stopped for plan approval
        (tree,) = self.footprint().worktrees - before.worktrees
        run = taskrun.TaskRun.attach(tree, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        run.approve_plan(run.status()["needs"]["token"])
        view = run.advance_until_input()
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertTrue((tree / "greet.py").is_file())
        self.assert_no_project_level_files(before, run, view)

    def footprint(self):
        """What a job could leave at the project level, to compare before and after it.

        The root's entries (Git does not show an empty directory), Git status with ignored files (a
        directory that ignores itself, as .autocode does, still shows), every ref and its commit,
        and the worktrees.
        """
        git = lambda *args: subprocess.run(
            ["git", "-C", str(self.workspace), *args], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        return SimpleNamespace(
            entries={entry.name for entry in self.workspace.iterdir()},
            status=set(git("status", "--porcelain", "--ignored", "--untracked-files=normal")),
            refs=dict(line.split(" ", 1) for line in git("for-each-ref", "--format=%(refname) %(objectname)")),
            worktrees={
                Path(line.removeprefix("worktree ")).resolve()
                for line in git("worktree", "list", "--porcelain")
                if line.startswith("worktree ")
            },
        )

    def assert_no_project_level_files(self, before, run, view, *, delivered=()):
        """#22: a normal single job creates no project-level files.

        Task flows and component builds coordinate several runs of one project and keep their
        records at the project level: .autocode/task-flows (`autocode tasks MANIFEST`) and
        .autocode-components (`autocode components ARCH`), with worktrees on branches of their own
        (components/<id>-<hex>). A single job adds .autocode, its learned project record, and the
        files it ``delivered`` to the project; launched without --in-place, also its task worktree
        and that worktree's branch.
        """
        workspace = self.workspace.resolve()
        after = self.footprint()
        tree = run.workspace if run.workspace != workspace else None  # a default launch's task worktree
        # Positive control: this is the run the job saved, in the project or in its task worktree.
        self.assertTrue((run.run_dir / "state.json").is_file(), run.run_dir)
        self.assertEqual(run.workspace / ".autocode" / "runs", run.run_dir.parent)
        self.assertEqual(before.entries | {".autocode", *delivered}, after.entries)
        outside_autocode = lambda rows: {
            row for row in rows if not row.endswith(".autocode/") and not row.startswith("!! .autocode/")
        }
        self.assertEqual(
            outside_autocode(before.status) | {f"?? {name}" for name in delivered},
            outside_autocode(after.status),
        )
        self.assertTrue((workspace / ".autocode" / "project.json").is_file())
        self.assertEqual(before.worktrees | ({tree} if tree else set()), after.worktrees)
        # No existing ref moves: the job never commits to the project's own branch.
        self.assertEqual({}, {ref: commit for ref, commit in before.refs.items() if after.refs.get(ref) != commit})
        added = {ref: commit for ref, commit in after.refs.items() if ref not in before.refs}
        branches = {ref for ref in added if ref.startswith("refs/heads/")}
        # The only new branch is the task worktree's own (autocode/<task>-<id>, docs/task-lanes.md).
        self.assertEqual({f"refs/heads/autocode/{tree.name}"} if tree else set(), branches)
        # Any other new ref is one of the run's code checkpoints, listed in its status view.
        checkpoints = {row["commit"] for row in view["code_checkpoints"]["rows"]}
        for ref in added.keys() - branches:
            self.assertTrue(ref.startswith("refs/autocode/code-checkpoints/") and added[ref] in checkpoints, ref)
        names = lambda directory: {entry.name for entry in directory.iterdir()}
        # Exactly the run and the runner's housekeeping beside it. A new runner file belongs in these sets
        # only if it is per-run housekeeping (a log, a lock); a record that coordinates runs does not.
        housekeeping = {".gitignore", "runs", "usage.jsonl", "usage.lock", "writer.lock"}
        if tree:  # the project keeps only the task worktree; the run and its housekeeping are inside it
            self.assertEqual(
                {".gitignore", "project-memory.lock", "project.json", "worktree-create.lock", "worktrees"},
                names(workspace / ".autocode"),
            )
            self.assertEqual({tree.name}, names(workspace / ".autocode" / "worktrees"))
            housekeeping.add("task-workspace.json")
        else:
            housekeeping.update({"project-memory.lock", "project.json"})
        self.assertEqual(housekeeping, names(run.workspace / ".autocode"))
        self.assertEqual({run.run_dir.name}, names(run.workspace / ".autocode" / "runs"))
        # The progressive record is written only when the Planner proposes progressive delivery,
        # which the fixture provider never does: this guards against writing it unconditionally.
        self.assertFalse((run.run_dir / "progressive").exists())
        self.assertNotIn("progressive", view)

    def test_plan_approval_assigns_the_selected_ignored_source_revision(self):
        # Existing deliverables must enter the assignment inventory only after
        # the real CLI approval; no saved state or approval flags are injected.
        originals = {"README.md": b"Existing documentation\n", "greet.py": b"# Existing greeting implementation\n"}
        for name, raw in originals.items():
            (self.workspace / name).write_bytes(raw)
        (self.workspace / "test_greet.py").write_text("# Existing regression tests\n")
        (self.workspace / "private-note.txt").write_text("Outside the approved deliverables\n")
        with (self.workspace / ".git/info/exclude").open("a") as exclude:
            exclude.write("/README.md\n/greet.py\n/private-note.txt\n")
        ignored = subprocess.check_output(["git", "-C", str(self.workspace), "check-ignore", *originals], text=True)
        self.assertEqual(set(originals), set(ignored.splitlines()))
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=60)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], {key: view[key] for key in ("status", "needs")})
        self.assertIsNone(view["current_task"])
        raw_source = util.snapshot(self.workspace)
        expected = source_snapshot.snapshot(self.workspace, paths=["greet.py", "test_greet.py", "README.md"])
        self.assertEqual(["README.md", "greet.py", "test_greet.py"], expected["source_paths"])
        for name in originals:
            self.assertNotIn(name, raw_source["files"])
            self.assertEqual(util.file_hash(self.workspace / name), expected["files"][name])
        self.assertNotIn("private-note.txt", expected["files"])
        self.assertNotEqual(raw_source["revision"], expected["revision"])

        approved = run.approve_plan(view["needs"]["token"])
        self.assertEqual("READY_TO_EXECUTE", approved["phase"], approved)
        # The public CLI status includes the complete bounded assignment.
        result = subprocess.run(
            [*run.command, "--workspace", str(self.workspace), "--run-dir", str(run.run_dir), "--status"],
            env={**os.environ, **self.env},
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        status = json.loads(result.stdout)
        task = status["current_task"]
        self.assertEqual(view["needs"]["token"], status["contract_token"])
        self.assertEqual(set(expected["source_paths"]), set(task["affected_paths"]))
        self.assertEqual(expected["revision"], task["source_revision"])
        self.assertEqual(originals, {name: (self.workspace / name).read_bytes() for name in originals})

    def use_question_preserving_planner(self, *, genuine_questions=0):
        # Reproduce the live Planner faithfully carrying a question from its handoff.
        provider = Path(self.env["PATH"].split(os.pathsep)[0]) / "codex"
        source = provider.read_text()
        amendment = textwrap.dedent("""
            if stage == "astra_discovery":
                questions = (contract.get("body") or {}).get("open_blocking_questions", [])
                if os.environ.get("FIXTURE_GENUINE_QUESTIONS"):
                    questions = [{"id": "readme-audience", "question": "Should the README target beginners or experienced Python users?",
                                  "why": "This fixture has a genuine unresolved documentation-audience decision.",
                                  "options": ["Beginners", "Experienced Python users"], "proposed_default": ""},
                                 {"id": "greeting-punctuation", "question": "Should the greeting end with an exclamation mark?",
                                  "why": "This fixture has a second genuine unresolved output decision.",
                                  "options": ["No punctuation", "Exclamation mark"], "proposed_default": ""},
                                 ][:int(os.environ["FIXTURE_GENUINE_QUESTIONS"])]
                questions = [dict(q, kind="decision", category="requested_outcome", delegable=False)
                             for q in questions]
                report["contract"]["open_blocking_questions"] = questions
                report["contract"].setdefault("initial_task", _planning_contract()["initial_task"])
                if questions:
                    report["contract"].update(technical_approach=[], milestones=[])
                    report["contract"]["initial_task"] = {
                        "objective": "", "affected_paths": [], "kind": "none", "milestone_id": "",
                        "requirements": [], "acceptance_criteria": [], "validation_plan": []}
        """)
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        self.assertIn(marker, source)
        provider.write_text(source.replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        if genuine_questions:
            self.env["FIXTURE_GENUINE_QUESTIONS"] = str(genuine_questions)

    def test_fresh_run_reaches_plan_approval_without_a_migration_question(self):
        self.use_question_preserving_planner()
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=60)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertFalse(view["done"])
        self.assertNotIn("migration-context", run.show_goal())
        self.assertFalse((self.workspace / "greet.py").exists(), "No implementation before approval")
        again = taskrun.TaskRun.attach(self.workspace, options=FIXTURE_OPTIONS, env=self.env)
        self.assertEqual(view["needs"], again.status()["needs"])
        with self.assertRaisesRegex(taskrun.TaskRunError, "approve plan exited"):
            again.approve_plan("not-the-displayed-token")

    def test_fresh_run_still_asks_a_genuine_planning_question(self):
        self.use_question_preserving_planner(genuine_questions=1)
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=60)
        view = run.status()
        self.assertEqual("answer", view["needs"]["kind"], view)
        self.assertEqual(["readme-audience"], [q["id"] for q in view["needs"]["questions"]])
        first = view["interaction_timing"]
        self.assertIsNotNone(first["first_question_at"])
        self.assertIsNotNone(first["first_question_seconds"])
        self.assertIsNone(first["first_plan_at"])
        self.assertIsNone(first["first_builder_at"])
        self.assertEqual(first, run.status()["interaction_timing"])
        self.assertFalse(view["done"])
        self.assertFalse((self.workspace / "greet.py").exists())

    def test_each_listed_question_is_answered_against_the_current_request(self):
        # An answer consumes the published AutoResolver request and the questions
        # left return under a new token, so one read before the first answer is stale (#382).
        self.use_question_preserving_planner(genuine_questions=2)
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=60)
        listed = run.status()["needs"]
        self.assertEqual(["readme-audience", "greeting-punctuation"], [q["id"] for q in listed["questions"]])
        for question in listed["questions"]:
            view = run.answer(question["id"], question["options"][0])
        self.assertEqual("continue", view["needs"]["kind"], view)

    def test_two_answers_use_printed_rotated_token_and_status_stays_json(self):
        self.use_question_preserving_planner(genuine_questions=2)
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=60)
        processes = []
        invoke = taskrun.run_process

        def capture(argv, **kwargs):
            process = invoke(argv, **kwargs)
            processes.append((argv, process))
            return process

        def printed_token(process):
            tokens = [
                line.removeprefix("Resolver token: ")
                for line in process.stderr.splitlines()
                if line.startswith("Resolver token: ")
            ]
            self.assertEqual(1, len(tokens), process.stderr)
            return tokens[0]

        with patch.object(taskrun, "run_process", side_effect=capture):
            before_status = self.footprint()
            initial = run.status()
            self.assertEqual(before_status, self.footprint())
            status = processes[-1][1]
            self.assertEqual(initial, json.loads(status.stdout)["view"])
            self.assertIn("readme-audience:", status.stderr)
            self.assertIn("greeting-punctuation:", status.stderr)
            first_token = printed_token(status)
            self.assertEqual(initial["needs"]["resolver_token"], first_token)
            first = initial["needs"]["questions"][0]
            remaining = run.answer(first["id"], first["options"][0], resolver_token=first_token)
            answer = next(process for argv, process in reversed(processes) if "--answer" in argv)
            next_token = printed_token(answer)
            self.assertNotEqual(first_token, next_token)
            self.assertEqual(remaining["needs"]["resolver_token"], next_token)
            self.assertIn("greeting-punctuation:", answer.stderr)
            self.assertNotIn("readme-audience:", answer.stderr)
            second = remaining["needs"]["questions"][0]
            with self.assertRaisesRegex(taskrun.TaskRunError, "exact current AutoResolver request and token"):
                run.answer(second["id"], second["options"][0], resolver_token=first_token)
            self.assertEqual(remaining["needs"], run.status()["needs"])
            final = run.answer(second["id"], second["options"][0], resolver_token=next_token)
            answer = next(process for argv, process in reversed(processes) if "--answer" in argv)
            self.assertIn("No questions left to answer.", answer.stderr)
            self.assertNotIn("Resolver token:", answer.stderr)
            self.assertEqual("continue", final["needs"]["kind"], final)
            self.assertEqual(initial["usage"], final["usage"], "Answers and status must not launch a model")
            self.assertFalse((self.workspace / "greet.py").exists())

    def test_documented_answer_loop_uses_each_pass_token_and_refuses_a_stale_one(self):
        self.use_question_preserving_planner(genuine_questions=2)
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=60)
        view = run.status()
        first_token = view["needs"]["resolver_token"]
        answered = []
        while view["needs"]["kind"] == "answer":  # the loop in docs/task-run.md
            need = view["needs"]
            question = need["questions"][0]
            if answered:  # an explicit token is the caller's binding: never silently refreshed
                with self.assertRaisesRegex(taskrun.TaskRunError, "exact current AutoResolver request and token"):
                    run.answer(question["id"], question["options"][0], resolver_token=first_token)
            view = run.answer(question["id"], question["options"][0], resolver_token=need.get("resolver_token"))
            answered.append(question["id"])
        self.assertEqual(["readme-audience", "greeting-punctuation"], answered)
        self.assertEqual("continue", view["needs"]["kind"], view)

    def test_joint_planning_upgrade_before_first_draft_reaches_approval_and_completes(self):
        run = taskrun.TaskRun.start(
            self.workspace,
            BRIEF,
            options=("--evidence-provenance", "fake", "--engine", "codex"),
            start_options=("--pause-after-stage",),
            env=self.env,
            timeout=60,
        )
        paused = run.status()
        self.assertEqual(("PAUSED_REQUESTED", "astra_discovery"), (paused["status"], paused["next_stage"]), paused)
        upgraded = taskrun.TaskRun(self.workspace, run.run_dir, options=FIXTURE_OPTIONS, env=self.env, timeout=120)
        view = upgraded.resume_paused()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertFalse((self.workspace / "greet.py").exists(), "Upgrade is not approval")
        with self.assertRaisesRegex(taskrun.TaskRunError, "approve plan exited"):
            upgraded.approve_plan("not-the-displayed-token")
        upgraded.approve_plan(view["needs"]["token"])
        self.assertEqual("TASK_COMPLETE", upgraded.advance_until_input()["status"])

    def test_follow_up_can_revise_a_protected_behavior_but_still_needs_new_plan_approval(self):
        request = "Add optional punctuation to the greeting API while preserving the current default."
        replacement = "Print Hello, NAME for one nonempty name and exit 0 by default; allow optional punctuation"
        provider = Path(self.env["PATH"].split(os.pathsep)[0]) / "codex"
        script = provider.read_text()
        anchor = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        self.assertIn(anchor, script)
        amendment = (
            textwrap.dedent("""
            if stage in ("astra_discovery", "glm_revise", "astra_finalize") and os.environ.get("FOLLOW_UP_PROVENANCE_TEST") == "1":
                previous = "Print Hello, NAME for one nonempty name and exit 0"
                event = next((e for e in data.get("brief_feedback", []) if e.get("text") == REQUEST), {})
                report["contract"].setdefault("initial_task", _planning_contract()["initial_task"])
                report["contract"]["required_behaviors"][0] = REPLACEMENT
                report["requirement_trace"][0]["evidence"] = REPLACEMENT
                # Re-cite after rewording so source_trace seals against the new corpus (#595).
                report["contract"] = _cite(report["contract"], data)
                if stage == "astra_finalize":
                    report["source_trace"] = _source_trace(report["contract"], data)
                behaviors = ((data.get("goal_contract") or {}).get("body") or {}).get("required_behaviors", [])
                if previous in behaviors or not behaviors:
                    report["contract_changes"] = [{"item": previous, "change": "reworded",
                        "replacement": REPLACEMENT, "basis": "user_feedback",
                        "answer_id": event.get("id", "feedback-unrecorded-follow-up"), "example_correction": None}]
        """)
            .replace("REQUEST", repr(request))
            .replace("REPLACEMENT", repr(replacement))
        )
        provider.write_text(script.replace(anchor, textwrap.indent(amendment, "    ") + "\n" + anchor, 1))
        run = taskrun.TaskRun.start(
            self.workspace,
            BRIEF,
            options=FIXTURE_OPTIONS,
            start_options=("--workflow", "build"),
            env=self.env,
            timeout=300,
        )
        first = run.status()["needs"]["token"]
        run.approve_plan(first)
        self.assertEqual("TASK_COMPLETE", run.advance_until_input()["status"])
        accepted = {name: (self.workspace / name).read_bytes() for name in ("greet.py", "test_greet.py", "README.md")}
        run.follow_up(request)
        self.env["FOLLOW_UP_PROVENANCE_TEST"] = "1"
        self.env["LIVE_FIXTURE_CLARITY"] = "clear"
        gathering = taskrun.TaskRun(
            self.workspace, run.run_dir, options=(*FIXTURE_OPTIONS, "--pause-after-stage"), env=self.env, timeout=300
        )
        boundary = gathering.advance()
        # The follow-up began at Requirements (#1860), which refreshed the prior handoff and named a build.
        self.assertEqual(
            ("PAUSED_REQUESTED", "astra_discovery"), (boundary["status"], boundary["next_stage"]), boundary
        )
        # Reattach: retain the recorded request and authorization identity.
        run = taskrun.TaskRun(self.workspace, run.run_dir, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        view = run.resume_paused()
        self.assertEqual("AWAITING_GOAL_APPROVAL", view["status"], view)
        self.assertEqual(2, view["turn"])
        self.assertIn(replacement, run.show_goal())
        self.assertNotEqual(first, view["needs"]["token"])
        with self.assertRaises(taskrun.TaskRunError):
            run.approve_plan(first)
        self.assertEqual("approve_plan", run.status()["needs"]["kind"])
        self.assertEqual(accepted, {name: (self.workspace / name).read_bytes() for name in accepted})

    def test_usage_errors_are_not_mistaken_for_a_pause(self):
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        broken = taskrun.TaskRun(self.workspace, run.run_dir, options=("--no-such-flag",), env=self.env)
        with self.assertRaisesRegex(taskrun.TaskRunError, "unrecognized arguments"):
            broken.advance()

    def test_rejected_verification_change_is_reported_to_the_caller(self):
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        before = (run.run_dir / "state.json").read_bytes()
        broken = taskrun.TaskRun(
            self.workspace, run.run_dir, options=("--test-command", "python -m unittest"), env=self.env
        )
        with self.assertRaisesRegex(taskrun.TaskRunError, "Changing saved verification commands"):
            broken.resume_paused()
        self.assertEqual(before, (run.run_dir / "state.json").read_bytes())


class TaskRunErrorDetailTests(unittest.TestCase):
    """A refusal's reason survives a long recovery command after it (#1032)."""

    REASON = "Input rejected: Cross-model verification violated: the Builder and Tester share gpt-5.6-terra"
    GUIDANCE = "Next command: autocode --resume-paused --workspace " + "/very/long/workspace/path" * 60

    def failure(self, stdout="", stderr="", code=2):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        proc = subprocess.CompletedProcess(["autocode"], code, stdout, stderr)
        with patch.object(taskrun, "run_process", return_value=proc), self.assertRaises(taskrun.TaskRunError) as caught:
            run._act("answer")
        self.assertIs(proc, caught.exception.process)  # the whole output stays on the error
        return str(caught.exception)

    def test_the_reason_is_kept_ahead_of_long_guidance_on_either_stream(self):
        long = self.REASON + "\n" + self.GUIDANCE
        for streams in ({"stderr": long}, {"stdout": long}, {"stdout": long, "stderr": "warning: " + "x" * 900}):
            with self.subTest(streams=sorted(streams)):
                message = self.failure(**streams)
                self.assertIn("Cross-model verification violated", message)
                self.assertLessEqual(len(message), len("answer exited 2: ") + taskrun.DETAIL_CHARACTERS)

    def test_other_failures_keep_the_end_of_their_output(self):
        self.assertEqual(
            "answer exited 1: " + ("y" * 2000)[-taskrun.DETAIL_CHARACTERS :], self.failure(stderr="y" * 2000, code=1)
        )
        short = self.REASON + "\nNext command: autocode --resume-paused"
        self.assertEqual(f"answer exited 2: {short}", self.failure(stderr=short))


class TaskRunTimeoutDetailTests(unittest.TestCase):
    """A call that outlives its limit keeps the output that reached the caller before it (#992)."""

    def timed_out(self, call, stdout, stderr):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"), timeout=60)
        expired = subprocess.TimeoutExpired(["autocode"], 60, output=stdout, stderr=stderr)
        with (
            patch.object(taskrun, "run_process", side_effect=expired),
            self.assertRaises(taskrun.TaskRunError) as caught,
        ):
            call(run)
        self.assertIsNone(caught.exception.process)  # it did not finish: there is no exit code
        self.assertIsNone(run.last_advance)
        return caught.exception

    def test_a_status_with_no_output_says_none_reached_the_caller(self):
        # A plain captured run's partial streams are bytes, None when nothing was read. The
        # message must not say the CLI printed nothing: its unflushed stdout never arrives.
        for stdout, stderr in ((None, None), (b"", b"  \n")):
            with self.subTest(stdout=stdout, stderr=stderr):
                error = self.timed_out(taskrun.TaskRun.status, stdout, stderr)
                self.assertEqual(
                    "status did not finish within 60 s; no output reached the caller before the limit", str(error)
                )
                self.assertEqual(("", (stderr or b"").decode()), error.partial_output)

    def test_an_advancing_call_keeps_the_end_of_each_stream(self):
        progress = "".join(f"stage event {n}\n" for n in range(200)) + "last stage event"
        error = self.timed_out(taskrun.TaskRun.resume_paused, progress, "warning: slow host\n")
        message = str(error)
        self.assertTrue(message.startswith("resume did not finish within 60 s; stdout before the limit ended: "))
        self.assertIn("last stage event; stderr before the limit ended: warning: slow host", message)
        self.assertNotIn("stage event 0\n", message)
        self.assertLessEqual(len(message), 200 + taskrun.DETAIL_CHARACTERS)
        self.assertEqual((progress, "warning: slow host\n"), error.partial_output)  # whole, on the error

    def test_undecodable_partial_bytes_are_replaced_not_raised(self):
        error = self.timed_out(taskrun.TaskRun.status, b"view \xff", None)
        self.assertEqual(("view �", ""), error.partial_output)
        self.assertTrue(str(error).endswith("stdout before the limit ended: view �"))


class TaskRunClientTests(unittest.TestCase):
    def test_model_assignment_refreshes_every_saved_override_before_resume(self):
        cases = (
            (
                "sol",
                (
                    "--terra-model=builder",
                    "--sol-model",
                    "old-first",
                    "--feedback=keep --sol-model=old-in-note",
                    "--sol-model=old-middle",
                    "--sol-model",
                    "old-last",
                    "--sol-reasoning-effort=high",
                ),
                (
                    "--terra-model=builder",
                    "--sol-model",
                    "selected",
                    "--feedback=keep --sol-model=old-in-note",
                    "--sol-model=selected",
                    "--sol-model",
                    "selected",
                    "--sol-reasoning-effort=high",
                ),
            ),
            (
                "stuck_investigator",
                ("--investigator-model=old-first", "--investigator-model", "old-last"),
                ("--investigator-model=selected", "--investigator-model", "selected"),
            ),
        )
        for role, options, expected in cases:
            with self.subTest(role=role):
                run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"), options=options)
                with patch.object(run, "answer", return_value={}):
                    run.assign_model(role, "selected", resolver_token="current")
                with patch.object(run, "_invoke") as invoke, patch.object(run, "status", return_value={}):
                    run.resume_paused()
                invoke.assert_called_once_with("resume", "--resume-paused", "--no-chat", *expected, advancing=True)

    def test_model_assignment_preserves_other_investigator_pin_and_absent_flags(self):
        for role, options in (
            ("investigator", ("--investigator-model=stuck", "--investigator-model", "stuck-last")),
            ("architect", ("--pause-after-stage",)),
            ("sol", ("--terra-model=builder",)),
        ):
            with self.subTest(role=role):
                run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"), options=options)
                with patch.object(run, "answer", return_value={}):
                    run.assign_model(role, "selected", resolver_token="current")
                with patch.object(run, "_invoke") as invoke, patch.object(run, "status", return_value={}):
                    run.resume_paused()
                invoke.assert_called_once_with("resume", "--resume-paused", "--no-chat", *options, advancing=True)

    def test_permission_answer_keeps_timeout_overrides_current_on_next_advance(self):
        for options in (
            ("--max-stage-seconds", "3600", "--max-tool-seconds", "1800", "--pause-after-stage"),
            ("--max-stage-seconds=3600", "--max-tool-seconds=1800", "--pause-after-stage"),
        ):
            with self.subTest(options=options):
                run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"), options=options)
                need = {
                    "kind": "answer",
                    "questions": [{"id": "q-timeout"}],
                    "resolver_token": "token",
                    "operational_options": [
                        {
                            "option": "Extend",
                            "changes": {
                                "settings.limits.tool_timeout_seconds": 5400,
                                "settings.limits.stage_timeout_seconds": 6000,
                            },
                        },
                        {"option": "Decline", "changes": {}},
                    ],
                }
                with patch.object(run, "status", return_value={"needs": need}), patch.object(run, "_act"):
                    run.answer("q-timeout", "Extend")
                with patch.object(run, "_invoke") as invoke, patch.object(run, "status", return_value={}):
                    run.advance()
                arguments = invoke.call_args.args
                self.assertNotIn("1800", arguments)
                self.assertNotIn("3600", arguments)
                self.assertTrue("5400" in arguments or "--max-tool-seconds=5400" in arguments)
                self.assertTrue("6000" in arguments or "--max-stage-seconds=6000" in arguments)
                self.assertIn("--pause-after-stage", arguments)

    def test_failed_permission_answer_does_not_rewrite_client_timeout_options(self):
        run = taskrun.TaskRun(
            Path("/work/repo"), Path("/work/repo/.autocode/runs/one"), options=("--max-stage-seconds", "3600")
        )
        need = {
            "kind": "answer",
            "questions": [{"id": "q"}],
            "resolver_token": "token",
            "operational_options": [{"option": "Extend", "changes": {"settings.limits.stage_timeout_seconds": 6000}}],
        }
        with (
            patch.object(run, "status", return_value={"needs": need}),
            patch.object(run, "_act", side_effect=taskrun.TaskRunError("stale decision")),
            self.assertRaises(taskrun.TaskRunError),
        ):
            run.answer("q", "Extend", resolver_token="token")
        self.assertEqual(("--max-stage-seconds", "3600"), run.options)

    def test_stale_operational_token_cannot_apply_settings_without_updating_client_options(self):
        run = taskrun.TaskRun(
            Path("/work/repo"), Path("/work/repo/.autocode/runs/one"), options=("--max-stage-seconds", "3600")
        )
        need = {
            "kind": "answer",
            "questions": [{"id": "q"}],
            "resolver_token": "new-token",
            "operational_options": [{"option": "Extend", "changes": {"settings.limits.stage_timeout_seconds": 6000}}],
        }
        with (
            patch.object(run, "status", return_value={"needs": need}),
            patch.object(run, "_act") as act,
            self.assertRaisesRegex(taskrun.TaskRunError, "stale"),
        ):
            run.answer("q", "Extend", resolver_token="old-token")
        act.assert_not_called()
        self.assertEqual(("--max-stage-seconds", "3600"), run.options)

    def test_advancing_command_rejects_input_error_instead_of_treating_it_as_a_pause(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        rejected = subprocess.CompletedProcess(
            [], 2, stdout="Input rejected: pending report repair must be reconciled\n", stderr=""
        )
        with (
            patch.object(taskrun, "run_captured", return_value=rejected),
            self.assertRaisesRegex(taskrun.TaskRunError, "pending report repair"),
        ):
            run._invoke("retry failed stage", "--resume-paused", "--retry-failed-stage", advancing=True)

    def test_assign_model_preserves_long_rejection_reason_from_either_stream(self):
        reason = "Input rejected: Cross-model verification violated; Builder/Tester: identical model"
        guidance = "Next command: " + "/a/long/runtime/path/" * 100
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                run = taskrun.TaskRun(
                    Path("/work/repo"),
                    Path("/work/repo/.autocode/runs/one"),
                    options=("--sol-model", "gpt-5.6-sol"),
                )
                message = reason + "\n" + guidance
                completed = subprocess.CompletedProcess(
                    [],
                    2,
                    stdout=message if stream == "stdout" else "",
                    stderr=message if stream == "stderr" else "",
                )
                with (
                    patch.object(taskrun, "run_process", return_value=completed),
                    patch.object(run, "status") as status,
                    self.assertRaisesRegex(taskrun.TaskRunError, "Cross-model verification violated") as raised,
                ):
                    run.assign_model("sol", "gpt-5.6-terra", resolver_token="opaque-token")
                self.assertIs(completed, raised.exception.process)
                self.assertLess(len(str(raised.exception)), 900)
                self.assertNotIn("Next command:", str(raised.exception))
                self.assertEqual(("--sol-model", "gpt-5.6-sol"), run.options)
                status.assert_not_called()

    def test_retry_failed_stage_uses_explicit_inspected_retry(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        with patch.object(run, "_invoke") as invoke, patch.object(run, "status", return_value={}) as status:
            run.retry_failed_stage()
        invoke.assert_called_once_with(
            "retry failed stage", "--resume-paused", "--retry-failed-stage", "--no-chat", advancing=True
        )
        status.assert_called_once()

    def test_retry_report_uses_exact_attempt_and_explicit_resume(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        with patch.object(run, "_invoke") as invoke, patch.object(run, "status", return_value={}) as status:
            run.retry_report("001/sol_report_repair-02")
        invoke.assert_called_once_with(
            "retry report", "--resume-paused", "--retry-report", "001/sol_report_repair-02", "--no-chat", advancing=True
        )
        status.assert_called_once()

    def test_an_effort_answer_survives_a_global_effort_flag_on_resume(self):
        # #2057: a saved run re-applies --reasoning-effort to every role, so the answered role's own flag,
        # which wins over it, must travel beside it; an existing role flag is rewritten in place.
        for options, expected in (
            (
                ("--reasoning-effort", "medium"),
                ("--reasoning-effort", "medium", "--plan-reviewer-reasoning-effort=high"),
            ),
            (("--plan-reviewer-reasoning-effort", "medium"), ("--plan-reviewer-reasoning-effort", "high")),
            (("--fake",), ("--fake",)),
        ):
            with self.subTest(options=options):
                run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"), options=options)
                with patch.object(run, "_act", return_value={}) as act:
                    run.assign_effort("plan_reviewer", "high")
                act.assert_called_once_with("assign effort", "--answer", "effort-plan_reviewer=high")
                self.assertEqual(expected, run.options)

    def test_review_rejection_carries_note_and_keeps_token_out_of_argv(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        with patch.object(run, "_act", return_value={"needs": {"kind": "resume"}}) as act:
            view = run.reject_review("C1", "artifact-token", "Explain invalid input")
        act.assert_called_once_with(
            "reject review",
            "--reject-review",
            "C1",
            "--review-token",
            "-",
            "--review-note",
            "Explain invalid input",
            secret_env={"AUTOCODE_REVIEW_TOKEN": "artifact-token"},
        )
        self.assertEqual("resume", view["needs"]["kind"])

    def test_operational_response_uses_resolver_command_not_question_answer(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        with patch.object(run, "_act", return_value={"needs": {"kind": "resume"}}) as act:
            view = run.respond_operational("request-1", "token-1", "Cause identified")
        act.assert_called_once_with(
            "resolver response",
            "--resolver-request",
            "request-1",
            "--resolver-token",
            "-",
            "--resolver-response",
            "provide_information",
            "--resolver-message",
            "Cause identified",
            secret_env={"AUTOCODE_RESOLVER_TOKEN": "token-1"},
        )
        self.assertEqual("resume", view["needs"]["kind"])

    def test_accept_transport_change_requires_explicit_cli_flag(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        with patch.object(run, "_invoke") as invoke, patch.object(run, "status", return_value={}) as status:
            run.accept_transport_change()
        invoke.assert_called_once_with(
            "accept transport change", "--resume-paused", "--accept-transport-change", "--no-chat", advancing=True
        )
        status.assert_called_once()

    def test_answer_forwards_the_current_resolver_token(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        with patch.object(run, "_act", return_value={"needs": {"kind": "answer"}}) as act:
            view = run.answer("Q1", "Use addition.py", resolver_token="current-token")
        act.assert_called_once_with(
            "answer",
            "--answer",
            "Q1=Use addition.py",
            "--resolver-token",
            "-",
            secret_env={"AUTOCODE_RESOLVER_TOKEN": "current-token"},
        )
        self.assertEqual("answer", view["needs"]["kind"])

    def test_answer_without_resolver_token_uses_the_current_request_token(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        current = {
            "needs": {"kind": "answer", "questions": [{"id": "Q1"}, {"id": "Q2"}], "resolver_token": "current-token"}
        }
        with patch.object(run, "status", return_value=current), patch.object(run, "_act") as act:
            run.answer("Q2", "Use addition.py")
        act.assert_called_once_with(
            "answer",
            "--answer",
            "Q2=Use addition.py",
            "--resolver-token",
            "-",
            secret_env={"AUTOCODE_RESOLVER_TOKEN": "current-token"},
        )

    def test_answer_without_resolver_token_refuses_a_question_the_run_is_not_asking(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        asking = {"kind": "answer", "questions": [{"id": "Q1"}], "resolver_token": "current-token"}
        for needs, message in (
            (None, "not waiting for an answer to Q2"),
            ({"kind": "approve_plan", "token": "plan"}, "needs approve_plan"),
            (asking, r"questions \['Q1'\]"),
            ({**asking, "questions": [{"id": "Q2"}], "resolver_token": None}, "advance the run to publish one"),
        ):
            with (
                self.subTest(needs=needs),
                patch.object(run, "status", return_value={"needs": needs}),
                patch.object(run, "_act") as act,
                self.assertRaisesRegex(taskrun.TaskRunError, message),
            ):
                run.answer("Q2", "Use addition.py")
            act.assert_not_called()

    def test_start_preserves_cli_error_when_no_run_was_created(self):
        for stderr, stdout in (("autocode: GoCode authentication check failed", ""), ("", "autocode: startup failed")):
            with self.subTest(stderr=stderr, stdout=stdout), tempfile.TemporaryDirectory() as root:
                completed = subprocess.CompletedProcess([], 2, stdout=stdout, stderr=stderr)
                with (
                    patch.object(taskrun.TaskRun, "_invoke", return_value=completed),
                    self.assertRaises(taskrun.TaskRunError) as raised,
                ):
                    taskrun.TaskRun.start(root, "A sample task")
                self.assertIn("start exited 2 without creating a run", str(raised.exception))
                self.assertIn(stderr or stdout, str(raised.exception))

    def test_start_bounds_diagnostic_output_when_no_run_was_created(self):
        with tempfile.TemporaryDirectory() as root:
            completed = subprocess.CompletedProcess([], 2, stdout="", stderr="x" * 2000 + "cause")
            with (
                patch.object(taskrun.TaskRun, "_invoke", return_value=completed),
                self.assertRaises(taskrun.TaskRunError) as raised,
            ):
                taskrun.TaskRun.start(root, "A sample task")
            self.assertTrue(str(raised.exception).endswith("cause"))
            self.assertLess(len(str(raised.exception)), 1000)

    def test_show_goal_returns_the_current_displayed_brief(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        completed = subprocess.CompletedProcess([], 0, stdout="Build brief r2\nAcceptance criteria:\n  [AC1] Works\n")
        with patch.object(run, "_invoke", return_value=completed) as invoke:
            self.assertEqual(completed.stdout, run.show_goal())
        invoke.assert_called_once_with("show goal", "--show-goal")


# A stand-in for the autocode CLI: a start saves a run, any other call reports its working directory.
STUB_CLI = textwrap.dedent("""
    import json, os, pathlib, sys
    args = sys.argv[1:]
    workspace = pathlib.Path(args[args.index("--workspace") + 1])
    if "--restore" in args:
        print(json.dumps({"workspace": str(workspace), "run_dir": str(workspace / ".autocode" / "runs" / "two")}))
        sys.exit(0)
    if "--status" in args:
        print(json.dumps({"view": {"status": "RUNNING", "cwd": os.getcwd()}}))
        sys.exit(0)
    if "--in-place" in args:
        (workspace / ".autocode" / "runs" / "one").mkdir(parents=True, exist_ok=True)
        (workspace / ".autocode" / "runs" / "one" / "state.json").write_text("{}")
    print("advanced in " + os.getcwd())
    if os.environ.get("STUB_REJECT"):
        print("Input rejected: the run is busy", file=sys.stderr)
    sys.exit(2)
""")


class TaskRunProcessTests(unittest.TestCase):
    """The invocation a caller keeps (last_advance, TaskRunError.process) and the CLI's working directory."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.cwd = self.root / "elsewhere"
        self.cwd.mkdir()
        stub = self.root / "stub_cli.py"
        stub.write_text(STUB_CLI)
        self.command = (sys.executable, str(stub))

    def test_start_keeps_the_advancing_invocation_and_status_reads_do_not_replace_it(self):
        run = taskrun.TaskRun.start(self.workspace, "A task", command=self.command, cwd=self.cwd)
        started = run.last_advance
        self.assertEqual((2, f"advanced in {self.cwd}\n"), (started.returncode, started.stdout))
        self.assertEqual(self.cwd, run.cwd)
        self.assertEqual(str(self.cwd), run.status()["cwd"])
        self.assertIs(started, run.last_advance)
        run.advance()
        self.assertIsNot(started, run.last_advance)
        self.assertEqual(2, run.last_advance.returncode)

    def test_a_rejected_advance_carries_its_process(self):
        run = taskrun.TaskRun.start(self.workspace, "A task", command=self.command)
        rejected = taskrun.TaskRun(self.workspace, run.run_dir, command=self.command, env={"STUB_REJECT": "1"})
        with self.assertRaisesRegex(taskrun.TaskRunError, "the run is busy") as raised:
            rejected.advance()
        self.assertEqual(2, raised.exception.process.returncode)
        self.assertIs(raised.exception.process, rejected.last_advance)
        self.assertIn("--run-dir", raised.exception.process.args)

    def test_a_start_that_creates_no_run_carries_its_process(self):
        (self.workspace / ".autocode" / "runs" / "one").mkdir(parents=True)
        (self.workspace / ".autocode" / "runs" / "one" / "state.json").write_text("{}")
        with self.assertRaisesRegex(taskrun.TaskRunError, "without creating a run") as raised:
            taskrun.TaskRun.start(self.workspace, "A task", command=self.command)
        self.assertEqual(2, raised.exception.process.returncode)

    def test_cwd_defaults_to_the_callers_and_attach_accepts_one(self):
        taskrun.TaskRun.start(self.workspace, "A task", command=self.command)
        attached = taskrun.TaskRun.attach(self.workspace, command=self.command, cwd=self.cwd)
        self.assertEqual((self.cwd, None), (attached.cwd, attached.last_advance))
        self.assertEqual(str(self.cwd), attached.status()["cwd"])
        plain = taskrun.TaskRun.attach(self.workspace, command=self.command)
        self.assertIsNone(plain.cwd)
        self.assertEqual(os.getcwd(), plain.status()["cwd"])

    def test_a_start_rejected_after_saving_its_run_names_that_run(self):
        with self.assertRaisesRegex(taskrun.TaskRunError, "the run is busy") as raised:
            taskrun.TaskRun.start(self.workspace, "A task", command=self.command, env={"STUB_REJECT": "1"})
        self.assertEqual(self.workspace / ".autocode" / "runs" / "one", raised.exception.run_dir)
        self.assertEqual(2, raised.exception.process.returncode)

    def test_a_call_that_cannot_run_is_a_task_run_error(self):
        run = taskrun.TaskRun(
            self.workspace,
            self.workspace / ".autocode" / "runs" / "one",
            command=self.command,
            cwd=self.root / "removed",
        )
        with self.assertRaisesRegex(taskrun.TaskRunError, "status could not run: .*No such file") as raised:
            run.status()
        self.assertIsNone(raised.exception.process)
        with self.assertRaisesRegex(taskrun.TaskRunError, "start could not run") as raised:
            taskrun.TaskRun.start(self.workspace, "A task", command=self.command, cwd=self.root / "removed")
        self.assertIsNone(raised.exception.run_dir)

    def test_attach_skips_the_runs_noted_before_the_start(self):
        before = taskrun.TaskRun.runs_in(self.workspace)
        self.assertEqual([], before)
        mine = taskrun.TaskRun.start(self.workspace, "A task", command=self.command)
        theirs = self.workspace / ".autocode" / "runs" / "theirs"
        theirs.mkdir()
        (theirs / "state.json").write_text("{}")
        self.assertEqual(sorted([mine.run_dir, theirs]), taskrun.TaskRun.runs_in(self.workspace))
        with self.assertRaises(taskrun.TaskRunError):
            taskrun.TaskRun.attach(self.workspace, command=self.command)
        self.assertIsNone(
            taskrun.TaskRun.attach(self.workspace, command=self.command, exclude=[str(mine.run_dir), theirs])
        )
        self.assertEqual(
            mine.run_dir, taskrun.TaskRun.attach(self.workspace, command=self.command, exclude=[theirs]).run_dir
        )

    def test_a_restored_continuation_keeps_the_working_directory(self):
        run = taskrun.TaskRun.start(self.workspace, "A task", command=self.command, cwd=self.cwd)
        continuation = run.restore_checkpoint("checkpoint-1", "token", "request-1")
        self.assertEqual(
            (self.workspace / ".autocode" / "runs" / "two", self.cwd), (continuation.run_dir, continuation.cwd)
        )
        self.assertEqual(str(self.cwd), continuation.status()["cwd"])

    def test_status_errors_carry_the_status_process(self):
        run = taskrun.TaskRun(
            self.workspace,
            self.workspace / ".autocode" / "runs" / "one",
            command=(sys.executable, "-c", "import sys; sys.exit(1)"),
        )
        with self.assertRaises(taskrun.TaskRunError) as raised:
            run.status()
        self.assertEqual(1, raised.exception.process.returncode)
        self.assertIsNone(run.last_advance)

    def test_timeout_stops_detached_workers_and_retains_saved_run_identity(self):
        self.assert_owned_cleanup("timeout")

    def test_sigterm_stops_detached_workers_and_restores_caller_handler(self):
        self.assert_owned_cleanup("sigterm")

    def assert_owned_cleanup(self, trigger):
        ready = self.root / "ready"
        os.mkfifo(ready)
        ready_fd = os.open(ready, os.O_RDWR)
        self.addCleanup(os.close, ready_fd)
        stub = self.root / "timeout_cli.py"
        stub.write_text(
            textwrap.dedent("""
            import json, os, pathlib, subprocess, sys
            args = sys.argv[1:]
            workspace = pathlib.Path(args[args.index('--workspace') + 1])
            run = workspace / '.autocode/runs/saved-before-timeout'
            run.mkdir(parents=True)
            (run / 'state.json').write_text('{}')
            worker = subprocess.Popen([sys.executable, '-c',
                'import os; reader,writer=os.pipe(); os.read(reader,1)'], start_new_session=True)
            (workspace / 'worker.json').write_text(json.dumps({'pid': worker.pid}))
            print('saved the run; waiting', flush=True)
            with open(os.environ['READY_FIFO'], 'w') as signal:
                signal.write('R')
            reader, writer = os.pipe()
            os.read(reader, 1)
        """)
        )
        sentinel = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
        supervisor_wait = captured_process.supervisor.wait
        real_clock = captured_process.supervisor.time.monotonic
        previous_handler = signal.getsignal(signal.SIGTERM)
        receipt = None

        def stop_after_fixture_is_ready(child, timeout):
            nonlocal receipt
            self.assertEqual(b"R", os.read(ready_fd, 1))
            if trigger == "sigterm":
                worker_pid = json.loads((self.workspace / "worker.json").read_text())["pid"]
                sample = processes.ProcessTree.sample
                interrupted = False

                def interrupt_owned_sample(tree, *args, **kwargs):
                    nonlocal interrupted, receipt
                    result = sample(tree, *args, **kwargs)
                    if worker_pid in tree.known and not interrupted:
                        interrupted = True
                        receipt = {"owned": list(tree.known.values())}
                        os.kill(os.getpid(), signal.SIGTERM)
                    return result

                # Signal only after the supervisor has recorded the detached
                # worker and entered the wait's cleanup-protected loop.
                with patch.object(processes.ProcessTree, "sample", interrupt_owned_sample):
                    return supervisor_wait(child, timeout)
            first = True

            def deadline_clock():
                nonlocal first
                now = real_clock()
                if first:
                    first = False
                    return now
                return now + timeout + 1

            # Advance the deadline without waiting for it in real time. Process
            # cleanup still uses the real monotonic clock after this offset.
            with patch.object(captured_process.supervisor.time, "monotonic", deadline_clock):
                code, expired, receipt = supervisor_wait(child, timeout)
            return code, expired, receipt

        try:
            expected_error = (
                self.assertRaisesRegex(taskrun.TaskRunError, "start did not finish within 60 s")
                if trigger == "timeout"
                else self.assertRaises(KeyboardInterrupt)
            )
            with (
                patch.object(captured_process.supervisor, "wait", stop_after_fixture_is_ready),
                expected_error as raised,
            ):
                taskrun.TaskRun.start(
                    self.workspace,
                    "A task",
                    command=(sys.executable, str(stub)),
                    env={"READY_FIFO": str(ready)},
                    timeout=60,
                )
            saved_run = self.workspace / ".autocode/runs/saved-before-timeout"
            self.assertEqual([saved_run], taskrun.TaskRun.runs_in(self.workspace))
            if trigger == "timeout":
                self.assertIsNone(raised.exception.process)
                self.assertEqual(saved_run, raised.exception.run_dir)
                # The supervisor's partial output, read after cleanup, stays on the error (#992).
                self.assertEqual(("saved the run; waiting\n", ""), raised.exception.partial_output)
                self.assertIn("stdout before the limit ended: saved the run; waiting", str(raised.exception))
            worker_pid = json.loads((self.workspace / "worker.json").read_text())["pid"]
            self.assertIn(worker_pid, {row["pid"] for row in receipt["owned"]})
            self.assertEqual([], processes.live_processes(receipt["owned"]))
            self.assertIsNone(sentinel.poll())
            self.assertIs(previous_handler, signal.getsignal(signal.SIGTERM))
        finally:
            sentinel.stdin.close()
            sentinel.wait(timeout=5)

    def test_captured_cli_can_be_driven_from_a_worker_thread(self):
        with ThreadPoolExecutor(max_workers=1) as worker:
            result = worker.submit(
                captured_process.run, [sys.executable, "-c", "print('thread caller')"], timeout=60
            ).result(timeout=10)
        self.assertEqual((0, "thread caller\n", ""), (result.returncode, result.stdout, result.stderr))

    def test_supervision_failure_is_a_task_run_error(self):
        with (
            patch.object(taskrun, "run_captured", side_effect=processes.ProcessError("ownership unavailable")),
            self.assertRaisesRegex(taskrun.TaskRunError, "could not supervise.*ownership unavailable") as raised,
        ):
            taskrun.TaskRun.start(self.workspace, "A task", command=self.command)
        self.assertIsNone(raised.exception.process)
        self.assertIsNone(raised.exception.run_dir)


_MIXED_TRANSPORT_FIXTURE = r"""#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

name = Path(sys.argv[0]).name
log = Path('__LOG__')
models = (['fixture-claude'] if name in ('fixture_command', 'kilocode') else
          ['zai-coding-plan/glm-5.3-flash', 'zai-coding-plan/glm-5.3',
           'xiaomi-token-plan-sgp/mimo-v2.6-pro'])
def record(value):
    with log.open('a') as stream:
        stream.write(json.dumps({'tool': name, **value}) + '\n')
if sys.argv[1:2] == ['debug']:
    print('This transport fixture is not a native Kilo policy runtime', file=sys.stderr)
    raise SystemExit(2)
if sys.argv[1:] == ['--version']:
    print('1.16.0')
    raise SystemExit(0)
if sys.argv[1:] == ['models']:
    record({'operation': 'models', 'models': models})
    print('\n'.join(models))
    raise SystemExit(0)
if sys.argv[1:3] == ['auth', 'list']:
    print('● Anthropic api')
    raise SystemExit(0)
prompt = sys.stdin.read()
data = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
model = sys.argv[sys.argv.index('--model') + 1]
effort_flag = '--variant' if '--variant' in sys.argv else '--effort'
effort = sys.argv[sys.argv.index(effort_flag) + 1]
if model not in models:
    record({'operation': 'refused', 'model': model})
    raise SystemExit(9)
session_arg = sys.argv[sys.argv.index('--session') + 1] if '--session' in sys.argv else None
session = session_arg or (name + '_' + uuid.uuid4().hex)
record({'operation': 'launch', 'stage': data['stage'], 'model': model, 'effort': effort,
        'session': session, 'resume_session': session_arg, 'argv': sys.argv[1:]})
spec = importlib.util.spec_from_file_location('mixed_greeting_fixture', '__FIXTURE__')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
# Reuse the deterministic greeting provider, never another test or a model.
def prompt_schema(text):
    # OpenCode and Kilo carry the output schema in the prompt, not as --output-schema: a compact JSON line
    # just before the handoff data, or a configured tool's TOOL OUTPUT CONTRACT block. The fixture reads
    # it to answer in the current protocol (a retained final review, #1858).
    head = text.split('\nCURRENT HANDOFF DATA\n', 1)[0]
    if '\nTOOL OUTPUT CONTRACT\n' in head:
        contract = head.rsplit('\nTOOL OUTPUT CONTRACT\n', 1)[1]
        return json.JSONDecoder().raw_decode(contract[contract.index('\n{') + 1:])[0]
    last = (head.rstrip().splitlines() or [''])[-1]
    return json.loads(last) if last.startswith('{') else {}
with tempfile.TemporaryDirectory(dir=log.parent) as temporary:
    output = Path(sys.argv[sys.argv.index('--report') + 1]) if '--report' in sys.argv else Path(temporary) / 'report.json'
    schema_path = Path(temporary) / 'schema.json'
    schema_path.write_text(json.dumps(prompt_schema(prompt)))
    sys.argv = ['fixture', '-o', str(output), '--output-schema', str(schema_path),
                *(['resume', session] if session_arg else [])]
    original_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(prompt)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            code = fixture.main()
    finally:
        sys.stdin = original_stdin
    if code:
        raise SystemExit(code)
    report = json.loads(output.read_text())
    reviews = [json.loads(line) for line in log.read_text().splitlines()
               if json.loads(line).get('operation') == 'launch'
               and json.loads(line).get('stage') == 'astra_review']
    if data['stage'] == 'astra_review' and len(reviews) == 1:
        # A deliberate fake permission stop gives the real CLI one continuation
        # on the same reader role. It grants no artifact/product acceptance.
        report.update(status='BLOCKED', blocker='Await the fixture read-only confirmation permission',
                      user_request={'kind': 'permission',
                                    'discovered': 'The fixture requires an explicit read-only confirmation decision.',
                                    'impact': 'The final reader must wait before repeating its existing confirmation.',
                                    'decision_needed': 'Allow the existing read-only greeting confirmation?',
                                    'options': ['Allow', 'Decline'],
                                    'proposed_delta': 'No goal, scope, criterion, or behavior change.'})
    commands = [json.loads(line)['item'] for line in captured.getvalue().splitlines()
                if json.loads(line).get('type') == 'item.completed']
    rows = [{'type': 'step_start', 'sessionID': session,
             'part': {'id': 'start', 'messageID': 'message'}}]
    for item in commands:
        rows.append({'type': 'tool_use', 'sessionID': session,
                     'part': {'id': item['id'], 'messageID': 'message', 'tool': 'bash',
                              'state': {'status': 'completed', 'input': {'command': item['command']},
                                        'metadata': {'exit': item['exit_code']},
                                        'output': item['aggregated_output']}}})
    rows += [{'type': 'text', 'sessionID': session,
              'part': {'id': 'report', 'messageID': 'message', 'text': json.dumps(report)}},
             {'type': 'step_finish', 'sessionID': session,
              'part': {'id': 'finish', 'messageID': 'message', 'reason': 'stop',
                       'tokens': {'input': 10, 'output': 10, 'reasoning': 0,
                                  'cache': {'read': 0, 'write': 0}}}}]
    for row in rows:
        print(json.dumps(row), flush=True)
"""


class MixedRoleTransportTaskRunTests(unittest.TestCase):
    """Real public CLI, approval, concrete fake commands and retained session/report parsing.

    Provider executables are deterministic scripts with disjoint catalogues. No
    controller/configure/admission/report function or result is mocked. This is
    ordinary transport qualification, never native isolation or image acceptance.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mixed-role-taskrun-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", self.workspace], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                self.workspace,
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            ],
            check=True,
        )
        binary = self.root / "bin"
        binary.mkdir()
        self.log = self.root / "provider-operations.jsonl"
        self.log.write_text("")
        script = _MIXED_TRANSPORT_FIXTURE.replace("__LOG__", str(self.log)).replace(
            "__FIXTURE__", str(HERE / "live_fixture_provider.py")
        )
        for name in ("opencode", "fixture_command"):
            executable = binary / name
            executable.write_text(script)
            executable.chmod(0o755)
        config_dir = self.root / "config" / "autocode" / "providers"
        config_dir.mkdir(parents=True)
        self.config = config_dir / "fixture_command.toml"
        self.config.write_text(
            'name = "fixture_command"\ncommand = ["fixture_command", "run", "--model", "{model}", "--effort", "{effort}", '
            '"--report", "{report}"]\nprompt = "stdin"\noutput = "opencode_events"\n'
            'resume = ["--session", "{session}"]\nmodels_command = ["fixture_command", "models"]\n'
            'version_command = ["fixture_command", "--version"]\n[roles]\n'
            + "\n".join(
                f'{role} = {{ model = "fixture-claude", effort = "high" }}'
                for role in ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")
            )
            + "\n"
        )
        self.env = {
            "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENCODE_PURE": "1",
        }
        self.options = (
            "--evidence-provenance",
            "fake",
            "--engine",
            "opencode",
            "--provider",
            "opencode",
            "--joint-planning",
            "--no-adaptive-planning",
            "--reasoning-effort",
            "high",
            "--requirements-model",
            "zai-coding-plan/glm-5.3-flash",
            "--requirements-reasoning-effort",
            "high",
            "--glm-model",
            "zai-coding-plan/glm-5.3",
            "--glm-reasoning-effort",
            "high",
            "--terra-model",
            "zai-coding-plan/glm-5.3",
            "--plan-reviewer-model",
            "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "--plan-reviewer-reasoning-effort",
            "high",
            "--resolver-model",
            "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "--resolver-reasoning-effort",
            "high",
            "--astra-model",
            "fixture-claude",
            "--sol-model",
            "fixture-claude",
            "--completion-model",
            "fixture-claude",
            "--pin-model-role",
            "astra",
            "--pin-model-role",
            "terra",
            "--pin-model-role",
            "sol",
            "--pin-model-role",
            "completion",
            "--max-parallel-builders",
            "1",
            "--allow-uncontained-tools",
        )
        self.start_options = tuple(
            value
            for role in ("astra", "sol", "completion")
            for value in ("--role-transport", role + "=fixture_command")
        )

    def operations(self, kind=None):
        values = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [row for row in values if kind is None or row["operation"] == kind]

    def start(self):
        return taskrun.TaskRun.start(
            self.workspace, BRIEF, options=self.options, start_options=self.start_options, env=self.env, timeout=300
        )

    def test_public_start_approval_stage_dispatch_reports_sessions_and_reattach(self):
        run = self.start()
        before = run.status()
        self.assertEqual("approve_plan", before["needs"]["kind"], before)
        self.assertEqual("zai-coding-plan/glm-5.3-flash", before["routes"]["requirements"]["model"])
        self.assertEqual("zai-coding-plan/glm-5.3", before["routes"]["glm"]["model"])
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", before["routes"]["plan_reviewer"]["model"])
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", before["routes"]["resolver"]["model"])
        self.assertEqual("fixture-claude", before["routes"]["sol"]["model"])
        # Reattach without repeating the immutable start-only selection.
        attached = taskrun.TaskRun(self.workspace, run.run_dir, options=self.options, env=self.env, timeout=300)
        attached.approve_plan(before["needs"]["token"])
        held = attached.advance_until_input()
        self.assertEqual("answer", held["needs"]["kind"], held)
        self.assertEqual("permission", held["needs"]["request_kind"])
        question = held["needs"]["questions"][0]
        attached.answer(question["id"], "Allow")
        result = attached.advance_until_input()
        self.assertEqual("TASK_COMPLETE", result["status"], result)
        self.assertEqual(before["routes"], result["routes"])
        launches = self.operations("launch")
        self.assertTrue(launches)
        self.assertTrue(all(row["session"] for row in launches))
        # The person set --reasoning-effort high, so the Builder's light first attempt (#1710) leaves every stage at high.
        self.assertEqual({"high"}, {row["effort"] for row in launches})
        self.assertTrue(all(row["session"].startswith(row["tool"] + "_") for row in launches))
        self.assertTrue(all(row["resume_session"] in (None, row["session"]) for row in launches))
        expected = {
            "requirements_gather": ("opencode", "zai-coding-plan/glm-5.3-flash"),
            "astra_discovery": ("opencode", "zai-coding-plan/glm-5.3"),
            "astra_challenge": ("opencode", "xiaomi-token-plan-sgp/mimo-v2.6-pro"),
            "terra": ("opencode", "zai-coding-plan/glm-5.3"),
            "sol": ("fixture_command", "fixture-claude"),
            "astra_review": ("fixture_command", "fixture-claude"),
        }
        for stage, wanted in expected.items():
            actual = [row for row in launches if row["stage"] == stage]
            self.assertTrue(actual, stage)
            self.assertTrue(all((row["tool"], row["model"]) == wanted for row in actual), actual)
        readers = [row for row in launches if row["stage"] == "astra_review"]
        self.assertEqual(2, len(readers), readers)
        self.assertEqual("fixture_command", readers[0]["tool"])
        self.assertIsNone(readers[0]["resume_session"])
        self.assertEqual(readers[0]["session"], readers[1]["resume_session"])
        self.assertEqual(readers[0]["session"], readers[1]["session"])
        self.assertIn("--session", readers[1]["argv"])
        self.assertNotIn(readers[0]["session"], {row["session"] for row in launches if row["tool"] == "opencode"})
        catalogues = self.operations("models")
        self.assertTrue(any(row["tool"] == "opencode" for row in catalogues))
        self.assertTrue(any(row["tool"] == "fixture_command" for row in catalogues))
        self.assertTrue(all("fixture-claude" not in row["models"] for row in catalogues if row["tool"] == "opencode"))
        evidence = attached.evidence_report()["document"]
        stages = {row["stage"]: row for row in evidence["attempts"] if not row["runner_owned"]}
        self.assertEqual("fixture-claude", stages["sol"]["model"])
        self.assertEqual(0, stages["sol"]["exit_code"])
        self.assertEqual("TASK_COMPLETE", taskrun.TaskRun.attach(self.workspace, env=self.env).status()["status"])

    def test_unqualified_kilo_reader_refuses_before_provider_launch(self):
        # A generic command fixture must not impersonate Kilo's native policy
        # handshake. Genuine Kilo execution is covered in test_kilocode_policy.
        executable = self.root / "bin" / "kilocode"
        executable.write_text((self.root / "bin" / "fixture_command").read_text())
        executable.chmod(0o755)
        (self.config.parent / "kilocode.toml").write_text(
            self.config.read_text().replace("fixture_command", "kilocode")
        )
        self.start_options = tuple(
            value for role in ("astra", "sol", "completion") for value in ("--role-transport", role + "=kilocode")
        )
        if sys.platform.startswith("linux"):
            # Version admission now refuses this generic 1.16.0 fixture before
            # a run can reach the later native read-only policy handshake.
            with self.assertRaisesRegex(taskrun.TaskRunError, "kilocode requires Kilo") as caught:
                self.start()
            self.assertIn("found 1.16.0", str(caught.exception))
            self.assertIn("no provider request was launched", str(caught.exception))
            self.assertFalse(self.operations("launch"))
            return
        run = self.start()
        before = run.status()
        self.assertEqual("approve_plan", before["needs"]["kind"])
        run.approve_plan(before["needs"]["token"])
        held = run.advance_until_input()
        self.assertEqual("PAUSED_TOOL_CONTAINMENT", held["status"])
        self.assertIn("Kilo readonly policy could not be resolved", held["needs"]["reason"])
        self.assertFalse([row for row in self.operations("launch") if row["tool"] == "kilocode"])

    def test_saved_override_and_selected_identity_drift_refuse_before_any_launch(self):
        run = self.start()
        launches = len(self.operations("launch"))
        saved = taskrun.TaskRun(
            self.workspace, run.run_dir, options=(*self.options, *self.start_options), env=self.env, timeout=300
        )
        with self.assertRaisesRegex(taskrun.TaskRunError, "only available when starting a new run"):
            saved.advance()
        self.assertEqual(launches, len(self.operations("launch")))
        attached = taskrun.TaskRun(self.workspace, run.run_dir, options=self.options, env=self.env, timeout=300)
        attached.approve_plan(run.status()["needs"]["token"])
        # Change only the owned fake CLI config; never private checkpoint bytes.
        self.config.write_text(self.config.read_text() + "\n# changed fake identity\n")
        self.assertEqual("PAUSED_TRANSPORT_CHANGED", attached.advance()["status"])
        self.assertEqual(launches, len(self.operations("launch")))


if __name__ == "__main__":
    unittest.main()
