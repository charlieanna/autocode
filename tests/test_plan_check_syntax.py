"""A new plan whose check command the replay's shell cannot parse is refused where its author writes it.

The replay runs each planned command as /bin/sh -c COMMAND. A live discuss-then-design-then-build run (djtwgjcl,
2026-10-07) had a Completion Reviewer write a next_task step with backticks inside double quotes, which sh reads
as an unterminated command substitution. Every replay stopped at "Syntax error: EOF in backquote substitution",
every Validator report citing the step was refused, and the run paused at PAUSED_INVALID_OUTPUT. The refusal
comes from the same function that refuses a plan naming git status (test_plan_git_status), with every row at once.
A plan approved or saved before the rule is not checked again.
"""
import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_resolver_human as human
import autocode_support as support
import autocode_verification_plan as plan
import autocode_verify as verify
from goal_fixtures import approve_fixture, body, seed_greeting_workspace

RULE = plan.SHELL_SYNTAX_RULE
# djtwgjcl's Completion Reviewer's next_task step, exactly as written.
LIVE = ("python3 -c \"import re;t=open('docs/design/metadata-cache.md').read();l=t.lower();assert '```python' not "
        "in t and not re.search(r'^(def |import )',t,re.M);assert '100+ tlds' not in l or 'safe even' not in l;"
        "assert all(k in l for k in ['ttl','flock','stale','429','corrupt','validat','in-memory','quota','cold start',"
        "'recycl','permission','host','lru_cache','conditional','peak'])\"")
# The same check as the rule says to write it: the code in single quotes, where a backtick is an ordinary character.
FIXED = ("python3 -c 'import re;t=open(\"docs/design/metadata-cache.md\").read();l=t.lower();assert \"```python\" not "
         "in t and not re.search(r\"^(def |import )\",t,re.M);assert all(k in l for k in [\"ttl\",\"flock\"])'")
QUOTED_BACKTICK = "python3 -c \"assert '`' not in open('README.md').read()\""
VALID = [
    FIXED,
    "python3 -m unittest -v 2>&1 | tail -n 3",
    "sh -c 'python3 -m app frobnicate; test $? -eq 2'",
    "python3 -c 'import json; assert json.load(open(\"docs/decisions/x.json\"))[\"recommendation\"]'",
    "Run `python3 -m unittest -v` and `sh -c 'python3 app.py --bad; test $? -eq 2'`",
    "Run `python3 -m app add x` and `python3 -m app frobnicate` and assert exit 0/2",
]
# Prose the Validator reads: no command is extracted from it, so it is never parsed, though sh could not parse it.
PROSE = [
    "Read docs/design/metadata-cache.md and confirm it has no ```python block",
    "The design doc's code fences (```) are gone and it reads as prose",
]


def shell_says(command):
    """What the replay's shell itself prints for a command it cannot parse (its first line)."""
    parsed = subprocess.run([plan.SHELL, "-n", "-c", command], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=10)
    return parsed.stderr.strip().splitlines()[0] if parsed.returncode else ""


def shown(text):
    return text if len(text) <= 240 else text[:237] + "..."


class RuleTests(unittest.TestCase):
    def test_the_live_step_is_refused_with_the_shells_own_error_and_valid_commands_are_not(self):
        error = shell_says(LIVE)
        self.assertIn(plan.SHELL, error)
        with self.assertRaises(ValueError) as refused:
            plan.refuse_new_plan([("next_task.validation_plan", "python3 -m unittest -v"),
                                  ("next_task.validation_plan", LIVE)])
        self.assertEqual(f"next_task.validation_plan `{shown(LIVE)}` ({error}) cannot be parsed by /bin/sh, so it "
                         "can never run. " + RULE, str(refused.exception))
        for row in VALID:
            with self.subTest(row=row):
                self.assertTrue(plan.commands(row), "no command is extracted, so the shell never reads it")
                plan.refuse_new_plan([("next_task.validation_plan", row)])

    def test_the_error_is_what_the_replay_printed(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        replayed = verify.run_command(LIVE, root, root / "replay.log", timeout=60)
        self.assertNotEqual(0, replayed["exit_code"])
        self.assertTrue(plan.shell_error(LIVE))
        self.assertIn(plan.shell_error(LIVE), Path(replayed["output"]).read_text())

    def test_prose_and_requirements_are_not_parsed(self):
        # A requirement is read by the Validator; the replay runs only the commands of the plan's steps.
        self.assertEqual([LIVE], plan.commands(LIVE))
        with patch.object(plan.subprocess, "run", wraps=subprocess.run) as shell:
            plan.refuse_new_plan([("next_task.validation_plan", row) for row in PROSE],
                                 [("next_task.requirements", LIVE)])
        self.assertEqual(0, shell.call_count)
        for row in PROSE:
            with self.subTest(row=row):
                self.assertEqual([], plan.commands(row))
                self.assertTrue(shell_says(row), "sh could not parse it, had it been read as a command")
        with self.assertRaisesRegex(ValueError, "^next_task.validation_plan `python3 -c .* cannot be parsed"):
            plan.refuse_new_plan([("next_task.validation_plan", LIVE)])

    def test_every_bad_row_is_named_at_once_with_any_git_status_row(self):
        status = "git status --porcelain app tests"
        requirement = "Give AC6 explicit evidence: `git status --porcelain app` must be empty"
        with self.assertRaises(ValueError) as refused:
            plan.refuse_new_plan([("Acceptance criterion C1's verification_method", status),
                                  ("Acceptance criterion C2's verification_method", LIVE),
                                  ("initial_task.validation_plan", "python3 -m unittest -v"),
                                  ("initial_task.validation_plan", QUOTED_BACKTICK)],
                                 [("initial_task.requirements", requirement)])
        self.assertEqual(
            f"Acceptance criterion C1's verification_method `{status}`; initial_task.requirements `{requirement}` "
            f"name git status. {plan.GIT_STATUS_RULE} "
            f"Acceptance criterion C2's verification_method `{shown(LIVE)}` ({shell_says(LIVE)}); "
            f"initial_task.validation_plan `{QUOTED_BACKTICK}` ({shell_says(QUOTED_BACKTICK)}) cannot be parsed by "
            f"/bin/sh, so they can never run. {RULE}", str(refused.exception))

    def test_a_command_that_must_fail_is_parsed_inside_the_runners_wrapper(self):
        # The replay runs sh -c '(COMMAND); autocode_plan_exit=$?; test ...', which parses; the inner script does not.
        row = "Run `python3 greet.py ${name` and assert exit 2"
        [wrapped] = plan.commands(row)
        self.assertEqual("", shell_says(wrapped))
        with self.assertRaisesRegex(ValueError, r"^step `Run .*` \(/bin/sh: .*\) cannot be parsed by /bin/sh"):
            plan.refuse_new_plan([("step", row)])

    def test_a_shell_that_cannot_start_or_answer_refuses_nothing(self):
        with self.assertRaises(ValueError):
            plan.refuse_new_plan([("step", LIVE)])
        with patch.object(plan, "SHELL", "/nonexistent/autocode-sh"):
            plan.refuse_new_plan([("step", LIVE)])
        with patch.object(plan.subprocess, "run", side_effect=subprocess.TimeoutExpired("sh", 5)):
            plan.refuse_new_plan([("step", LIVE)])


class ContractTests(unittest.TestCase):
    def state(self):
        return {"task_id": "t", "answers": {}, "user_events": []}

    def draft(self, method="python3 -m unittest -v", step="python3 -m unittest -v", requirement="Deliver CLI"):
        value = body()
        value["acceptance_criteria"][0].update(verification_method=method)
        value["initial_task"] = {"objective": "Deliver greeting", "affected_paths": ["greet.py"], "kind": "implement",
                                 "milestone_id": "M1", "requirements": [requirement], "acceptance_criteria": ["C1"],
                                 "validation_plan": [step]}
        return value

    def test_a_draft_with_an_unparsable_check_is_not_installed(self):
        for value, where in ((self.draft(method=LIVE), "Acceptance criterion C1's verification_method"),
                             (self.draft(step=LIVE), "initial_task.validation_plan")):
            with self.subTest(where=where):
                state = self.state()
                with self.assertRaises(ValueError) as refused:
                    lifecycle.install_draft(state, value, origin="plan")
                self.assertTrue(str(refused.exception).startswith(where + " `python3 -c"), str(refused.exception))
                self.assertTrue(str(refused.exception).endswith(RULE), str(refused.exception))
                self.assertNotIn("goal_contract", state)
        state = self.state()
        lifecycle.install_draft(state, self.draft(method=FIXED, step=FIXED, requirement=LIVE), origin="plan")
        self.assertEqual(1, state["goal_contract"]["revision"])

    def test_a_draft_saved_before_the_rule_is_still_approved_and_replayed(self):
        value = body() | {"acceptance_criteria": [
            {"id": "C1", "criterion": "Design holds", "verification_method": LIVE, "human_review": False}]}
        with self.assertRaisesRegex(ValueError, "cannot be parsed by /bin/sh"):
            lifecycle.install_draft(self.state(), copy.deepcopy(value), origin="fixture")
        state = {"task_id": "t", "task": "Greet", "answers": {}, "user_events": [], "acceptance_criteria": [],
                 "status": "RUNNING"}
        lifecycle.migrate(state)
        with patch.object(plan, "refuse_new_plan"):
            lifecycle.install_draft(state, value, origin="fixture")
        # Approval validates the saved draft again and assigns its initial task; neither refuses it.
        human.evaluate(state)
        lifecycle.present(state)
        lifecycle.approve(state, goals.token(state["goal_contract"]))
        self.assertTrue(goals.approved(state))
        self.assertEqual([LIVE], plan.approved_commands(state))


class TaskAuthorTests(unittest.TestCase):
    """The Completion Reviewer's next_task, through the runner's result application."""

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

    def decision(self, status, step, output=None):
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
                "next_task": {"kind": "implement", "milestone_id": "M1", "requirements": ["Greet names"],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Run both cases", step],
                              "findings": []},
                "findings": [{"severity": "high", "finding": "Empty names are accepted", "evidence": "event:check",
                              "blocking": True}] if status == "REWORK" else [],
                "finding_dispositions": [], "agreed_limitations": [], "evidence": ["event:check"], "blocker": "",
                "plan": ["Fix", "Recheck"], "affected_paths": ["greet.py"]}

    def apply(self, value, output):
        record = {"output": str(self.run / output), "source_revision": support.snapshot(self.root)["revision"]}
        with patch.object(runner, "run_role", side_effect=AssertionError("no agent may launch")):
            runner.apply_result(self.state, "astra_review", value, record, self.root, self.run)

    def test_the_completion_reviewers_unparsable_step_goes_back_to_it(self):
        for status in ("CONTINUE", "REWORK"):
            with self.subTest(status=status):
                before = copy.deepcopy(self.state)
                with self.assertRaises(ValueError) as refused:
                    self.apply(self.decision(status, LIVE, "review-01.json"), "review-01.json")
                self.assertTrue(str(refused.exception).startswith(
                    f"next_task.validation_plan `{shown(LIVE)}` ({shell_says(LIVE)}) cannot be parsed"),
                    str(refused.exception))
                self.assertEqual(before, self.state)
                self.assertNotIn("resolution_request", self.state)
        self.apply(self.decision("CONTINUE", FIXED, "review-02.json"), "review-02.json")
        self.assertIn(FIXED, self.state["current_task"]["validation_plan"])


class PromptTests(unittest.TestCase):
    """The Planner and Plan Reviewer, the Completion Reviewer and the Resolver are told the rule."""

    def upstream(self):
        from units.common import ModelRequest
        return ModelRequest("astra", "astra_review", "Review it.\nCURRENT HANDOFF DATA\n{}", {},
                            {"properties": {"status": {"enum": ["COMPLETE"]}}, "required": []}, False)

    def test_each_plan_author_is_told_the_rule(self):
        from units import autoplanner, autoresolver, autoreview
        self.assertIn(RULE, autoplanner.EVIDENCE_FACTS)
        for stage in ("astra_review", "astra_checkpoint"):
            with self.subTest(stage=stage), patch.object(autoreview, "execution_request",
                                                         return_value=self.upstream()):
                review = autoreview.prepare({"settings": {}}, stage, "/run/state.json", None)
                self.assertIn(RULE, review.prompt.split("CURRENT HANDOFF DATA\n", 1)[0])
        state = {"workspace": "/ws", "settings": {"roles": {"astra": {"engine": "codex"}}},
                 "resolution_request": {"source_revision": "r"}}
        with patch.object(autoresolver, "guard"), \
                patch.object(autoresolver, "execution_request", return_value=self.upstream()):
            resolve = autoresolver.prepare(state, "astra_resolve", "/run/state.json", None)
        self.assertIn(RULE, resolve.prompt.split("CURRENT HANDOFF DATA\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
