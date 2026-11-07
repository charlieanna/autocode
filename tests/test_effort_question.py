"""A person-named effort the model does not declare is a pause with a named answer (#2057).

Before #2057 such a level was a refusal in prose (#2020, #2039): the chat could offer only
``Continue? [y/N]``, and continuing stopped at the same launch. These tests hold every routable
role to the rule through public functions: the status view names ``--answer effort-ROLE=EFFORT``,
the CLI parses it, the shared client offers exactly the declared levels, the headless
conformance client refuses any other, and the saved answer clears the launch guard.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import autocode_client_actions as actions
import autocode_effort_question as effort_question
import autocode_quota_route as quota_route
import autocode_route_fit as route_fit
import autocode_run_view as run_view
import autocode_stop_explanations as stop_explanations

from scenarios.harness import conformance, needs_command

GLM = "zai-coding-plan/glm-5.3"
DECLARED = ["low", "high", "max"]  # GLM 5.3 declares no medium
WORKSPACE = Path("/work/project")
RUN = WORKSPACE / ".autocode" / "runs" / "r1"


def paused(role, effort="medium", *, origin=route_fit.USER_EXPLICIT, declared=DECLARED):
    settings = {
        "engine": "opencode",
        "roles": {
            "terra": {"engine": "opencode", "model": "openai/gpt-5.6-terra", "reasoning_effort": "high"},
            role: {"engine": "opencode", "model": GLM, "reasoning_effort": effort},
        },
        "declared_variants": {GLM: list(declared)} if declared is not None else {},
    }
    if origin:
        settings["effort_origins"] = {role: origin}
    return {
        "version": 2,
        "status": effort_question.STATUS,
        "stop_reason": "named effort is not declared",
        "workspace": str(WORKSPACE),
        "run_dir": str(RUN),
        "settings": settings,
        "user_events": [],
    }


class EveryRoleContract(unittest.TestCase):
    def test_every_routable_role_pauses_with_a_named_answer_every_client_takes(self):
        for role in quota_route.ROUTABLE:
            with self.subTest(role=role):
                state = paused(role)
                with self.assertRaises(route_fit.EffortUnsupported) as raised:
                    route_fit.guard(state["settings"])
                self.assertEqual(effort_question.STATUS, raised.exception.status)
                view = run_view.view(state, workspace=WORKSPACE, run_dir=RUN)
                need = view["needs"]
                self.assertEqual("answer", need["kind"])
                self.assertEqual(f"effort-{role}", need["effort"]["question_id"])
                self.assertEqual(DECLARED, need["effort"]["declared"])
                self.assertEqual(("medium", "high"), (need["effort"]["requested"], need["effort"]["nearest"]))
                self.assertEqual(DECLARED, need["questions"][0]["options"])
                self.assertEqual("", need["questions"][0]["proposed_default"], "a person's choice, never a default")
                self.assertTrue(view["next_command"].endswith(f"--answer effort-{role}=EFFORT"), view["next_command"])
                needs_command.accept(view["next_command"])

                # The shared client: one effort decision, offered the declared levels and nothing else.
                command = actions.named(view, WORKSPACE, RUN)
                decided = actions.decisions(view, command)
                self.assertEqual([(f"effort-{role}", "effort")], [(row.key, row.kind) for row in decided])
                filled = actions.fill(view, command, {f"effort-{role}": "High"})
                self.assertEqual(["--answer", f"effort-{role}=high"], filled.argv)
                needs_command.accept(f"autocode --run-dir {RUN} --workspace {WORKSPACE} {' '.join(filled.argv)}")
                with self.assertRaisesRegex(actions.ClientGap, "Choose one of low, high, max"):
                    actions.fill(view, command, {f"effort-{role}": "medium"})

                # The headless conformance client takes only a declared level a scenario names.
                client = conformance.ConformanceDriver.__new__(conformance.ConformanceDriver)
                client.answers, client.explicit_answers = [], {f"effort-{role}": "max"}
                self.assertEqual("max", client.answer_for(need, f"effort-{role}", "EFFORT"))
                client.explicit_answers = {f"effort-{role}": "medium"}
                with self.assertRaises(conformance.ConformanceGap):
                    client.answer_for(need, f"effort-{role}", "EFFORT")
                client.explicit_answers = {}
                with self.assertRaises(conformance.ConformanceGap):
                    client.answer_for(need, f"effort-{role}", "EFFORT")

                # The saved answer is the person's level, recorded, and the launch guard clears.
                asked = effort_question.question(state)
                effort = effort_question.parse_answer([f"effort-{role}=high"], asked)
                record = effort_question.assign(state, asked, effort, at="t")
                self.assertEqual(("medium", "high"), (record["from"], record["to"]))
                self.assertEqual(effort_question.KIND, state["user_events"][-1]["kind"])
                self.assertEqual("high", state["settings"]["roles"][role]["reasoning_effort"])
                self.assertEqual(route_fit.USER_EXPLICIT, state["settings"]["effort_origins"][role])
                route_fit.guard(state["settings"])
                self.assertIsNone(effort_question.question(state))
                after = run_view.view(state, workspace=WORKSPACE, run_dir=RUN)
                self.assertNotIn("effort", after["needs"])
                self.assertIn("--resume-paused", after["next_command"])

    def test_the_pause_is_explained_and_is_a_persons_decision(self):
        text = stop_explanations.explain(effort_question.STATUS, stop_reason="x")
        self.assertIn("--answer effort-ROLE=EFFORT", text["what_the_command_does"])
        view = run_view.view(paused("plan_reviewer"), workspace=WORKSPACE, run_dir=RUN)
        self.assertEqual("your_decision", view["pause_category"])


class WhatPausesAndWhatDoesNot(unittest.TestCase):
    def test_a_runner_chosen_or_unknown_or_unlisted_effort_never_pauses(self):
        for state in (
            paused("plan_reviewer", origin="runner_default"),  # the fit moves it instead
            paused("plan_reviewer", origin=None),  # a legacy run's unknown provenance is never moved or paused
            paused("plan_reviewer", declared=None),  # no listing: the launch check decides
            paused("plan_reviewer", effort="high"),  # declared
        ):
            route_fit.guard(state["settings"])
            self.assertIsNone(effort_question.question(state))

    def test_two_roles_are_asked_in_turn(self):
        state = paused("plan_reviewer")
        state["settings"]["roles"]["completion"] = {"engine": "opencode", "model": GLM, "reasoning_effort": "xhigh"}
        state["settings"]["effort_origins"]["completion"] = route_fit.USER_EXPLICIT
        first = effort_question.question(state)
        self.assertEqual("effort-completion", first["id"])
        self.assertEqual("max", first["nearest"])
        effort_question.assign(state, first, "max", at="t")
        self.assertEqual("effort-plan_reviewer", effort_question.question(state)["id"])

    def test_an_answer_for_another_role_or_an_undeclared_level_is_refused(self):
        asked = effort_question.question(paused("plan_reviewer"))
        for answers, message in (
            (["effort-terra=high"], "not the effort question"),
            (["effort-plan_reviewer=medium"], "declares low, high, max"),
            (["effort-plan_reviewer=high", "effort-plan_reviewer=max"], "on its own"),
            (["effort-plan_reviewer"], "not the effort question"),
        ):
            with self.subTest(answers=answers), self.assertRaisesRegex(ValueError, message):
                effort_question.parse_answer(answers, asked)


if __name__ == "__main__":
    unittest.main()
