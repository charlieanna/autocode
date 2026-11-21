"""AutoCode as a step inside another coding agent (autocode_agent_step, #693).

The Claude Code skill (examples/agent-step/skills/autocode/SKILL.md) runs this
module's command line. The end-to-end test drives that command line the way the
skill tells an agent to, against the offline fixture provider: a question, plan
approval, completion and the evidence report, reading the status at each pause.
"""

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import autocode_agent_background as background
import autocode_agent_step as agent_step
import autocode_taskrun as taskrun
import autocode_workflows as workflows
from harness import catalog, driver

from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
STEP = ROOT / "tools" / "autocode_agent_step.py"
SKILL = ROOT / "examples" / "agent-step" / "skills" / "autocode" / "SKILL.md"
INTEGRATIONS = ("autocode_agent_step", "autocode_mcp", "autocode_agent_background")
# Long enough for a fixture run's work to its next stop; the call itself returns at that stop.
WAIT = "250"


def fixture_workspace(test: unittest.TestCase) -> tuple[Path, dict[str, str]]:
    """A committed Git project and the environment that puts the offline fixture provider on PATH as codex."""
    temp = tempfile.TemporaryDirectory(prefix="agent-step-")
    test.addCleanup(temp.cleanup)
    root = Path(temp.name)
    workspace = root / "project"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    identity = ["-c", "user.name=T", "-c", "user.email=t@example.test"]
    subprocess.run(["git", "-C", str(workspace), *identity, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    bindir = root / "bin"
    bindir.mkdir()
    shutil.copy2(ROOT / "tools" / "live_fixture_provider.py", bindir / "codex")
    (bindir / "codex").chmod(0o755)
    config_home = root / "xdg-config"
    config_home.mkdir()
    codex_home = root / "codex-home"
    codex_home.mkdir()
    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "AUTOCODE_HOME": str(root / "registry"),
        "XDG_CONFIG_HOME": str(config_home),
        "CODEX_HOME": str(codex_home),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return workspace, env


def review_workspace(test: unittest.TestCase) -> tuple[Path, dict[str, str], list[str]]:
    """A committed project with a change left uncommitted, and the scenario fake whose Code Reviewer judges it."""
    scenario = catalog.load("review-planted-defects")
    temp = tempfile.TemporaryDirectory(prefix="agent-step-review-")
    test.addCleanup(temp.cleanup)
    root = Path(temp.name)
    workspace = root / "project"
    shutil.copytree(scenario.dir / "seed", workspace, ignore=shutil.ignore_patterns("*.patch"))
    git = ["git", "-C", str(workspace), "-c", "user.name=T", "-c", "user.email=t@example.test"]
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)
    [patch] = (scenario.dir / "seed").glob("*.patch")
    subprocess.run([*git, "apply", str(patch)], check=True)
    flags, env = driver.fake_setup(scenario, root / "fake", scenario.dir / "reference")
    return workspace, {**env, "AUTOCODE_HOME": str(root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"}, flags


def ask_one_question_once(test: unittest.TestCase, env: dict[str, str]) -> None:
    """Have the fixture's Requirements ask one genuine question, once: the plan that follows needs no answer again."""
    provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
    amendment = textwrap.dedent("""
        marker = Path(os.environ["FIXTURE_QUESTION_ASKED"])
        if stage == "draft_plan" and not marker.exists():
            marker.write_text("asked")
            report["contract"]["open_blocking_questions"] = [{
                "id": "readme-audience", "question": "Should the README target beginners or experienced Python users?",
                "why": "This fixture has a genuine unresolved documentation-audience decision.",
                "options": ["Beginners", "Experienced Python users"], "proposed_default": "",
                "kind": "decision", "category": "requested_outcome", "delegable": False}]
            report["contract"].update(technical_approach=[], milestones=[])
            report["contract"]["initial_task"] = {
                "objective": "", "affected_paths": [], "kind": "none", "milestone_id": "",
                "requirements": [], "acceptance_criteria": [], "validation_plan": []}
    """)
    marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
    source = provider.read_text(encoding="utf-8")
    test.assertIn(marker, source)
    provider.write_text(source.replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker), encoding="utf-8")
    env["FIXTURE_QUESTION_ASKED"] = str(provider.parent / "question-asked")


def hold_the_provider(test: unittest.TestCase, env: dict[str, str]) -> Path:
    """Make every fixture provider call wait while the returned file exists: the run stays working until it is gone."""
    provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
    hold = provider.parent / "hold"
    hold.write_text("hold", encoding="utf-8")
    amendment = textwrap.dedent("""
        import time as _hold_time
        while Path(os.environ["FIXTURE_HOLD"]).exists():
            _hold_time.sleep(0.1)
    """)
    marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
    source = provider.read_text(encoding="utf-8")
    test.assertIn(marker, source)
    provider.write_text(source.replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker), encoding="utf-8")
    env["FIXTURE_HOLD"] = str(hold)
    test.addCleanup(hold.unlink, missing_ok=True)
    return hold


def callers(run_dir: str) -> set[str]:
    """Who made each call that changed the run, from its activity log."""
    lines = (Path(run_dir) / "activity.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    return {entry.get("caller") for entry in entries if entry.get("event") == "invocation"}


def subcommands(parser: argparse.ArgumentParser) -> set[str]:
    return {
        name for action in parser._actions if isinstance(action, argparse._SubParsersAction) for name in action.choices
    }


class StepCommandLine(unittest.TestCase):
    """Runs the step's command line in ``self.workspace`` with ``self.env``, as the skill does."""

    workspace: Path
    env: dict[str, str]

    def step(self, *args, env=None, code=0, timeout=300):
        process = subprocess.run(
            [sys.executable, str(STEP), "--workspace", str(self.workspace), *args],
            capture_output=True,
            text=True,
            env={**os.environ, **self.env, **(env or {})},
            timeout=timeout,
        )
        self.assertEqual(code, process.returncode, process.stderr)
        return process

    def summary(self, *args, env=None):
        return json.loads(self.step(*args, env=env).stdout)


class SkillCommandLineTests(StepCommandLine):
    """End to end through the step's command line, with the offline fixture provider (under a minute)."""

    def setUp(self):
        self.workspace, self.env = fixture_workspace(self)

    def test_the_skill_drives_a_run_through_a_question_and_plan_approval_to_its_evidence(self):
        ask_one_question_once(self, self.env)
        asked = self.summary("start", BRIEF, "--wait", WAIT, "--", *FIXTURE_OPTIONS)
        self.assertFalse(asked["working"], asked)
        self.assertEqual("answer", asked["needs"]["kind"], asked)
        self.assertEqual("your_decision", asked["pause_category"])
        self.assertEqual("Your decision", asked["pause_category_label"])
        self.assertIn("--answer", asked["next_command"])
        self.assertIn("Waiting for you", asked["progress"])
        read = self.summary("status", "--full")
        self.assertEqual(asked["needs"], read["needs"])
        self.assertEqual(asked["needs"], read["view"]["needs"])  # the status view itself, unchanged
        question = asked["needs"]["questions"][0]

        # Only the person's decisions, each against the token of the view they saw.
        refused = self.step("approve", "a-token", code=1)
        self.assertIn("person's to decide", refused.stderr)
        self.assertIn(asked["next_command"], refused.stderr)
        stale = self.step("answer", question["id"], question["options"][0], "--token", "stale", code=1)
        self.assertIn("exact current AutoResolver request and token", stale.stderr)
        self.assertEqual(asked["needs"], self.summary("status")["needs"])

        token = {"AUTOCODE_RESOLVER_TOKEN": asked["needs"]["resolver_token"]}
        planned = self.summary(
            "answer", question["id"], question["options"][0], "--token", "-", "--wait", WAIT, env=token
        )
        self.assertEqual("approve_plan", planned["needs"]["kind"], planned)
        self.assertEqual("your_decision", planned["pause_category"])
        self.assertIn("Approval token: " + planned["needs"]["token"], planned["plan"])
        self.assertIn("--approve-goal", planned["next_command"])
        self.assertEqual(planned["run_dir"], asked["run_dir"])
        waiting = self.step("continue", code=1).stderr
        self.assertIn("person's to decide", waiting)
        self.assertIn("needs approve_plan", waiting)
        self.assertFalse((self.workspace / "greet.py").exists(), "nothing is built before the person approves")

        done = self.summary(
            "approve", "-", "--wait", WAIT, env={"AUTOCODE_APPROVE_GOAL_TOKEN": planned["needs"]["token"]}
        )
        self.assertEqual(("TASK_COMPLETE", True, False), (done["status"], done["done"], done["working"]), done)
        self.assertIsNone(done["next_command"])
        self.assertNotIn("plan", done)
        self.assertTrue((self.workspace / "greet.py").is_file())
        self.assertTrue(self.step("evidence", "--markdown").stdout.startswith("## AutoCode evidence"))
        self.assertEqual({"agent-step"}, callers(done["run_dir"]))

    def test_no_step_waits_for_the_runs_work_unless_it_asks_to(self):
        # #3984: an agent's tool call has a time limit and a run's work does not. Before, start held the call
        # until the run's first stop, which here never comes while the provider is held.
        hold = self.hold_provider()
        started = self.summary_within(30, "start", BRIEF, "--", *FIXTURE_OPTIONS)
        self.assertTrue(started["working"], started)
        self.assertIn(f"{os.sep}.autocode{os.sep}runs{os.sep}", started["run_dir"])
        self.assertNotIn("plan", started)
        waited = self.summary_within(30, "status", "--wait", "1")
        self.assertEqual((started["run_dir"], True), (waited["run_dir"], waited["working"]), waited)

        # While it works, nothing advances it again or starts another run in the workspace.
        for args in (("continue",), ("approve", "a-token"), ("evidence",), ("start", "Another request")):
            with self.subTest(step=args[0]):
                self.assertIn("still working", self.step(*args, code=1, timeout=30).stderr)

        hold.unlink()
        planned = self.summary("status", "--wait", WAIT)
        self.assertEqual((False, "approve_plan"), (planned["working"], planned["needs"]["kind"]), planned)
        self.assertEqual("stopped", planned["background"]["state"])
        self.assertIn("Approval token: " + planned["needs"]["token"], planned["plan"])
        approved = self.summary_within(
            30, "approve", "-", env={"AUTOCODE_APPROVE_GOAL_TOKEN": planned["needs"]["token"]}
        )
        self.assertEqual(planned["run_dir"], approved["run_dir"])
        self.assertTrue(approved["working"], approved)  # the approval is saved; the build works in the background
        done = self.summary("status", "--wait", WAIT)
        self.assertEqual(("TASK_COMPLETE", True, False), (done["status"], done["done"], done["working"]), done)
        self.assertEqual({"agent-step"}, callers(done["run_dir"]))

    def hold_provider(self) -> Path:
        return hold_the_provider(self, self.env)

    def summary_within(self, seconds, *args, env=None):
        return json.loads(self.step(*args, env=env, timeout=seconds).stdout)

    def test_engine_options_are_taken_only_when_a_run_starts(self):
        refused = self.step("status", "--", "--engine", "codex", code=2)
        self.assertIn("only on start", refused.stderr)
        self.assertIn("no AutoCode run yet", self.step("status", code=1).stderr)


class VerifyChangeCommandLineTests(StepCommandLine):
    """docs/agent-step.md "Verify a change you made", through the step's command line with the scenario fake."""

    def setUp(self):
        self.workspace, self.env, self.flags = review_workspace(self)

    def test_a_review_of_the_agents_change_reports_its_recorded_evidence(self):
        files = (
            subprocess.check_output(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=self.workspace
            )
            .decode()
            .split("\0")
        )
        before = {name: (self.workspace / name).read_bytes() for name in files if name}
        brief = catalog.load("review-planted-defects").brief.replace(
            "Review pr-184.patch before I merge it.",
            "Review the uncommitted change in this workspace (git diff HEAD) before I merge it.",
        )
        config_path = Path(self.env["SCENARIO_FAKE_CONFIG"])
        config = json.loads(config_path.read_text())
        config["brief"] = brief
        config_path.write_text(json.dumps(config))
        planned = self.summary("start", brief, "--workflow", "review", "--wait", WAIT, "--", *self.flags)
        self.assertEqual("approve_plan", planned["needs"]["kind"], planned)
        self.assertIn("Approval token: " + planned["needs"]["token"], planned["plan"])
        reviewed = self.summary(
            "approve", "-", "--wait", WAIT, env={"AUTOCODE_APPROVE_GOAL_TOKEN": planned["needs"]["token"]}
        )
        self.assertEqual(
            ("TASK_COMPLETE", True, "review"), (reviewed["status"], reviewed["done"], reviewed["workflow"]), reviewed
        )
        self.assertEqual(before, {name: (self.workspace / name).read_bytes() for name in before})
        findings = json.loads((self.workspace / "review/findings.json").read_text())
        self.assertEqual("request_changes", findings["verdict"])
        self.assertEqual({"F1", "F2"}, {row["id"] for row in findings["findings"] if row["severity"] == "blocking"})
        # Reviews now use the full pipeline; its delivered findings have current evidence.
        report = self.summary("evidence")
        self.assertEqual("current", report["availability"], report)
        self.assertTrue(report["markdown"].startswith("## AutoCode evidence"))
        self.assertEqual(report["markdown"], self.step("evidence", "--markdown").stdout.removesuffix("\n"))
        self.assertEqual({"agent-step"}, callers(reviewed["run_dir"]))


class AgentStepTests(unittest.TestCase):
    """The operations' own rules, with the task-run client stubbed (no provider)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="agent-step-unit-")
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve()
        self.run_dir = self.workspace / ".autocode" / "runs" / "one"
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "state.json").write_text("{}", encoding="utf-8")
        self.step = agent_step.AgentStep(self.workspace, spawn=agent_step.in_process)

    def test_the_job_kinds_are_the_runners(self):
        self.assertEqual(workflows.WORKFLOWS, agent_step.WORKFLOWS)

    def test_verifying_a_change_starts_a_review_run(self):
        # docs/agent-step.md: "verify my change" names the review workflow at start; later calls do not repeat it.
        def started(workspace, brief, *, run_dir, **_):
            (run_dir / "state.json").parent.mkdir(parents=True)
            (run_dir / "state.json").write_text("{}", encoding="utf-8")
            return taskrun.TaskRun(workspace, run_dir)

        with (
            mock.patch.object(taskrun.TaskRun, "start", side_effect=started) as start,
            mock.patch.object(taskrun.TaskRun, "advance_until_input", return_value={"status": "TASK_COMPLETE"}),
            mock.patch.object(taskrun.TaskRun, "status", return_value={"status": "TASK_COMPLETE", "done": True}),
        ):
            summary = self.step.start("Review the uncommitted change (git diff HEAD)", workflow="review")
        self.assertEqual(("--workflow", "review"), start.call_args.kwargs["start_options"])
        self.assertEqual("agent-step", start.call_args.kwargs["env"]["AUTOCODE_CALLER"])
        self.assertEqual(str(start.call_args.kwargs["run_dir"]), summary["run_dir"])
        self.assertEqual(self.workspace / ".autocode" / "runs", start.call_args.kwargs["run_dir"].parent)
        self.assertEqual((False, "stopped"), (summary["working"], summary["background"]["state"]))
        with self.assertRaisesRegex(agent_step.StepError, "workflow must be one of"):
            self.step.start("Review it", workflow="audit")

    def test_an_operational_request_is_the_persons_although_it_is_an_answer(self):
        view = {
            "status": "PAUSED_RESOLVER",
            "done": False,
            "pause_category_label": "AutoCode",
            "next_command": "autocode --resolver-request R --resolver-response provide_information",
            "needs": {"kind": "answer", "resolver_scope": "blocker", "resolver_token": "t", "questions": [{"id": "q"}]},
        }
        with (
            mock.patch.object(taskrun.TaskRun, "status", return_value=view),
            mock.patch.object(taskrun.TaskRun, "answer") as answer,
        ):
            with self.assertRaisesRegex(agent_step.StepError, "person's to decide.*--resolver-response"):
                self.step.respond("answer", token="t", question_id="q", text="yes")
        answer.assert_not_called()

    def test_a_finished_run_takes_no_decision(self):
        view = {"status": "TASK_COMPLETE", "done": True, "needs": None}
        for action in agent_step.ACTIONS:
            with self.subTest(action=action), mock.patch.object(taskrun.TaskRun, "status", return_value=view):
                with self.assertRaisesRegex(agent_step.StepError, r"finished \(TASK_COMPLETE\); read its evidence"):
                    self.step.respond(action, token="t", question_id="q", text="yes")

    def test_a_decision_needs_the_token_the_person_saw(self):
        asking = {"status": "AWAITING_INPUT", "done": False, "needs": {"kind": "answer", "questions": [{"id": "q"}]}}
        approving = {"status": "AWAITING_GOAL_APPROVAL", "done": False, "needs": {"kind": "approve_plan", "token": "t"}}
        for view, action in ((asking, "answer"), (approving, "approve_plan")):
            with self.subTest(action=action), mock.patch.object(taskrun.TaskRun, "status", return_value=view):
                with self.assertRaisesRegex(agent_step.StepError, "needs"):
                    self.step.respond(action, question_id="q", text="yes")

    def test_only_a_job_that_only_reports_gives_its_recorded_evidence(self):
        # A build's report must still match the workspace; a review's recorded report is its evidence.
        review = {"workflow_result": {"outcome": "approve", "artifact": "review/findings.json"}}
        cases = (
            ({}, "current", True),
            ({}, "recorded", False),
            ({}, "stale", False),
            (review, "current", True),
            (review, "recorded", True),
            (review, "stale", False),
        )
        for document, availability, given in cases:
            report = {"availability": availability, "reasons": ["why"], "document": document, "markdown": "## E"}
            with (
                self.subTest(job="review" if document else "build", availability=availability),
                mock.patch.object(taskrun.TaskRun, "evidence_report", return_value=report) as read,
            ):
                if given:
                    summary = self.step.evidence()
                    self.assertEqual((availability, "## E"), (summary["availability"], summary["markdown"]))
                else:
                    with self.assertRaisesRegex(agent_step.StepError, "not current: why"):
                        self.step.evidence()
                read.assert_called_once_with(require_current=False)

    def test_a_failed_start_is_reported_with_its_reason(self):
        with mock.patch.object(taskrun.TaskRun, "start", side_effect=taskrun.TaskRunError("no engine")):
            with self.assertRaisesRegex(agent_step.StepError, "did not start: no engine"):
                self.step.start("Build it")

    def test_a_wait_is_a_bounded_number_of_seconds(self):
        for wait in (-1, agent_step.MAX_WAIT_SECONDS + 1, "5", True):
            with self.subTest(wait=wait), self.assertRaisesRegex(agent_step.StepError, "wait must be"):
                self.step.status(wait=wait)

    def test_a_launch_works_while_its_process_lives_and_is_lost_when_it_ends_without_a_result(self):
        run_dir = self.workspace / ".autocode" / "runs" / "held"
        alive = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(alive.wait)
        self.addCleanup(alive.kill)
        background.launch(self.workspace, run_dir, {"operation": "advance"}, ["drive"], {}, lambda *_: alive.pid)
        self.assertEqual("working", background.state(self.workspace, run_dir)["state"])
        self.assertEqual([run_dir], background.working(self.workspace))
        ticks = iter(range(100))
        waited = background.wait(self.workspace, run_dir, 3, sleep=lambda _: None, clock=lambda: next(ticks))
        self.assertEqual("working", waited["state"])  # the wait ended at its deadline, not at a stop
        alive.kill()
        alive.wait()
        lost = background.state(self.workspace, run_dir)
        self.assertEqual("lost", lost["state"])
        self.assertIn("ended without recording", lost["error"])
        self.assertEqual([], background.working(self.workspace))

        request = background.read_request(self.workspace / background.RECORDS / "held.request.json")
        background.finish(request, {"state": "stopped"})
        self.assertEqual("stopped", background.state(self.workspace, run_dir)["state"])

    def test_a_run_is_named_when_the_workspace_has_several_or_is_not_one_of_them(self):
        second = self.workspace / ".autocode" / "runs" / "two"
        second.mkdir()
        (second / "state.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(agent_step.StepError, "has 2 runs"):
            self.step.status()
        with self.assertRaisesRegex(agent_step.StepError, "is not a run in"):
            self.step.status(self.workspace)
        with mock.patch.object(taskrun.TaskRun, "status", return_value={"status": "RUNNING"}):
            self.assertEqual(str(second), self.step.status(second)["run_dir"])
            self.assertEqual(str(second), self.step.status()["run_dir"])  # the run named last stays the default

    def test_the_integrations_reach_runs_only_through_the_task_run_client(self):
        # AGENTS.md rule 5, and no dependency outside the standard library.
        allowed = set(sys.stdlib_module_names) | {"autocode_taskrun", *INTEGRATIONS}
        for name in INTEGRATIONS:
            source = (ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
            imported = set()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    imported |= {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    imported |= {node.module.split(".")[0]} if node.module else {alias.name for alias in node.names}
            with self.subTest(module=name):
                self.assertLessEqual(imported, allowed)
                self.assertNotIn("state.json", source)


class SkillFileTests(unittest.TestCase):
    def test_the_skill_names_every_step_and_the_token_variables_the_step_reads(self):
        text = SKILL.read_text(encoding="utf-8")
        front = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(front)
        fields = dict(line.split(": ", 1) for line in front.group(1).splitlines())
        self.assertEqual("autocode", fields["name"])
        self.assertGreater(len(fields["description"]), 40)
        self.assertEqual(subcommands(agent_step.parser()), set(re.findall(r"\$STEP (\w+)", text)))
        for variable in (agent_step.APPROVE_TOKEN_VARIABLE, agent_step.RESOLVER_TOKEN_VARIABLE):
            self.assertIn(variable, text)
        self.assertIn("autocode_cli.autocode_agent_step", text)


if __name__ == "__main__":
    unittest.main()
