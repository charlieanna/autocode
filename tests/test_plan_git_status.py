"""A new plan that names git status is refused where its author writes it, so no Validator is told to run it.

Clean replay refuses a Validator check that runs git status (#589). Live discuss-then-design-then-build runs
(#185, 2026-10-07) wrote such checks into the plan instead: the Planner and the Plan Reviewer into the contract,
the Completion Reviewer and the Resolver into the next task. Every Validator report that ran them was refused,
and wgmlq3o7 and pt6xzqan paused at PAUSED_INVALID_OUTPUT. A refusal raises ValueError from the author's own
report, which the runner sends back to that stage as a report repair. A plan approved before the rule is not
checked again.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import autocode as runner
import autocode_bug_job as bug_job
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_planning as planning
import autocode_planning_artifacts as artifacts
import autocode_resolver_human as human
import autocode_support as support
import autocode_verification_plan as plan
import autopilot
from goal_fixtures import approve_fixture, body, seed_greeting_workspace

RULE = plan.GIT_STATUS_RULE
# Every plan row that names git status in the three live runs' contracts, tasks and Completion Reviewer and
# Resolver reports (wgmlq3o7, pt6xzqan, wymv4k6n), plus a Completion Reviewer's requirement (uz0sw06p).
LIVE = [
    "python3 -c asserts the file mentions 'tests/test_metadata.py', 'app/server.py', 'stdlib', 'grace_days', "
    "'max_years'; run 'git status --porcelain' and confirm the only changed/added path outside ignored caches and "
    ".autocode/ is docs/design/metadata-cache.md; read the file to confirm it contains no complete function "
    "implementations.",
    "git status --porcelain shows only docs/design/metadata-cache.md changed",
    "git status --porcelain lists exactly docs/decisions/metadata-cache.json (pre-existing untracked) and "
    "docs/design/metadata-cache.md; git status --porcelain app tests deploy docs/design/README.md is empty",
    "`sh -c 'test -z \"$(git status --porcelain app tests deploy docs/design/README.md)\"'`",
    "Manual read: recompute every budget number by hand with R as total requests/hour (N x R/2000 for recycles, "
    "4 x N for cold start); summary figures match the budget section; git status --porcelain lists only "
    "docs/decisions/metadata-cache.json (pre-existing) and docs/design/metadata-cache.md",
    "git status --porcelain -- . ':!.autocode' ':!*__pycache__*' ':!*.pyc' shows only "
    "docs/design/metadata-cache.md; git diff --stat -- app deploy tests docs/decisions is empty",
    "git status --porcelain -- . ':!.autocode' ':!*__pycache__*' ':!*.pyc'",
    "python3 -c \"import subprocess,hashlib;o=subprocess.run(['git','status','--porcelain','--','.',"
    "':!.autocode',':!*__pycache__*',':!*.pyc'],capture_output=True,text=True).stdout.splitlines();"
    "ok={'?? docs/decisions/metadata-cache.json','?? docs/design/metadata-cache.md'};"
    "assert set(o)<=ok and '?? docs/design/metadata-cache.md' in o,o\"",
    "Execute every approved command via capture, including the AC7 command (git status --porcelain + "
    "decision-record sha256) exactly as written in the contract; do not drop it",
    "Run `python3 -c \"import json;d=json.load(open('docs/decisions/metadata-cache.json'));"
    "assert d['recommendation']=='shared-file'\"`; `git status --porcelain app deploy tests` (must be empty, "
    "ignoring __pycache__); read the open questions section.",
    "git status --porcelain app deploy tests",
    "Give AC6 explicit evidence: `git status --porcelain app tests deploy` must be empty, and the decision-JSON "
    "schema/content assertion must print ok.",
    "Git status --porcelain shows only docs/design/x.md",
    "git -C . status --porcelain",
]
# Prose that mentions git and a status but tells no one to run git status.
PROSE = [
    "git diff --stat lists only app/status.py",
    "Run `git log -1` and confirm the message names the /status endpoint",
    "Validator runs git diff and records the exit status",
    "Check the CLI prints git-style status lines",
    "git show HEAD:app/status.py",
    "sh tests/test-git-status.sh",
]
STEP = LIVE[3]  # wgmlq3o7's repair task step, which every later Validator report cited and the replay refused
REQUIREMENT = LIVE[-3]  # uz0sw06p's Completion Reviewer, in next_task.requirements


class RuleTests(unittest.TestCase):
    def test_every_live_row_is_refused_and_prose_is_not(self):
        for row in LIVE:
            with self.subTest(row=row):
                with self.assertRaises(ValueError) as refused:
                    plan.refuse_git_status([("AC1's verification_method", "python3 -m unittest"),
                                            ("AC5's verification_method", row)])
                message = str(refused.exception)
                self.assertTrue(message.startswith("AC5's verification_method `"), message)
                self.assertTrue(message.endswith("names git status. " + RULE), message)
        plan.refuse_git_status([("step", row) for row in PROSE])

    def test_every_row_that_names_it_is_refused_at_once(self):
        # wgmlq3o7's draft named it in a criterion and in the initial task: one repair must see both.
        with self.assertRaises(ValueError) as refused:
            plan.refuse_git_status([("AC5's verification_method", LIVE[1]), ("step", "python3 -m unittest"),
                                    ("initial_task.validation_plan", STEP)])
        self.assertTrue(str(refused.exception).startswith(
            f"AC5's verification_method `{LIVE[1]}`; initial_task.validation_plan `{STEP}` name git status. "),
            str(refused.exception))

    def test_global_options_are_read_once(self):
        self.assertTrue(plan.GIT_STATUS.search("git -c core.quotepath=off -C app --no-pager status"))
        self.assertIsNone(plan.GIT_STATUS.search("git -c a=b -C app --no-pager log"))
        # -c is never also read as a plain option: with both readings 60 of them took 2**60 tries.
        self.assertIsNone(plan.GIT_STATUS.search("git" + " -c" * 60 + " x"))

    def test_a_row_whose_commands_cannot_be_read_is_left_to_the_command_checks(self):
        # Requirements were never read as commands; a malformed exit declaration there is not a git status.
        row = "Run `go test ./a` and `go test ./b` and confirm exit code 2."
        with self.assertRaisesRegex(ValueError, "one status"):
            plan.commands(row)
        plan.refuse_git_status([("next_task.requirements", row)])

    def test_the_rule_says_what_the_runner_enforces_instead(self):
        # assert_within_assignment over current_task.affected_paths; stage_access.stray for workflow jobs.
        self.assertIn("pauses a Builder that changes a file outside its task's affected_paths (when the task "
                      "names them)", RULE)
        self.assertIn("rejects a workflow job's change outside the paths that job may write", RULE)
        self.assertIn("not even to forbid it", RULE)


class ContractTests(unittest.TestCase):
    def draft(self, method="python3 -m unittest -v", step="python3 -m unittest -v", requirement="Deliver CLI",
              human_review=False):
        value = body()
        value["acceptance_criteria"][0].update(verification_method=method, human_review=human_review)
        value["initial_task"] = {"objective": "Deliver greeting", "affected_paths": ["greet.py"], "kind": "implement",
                                 "milestone_id": "M1", "requirements": [requirement], "acceptance_criteria": ["C1"],
                                 "validation_plan": [step]}
        return value

    def test_a_draft_that_names_git_status_is_not_installed(self):
        state = {"task_id": "t", "answers": {}, "user_events": []}
        for value, where in ((self.draft(method=LIVE[1]), "Acceptance criterion C1's verification_method"),
                             # autocode_dispatch.task_for puts a human-review method in other milestones' plans.
                             (self.draft(method=LIVE[1], human_review=True),
                              "Acceptance criterion C1's verification_method"),
                             (self.draft(step=LIVE[-1]), "initial_task.validation_plan"),
                             (self.draft(requirement=REQUIREMENT), "initial_task.requirements")):
            with self.subTest(where=where):
                with self.assertRaises(ValueError) as refused:
                    lifecycle.install_draft(state, value, origin="plan")
                self.assertTrue(str(refused.exception).startswith(where + " `"), str(refused.exception))
                self.assertNotIn("goal_contract", state)
        lifecycle.install_draft(state, self.draft(), origin="plan")
        self.assertEqual(1, state["goal_contract"]["revision"])

    def test_a_draft_no_model_writes_at_install_is_not_refused(self):
        # The Plan Reviewer's approval installs the Planner's draft again, and a bug job's small correction is
        # built by the runner: a refusal of their wording would go to a stage that cannot change it.
        self.assertIn(bug_job.ORIGIN, lifecycle.UNAUTHORED_DRAFTS)
        for origin in lifecycle.UNAUTHORED_DRAFTS:
            with self.subTest(origin=origin):
                state = {"task_id": "t", "answers": {}, "user_events": []}
                lifecycle.install_draft(state, self.draft(method=LIVE[1]), origin=origin)
                self.assertEqual(LIVE[1], state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"])

    def test_a_draft_saved_before_the_rule_is_still_approved_and_replayed(self):
        # Approval validates the saved draft again and assigns its initial task; neither refuses it.
        state = {"task_id": "t", "task": "Greet", "answers": {}, "user_events": [], "acceptance_criteria": [],
                 "status": "RUNNING"}
        lifecycle.migrate(state)
        with patch.object(plan, "refuse_git_status"):
            lifecycle.install_draft(state, body() | {"acceptance_criteria": [
                {"id": "C1", "criterion": "Contract holds", "verification_method": STEP, "human_review": False}]},
                origin="fixture")
        human.evaluate(state)
        lifecycle.present(state)
        lifecycle.approve(state, goals.token(state["goal_contract"]))
        self.assertTrue(goals.approved(state))
        self.assertEqual([STEP.strip("`")], plan.approved_commands(state))

    def test_the_planners_report_is_refused_until_it_drops_git_status(self):
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        state = {"task_id": "task", "task": "Task", "workspace": str(root), "answers": {}, "user_events": [],
                 "acceptance_criteria": [], "settings": {"joint_planning": True, "planning_flow": "v2", "roles": {
                     "requirements": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3"},
                     "glm": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3"},
                     "plan_reviewer": {"engine": "opencode", "model": planning.PINNED_REVIEWER_MODEL,
                                       "model_pinned": True}}}}

        def apply(stage, value):
            autopilot.apply_planning(state, stage, value, {"output": f"{stage}.json"}, run_dir=root)
            artifacts.flush_pending(state, root)

        requirements = body()
        for field in goals.BRIEF_FIELDS:
            requirements.pop(field)
        apply("requirements", {"requirements": requirements, "summary": "Requirements ready"})
        before = copy.deepcopy(state["planning"])
        with self.assertRaisesRegex(ValueError, "^Acceptance criterion C1's verification_method `git status"):
            apply("plan", {"contract": self.draft(method=LIVE[1]), "summary": "Plan ready"})
        self.assertNotIn("goal_contract", state)
        self.assertEqual(before, state["planning"])
        apply("plan", {"contract": self.draft(), "summary": "Plan ready"})
        self.assertEqual(1, state["goal_contract"]["revision"])


class TaskAuthorTests(unittest.TestCase):
    """The Completion Reviewer's and the Resolver's next_task, through the runner's result application."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry-home")})
        environment.start()
        self.addCleanup(environment.stop)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.test", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        seed_greeting_workspace(self.root)
        self.state = {"version": 2, "workspace": str(self.root), "task": "Greet", "status": "RUNNING",
                      "iteration": 1, "stages": [], "history": [], "acceptance_criteria": [],
                      "settings": {"roles": {r: {"model": f"model-{r}"} for r in ("astra", "terra", "sol")},
                                   "context_soft_tokens": 1000, "headroom": {"enabled": False}}}
        approve_fixture(self.state, runner.goals)
        runner.lifecycle.assign_task(self.state, self.decision("CONTINUE", "Run both cases"),
                                     support.snapshot(self.root))

    def decision(self, status, step, output=None, requirement="Greet names"):
        if output:
            (self.run / output).write_text(json.dumps({"status": status}))
        contract = self.state["goal_contract"]
        return {"contract_revision": contract["revision"], "contract_hash": contract["hash"],
                "task_id": self.state.get("current_task", {}).get("id", ""), "deferred_backlog": [],
                "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                                 "options": [], "proposed_delta": ""},
                "status": status, "next_objective": "Restore the greeting",
                "acceptance_criteria": [{**row, "status": "unverified", "evidence": "event:check"}
                                        for row in self.state["acceptance_criteria"]],
                "next_task": {"kind": "implement", "milestone_id": "M1", "requirements": [requirement],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Run both cases", step],
                              "findings": []},
                "findings": [{"severity": "high", "finding": "Empty names are accepted", "evidence": "event:check",
                              "blocking": True}] if status == "REWORK" else [],
                "finding_dispositions": [], "agreed_limitations": [], "evidence": ["event:check"], "blocker": "",
                "plan": ["Fix", "Recheck"], "affected_paths": ["greet.py"]}

    def apply(self, stage, value, output):
        record = {"output": str(self.run / output), "source_revision": support.snapshot(self.root)["revision"]}
        with patch.object(runner, "run_role", side_effect=AssertionError("no agent may launch")):
            runner.apply_result(self.state, stage, value, record, self.root, self.run)

    def assert_refused(self, stage, value, output, where="next_task.validation_plan `" + STEP):
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError) as refused:
            self.apply(stage, value, output)
        self.assertTrue(str(refused.exception).startswith(where), str(refused.exception))
        self.assertTrue(str(refused.exception).endswith(RULE))
        self.assertEqual(before, self.state)

    def test_the_completion_reviewers_continue_is_refused(self):
        self.assert_refused("astra_review", self.decision("CONTINUE", STEP, "review-01.json"), "review-01.json")
        self.assert_refused("astra_review", self.decision("CONTINUE", "python3 -m unittest -v", "review-01.json",
                                                          requirement=REQUIREMENT),
                            "review-01.json", where="next_task.requirements `Give AC6")
        self.apply("astra_review", self.decision("CONTINUE", "python3 -m unittest -v", "review-02.json"),
                   "review-02.json")
        self.assertIn("python3 -m unittest -v", self.state["current_task"]["validation_plan"])

    def test_the_completion_reviewers_rework_is_refused_not_handed_to_the_resolver(self):
        # A refused direct assignment would otherwise send the task on to the Resolver (rework_policy.route).
        self.assert_refused("astra_review", self.decision("REWORK", STEP, "review-01.json"), "review-01.json")
        self.assertNotIn("resolution_request", self.state)
        self.apply("astra_review", self.decision("REWORK", "python3 -m unittest -v", "review-02.json"),
                   "review-02.json")
        self.assertEqual("astra_resolve", self.state["next_stage"])

    def test_the_resolvers_repair_task_is_refused(self):
        self.apply("astra_review", self.decision("REWORK", "python3 -m unittest -v", "review-01.json"),
                   "review-01.json")
        diagnosis = {**self.decision("REWORK", STEP, "resolve-01.json"), "findings": [],
                     "diagnosis": "The blank check runs after the greeting is printed"}
        self.assert_refused("astra_resolve", diagnosis, "resolve-01.json")
        diagnosis["next_task"]["validation_plan"][-1] = "python3 -m unittest -v"
        self.apply("astra_resolve", diagnosis, "resolve-01.json")
        self.assertEqual("terra", self.state["next_stage"])


class PromptTests(unittest.TestCase):
    """The Planner and Plan Reviewer, the Completion Reviewer and the Resolver are told the same rule."""

    def upstream(self):
        from units.common import ModelRequest
        return ModelRequest("astra", "astra_review", "Review it.\nCURRENT HANDOFF DATA\n{}", {},
                            {"properties": {"status": {"enum": ["COMPLETE"]}}, "required": []}, False)

    def test_each_plan_author_is_told_the_rule(self):
        from units import autoplanner, autoresolver, autoreview
        self.assertIn(RULE, autoplanner.EVIDENCE_FACTS)
        with patch.object(autoreview, "execution_request", return_value=self.upstream()):
            review = autoreview.prepare({"settings": {}}, "astra_review", "/run/state.json", None)
        self.assertIn(RULE, review.prompt.split("CURRENT HANDOFF DATA\n", 1)[0])
        # Under reviewer routing the checkpoint's decision is applied as the Completion Reviewer's.
        with patch.object(autoreview, "execution_request", return_value=self.upstream()):
            checkpoint = autoreview.prepare({"settings": {}}, "astra_checkpoint", "/run/state.json", None)
        self.assertIn(RULE, checkpoint.prompt.split("CURRENT HANDOFF DATA\n", 1)[0])
        state = {"workspace": "/ws", "settings": {"roles": {"astra": {"engine": "codex"}}},
                 "resolution_request": {"source_revision": "r"}}
        with patch.object(autoresolver, "guard"), \
                patch.object(autoresolver, "execution_request", return_value=self.upstream()):
            resolve = autoresolver.prepare(state, "astra_resolve", "/run/state.json", None)
        self.assertIn(RULE, resolve.prompt.split("CURRENT HANDOFF DATA\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
