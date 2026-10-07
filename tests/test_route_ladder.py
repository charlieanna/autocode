"""One route ladder per role: tables, rung matching, serveability, outcomes."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_route_ladder as route_ladder
import autocode_escalation as escalation


class RungTests(unittest.TestCase):
    def test_rung_matching_normalizes_a_bare_openai_model(self):
        ladder = route_ladder.EFFORT_LADDERS["sol"]
        self.assertEqual(0, route_ladder.rung_index(ladder, "gpt-6-sol", "high"))
        self.assertEqual(1, route_ladder.rung_index(ladder, "openai/gpt-6-sol", "xhigh"))
        self.assertIsNone(route_ladder.rung_index(ladder, "custom/gpt-6-sol", "high"))
        self.assertIsNone(route_ladder.rung_index(ladder, "openai/gpt-6-sol", "low"))

    def test_next_rung_is_none_on_the_final_rung_and_off_any_ladder(self):
        ladder = route_ladder.EFFORT_LADDERS["astra"]
        self.assertEqual(("openai/gpt-6-astra", "xhigh", "GPT-6 Astra XHigh"),
                         route_ladder.next_rung(ladder, "openai/gpt-6-astra", "high"))
        self.assertIsNone(route_ladder.next_rung(ladder, "openai/gpt-6-astra", "max"))
        self.assertIsNone(route_ladder.next_rung(ladder, "zai-coding-plan/glm-5.3", "high"))

    def test_engine_formatting_keeps_openai_and_other_prefixes_straight(self):
        self.assertEqual("openai/gpt-6-sol", route_ladder.format_model("openai/gpt-6-sol", "opencode"))
        self.assertEqual("gpt-6-sol", route_ladder.format_model("openai/gpt-6-sol", "codex"))
        self.assertEqual("custom/gpt-6-sol", route_ladder.format_model("custom/gpt-6-sol", "codex"))
        self.assertEqual("gpt-6-sol", route_ladder.format_model("gpt-6-sol", "codex"))

    def test_strong_rung_reads_the_builder_retry_policy(self):
        self.assertEqual(("openai/gpt-6-sol", "xhigh"),
                         route_ladder.strong_rung({"strong_model": "openai/gpt-6-sol",
                                                   "strong_reasoning_effort": "xhigh"}))
        self.assertEqual(("openai/gpt-6-sol", "xhigh"),
                         route_ladder.strong_rung({"strong_model": "gpt-6-sol",
                                                   "strong_reasoning_effort": "xhigh"}))
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
        self.assertEqual((), served["astra"])
        self.assertEqual(route_ladder.EFFORT_LADDERS["sol"], served["sol"])
        self.assertEqual(route_ladder.EFFORT_LADDERS, route_ladder.served_ladders(route_ladder.EFFORT_LADDERS, None))

    def test_a_new_run_persists_filtered_ladders_and_older_runs_fall_back(self):
        persisted = route_ladder.configure_ladders(["openai/gpt-6-sol"])
        # Astra's rungs name a model the list omits: the persisted ladder
        # empties out, and the reader drops the role entirely. Persisted rungs
        # are lists, the shape state.json keeps.
        self.assertEqual([], persisted["effort"]["astra"])
        self.assertNotIn("astra", route_ladder.ladders({"route_ladders": persisted}))
        self.assertEqual(route_ladder.EFFORT_LADDERS["sol"],
                         route_ladder.ladders({"route_ladders": persisted})["sol"])
        # Runs saved before route_ladders existed keep the static tables.
        self.assertEqual(route_ladder.EFFORT_LADDERS, route_ladder.ladders({}))

    def test_an_escalation_refuses_a_rung_the_provider_cannot_serve(self):
        state = {"settings": {"engine": "opencode",
                              "roles": {"sol": {"model": "openai/gpt-6-sol", "reasoning_effort": "high"}},
                              "route_ladders": route_ladder.configure_ladders(["openai/gpt-6-astra"])},
                 "sessions": {"sol": "old"}}
        self.assertIsNone(escalation.advance(state, "sol", trigger="validation_rework"))
        self.assertEqual({"sol": "old"}, state["sessions"])
        self.assertEqual({"sol": "old"}, state["sessions"])
        full = {"settings": {**state["settings"], "route_ladders": route_ladder.configure_ladders(None)},
                "sessions": {"sol": "old"}}
        self.assertIsNotNone(escalation.advance(full, "sol", trigger="validation_rework"))


class OutcomeTests(unittest.TestCase):
    def state(self):
        return {"reasoning_escalations": [{"role": "sol", "trigger": "validation_rework"},
                                          {"role": "terra", "trigger": "no_progress", "outcome": "passed"}],
                "builder_retry_decisions": [{"action": "retry"}, {"action": "escalate"},
                                            {"action": "pause"}, {"action": "retry", "outcome": "paused"}]}

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
        state = {"settings": {"roles": {"terra": {"model": "gpt-6-sol", "reasoning_effort": "high"}}},
                 "reasoning_escalations": [{"role": "terra", "trigger": "no_progress"}],
                 "builder_retry_decisions": [{"action": "escalate"}]}
        current = {"failures": ["e1", "e2"], "action": None}
        _decide(state, current, "pause", "e3", "still failing", {}, "exhausted")
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", state["status"])
        # The open escalation and the open retry decision record how they ended;
        # the pause decision itself is the ending, so it stays unstamped.
        self.assertEqual("paused", state["reasoning_escalations"][0]["outcome"])
        self.assertEqual("paused", state["builder_retry_decisions"][0]["outcome"])
        self.assertNotIn("outcome", state["builder_retry_decisions"][1])


if __name__ == "__main__":
    unittest.main()
