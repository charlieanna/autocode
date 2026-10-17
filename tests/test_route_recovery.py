"""The quota auto-switch recovery (#1426 step 9): switch, don't ask, inside the permitted set."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_recovery_dispatch as dispatch
import autocode_route_recovery as route_recovery
import autocode_route_selector as selector
import autocode_support as support

QUOTA = {"type": "error", "error": {"message": "subscription usage limit reached"}}
GLM = "zai-coding-plan/glm-5.3"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
SOL = "openai/gpt-6-sol"
ASTRA = "openai/gpt-6-astra"


def accounts():
    return [
        {"model": m, "tool": "opencode", "account": a, "billing": "subscription", "levels": None, "probe": "known"}
        for m, a in (
            (GLM, "Z.AI Coding Plan"),
            (MIMO, "Xiaomi Token Plan (Singapore)"),
            (SOL, "ChatGPT login"),
            (ASTRA, "ChatGPT login"),
        )
    ]


class RouteRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runtime = SimpleNamespace(now=lambda: "2026-10-17T00:00:00Z", native_calls=None)

    def state(self, error=QUOTA, *, role="sol"):
        events = self.root / "validator-01.jsonl"
        events.write_text(json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n" + json.dumps(error) + "\n")
        result = selector.select(selector.PIPELINE_ROLES, accounts())
        result["candidates"] = selector.permitted_set(accounts())
        return {
            "status": "RUNNING",
            "settings": {
                "engine": "opencode",
                "roles": {
                    role_: {"model": route["model"], "reasoning_effort": route["reasoning_effort"]}
                    for role_, route in result["routes"].items()
                },
                "route_selection": result,
            },
            "sessions": {role: "old-session"},
            "active_stage": {
                "stage": "astra_review" if role == "sol" else "terra",
                "role": role,
                "route_role": role,
                "iteration": 1,
                "output": str(self.root / "validator-01.json"),
                "events": str(events),
            },
        }

    def test_a_dead_tester_switches_on_its_own_and_the_pause_never_happens(self):
        state = self.state()
        error = support.Paused("PAUSED_BUDGET", "quota")
        self.assertTrue(route_recovery.recover(self.runtime, state, self.root, self.root, error))
        self.assertEqual(SOL, state["settings"]["roles"]["sol"]["model"])
        self.assertNotIn("sol", state.get("sessions") or {})
        (record,) = [row for row in state["user_events"] if row["kind"] == "route_assignment"]
        self.assertEqual("runner", record["actor"])
        self.assertEqual(GLM, record["from"])
        self.assertEqual(SOL, record["to"])
        self.assertEqual("quota_assigned", state["settings"]["route_selection"]["routes"]["sol"]["origin"])

    def test_when_nothing_remains_the_pause_stands(self):
        state = self.state(role="terra")  # the Builder died; only a clashing model remains
        error = support.Paused("PAUSED_BUDGET", "quota")
        self.assertFalse(route_recovery.recover(self.runtime, state, self.root, self.root, error))
        self.assertEqual(MIMO, state["settings"]["roles"]["terra"]["model"])
        self.assertNotIn("user_events", state)

    def test_a_run_without_a_permitted_set_keeps_today_s_question(self):
        state = self.state()
        del state["settings"]["route_selection"]["candidates"]
        self.assertFalse(
            route_recovery.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "q"))
        )

    def test_other_stops_and_native_runs_are_not_this_recovery_s_business(self):
        state = self.state()
        capacity = {"type": "error", "error": {"message": "Selected model is at capacity"}}
        state = self.state(error=capacity)
        self.assertFalse(
            route_recovery.recover(
                self.runtime, state, self.root, self.root, support.Paused("PAUSED_PROVIDER_CAPACITY", "c")
            )
        )
        native = SimpleNamespace(now=lambda: "t", native_calls=object())
        self.assertFalse(
            route_recovery.recover(native, self.state(), self.root, self.root, support.Paused("PAUSED_BUDGET", "q"))
        )

    def test_the_chain_fires_the_route_row_before_the_pause(self):
        state = self.state()
        self.assertEqual(
            "route",
            dispatch.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota")),
        )


if __name__ == "__main__":
    unittest.main()
