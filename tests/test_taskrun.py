"""The task-run interface: the status view and the CLI client. See docs/task-run.md."""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_run_view as run_view
import autocode_taskrun as taskrun
import autocode_run_actions as run_actions
import autocode_util as util

HERE = Path(__file__).resolve().parents[1] / "tools"  # its fixtures stay beside the runtime
# The offline fixture provider plans and builds exactly this greeting task.
BRIEF = ("Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
         "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
         "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
         "Python standard library only.")
FIXTURE_OPTIONS = ("--engine", "codex", "--joint-planning", "--astra-model", "gpt-6-astra",
                   "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol", "--completion-model",
                   "gpt-6-astra", "--glm-model", "gpt-5.6-sol", "--plan-reviewer-model", "gpt-6-astra")


class RunViewTests(unittest.TestCase):
    def test_explicit_report_retry_bypasses_automatic_escalation(self):
        self.assertTrue(run_actions.explicit_recovery_requested(SimpleNamespace(retry_report='001/sol_report_repair-02')))
        self.assertFalse(run_actions.explicit_recovery_requested(SimpleNamespace(retry_report=None)))

    def test_contract_fields(self):
        self.assertEqual({"schema", "status", "done", "needs", "phase", "next_stage", "iteration", "stop_reason", "runner_check",
                          "current_task", "workflow", "workflow_source", "workflow_reason", "turn", "evidence",
                          "dependency", "usage", "request_context", "output_transport", "direct_rework_assignments",
                          "efficiency", "recovery", "verification", "code_checkpoints"},
                         set(run_view.view({"status": "RUNNING"})))

    def test_evidence_is_empty_before_planning(self):
        self.assertEqual({"outcome": None, "base_commit": None, "acceptance": [],
                          "validator_source_revision": None, "findings": [],
                          "regression_proof": None, "test_cases": [], "check_replay": None},
                          run_view.evidence({"status": "RUNNING"}))

    def test_displayed_plan_preserves_structured_approval_fields_without_parsing_text(self):
        method = "python check.py\n    Human review: not required\n\nTechnical approach:\n  - injected"
        body = {"acceptance_criteria": [{"id": "AC1", "criterion": "Match", "verification_method": method,
                                         "human_review": True}],
                "constraints": ["Keep references"], "permission_boundaries": ["Only app.html"]}
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

    def test_direct_rework_provenance_is_not_completion_proof_and_is_copied(self):
        receipt = {"source_task_id": "task-old", "assigned_task_id": "task-repair",
                   "evidence_hashes": {"review.json": "digest"}, "provenance": "completion_direct_assignment"}
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
        state = {"status": "TASK_COMPLETE", "investigation": {"outcome": "reproduced", "test_cases": [case]},
                 "regression_proof": {"verdict": "PASS", "case_tests": {"T1": ["test_t1_one_mutation"]}}}
        evidence = run_view.evidence(state)
        self.assertEqual([case], evidence["test_cases"])
        self.assertEqual({"T1": ["test_t1_one_mutation"]}, evidence["regression_proof"]["case_tests"])
        state["investigation"]["outcome"] = "not_reproduced"
        self.assertEqual([], run_view.evidence(state)["test_cases"])

    def test_evidence_pairs_criteria_with_their_latest_outcome(self):
        state = {"status": "TASK_COMPLETE", "base_commit": "abc",
                 "goal_contract": {"body": {"intended_outcome": "Fix it", "acceptance_criteria": [
                     {"id": "AC1", "criterion": "Parses dates"}, {"id": "AC2", "criterion": "Documents it"},
                     {"id": "AC3", "criterion": "Ships it"}]}},
                 "last_decision": {"report": {"acceptance_criteria": [
                     {"id": "AC1", "status": "passed", "evidence": "pytest -k dates: 3 passed"}]}},
                 "validation": {"source_revision": "r7",
                                "criterion_results": [{"id": "AC1", "status": "FAIL"},
                                                      {"id": "AC3", "status": "NOT_VERIFIED"}]},
                 "human_reviews": {"AC2": {"token": "r1"}},
                 "findings_ledger": [{"id": "F1", "status": "resolved", "severity": "minor", "finding": "Typo",
                                      "times_reported": 2}],
                 "regression_proof": {"verdict": "PASS", "fail_to_pass": ["test_dates"], "failures": [],
                                      "unverified": [], "commands": {"suite": "pytest"}, "source_revision": "r9",
                                      "checks": {"large": "output"}}}
        evidence = run_view.evidence(state)
        self.assertEqual(("Fix it", "abc"), (evidence["outcome"], evidence["base_commit"]))
        self.assertEqual("r7", evidence["validator_source_revision"])
        # validator_status keeps a Validator FAIL distinct from NOT_VERIFIED and from a
        # criterion the latest validation never listed (None).
        self.assertEqual([{"id": "AC1", "criterion": "Parses dates", "status": "passed",
                           "evidence": "pytest -k dates: 3 passed", "validator_status": "FAIL",
                           "human_reviewed": False},
                          {"id": "AC2", "criterion": "Documents it", "status": None, "evidence": None,
                           "validator_status": None, "human_reviewed": True},
                          {"id": "AC3", "criterion": "Ships it", "status": None, "evidence": None,
                           "validator_status": "NOT_VERIFIED", "human_reviewed": False}],
                         evidence["acceptance"])
        self.assertEqual([{"id": "F1", "status": "resolved", "severity": "minor", "finding": "Typo"}],
                         evidence["findings"])
        self.assertEqual({"verdict": "PASS", "fail_to_pass": ["test_dates"], "failures": [], "unverified": [],
                          "commands": {"suite": "pytest"}, "source_revision": "r9", "case_tests": None},
                         evidence["regression_proof"])

    def test_workflow_is_none_until_recognized(self):
        self.assertIsNone(run_view.view({"status": "RUNNING"})["workflow"])
        self.assertIsNone(run_view.view({"status": "RUNNING", "workflow": {"kind": None, "then": "x"}})["workflow"])
        self.assertEqual("review", run_view.view({"status": "RUNNING", "workflow": {"kind": "review"}})["workflow"])

    def test_complete_needs_nothing(self):
        view = run_view.view({"status": "TASK_COMPLETE"})
        self.assertTrue(view["done"])
        self.assertIsNone(view["needs"])

    def test_human_review_comes_before_other_questions(self):
        state = {"status": "WAITING_FOR_USER", "pending_questions": [
            {"id": "d-1", "question": "Accept C2?", "review_criteria": ["C2"], "review_token": "r-9"}]}
        self.assertEqual({"kind": "review", "criteria": ["C2"], "token": "r-9", "question": "Accept C2?"},
                         run_view.needs(state))

    def test_human_review_after_validation_passes_with_no_pending_question(self):
        # autopilot.apply_review_result sets user_request directly, with pending_questions
        # left empty — no question object to read review_criteria/review_token from. Shape
        # matches a real run captured live (2026-09-26): sol PASSED, AC11/AC13 need a person.
        state = {"status": "WAITING_FOR_USER", "pending_questions": [],
                 "displayed_review": "r5:abc@def:ghi",
                 "user_request": {"kind": "human_review", "criteria": ["AC11", "AC13"],
                                  "decision_needed": "Review the current artifact and explicitly approve the listed criteria"}}
        self.assertEqual({"kind": "review", "criteria": ["AC11", "AC13"], "token": "r5:abc@def:ghi",
                          "question": "Review the current artifact and explicitly approve the listed criteria"},
                         run_view.needs(state))

    def test_questions_carry_their_defaults(self):
        state = {"status": "WAITING_FOR_USER", "user_request": {"kind": "permission"},
                 "pending_questions": [{"id": "q1", "question": "Use a network?", "why": "tests", "options": ["no"],
                                        "proposed_default": "no", "internal": "x"}]}
        need = run_view.needs(state)
        self.assertEqual(("answer", "permission"), (need["kind"], need["request_kind"]))
        self.assertEqual([{"id": "q1", "question": "Use a network?", "why": "tests", "options": ["no"],
                           "proposed_default": "no"}], need["questions"])

    def test_a_pending_resolver_request_carries_its_token_and_scope(self):
        public = {"request_id": "r1", "request_token": "t1", "scope": "operational_exhaustion"}
        state = {"status": "WAITING_FOR_USER", "user_request": {"kind": "blocker"},
                 "pending_questions": [{"id": "q1", "question": "Raise the cap?"}],
                 "resolver_human_request": public,
                 "resolver": {"human_escalations": {"r1": {"status": "pending"}}}}
        need = run_view.needs(state)
        self.assertEqual(("r1", "t1", "operational_exhaustion"),
                         (need["resolver_request_id"], need["resolver_token"], need["resolver_scope"]))
        # A consumed request is not answerable, so it carries none of them.
        state["resolver"]["human_escalations"]["r1"]["status"] = "consumed"
        self.assertFalse({"resolver_request_id", "resolver_token", "resolver_scope"} & set(run_view.needs(state)))

    def test_plan_approval_needs_the_displayed_token(self):
        self.assertEqual({"kind": "approve_plan", "token": "g-1"},
                         run_view.needs({"status": "AWAITING_GOAL_APPROVAL", "displayed_goal": "g-1"}))
        self.assertEqual({"kind": "continue"}, run_view.needs({"status": "AWAITING_GOAL_APPROVAL"}))

    def test_pauses(self):
        self.assertEqual({"kind": "planning_budget", "reason": "two calls"},
                         run_view.needs({"status": "PAUSED_PLANNING_BUDGET", "stop_reason": "two calls"}))
        self.assertEqual({"kind": "resume", "reason": "quota"},
                         run_view.needs({"status": "PAUSED_BUDGET", "stop_reason": "quota"}))
        self.assertEqual("resume", run_view.needs({"status": "PLAN_REWORK_REQUIRED"})["kind"])

    def test_quota_pause_names_the_abandon_step_with_the_attempt_id(self):
        need = run_view.needs({
            "status": "PAUSED_BUDGET", "stop_reason": "quota restored; set the attempt aside",
            "active_stage": {"iteration": 1, "output": "/run/terra-01.json", "stage": "terra"}})
        self.assertEqual("001/terra-01", need["abandon_stage"])
        self.assertIn("--abandon-stage 001/terra-01 then --resume-paused", need["action"])

    def test_rejected_validator_report_exposes_exact_retry_attempt(self):
        state = {"status": "PAUSED_REPEATED_FAILURE", "stop_reason": "report rejected",
                 "settings": {"report_repair": {"max_attempts": 2}},
                 "pending_report_repair": {"error": "Check is not supported by an exact executed Validator event",
                                           "attempts": 2,
                                           "latest_rejected": {"iteration": 1, "output": "/run/sol_report_repair-02.json"}}}
        self.assertEqual("001/sol_report_repair-02", run_view.needs(state)["retry_report_attempt"])

    def test_legacy_job_without_source_identity_exposes_recovery_not_retry(self):
        failure = {'reason': 'Provider stopped', 'stage': 'investigate_bug',
                   'attempt_id': '001/bug-investigation-01', 'job_retry_token': 'jr:old',
                   'archive': '/run/archive', 'source_identity': None,
                   'write_diagnosis': {'unrestored': ['original source capture']},
                   'unrestored': ['original source capture']}
        for status in ('PAUSED_JOB_FAILURE', 'PAUSED_STAGE_ABANDONED'):
            need = run_view.needs({'status': status, 'job_failure': failure})
            self.assertEqual('recover_source', need['kind'])
            self.assertIsNone(need['action'])
            self.assertIn('original source identity', need['recovery_hint'])
            self.assertEqual('jr:old', need['job_retry_token'])
            self.assertEqual('/run/archive', need['archive'])
            self.assertEqual(failure['write_diagnosis'], need['write_diagnosis'])

    def test_running_continues(self):
        self.assertEqual({"kind": "continue"}, run_view.needs({"status": "RUNNING", "pending_questions": []}))


class TaskRunTests(unittest.TestCase):
    """End to end through the real CLI with the offline fixture provider (a few seconds)."""

    def setUp(self):
        if artifacts := os.environ.get('BUILD_AUDIT_ARTIFACTS'):
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            root = Path(tempfile.mkdtemp(prefix="taskrun-", dir=artifacts))
        else:
            temp = tempfile.TemporaryDirectory(prefix="taskrun-")
            self.addCleanup(temp.cleanup)
            root = Path(temp.name)
        self.workspace = root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test",
                        "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(HERE / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "AUTOCODE_HOME": str(root / "registry"),
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def test_retired_token_cap_option_is_rejected_before_a_run_starts(self):
        for cap in ("0", "1", "2000000"):
            with self.subTest(cap=cap), self.assertRaisesRegex(taskrun.TaskRunError, "unrecognized arguments"):
                taskrun.TaskRun.start(self.workspace, BRIEF,
                    options=[*FIXTURE_OPTIONS, "--max-reported-tokens", cap], env=self.env)
        self.assertFalse((self.workspace / ".autocode").exists())

    def test_start_approve_and_complete(self):
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertEqual("Waiting for you", view["progress"]["headline"], view["progress"])
        self.assertEqual("plan approval needed", view["progress"]["needs_you"])
        with self.assertRaisesRegex(taskrun.TaskRunError, "approve plan exited"):
            run.approve_plan("not-the-displayed-token")
        run.approve_plan(view["needs"]["token"])
        view = run.advance_until_input()
        self.assertTrue(view["done"], {key: view.get(key) for key in ("status", "needs", "runner_check")})
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
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=("--engine", "codex"),
                                   start_options=("--pause-after-stage",), env=self.env, timeout=60)
        paused = run.status()
        self.assertEqual(("PAUSED_REQUESTED", "astra_discovery"),
                         (paused["status"], paused["next_stage"]), paused)
        upgraded = taskrun.TaskRun(self.workspace, run.run_dir, options=FIXTURE_OPTIONS,
                                   env=self.env, timeout=120)
        view = upgraded.resume_paused()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertFalse((self.workspace / "greet.py").exists(), "Upgrade is not approval")
        with self.assertRaisesRegex(taskrun.TaskRunError, "approve plan exited"):
            upgraded.approve_plan("not-the-displayed-token")
        upgraded.approve_plan(view["needs"]["token"])
        self.assertEqual("TASK_COMPLETE", upgraded.advance_until_input()["status"])

    def test_follow_up_can_revise_a_protected_behavior_but_still_needs_new_plan_approval(self):
        request = "Add optional punctuation to the greeting API while preserving the current default."
        replacement = "Print Hello, NAME for one nonempty name by default; allow optional punctuation"
        provider = Path(self.env["PATH"].split(os.pathsep)[0]) / "codex"
        script = provider.read_text()
        anchor = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        self.assertIn(anchor, script)
        amendment = textwrap.dedent("""
            if stage in ("astra_discovery", "glm_revise", "astra_finalize") and os.environ.get("FOLLOW_UP_PROVENANCE_TEST") == "1":
                previous = "Print Hello, NAME for one nonempty name"
                event = next((e for e in data.get("brief_feedback", []) if e.get("text") == REQUEST), {})
                report["contract"].setdefault("initial_task", _planning_contract()["initial_task"])
                report["contract"]["required_behaviors"][0] = REPLACEMENT
                report["requirement_trace"][0]["evidence"] = REPLACEMENT
                behaviors = ((data.get("goal_contract") or {}).get("body") or {}).get("required_behaviors", [])
                if previous in behaviors or not behaviors:
                    report["contract_changes"] = [{"item": previous, "change": "reworded",
                        "replacement": REPLACEMENT, "basis": "user_feedback",
                        "answer_id": event.get("id", "feedback-unrecorded-follow-up"), "example_correction": None}]
        """).replace("REQUEST", repr(request)).replace("REPLACEMENT", repr(replacement))
        provider.write_text(script.replace(anchor, textwrap.indent(amendment, "    ") + "\n" + anchor, 1))
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS,
                                   start_options=("--workflow", "build"), env=self.env, timeout=300)
        first = run.status()["needs"]["token"]
        run.approve_plan(first)
        self.assertEqual("TASK_COMPLETE", run.advance_until_input()["status"])
        accepted = {name: (self.workspace / name).read_bytes() for name in ("greet.py", "test_greet.py", "README.md")}
        run.follow_up(request)
        self.env["FOLLOW_UP_PROVENANCE_TEST"] = "1"
        self.env["LIVE_FIXTURE_CLARITY"] = "clear"
        recognizing = taskrun.TaskRun(self.workspace, run.run_dir,
            options=(*FIXTURE_OPTIONS, "--pause-after-stage"), env=self.env, timeout=300)
        boundary = recognizing.advance()
        self.assertEqual(("PAUSED_REQUESTED", "requirements_gather"),
                         (boundary["status"], boundary["next_stage"]), boundary)
        # Reattach: refresh the prior handoff, and retain the recorded request and authorization identity.
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
        before = (run.run_dir / 'state.json').read_bytes()
        broken = taskrun.TaskRun(self.workspace, run.run_dir,
                                options=('--test-command', 'python -m unittest'), env=self.env)
        with self.assertRaisesRegex(taskrun.TaskRunError, 'Changing saved verification commands'):
            broken.resume_paused()
        self.assertEqual(before, (run.run_dir / 'state.json').read_bytes())


    def test_rejected_verification_change_is_reported_to_the_caller(self):
        run = taskrun.TaskRun.start(self.workspace, BRIEF, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        before = (run.run_dir / 'state.json').read_bytes()
        broken = taskrun.TaskRun(self.workspace, run.run_dir,
                                options=('--test-command', 'python -m unittest'), env=self.env)
        with self.assertRaisesRegex(taskrun.TaskRunError, 'Changing saved verification commands'):
            broken.resume_paused()
        self.assertEqual(before, (run.run_dir / 'state.json').read_bytes())


class TaskRunClientTests(unittest.TestCase):
    def test_advancing_command_rejects_input_error_instead_of_treating_it_as_a_pause(self):
        run = taskrun.TaskRun(Path('/work/repo'), Path('/work/repo/.autocode/runs/one'))
        rejected = subprocess.CompletedProcess([], 2,
                                               stdout='Input rejected: pending report repair must be reconciled\n',
                                               stderr='')
        with patch.object(taskrun.subprocess, 'run', return_value=rejected), \
                self.assertRaisesRegex(taskrun.TaskRunError, 'pending report repair'):
            run._invoke('retry failed stage', '--resume-paused', '--retry-failed-stage', advancing=True)

    def test_retry_failed_stage_uses_explicit_inspected_retry(self):
        run = taskrun.TaskRun(Path('/work/repo'), Path('/work/repo/.autocode/runs/one'))
        with patch.object(run, '_invoke') as invoke, patch.object(run, 'status', return_value={}) as status:
            run.retry_failed_stage()
        invoke.assert_called_once_with('retry failed stage', '--resume-paused', '--retry-failed-stage',
                                       '--no-chat', advancing=True)
        status.assert_called_once()

    def test_retry_report_uses_exact_attempt_and_explicit_resume(self):
        run = taskrun.TaskRun(Path('/work/repo'), Path('/work/repo/.autocode/runs/one'))
        with patch.object(run, '_invoke') as invoke, patch.object(run, 'status', return_value={}) as status:
            run.retry_report('001/sol_report_repair-02')
        invoke.assert_called_once_with('retry report', '--resume-paused', '--retry-report',
                                       '001/sol_report_repair-02', '--no-chat', advancing=True)
        status.assert_called_once()

    def test_operational_response_uses_resolver_command_not_question_answer(self):
        run = taskrun.TaskRun(Path('/work/repo'), Path('/work/repo/.autocode/runs/one'))
        with patch.object(run, '_act', return_value={'needs': {'kind': 'resume'}}) as act:
            view = run.respond_operational('request-1', 'token-1', 'Cause identified')
        act.assert_called_once_with('resolver response', '--resolver-request', 'request-1',
                                    '--resolver-token', 'token-1', '--resolver-response',
                                    'provide_information', '--resolver-message', 'Cause identified')
        self.assertEqual('resume', view['needs']['kind'])

    def test_accept_transport_change_requires_explicit_cli_flag(self):
        run = taskrun.TaskRun(Path('/work/repo'), Path('/work/repo/.autocode/runs/one'))
        with patch.object(run, '_invoke') as invoke, patch.object(run, 'status', return_value={}) as status:
            run.accept_transport_change()
        invoke.assert_called_once_with('accept transport change', '--resume-paused',
                                       '--accept-transport-change', '--no-chat', advancing=True)
        status.assert_called_once()

    def test_answer_forwards_the_current_resolver_token(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        with patch.object(run, "_act", return_value={"needs": {"kind": "answer"}}) as act:
            view = run.answer("Q1", "Use addition.py", resolver_token="current-token")
        act.assert_called_once_with("answer", "--answer", "Q1=Use addition.py",
                                     "--resolver-token", "current-token")
        self.assertEqual("answer", view["needs"]["kind"])

    def test_answer_without_resolver_token_uses_the_current_request_token(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        current = {"needs": {"kind": "answer", "questions": [{"id": "Q1"}, {"id": "Q2"}],
                             "resolver_token": "current-token"}}
        with patch.object(run, "status", return_value=current), patch.object(run, "_act") as act:
            run.answer("Q2", "Use addition.py")
        act.assert_called_once_with("answer", "--answer", "Q2=Use addition.py",
                                    "--resolver-token", "current-token")

    def test_answer_without_resolver_token_refuses_a_question_the_run_is_not_asking(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        asking = {"kind": "answer", "questions": [{"id": "Q1"}], "resolver_token": "current-token"}
        for needs, message in ((None, "not waiting for an answer to Q2"),
                               ({"kind": "approve_plan", "token": "plan"}, "needs approve_plan"),
                               (asking, r"questions \['Q1'\]"),
                               ({**asking, "questions": [{"id": "Q2"}], "resolver_token": None},
                                "advance the run to publish one")):
            with self.subTest(needs=needs), patch.object(run, "status", return_value={"needs": needs}), \
                    patch.object(run, "_act") as act, self.assertRaisesRegex(taskrun.TaskRunError, message):
                run.answer("Q2", "Use addition.py")
            act.assert_not_called()

    def test_start_preserves_cli_error_when_no_run_was_created(self):
        for stderr, stdout in (("autocode: GoCode authentication check failed", ""),
                               ("", "autocode: startup failed")):
            with self.subTest(stderr=stderr, stdout=stdout), tempfile.TemporaryDirectory() as root:
                completed = subprocess.CompletedProcess([], 2, stdout=stdout, stderr=stderr)
                with patch.object(taskrun.TaskRun, "_invoke", return_value=completed), \
                     self.assertRaises(taskrun.TaskRunError) as raised:
                    taskrun.TaskRun.start(root, "A sample task")
                self.assertIn("start exited 2 without creating a run", str(raised.exception))
                self.assertIn(stderr or stdout, str(raised.exception))

    def test_start_bounds_diagnostic_output_when_no_run_was_created(self):
        with tempfile.TemporaryDirectory() as root:
            completed = subprocess.CompletedProcess([], 2, stdout="", stderr="x" * 2000 + "cause")
            with patch.object(taskrun.TaskRun, "_invoke", return_value=completed), \
                 self.assertRaises(taskrun.TaskRunError) as raised:
                taskrun.TaskRun.start(root, "A sample task")
            self.assertTrue(str(raised.exception).endswith("cause"))
            self.assertLess(len(str(raised.exception)), 1000)

    def test_show_goal_returns_the_current_displayed_brief(self):
        run = taskrun.TaskRun(Path("/work/repo"), Path("/work/repo/.autocode/runs/one"))
        completed = subprocess.CompletedProcess([], 0, stdout="Build brief r2\nAcceptance criteria:\n  [AC1] Works\n")
        with patch.object(run, "_invoke", return_value=completed) as invoke:
            self.assertEqual(completed.stdout, run.show_goal())
        invoke.assert_called_once_with("show goal", "--show-goal")


if __name__ == "__main__":
    unittest.main()
