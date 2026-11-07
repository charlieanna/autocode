"""The quota auto-switch recovery (#1426 step 9): switch, don't ask, inside the permitted set.

With GLM, MiMo and the ChatGPT login connected, selection builds on GPT-6 Sol and checks with GLM
(#1729). A stopped Sol Builder moves to MiMo on its own. A stopped GLM Tester has nowhere to go,
because its only other candidate, Sol, is the Builder's family, so that pause stands as before.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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

        def abandon(state, run_dir, workspace, attempt, **kwargs):
            record = state.pop("active_stage")
            state.setdefault("stages", []).append({**record, "abandoned": True})
            state["status"] = "PAUSED_STAGE_ABANDONED"

        self.runtime = SimpleNamespace(
            now=lambda: "2026-10-17T00:00:00Z",
            native_calls=None,
            timeout_recovery_guard=mock.Mock(),
            count_automatic_recovery=mock.Mock(),
            abandon_stage=mock.Mock(side_effect=abandon),
            write_json=lambda path, value: path.write_text(json.dumps(value)),
        )

    def state(self, error=QUOTA, *, role="terra"):
        events = self.root / "validator-01.jsonl"
        events.write_text(json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n" + json.dumps(error) + "\n")
        result = selector.select(selector.PIPELINE_ROLES, accounts())
        result["candidates"] = selector.permitted_set(accounts())
        self.assertEqual((SOL, GLM, GLM), tuple(result["routes"][r]["model"] for r in ("terra", "sol", "completion")))
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

    def test_a_dead_builder_switches_on_its_own_and_the_pause_never_happens(self):
        state = self.state()
        error = support.Paused("PAUSED_BUDGET", "quota")
        self.assertTrue(route_recovery.recover(self.runtime, state, self.root, self.root, error))
        self.assertEqual(MIMO, state["settings"]["roles"]["terra"]["model"])
        self.assertNotIn("terra", state.get("sessions") or {})
        (record,) = [row for row in state["user_events"] if row["kind"] == "route_assignment"]
        self.assertEqual("runner", record["actor"])
        self.assertEqual(SOL, record["from"])
        self.assertEqual(MIMO, record["to"])
        self.assertEqual("quota_assigned", state["settings"]["route_selection"]["routes"]["terra"]["origin"])

    def test_a_switch_onto_another_way_of_paying_is_recorded_and_said(self):
        # #2061: the permitted set says MiMo bills per token here, so the switch from the Sol
        # subscription says so in its route record and its printed line.
        state = self.state()
        for candidates in state["settings"]["route_selection"]["candidates"].values():
            for candidate in candidates:
                if candidate["model"] == MIMO:
                    candidate["billing"] = "pay per token"
        with mock.patch("builtins.print") as printed:
            self.assertTrue(
                route_recovery.recover(
                    self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota")
                )
            )
        (record,) = [row for row in state["user_events"] if row["kind"] == "route_assignment"]
        self.assertEqual({"from": "subscription", "to": "pay per token"}, record["billing"])
        self.assertIn(f"it bills pay per token where {SOL} was subscription", printed.call_args.args[0])

    def test_a_switch_on_the_same_way_of_paying_adds_no_billing_note(self):
        state = self.state()
        self.assertTrue(
            route_recovery.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota"))
        )
        (record,) = [row for row in state["user_events"] if row["kind"] == "route_assignment"]
        self.assertNotIn("billing", record)

    def test_replacement_checks_actual_routes_after_catalogue_substitution(self):
        state = self.state()
        # The saved selection still names GLM as Tester, but the actual Tester now uses
        # MiMo. Moving the stopped Sol Builder to MiMo would have it grade its own work.
        state["settings"]["roles"]["sol"]["model"] = MIMO
        before = json.dumps(state, sort_keys=True)
        self.assertFalse(
            route_recovery.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota"))
        )
        self.assertEqual(before, json.dumps(state, sort_keys=True))
        self.runtime.abandon_stage.assert_not_called()
        self.runtime.count_automatic_recovery.assert_not_called()

    def test_exhausted_alternatives_pause_even_after_a_restart(self):
        state = self.state()
        error = support.Paused("PAUSED_BUDGET", "quota")
        attempt = dict(state["active_stage"])
        self.assertTrue(route_recovery.recover(self.runtime, state, self.root, self.root, error))
        self.runtime.abandon_stage.assert_called_once()
        # Simulate restarting from the saved checkpoint, then the replacement's quota stop.
        saved = json.loads((self.root / "state.json").read_text())
        self.assertNotIn("active_stage", saved)
        self.assertEqual("RUNNING", saved["status"])
        saved["active_stage"] = {**attempt, "iteration": 2, "command": ["opencode", "--model", MIMO]}
        # GLM is the checkers' model and Astra never builds: nothing permitted remains.
        self.assertFalse(route_recovery.recover(self.runtime, saved, self.root, self.root, error))
        self.assertEqual(MIMO, saved["settings"]["roles"]["terra"]["model"])
        self.runtime.abandon_stage.assert_called_once()

    def test_real_abandonment_retains_evidence_and_accounts_before_retry(self):
        import autocode_recovery_accounting as accounting
        import autocode_stage_recovery as stage_recovery

        state = self.state()
        state["active_stage"]["duration_seconds"] = 7
        before = self.root / "validator-01.before.json"
        before.write_text(json.dumps({"revision": "before", "files": {}}))
        state["active_stage"]["before_ref"] = str(before)
        partial = self.root / "partial.txt"
        partial.write_text("keep this work")
        self.runtime.abandon_stage = stage_recovery.abandon_stage
        self.runtime.count_automatic_recovery = accounting.count
        with mock.patch.object(
            stage_recovery.source_scope,
            "snapshot",
            return_value={"revision": "after", "files": {"partial.txt": "hash"}},
        ):
            self.assertTrue(
                route_recovery.recover(
                    self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota")
                )
            )
        saved = json.loads((self.root / "state.json").read_text())
        self.assertEqual("RUNNING", saved["status"])
        self.assertNotIn("active_stage", saved)
        self.assertEqual(7, saved["active_seconds"])
        self.assertEqual(1, accounting.spent(saved))
        self.assertEqual("keep this work", partial.read_text())
        self.assertTrue(Path(saved["stages"][-1]["events"]).is_file())
        self.assertEqual(["partial.txt"], saved["stages"][-1]["changed_files"])
        self.assertEqual("runner", saved["user_events"][-1]["actor"])
        self.assertEqual("terra", saved["next_stage"])

    def test_a_live_attempt_cannot_change_the_route(self):
        state = self.state()
        before = json.dumps(state, sort_keys=True)
        self.runtime.abandon_stage.side_effect = support.Paused("PAUSED_WORKSPACE_BUSY", "still running")
        with self.assertRaises(support.Paused):
            route_recovery.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota"))
        self.assertEqual(before, json.dumps(state, sort_keys=True))

    def test_when_nothing_remains_the_pause_stands(self):
        state = self.state(role="sol")  # the Tester died; its only other candidate is the Builder's family
        error = support.Paused("PAUSED_BUDGET", "quota")
        self.assertFalse(route_recovery.recover(self.runtime, state, self.root, self.root, error))
        self.assertEqual(GLM, state["settings"]["roles"]["sol"]["model"])
        self.assertNotIn("user_events", state)

    def test_a_pinned_model_keeps_today_s_question(self):
        # --terra-model named this Builder: the person's choice is not the selector's to replace.
        state = self.state()
        state["settings"]["route_selection"]["routes"]["terra"]["origin"] = selector.role_route.USER_EXPLICIT
        before = json.dumps(state, sort_keys=True)
        self.assertFalse(
            route_recovery.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota"))
        )
        self.assertEqual(before, json.dumps(state, sort_keys=True))
        self.runtime.abandon_stage.assert_not_called()

    def test_a_run_without_a_permitted_set_keeps_today_s_question(self):
        state = self.state()
        del state["settings"]["route_selection"]["candidates"]
        self.assertFalse(
            route_recovery.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "q"))
        )

    def test_other_stops_and_native_runs_are_not_this_recovery_s_business(self):
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
