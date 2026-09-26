"""Automatic reasoning/model escalation tests; no provider calls."""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
from pathlib import Path
import sys
import unittest

_ROOT = _Path(__file__).resolve().parents[1] if _Path(__file__).name != 'live_trial.py' else _Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / 'tools', _ROOT / 'tests', _ROOT / 'tests' / 'fakes'):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import autocode_escalation as escalation


class EscalationTests(unittest.TestCase):
    def state(self, role, model, effort, *, engine="opencode", provider=None):
        return {"settings": {"engine": engine, "roles": {role: {
                    "engine": engine, "provider": provider, "model": model,
                    "reasoning_effort": effort}}},
                "sessions": {role: "old-session"}}

    def test_exact_role_ladders(self):
        expected = {
            "astra": ["Mimo Pro High", "Mimo Pro XHigh", "Mimo Pro Max"],
            "terra": ["Mimo Pro Medium", "Mimo Pro High", "Mimo Pro XHigh", "Mimo Pro Max"],
            "sol": ["Mimo Pro High", "Mimo Pro XHigh", "Mimo Pro Max"],
            "completion": ["Mimo Pro Medium", "Mimo Pro High", "Mimo Pro Max"],
        }
        self.assertEqual(expected, {role: [row[2] for row in ladder]
                                    for role, ladder in escalation.LADDERS.items()})

    def test_advance_changes_one_rung_and_rotates_session(self):
        state = self.state("astra", "xiaomi-token-plan-sgp/mimo-v2.6-pro", "high")
        event = escalation.advance(state, "astra", trigger="rejected_output", detail="bad report")
        self.assertEqual("Mimo Pro XHigh", event["selected"]["profile"])
        self.assertEqual("xhigh", state["settings"]["roles"]["astra"]["reasoning_effort"])
        self.assertEqual({}, state["sessions"])
        self.assertEqual("old-session", state["session_rotations"][0]["old_session"])
        self.assertEqual([event], state["reasoning_escalations"])

    def test_second_sol_failure_switches_to_astra_high(self):
        state = self.state("sol", "xiaomi-token-plan-sgp/mimo-v2.6-pro", "xhigh")
        event = escalation.advance(state, "sol", trigger="validation_rework")
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", state["settings"]["roles"]["sol"]["model"])
        self.assertEqual("max", state["settings"]["roles"]["sol"]["reasoning_effort"])
        self.assertEqual("Mimo Pro Max", event["selected"]["profile"])

    def test_same_failed_iteration_advances_only_one_rung(self):
        state = self.state("terra", "xiaomi-token-plan-sgp/mimo-v2.6-pro", "medium")
        escalation.advance(state, "terra", trigger="no_progress", struggle_id="iteration:7")
        self.assertIsNone(escalation.advance(
            state, "terra", trigger="validation_rework", struggle_id="iteration:7"))
        self.assertEqual("high", state["settings"]["roles"]["terra"]["reasoning_effort"])
        self.assertEqual(1, len(state["reasoning_escalations"]))

    def test_codex_routes_keep_bare_model_names(self):
        state = self.state("completion", "mimo-v2.6-pro", "high", engine="codex")
        escalation.advance(state, "completion", trigger="rejected_output")
        self.assertEqual("mimo-v2.6-pro", state["settings"]["roles"]["completion"]["model"])

    def test_custom_provider_custom_profile_and_final_rung_are_stable(self):
        cases = [
            self.state("terra", "other/model", "medium"),
            self.state("terra", "xiaomi-token-plan-sgp/mimo-v2.6-pro", "medium", provider="custom"),
            self.state("terra", "xiaomi-token-plan-sgp/mimo-v2.6-pro", "max"),
        ]
        for state in cases:
            before = state["settings"]["roles"]["terra"].copy()
            self.assertIsNone(escalation.advance(state, "terra", trigger="no_progress"))
            self.assertEqual(before, state["settings"]["roles"]["terra"])
            self.assertEqual("old-session", state["sessions"]["terra"])

    def test_pinned_role_keeps_requested_model_after_a_struggle(self):
        state = self.state("astra", "xiaomi-token-plan-sgp/mimo-v2.6-pro", "high")
        state["settings"]["roles"]["astra"]["model_pinned"] = True
        self.assertIsNone(escalation.advance(state, "astra", trigger="rejected_output"))
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", state["settings"]["roles"]["astra"]["model"])
        self.assertEqual("high", state["settings"]["roles"]["astra"]["reasoning_effort"])
        self.assertEqual("old-session", state["sessions"]["astra"])

    def test_planning_roles_without_ladders_never_escalate(self):
        cases = [
            self.state("requirements_planner", "zai-coding-plan/glm-5.3", None),
            self.state("technical_planner", "zai-coding-plan/glm-5.3", None),
            self.state("plan_reviewer", "cursor-acp/claude-opus-5-5-high", None),
        ]
        cases[-1]["settings"]["roles"]["plan_reviewer"]["model_pinned"] = True
        for state in cases:
            role = next(iter(state["settings"]["roles"]))
            with self.subTest(role=role):
                before = state["settings"]["roles"][role].copy()
                self.assertIsNone(escalation.advance(state, role, trigger="rejected_output"))
                self.assertEqual(before, state["settings"]["roles"][role])
                self.assertEqual("old-session", state["sessions"][role])


if __name__ == "__main__":
    unittest.main()
