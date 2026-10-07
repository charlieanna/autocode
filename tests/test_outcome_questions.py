"""Issue #450: planning asks a person about outcomes and constraints and recommends mechanisms itself.

A live design run asked "Which Slack destination and integration: incoming webhook, Slack app with
chat:write, or AWS Chatbot?". These tests check that each planning job's prompt carries its rule
(autocode_outcome_questions) and that no execution stage's prompt does. The end-to-end behaviour is the
scenario ``design-alerting-outcomes`` (scenarios/catalog), driven through the CLI.
"""
import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_outcome_questions as outcome_questions
import autocode_stage_context as stage_context
from tests.test_bug_job import state_for
from units import autoplanner

HEADINGS = (outcome_questions.REQUIREMENTS_HEADING, outcome_questions.PLANNER_HEADING,
            outcome_questions.REVIEWER_HEADING)
TASK = ("Design how we should monitor our order pipeline's dead-letter queue on AWS and alert the team "
        "in Slack. Do not implement or deploy anything.")


def joint_state(workflow="design"):
    state = {**state_for(task=TASK), "workflow": {"kind": workflow}, "answers": {}, "user_events": []}
    return copy.deepcopy(state)


def headings_in(prompt):
    return {heading for heading in HEADINGS if heading in prompt}


def missing(prompt, phrases):
    """The phrases a prompt lacks, line breaks aside (a short failure message instead of the whole prompt)."""
    text = " ".join(prompt.split())
    return [phrase for phrase in phrases if phrase not in text]


class JointPlanningPromptTests(unittest.TestCase):
    """Today's pipeline: Requirements, Planner, Plan Reviewer each get their own rule, and only it."""

    def prompt(self, stage, workflow="design"):
        prompt, _ = autoplanner.context(joint_state(workflow), stage, Path("/tmp/state.json"))
        return prompt

    def test_requirements_ask_about_outcomes_and_constraints_not_mechanisms(self):
        prompt = self.prompt("requirements_gather")
        self.assertEqual({outcome_questions.REQUIREMENTS_HEADING}, headings_in(prompt))
        self.assertEqual([], missing(prompt, (
            "Inspect the accessible repository conventions first", "thresholds, deadlines",
            "privacy and data-handling limits", "organizational restrictions",
            "Which channel should receive alerts, and must we use an existing integration?",
            "Separate parameterizable identities from true blockers",
            "healthy-path target or an unconditional guarantee", "uncertain delivery result")))

    def test_the_planner_recommends_mechanisms_and_keeps_blockers(self):
        for stage in ("astra_discovery", "glm_revise"):
            with self.subTest(stage=stage):
                prompt = self.prompt(stage)
                self.assertEqual({outcome_questions.PLANNER_HEADING}, headings_in(prompt))
                self.assertEqual([], missing(prompt, (
                    "name the viable options with their tradeoffs", "basis=agent_proposed",
                    "only when it is a binding constraint", "do not ask again which API",
                    "never permission to deploy", "say so in constraints",
                    "in open_blocking_questions until the person decides them")))

    def test_the_plan_reviewer_checks_both(self):
        for stage in ("astra_challenge", "astra_finalize"):
            with self.subTest(stage=stage):
                prompt = self.prompt(stage)
                self.assertEqual({outcome_questions.REVIEWER_HEADING}, headings_in(prompt))
                self.assertEqual([], missing(prompt, ("records a mechanism recommendation as a user decision",)))

    def test_the_v2_requirements_stage_gets_the_requirements_rule(self):
        state = joint_state()
        state["settings"]["planning_flow"] = "v2"
        prompt, _ = autoplanner.context(state, "requirements", Path("/tmp/state.json"))
        self.assertEqual({outcome_questions.REQUIREMENTS_HEADING}, headings_in(prompt))

    def test_a_build_job_gets_the_same_rules(self):
        # Not only designs: any request can hide a mechanism question behind its outcome.
        self.assertEqual({outcome_questions.REQUIREMENTS_HEADING}, headings_in(self.prompt("requirements_gather", "build")))
        self.assertEqual({outcome_questions.PLANNER_HEADING}, headings_in(self.prompt("astra_discovery", "build")))


class ExecutionPromptTests(unittest.TestCase):
    """By the Builder the plan is approved: no execution stage is told to ask or recommend anything."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        (cls.root / "README.md").write_text("Order pipeline\n")
        git = ["git", "-C", str(cls.root), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run(["git", "init", "-q", str(cls.root)], check=True)
        subprocess.run([*git, "add", "README.md"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "seed"], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.state = joint_state()
        self.state.update(workspace=str(self.root), settings={**self.state["settings"], "limits": {}})

    def test_builder_tester_and_completion_prompts_carry_no_outcome_rule(self):
        for stage in ("terra", "sol", "astra_plan", "astra_review", "astra_checkpoint"):
            with self.subTest(stage=stage):
                prompt, _ = stage_context.context_packet(copy.deepcopy(self.state), stage, self.root / "state.json")
                self.assertEqual(set(), headings_in(prompt))

    def test_discovery_without_joint_planning_gathers_requirements_and_plans_so_it_gets_both(self):
        state = copy.deepcopy(self.state)
        state["settings"]["joint_planning"] = False
        prompt, _ = stage_context.context_packet(state, "astra_discovery", self.root / "state.json")
        self.assertEqual({outcome_questions.REQUIREMENTS_HEADING, outcome_questions.PLANNER_HEADING},
                         headings_in(prompt))

    def test_job_recognition_carries_no_outcome_rule(self):
        self.assertEqual("", outcome_questions.rule("recognize_workflow"))
        self.assertEqual("", outcome_questions.rule("terra"))


class RuleTableTests(unittest.TestCase):
    def test_the_v2_planning_flow_maps_to_the_same_jobs(self):
        self.assertEqual(outcome_questions.REQUIREMENTS_RULE, outcome_questions.rule("requirements"))
        for stage in ("plan", "plan_revise"):
            self.assertEqual(outcome_questions.PLANNER_RULE, outcome_questions.rule(stage))
        for stage in ("plan_review", "plan_finalize"):
            self.assertEqual(outcome_questions.REVIEWER_RULE, outcome_questions.rule(stage))

    def test_the_scenario_fake_reads_these_headings(self):
        source = (Path(__file__).resolve().parents[1] / "scenarios" / "harness"
                  / "outcome_questions_provider.py").read_text()
        for heading in HEADINGS:
            self.assertIn(repr(heading), source)

    def test_each_rule_starts_with_its_own_heading(self):
        # The scenario harness's scripted model reads the headings (scenarios/ may not import tools/).
        for heading, text in zip(HEADINGS, (outcome_questions.REQUIREMENTS_RULE, outcome_questions.PLANNER_RULE,
                                            outcome_questions.REVIEWER_RULE)):
            self.assertTrue(text.lstrip().startswith(heading))
            self.assertEqual(1, sum(text.count(other) for other in HEADINGS))


if __name__ == "__main__":
    unittest.main()
