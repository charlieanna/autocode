"""One route ladder per role: tables, rung matching, serveability, outcomes."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_escalation as escalation
import autocode_route_ladder as route_ladder


class RungTests(unittest.TestCase):
    def test_rung_matching_normalizes_a_bare_openai_model(self):
        ladder = route_ladder.EFFORT_LADDERS["sol"]
        self.assertEqual(0, route_ladder.rung_index(ladder, "gpt-6-sol", "high"))
        self.assertEqual(1, route_ladder.rung_index(ladder, "openai/gpt-6-sol", "xhigh"))
        self.assertIsNone(route_ladder.rung_index(ladder, "custom/gpt-6-sol", "high"))
        self.assertIsNone(route_ladder.rung_index(ladder, "openai/gpt-6-sol", "low"))

    def test_next_rung_is_none_on_the_final_rung_and_off_any_ladder(self):
        ladder = route_ladder.EFFORT_LADDERS["resolver"]
        self.assertEqual(
            ("openai/gpt-6-astra", "xhigh", "GPT-6 Astra XHigh"),
            route_ladder.next_rung(ladder, "openai/gpt-6-astra", "high"),
        )
        self.assertIsNone(route_ladder.next_rung(ladder, "openai/gpt-6-astra", "max"))
        self.assertIsNone(route_ladder.next_rung(ladder, "zai-coding-plan/glm-5.3", "high"))

    def test_engine_formatting_keeps_openai_and_other_prefixes_straight(self):
        self.assertEqual("openai/gpt-6-sol", route_ladder.format_model("openai/gpt-6-sol", "opencode"))
        self.assertEqual("gpt-6-sol", route_ladder.format_model("openai/gpt-6-sol", "codex"))
        self.assertEqual("custom/gpt-6-sol", route_ladder.format_model("custom/gpt-6-sol", "codex"))
        self.assertEqual("gpt-6-sol", route_ladder.format_model("gpt-6-sol", "codex"))

    def test_strong_rung_reads_the_builder_retry_policy(self):
        self.assertEqual(
            ("openai/gpt-6-sol", "xhigh"),
            route_ladder.strong_rung({"strong_model": "openai/gpt-6-sol", "strong_reasoning_effort": "xhigh"}),
        )
        self.assertEqual(
            ("openai/gpt-6-sol", "xhigh"),
            route_ladder.strong_rung({"strong_model": "gpt-6-sol", "strong_reasoning_effort": "xhigh"}),
        )
        self.assertIsNone(route_ladder.strong_rung({"strong_model": None}))
        self.assertIsNone(route_ladder.strong_rung(None))


class ServeabilityTests(unittest.TestCase):
    def test_a_published_list_decides_and_an_unknown_list_serves_everything(self):
        self.assertTrue(route_ladder.unserved("openai/gpt-6-sol", ["claude-opus-5-5"]))
        self.assertFalse(route_ladder.unserved("openai/gpt-6-sol", ["openai/gpt-6-sol"]))
        self.assertFalse(route_ladder.unserved("openai/gpt-6-sol", None))
        # A provider that publishes an empty list serves nothing.
        self.assertTrue(route_ladder.unserved("openai/gpt-6-sol", []))

    def test_served_ladders_drop_unserved_rungs_and_keep_unknown_catalogues(self):
        listed = ["openai/gpt-6-sol"]
        served = route_ladder.served_ladders(route_ladder.EFFORT_LADDERS, listed)
        self.assertEqual((), served["resolver"])
        self.assertEqual(route_ladder.EFFORT_LADDERS["sol"], served["sol"])
        self.assertEqual(route_ladder.EFFORT_LADDERS, route_ladder.served_ladders(route_ladder.EFFORT_LADDERS, None))

    def test_a_new_run_persists_filtered_ladders_and_older_runs_fall_back(self):
        persisted = route_ladder.configure_ladders(["openai/gpt-6-sol"])
        # The Resolver's Astra rungs name a model the list omits: the persisted
        # ladder empties out, and the reader drops the role entirely. Persisted
        # rungs are lists, the shape state.json keeps.
        self.assertEqual([], persisted["effort"]["resolver"])
        self.assertNotIn("resolver", route_ladder.ladders({"route_ladders": persisted}))
        self.assertEqual(route_ladder.EFFORT_LADDERS["sol"], route_ladder.ladders({"route_ladders": persisted})["sol"])
        # Runs saved before route_ladders existed keep the static tables.
        self.assertEqual(route_ladder.EFFORT_LADDERS, route_ladder.ladders({}))

    def test_a_ladder_saved_under_astra_is_read_as_the_resolvers(self):
        """Before #1345 the Astra ladder was persisted under "astra", the per-task Planner's
        route, while the Resolver runs on "resolver". The saved, serveability-filtered
        ladder is read as the Resolver's at read time; state.json is not migrated."""
        astra = [list(rung) for rung in route_ladder.EFFORT_LADDERS["resolver"]]
        sol = [list(rung) for rung in route_ladder.EFFORT_LADDERS["sol"]]
        saved = {"route_ladders": {"effort": {"astra": astra, "sol": sol}}}
        read = route_ladder.ladders(saved)
        self.assertEqual({"resolver", "sol"}, set(read))
        self.assertEqual(route_ladder.EFFORT_LADDERS["resolver"], read["resolver"])
        self.assertEqual({"effort": {"astra": astra, "sol": sol}}, saved["route_ladders"])
        # A saved run whose provider served no Astra kept the ladder empty: no Resolver ladder.
        emptied = route_ladder.ladders({"route_ladders": {"effort": {"astra": [], "sol": sol}}})
        self.assertEqual({"sol"}, set(emptied))
        # The static table has no "astra" key either, so a run saved before
        # route_ladders existed climbs the Resolver and not the astra route.
        self.assertNotIn("astra", route_ladder.EFFORT_LADDERS)

    def test_an_escalation_refuses_a_rung_the_provider_cannot_serve(self):
        state = {
            "settings": {
                "engine": "opencode",
                "roles": {"sol": {"model": "openai/gpt-6-sol", "reasoning_effort": "high"}},
                "route_ladders": route_ladder.configure_ladders(["openai/gpt-6-astra"]),
            },
            "sessions": {"sol": "old"},
        }
        self.assertIsNone(escalation.advance(state, "sol", trigger="validation_rework"))
        self.assertEqual({"sol": "old"}, state["sessions"])
        self.assertEqual({"sol": "old"}, state["sessions"])
        full = {
            "settings": {**state["settings"], "route_ladders": route_ladder.configure_ladders(None)},
            "sessions": {"sol": "old"},
        }
        self.assertIsNotNone(escalation.advance(full, "sol", trigger="validation_rework"))


class NextRouteTests(unittest.TestCase):
    def settings(self, role="sol", model="openai/gpt-6-sol", effort="high", **route):
        return {"engine": "opencode", "roles": {role: {"model": model, "reasoning_effort": effort, **route}}}

    def test_a_struggling_role_climbs_one_rung_spelled_for_its_engine(self):
        self.assertEqual(
            {
                "model": "openai/gpt-6-sol",
                "reasoning_effort": "xhigh",
                "profile": "GPT-6 Sol XHigh",
                "previous_profile": "GPT-6 Sol High",
            },
            route_ladder.next_route(self.settings(), "sol"),
        )
        codex = self.settings(model="gpt-6-sol", engine="codex")
        self.assertEqual("gpt-6-sol", route_ladder.next_route(codex, "sol")["model"])

    def test_every_reason_a_role_stays_is_named(self):
        climbed = {"struggle_id": "iteration:3", "escalations": [{"role": "sol", "struggle_id": "iteration:3"}]}
        cases = {
            "fixed_profile": ({**self.settings(), "conversation_profile": "continuous-v1"}, "sol", None),
            "climbed": (self.settings(), "sol", climbed),
            "no_route": (self.settings(), "completion", None),
            "pinned": (self.settings(model_pinned=True), "sol", None),
            "other_provider": (self.settings(provider="custom"), "sol", None),
            "off_ladder": (self.settings(model="zai-coding-plan/glm-5.3"), "sol", None),
            "ladder_end": (self.settings(effort="max"), "sol", None),
        }
        self.assertEqual(set(route_ladder.STAYS), set(cases))
        for reason, (settings, role, evidence) in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual({"stay": reason}, route_ladder.next_route(settings, role, evidence))
        # Another struggle, or another role's climb for the same one, still climbs.
        for evidence in (
            {**climbed, "struggle_id": "iteration:4"},
            {**climbed, "escalations": [{"role": "terra", "struggle_id": "iteration:3"}]},
        ):
            self.assertIn("model", route_ladder.next_route(self.settings(), "sol", evidence))

    def test_it_climbs_only_the_rungs_the_run_persisted(self):
        settings = self.settings()
        settings["route_ladders"] = {"effort": {"sol": [["openai/gpt-6-sol", "high", "GPT-6 Sol High"]]}}
        self.assertEqual({"stay": "ladder_end"}, route_ladder.next_route(settings, "sol"))
        settings["route_ladders"]["effort"]["sol"] = []
        self.assertEqual({"stay": "off_ladder"}, route_ladder.next_route(settings, "sol"))


class ServeTests(unittest.TestCase):
    """A new run's rungs against the one model list its provider gave for its routes (#612)."""

    OFFERED = {"zai-coding-plan/glm-5.3", "openai/gpt-6-sol", "openai/gpt-6-luna", "openai/gpt-6-astra"}

    def settings(self, *, strong="openai/gpt-6-sol", checker="zai-coding-plan/glm-5.3", **models):
        routes = {"terra": "zai-coding-plan/glm-5.3", "sol": "openai/gpt-6-sol", "completion": "openai/gpt-6-sol"}
        routes.update(models)
        return {
            "engine": "opencode",
            "roles": {role: {"model": model, "reasoning_effort": "high"} for role, model in routes.items()},
            "builder_retry": {"strong_model": strong, "strong_reasoning_effort": "xhigh", "checker_model": checker},
            "route_ladders": route_ladder.configure_ladders(None),
        }

    def test_a_provider_that_offers_every_rung_keeps_them_all(self):
        settings = self.settings()
        expected = copy.deepcopy(settings)
        self.assertIs(settings, route_ladder.serve(settings, self.OFFERED))
        self.assertEqual(expected, settings)

    def test_a_provider_that_cannot_list_keeps_every_rung(self):
        settings = self.settings(strong="openai/gpt-7-nope")
        expected = copy.deepcopy(settings)
        self.assertEqual(expected, route_ladder.serve(settings, None, named_strong="openai/gpt-7-nope"))

    def test_effort_rungs_the_provider_does_not_offer_are_removed(self):
        settings = route_ladder.serve(self.settings(), self.OFFERED - {"openai/gpt-6-astra"})
        self.assertEqual([], settings["route_ladders"]["effort"]["resolver"])
        self.assertEqual(
            [list(rung) for rung in route_ladder.EFFORT_LADDERS["sol"]], settings["route_ladders"]["effort"]["sol"]
        )

    def test_an_unoffered_strong_model_leaves_the_run_without_a_stronger_attempt(self):
        settings = route_ladder.serve(
            self.settings(sol="openai/gpt-6-luna", completion="openai/gpt-6-luna"),
            {
                "zai-coding-plan/glm-5.3",
                "openai/gpt-6-luna",
            },
        )
        self.assertIsNone(settings["builder_retry"]["strong_model"])
        self.assertEqual("openai/gpt-6-sol", settings["builder_retry"]["not_offered"])

    def test_an_unoffered_checker_model_matters_only_when_a_checker_would_move_to_it(self):
        offered = self.OFFERED - {"zai-coding-plan/glm-5.3"}
        # The Tester and Completion Reviewer run the strong model, so the stronger attempt moves them.
        settings = route_ladder.serve(self.settings(terra="openai/gpt-6-luna"), offered)
        self.assertIsNone(settings["builder_retry"]["strong_model"])
        self.assertEqual("zai-coding-plan/glm-5.3", settings["builder_retry"]["not_offered"])
        # Checkers on another model stay where they are, so the checker model is never launched.
        kept = self.settings(terra="openai/gpt-6-luna", strong="openai/gpt-6-astra")
        self.assertEqual("openai/gpt-6-astra", route_ladder.serve(kept, offered)["builder_retry"]["strong_model"])

    def test_a_builder_that_can_never_escalate_keeps_its_policy(self):
        """A pinned or custom-provider Builder pauses instead of escalating, so no rung of its attempt launches."""
        offered = {"openai/gpt-6-luna", "zai-coding-plan/glm-5.3"}
        for route in ({"model_pinned": True}, {"provider": "custom"}):
            with self.subTest(route=route):
                settings = self.settings(
                    terra="openai/gpt-6-luna", sol="openai/gpt-6-luna", completion="openai/gpt-6-luna"
                )
                settings["roles"]["terra"].update(route)
                expected = copy.deepcopy(settings["builder_retry"])
                served = route_ladder.serve(settings, offered, named_strong="openai/gpt-6-sol")
                self.assertEqual(expected, served["builder_retry"])

    def test_only_a_checker_the_stronger_attempt_would_move_needs_the_checker_model(self):
        offered = self.OFFERED - {"zai-coding-plan/glm-5.3"}
        for change, route in (
            ("pinned", {"model_pinned": True}),
            ("custom provider", {"provider": "custom"}),
            ("bare name", {"model": "gpt-6-sol"}),
            # The OpenCode list does not describe a Codex-engine route, whatever its model's spelling.
            ("Codex engine", {"engine": "codex"}),
        ):
            with self.subTest(checkers=change):
                settings = self.settings(terra="openai/gpt-6-luna")
                for role in ("sol", "completion"):
                    settings["roles"][role].update(route)
                served = route_ladder.serve(settings, offered)
                self.assertEqual("openai/gpt-6-sol", served["builder_retry"]["strong_model"])
                self.assertNotIn("not_offered", served["builder_retry"])

    def test_a_strong_model_the_person_named_is_refused_instead_of_dropped(self):
        settings = self.settings(strong="openai/gpt-7-nope")
        expected = copy.deepcopy(settings)
        with self.assertRaisesRegex(ValueError, "--builder-strong-model openai/gpt-7-nope is not offered"):
            route_ladder.serve(settings, self.OFFERED, named_strong="openai/gpt-7-nope")
        offered = self.OFFERED - {"zai-coding-plan/glm-5.3"}
        with self.assertRaisesRegex(ValueError, "to zai-coding-plan/glm-5.3, which this run's provider does not offer"):
            route_ladder.serve(self.settings(terra="openai/gpt-6-luna"), offered, named_strong="openai/gpt-6-sol")
        self.assertEqual("openai/gpt-7-nope", settings["builder_retry"]["strong_model"])
        self.assertEqual(expected["builder_retry"], settings["builder_retry"])

    def test_only_opencode_routes_are_checked_against_the_opencode_list(self):
        settings = self.settings(strong="gpt-6-sol")
        settings["roles"]["terra"]["engine"] = "codex"
        settings["roles"]["sol"]["engine"] = "codex"
        served = route_ladder.serve(settings, {"zai-coding-plan/glm-5.3"})
        self.assertEqual("gpt-6-sol", served["builder_retry"]["strong_model"])
        self.assertEqual(
            [list(rung) for rung in route_ladder.EFFORT_LADDERS["sol"]], served["route_ladders"]["effort"]["sol"]
        )
        self.assertEqual([], served["route_ladders"]["effort"]["completion"])

    def test_a_bare_strong_model_is_offered_under_its_openai_name(self):
        settings = route_ladder.serve(self.settings(strong="gpt-6-sol"), self.OFFERED)
        self.assertEqual("gpt-6-sol", settings["builder_retry"]["strong_model"])

    def test_serve_filters_only_specified_roles_and_preserves_other_ladders(self):
        settings = self.settings()
        # The provider offers none of the models on any effort ladder:
        # when roles={"sol"} is specified, only sol's ladder is filtered (emptied).
        # resolver, terra, and completion ladders are preserved.
        served = route_ladder.serve(settings, ["some-custom-model"], roles={"sol"})
        self.assertEqual([], served["route_ladders"]["effort"]["sol"])
        self.assertEqual(
            [list(rung) for rung in route_ladder.EFFORT_LADDERS["terra"]],
            served["route_ladders"]["effort"]["terra"],
        )
        self.assertEqual(
            [list(rung) for rung in route_ladder.EFFORT_LADDERS["completion"]],
            served["route_ladders"]["effort"]["completion"],
        )
        self.assertEqual(
            [list(rung) for rung in route_ladder.EFFORT_LADDERS["resolver"]],
            served["route_ladders"]["effort"]["resolver"],
        )

    def test_serve_maps_astra_to_resolver(self):
        settings = self.settings()
        served = route_ladder.serve(settings, ["some-custom-model"], roles={"astra"})
        self.assertEqual([], served["route_ladders"]["effort"]["resolver"])
        self.assertEqual(
            [list(rung) for rung in route_ladder.EFFORT_LADDERS["sol"]],
            served["route_ladders"]["effort"]["sol"],
        )

    def test_serve_leaves_builder_retry_untouched_when_terra_not_in_roles(self):
        settings = self.settings()
        expected = copy.deepcopy(settings["builder_retry"])
        # Provider offers neither strong_model nor checker_model, but terra is not in roles:
        served = route_ladder.serve(settings, ["some-custom-model"], roles={"sol"}, named_strong="openai/gpt-6-sol")
        self.assertEqual(expected, served["builder_retry"])


class OutcomeTests(unittest.TestCase):
    def state(self):
        return {
            "reasoning_escalations": [
                {"role": "sol", "trigger": "validation_rework"},
                {"role": "terra", "trigger": "no_progress", "outcome": "passed"},
            ],
            "builder_retry_decisions": [
                {"action": "retry"},
                {"action": "escalate"},
                {"action": "pause"},
                {"action": "retry", "outcome": "paused"},
            ],
        }

    def test_record_outcome_stamps_only_open_events_and_decisions(self):
        state = self.state()
        stamped = route_ladder.record_outcome(state, "passed")
        self.assertEqual(3, len(stamped))
        self.assertEqual("passed", state["reasoning_escalations"][0]["outcome"])
        # The already-stamped event keeps its recorded outcome.
        self.assertEqual("passed", state["reasoning_escalations"][1]["outcome"])
        self.assertEqual("passed", state["builder_retry_decisions"][0]["outcome"])
        self.assertEqual("passed", state["builder_retry_decisions"][1]["outcome"])
        self.assertNotIn("outcome", state["builder_retry_decisions"][2])  # a pause decision is not a retry
        self.assertEqual("paused", state["builder_retry_decisions"][3]["outcome"])
        # Stamping twice changes nothing further.
        self.assertEqual([], route_ladder.record_outcome(state, "passed"))

    def test_summary_counts_passed_paused_and_open(self):
        state = self.state()
        self.assertEqual({"passed": 1, "paused": 1, "open": 3}, route_ladder.outcome_summary(state))
        route_ladder.record_outcome(state, "paused")
        self.assertEqual({"passed": 1, "paused": 4, "open": 0}, route_ladder.outcome_summary(state))

    def test_a_pause_decision_stamps_its_run_out(self):
        from autocode_builder_policy import _decide

        state = {
            "settings": {"roles": {"terra": {"model": "gpt-6-sol", "reasoning_effort": "high"}}},
            "reasoning_escalations": [{"role": "terra", "trigger": "no_progress"}],
            "builder_retry_decisions": [{"action": "escalate"}],
        }
        current = {"failures": ["e1", "e2"], "action": None}
        _decide(state, current, "pause", "e3", "still failing", {}, "exhausted")
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", state["status"])
        # The open escalation and the open retry decision record how they ended;
        # the pause decision itself is the ending, so it stays unstamped.
        self.assertEqual("paused", state["reasoning_escalations"][0]["outcome"])
        self.assertEqual("paused", state["builder_retry_decisions"][0]["outcome"])
        self.assertNotIn("outcome", state["builder_retry_decisions"][1])


class PassStampTests(unittest.TestCase):
    """record_pass: the runner's `passed` stamp where the next task is assigned on a validation PASS (#612)."""

    PASSED = "2026-10-16T10:00:00+00:00"  # when the Tester attempt that passed the work started

    def state(self, **change):
        state = {
            "stages": [{"stage": "sol", "output": "sol-02.json", "started_at": self.PASSED}],
            "validation": {"verdict": "PASS", "output": "sol-02.json"},
            "reasoning_escalations": [{"role": "sol", "trigger": "validation_rework"}],
            "builder_retry_decisions": [{"action": "retry", "at": "2026-10-16T09:30:00+00:00"}],
            "current_task": {
                "id": "t3",
                "kind": "implement",
                "decision": "CONTINUE",
                "assigned_at": "2026-10-16T10:09:00+00:00",
            },
        }
        return {**state, **change}

    def stamped(self, state):
        route_ladder.record_pass(state)
        return [
            row.get("outcome", "open") for row in (*state["reasoning_escalations"], *state["builder_retry_decisions"])
        ]

    def test_work_the_run_moves_on_from_stamps_every_open_escalation(self):
        self.assertEqual(["passed", "passed"], self.stamped(self.state()))

    def test_work_sent_back_since_the_pass_leaves_the_builders_own_open(self):
        sent_back = {
            "a stronger attempt charged on the review": self.state(
                builder_retry_decisions=[
                    {"action": "retry", "at": "2026-10-16T09:30:00+00:00"},
                    {"action": "escalate", "at": "2026-10-16T10:05:00+00:00"},
                ]
            ),
            "a correction task": self.state(
                current_task={"kind": "implement", "decision": "REWORK", "assigned_at": "2026-10-16T10:09:00+00:00"}
            ),
            "a Builder climb and a correction task": self.state(
                reasoning_escalations=[{"role": "sol"}, {"role": "terra"}],
                current_task={"kind": "implement", "decision": "REWORK", "assigned_at": "2026-10-16T10:09:00+00:00"},
            ),
        }
        for case, state in sent_back.items():
            with self.subTest(case):
                outcomes = self.stamped(state)
                # The Tester's own climb passed its validation, as before; the Builder's work did not pass.
                self.assertEqual("passed", outcomes[0])
                self.assertEqual({"open"}, set(outcomes[1:]))
                self.assertTrue(route_ladder.sent_back(state))

    def test_a_charge_or_correction_before_the_pass_is_not_a_send_back(self):
        state = self.state(
            builder_retry_decisions=[
                {"action": "retry", "at": "2026-10-16T09:30:00+00:00"},
                {"action": "escalate", "at": "2026-10-16T09:50:00+00:00"},
            ],
            task_archive=[{"kind": "implement", "decision": "REWORK", "assigned_at": "2026-10-16T09:51:00+00:00"}],
        )
        self.assertFalse(route_ladder.sent_back(state))
        self.assertEqual(["passed", "passed", "passed"], self.stamped(state))

    def test_an_unknown_pass_leaves_the_builders_own_open(self):
        for case, stages in {"no record of it": [], "no start": [{"output": "sol-02.json"}]}.items():
            with self.subTest(case):
                self.assertEqual(["passed", "open"], self.stamped(self.state(stages=stages)))


if __name__ == "__main__":
    unittest.main()
