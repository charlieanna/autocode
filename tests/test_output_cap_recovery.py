"""A stage that spends its whole output limit on reasoning runs again one level lower, without asking.

Live run, 2026-11-11: Requirements on MiMo 2.6 Pro at the saved ``medium`` reasoned for 64,003 tokens
in one request with no visible output, stopped at the 64k output limit, and the run asked the person
to name another model. The fixture holds that attempt's own step events (its reasoning previews and
tool calls removed).
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_output_cap as output_cap
import autocode_output_cap_recovery as recovery
import autocode_recovery_accounting as accounting
import autocode_recovery_dispatch as dispatch
import autocode_review_effort as review_effort
import autocode_stage_recovery as stage_recovery
import autocode_support as support

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "output_cap" / "mimo-requirements-reasoning-length.jsonl"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
STOP = support.Paused(output_cap.STATUS, "The response exhausted its output token limit (finish reason: length)")


class ReasoningExhaustionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runtime = SimpleNamespace(
            now=lambda: "2026-11-11T20:06:51Z",
            native_calls=None,
            timeout_recovery_guard=mock.Mock(),
            count_automatic_recovery=accounting.count,
            abandon_stage=stage_recovery.abandon_stage,
            write_json=lambda path, value: path.write_text(json.dumps(value)),
            # The recoverers ahead of this one in the shared chain; none of them takes this stop.
            automatically_recover_truncated_review=mock.Mock(return_value=False),
        )
        snapshot = {"revision": "r1", "files": {}}
        for module in (stage_recovery, recovery):
            patcher = mock.patch.object(module.source_scope, "snapshot", return_value=snapshot)
            patcher.start()
            self.addCleanup(patcher.stop)

    def state(self, *, level="medium", origin="stage_choice", events=FIXTURE):
        iteration = self.root / "iterations" / "001"
        iteration.mkdir(parents=True, exist_ok=True)
        self.attempt = getattr(self, "attempt", 2) + 1
        name = f"requirements-gather-{self.attempt:02d}"
        copied = iteration / f"{name}.jsonl"
        shutil.copyfile(events, copied)
        before = iteration / f"{name}.before.json"
        before.write_text(json.dumps({"revision": "r1", "files": {}}))
        return {
            "status": "RUNNING",
            "phase": "DISCOVERING",
            "next_stage": "requirements_gather",
            "iteration": 1,
            "settings": {
                "engine": "opencode",
                "roles": {"requirements": {"model": MIMO, "reasoning_effort": level, "engine": "opencode"}},
                "effort_origins": {"requirements": origin},
                "declared_variants": {MIMO: ["low", "medium", "high"]},
            },
            "sessions": {"requirements": "ses_e33da7166ffejn6mBBkWBaR4Xq"},
            "active_stage": {
                "stage": "requirements_gather",
                "role": "requirements",
                "route_role": "requirements",
                "iteration": 1,
                "engine": "opencode",
                "output": str(iteration / f"{name}.json"),
                "events": str(copied),
                "before_ref": str(before),
                "launch_route": {"engine": "opencode", "model": MIMO, "reasoning_effort": level},
                "reasoning_effort": level,
                "exit_code": 0,
                "timed_out": False,
                "finished_at": "2026-11-11T20:06:51Z",
                "expected_session": None,
                "duration_seconds": 1237,
            },
        }

    def test_the_live_stop_runs_again_one_level_lower_on_the_same_model(self):
        state = self.state()
        self.assertEqual("reasoning_exhaustion", dispatch.recover(self.runtime, state, self.root, self.root, STOP))
        saved = json.loads((self.root / "state.json").read_text())
        self.assertEqual(("RUNNING", "requirements_gather"), (saved["status"], saved["next_stage"]))
        self.assertNotIn("active_stage", saved)
        self.assertNotIn("stop_reason", saved)
        self.assertTrue(saved["stages"][-1]["abandoned"])
        self.assertEqual("runner", saved["user_events"][-1]["actor"])
        self.assertEqual(1, accounting.spent(saved))
        [row] = saved[recovery.KEY]
        self.assertEqual(
            ("requirements_gather", MIMO, "medium", "low", 79213, 79076),
            tuple(row[k] for k in ("stage", "model", "from", "to", "output_tokens", "reasoning_tokens")),
        )
        # The model stays the person's; only the level of this stage's launches moves, and says why.
        self.assertEqual(MIMO, saved["settings"]["roles"]["requirements"]["model"])
        self.assertEqual("medium", saved["settings"]["roles"]["requirements"]["reasoning_effort"])
        route = saved["settings"]["roles"]["requirements"]
        level, decision = review_effort.launch(saved, "requirements_gather", "requirements", False, route)
        self.assertEqual("low", level)
        self.assertEqual(("medium", recovery.REASON), (decision["ceiling"], decision["reason"]))
        self.assertIn("spent its whole output limit on reasoning", review_effort.note(decision))
        # Another stage on the same model keeps its level.
        self.assertIsNone(recovery.decide(saved, "draft_plan", "medium", model=MIMO))

    def test_a_default_route_run_reads_the_listing_it_needs_at_the_stop(self):
        # #3316: a run whose levels came from a default route records no listing at launch, so
        # nothing knows the model offers a lower level until the stop reads the tool's listing.
        state = self.state(origin="runner_default")
        del state["settings"]["declared_variants"]
        provider = object()
        with mock.patch.object(recovery, "_provider_module", return_value=provider):
            with mock.patch.object(recovery, "_listed_levels", return_value=["low", "medium", "high"]) as listed:
                self.assertEqual(
                    "reasoning_exhaustion",
                    dispatch.recover(self.runtime, state, self.root, self.root, STOP),
                )
                listed.assert_called_once_with(provider, self.root, MIMO)
        saved = json.loads((self.root / "state.json").read_text())
        [row] = saved[recovery.KEY]
        self.assertEqual(("medium", "low"), (row["from"], row["to"]))
        # The read answer is kept for the rest of the run, beside a launch's own listing.
        self.assertEqual(["low", "medium", "high"], saved["settings"]["declared_variants"][MIMO])

    def test_a_listing_that_will_not_read_leaves_the_pause_standing(self):
        state = self.state(origin="runner_default")
        del state["settings"]["declared_variants"]
        before = json.dumps(state, sort_keys=True)
        with mock.patch.object(recovery, "_provider_module", return_value=object()):
            with mock.patch.object(recovery, "_listed_levels", return_value=None):
                self.assertFalse(recovery.recover(self.runtime, state, self.root, self.root, STOP))
        self.assertEqual(before, json.dumps(state, sort_keys=True))

    def test_a_recorded_listing_is_not_read_again(self):
        with mock.patch.object(recovery, "_listed_levels", return_value=["low", "medium", "high"]) as listed:
            self.assertEqual(
                "reasoning_exhaustion",
                dispatch.recover(self.runtime, self.state(), self.root, self.root, STOP),
            )
            listed.assert_not_called()

    def test_the_pause_stands_when_a_lower_level_would_not_help_or_is_not_autocodes_to_choose(self):
        long_report = self.root / "long-report.jsonl"
        long_report.write_text(
            FIXTURE.read_text().replace('"output":0,"reasoning":64003', '"output":60000,"reasoning":4003')
        )
        cases = {
            "a long report, not reasoning": self.state(events=long_report),
            "a level the person typed": self.state(origin="user_explicit"),
            "the model's lowest level": self.state(level="low"),
        }
        repeated = self.state()
        repeated[recovery.KEY] = [{"stage": "requirements_gather", "model": MIMO, "from": "medium", "to": "low"}]
        cases["this level was already lowered once"] = repeated
        changed = self.state()
        cases["an attempt that changed source"] = changed
        repair = self.state()
        repair["active_stage"]["report_only"] = True
        cases["a format repair"] = repair
        for name, state in cases.items():
            with self.subTest(name):
                before = json.dumps(state, sort_keys=True)
                with mock.patch.object(
                    recovery.source_scope,
                    "snapshot",
                    return_value={"revision": "r2" if state is changed else "r1", "files": {}},
                ):
                    self.assertFalse(recovery.recover(self.runtime, state, self.root, self.root, STOP))
                self.assertEqual(before, json.dumps(state, sort_keys=True))
        self.assertFalse(recovery.recover(self.runtime, self.state(), self.root, self.root, support.Paused("X", "y")))


if __name__ == "__main__":
    unittest.main()
