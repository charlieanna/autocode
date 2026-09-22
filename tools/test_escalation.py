"""Automatic reasoning/model escalation tests; no provider calls."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_escalation as escalation


class EscalationTests(unittest.TestCase):
    def state(self, role, model, effort, *, engine="opencode", provider=None):
        return {"settings": {"engine": engine, "roles": {role: {
                    "engine": engine, "provider": provider, "model": model,
                    "reasoning_effort": effort}}},
                "sessions": {role: "old-session"}}

    def test_exact_role_ladders(self):
        expected = {
            "astra": ["Sol High", "Sol XHigh", "Astra High"],
            "terra": ["Terra Medium", "Terra High", "Terra XHigh", "Terra Max"],
            "sol": ["Sol High", "Sol XHigh", "Astra High"],
            "completion": ["Sol Medium", "Sol High", "Astra High"],
        }
        self.assertEqual(expected, {role: [row[2] for row in ladder]
                                    for role, ladder in escalation.LADDERS.items()})

    def test_advance_changes_one_rung_and_rotates_session(self):
        state = self.state("astra", "openai/gpt-5.6-sol", "high")
        event = escalation.advance(state, "astra", trigger="rejected_output", detail="bad report")
        self.assertEqual("Sol XHigh", event["selected"]["profile"])
        self.assertEqual("xhigh", state["settings"]["roles"]["astra"]["reasoning_effort"])
        self.assertEqual({}, state["sessions"])
        self.assertEqual("old-session", state["session_rotations"][0]["old_session"])
        self.assertEqual([event], state["reasoning_escalations"])

    def test_second_sol_failure_switches_to_astra_high(self):
        state = self.state("sol", "openai/gpt-5.6-sol", "xhigh")
        event = escalation.advance(state, "sol", trigger="validation_rework")
        self.assertEqual("openai/gpt-6-astra", state["settings"]["roles"]["sol"]["model"])
        self.assertEqual("high", state["settings"]["roles"]["sol"]["reasoning_effort"])
        self.assertEqual("Astra High", event["selected"]["profile"])

    def test_same_failed_iteration_advances_only_one_rung(self):
        state = self.state("terra", "openai/gpt-5.6-terra", "medium")
        escalation.advance(state, "terra", trigger="no_progress", struggle_id="iteration:7")
        self.assertIsNone(escalation.advance(
            state, "terra", trigger="validation_rework", struggle_id="iteration:7"))
        self.assertEqual("high", state["settings"]["roles"]["terra"]["reasoning_effort"])
        self.assertEqual(1, len(state["reasoning_escalations"]))

    def test_codex_routes_keep_bare_model_names(self):
        state = self.state("completion", "gpt-5.6-sol", "high", engine="codex")
        escalation.advance(state, "completion", trigger="rejected_output")
        self.assertEqual("gpt-6-astra", state["settings"]["roles"]["completion"]["model"])

    def test_custom_provider_custom_profile_and_final_rung_are_stable(self):
        cases = [
            self.state("terra", "other/model", "medium"),
            self.state("terra", "openai/gpt-5.6-terra", "medium", provider="custom"),
            self.state("terra", "openai/gpt-5.6-terra", "max"),
        ]
        for state in cases:
            before = state["settings"]["roles"]["terra"].copy()
            self.assertIsNone(escalation.advance(state, "terra", trigger="no_progress"))
            self.assertEqual(before, state["settings"]["roles"]["terra"])
            self.assertEqual("old-session", state["sessions"]["terra"])


if __name__ == "__main__":
    unittest.main()
