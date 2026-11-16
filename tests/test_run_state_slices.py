"""Regression tests for RunState Phase 2 domain codecs and fixture isolation (#3468)."""

from __future__ import annotations

import copy
import json
import unittest

import autocode_budget_state as budget_state
import autocode_evidence_export as evidence_export
import autocode_fixture_state as fixture_state
import autocode_interaction_timing as interaction_timing
import autocode_regression_state as regression_state
import autocode_run_setup as run_setup
from autocode_test_framework import Framework


class FixtureEnvelopeTests(unittest.TestCase):
    def test_reads_envelope_falls_back_to_legacy_and_strips_on_write(self):
        state = {
            "_fixture_accepted": True,
            "_fixture_attempt": 2,
            "_fixture": {"attempt": 9, "custom_hook": {"keep": 1}},
        }
        original = copy.deepcopy(state)
        envelope = fixture_state.FixtureEnvelope.from_state(state)
        self.assertEqual(original, state)
        self.assertIs(True, envelope.get("accepted"))
        self.assertEqual(9, envelope.get("attempt"))
        self.assertEqual({"keep": 1}, envelope.get("custom_hook"))

        updated = envelope.with_field("monitor", {"ok": True}).without_field("accepted")
        updated.write_to_state(state)
        self.assertEqual(
            {"attempt": 9, "custom_hook": {"keep": 1}, "monitor": {"ok": True}},
            state["_fixture"],
        )
        for legacy_key in fixture_state.LEGACY_KEYS.values():
            self.assertNotIn(legacy_key, state)

    def test_empty_envelope_removes_top_level_key(self):
        state = {"_fixture_monitor": {"x": 1}}
        fixture_state.FixtureEnvelope().write_to_state(state)
        self.assertEqual({}, state)


class TimingSliceTests(unittest.TestCase):
    def test_lossless_round_trip_unknown_keys_and_corrupt_value(self):
        raw_v3 = {
            "launched_at": "2026-10-10T08:00:00+00:00",
            "first_question_at": None,
            "first_plan_at": None,
            "first_builder_at": None,
            "future_field": {"preserved": [1, 2]},
        }
        state = {"interaction_timing": copy.deepcopy(raw_v3)}
        sliced = interaction_timing.TimingSlice.from_state(state)
        self.assertEqual(raw_v3, sliced.to_state_value())

        self.assertTrue(interaction_timing.mark(state, "plan", "2026-10-10T08:01:00+00:00"))
        self.assertFalse(interaction_timing.mark(state, "plan", "2026-10-10T08:05:00+00:00"))
        self.assertEqual("2026-10-10T08:01:00+00:00", state["interaction_timing"]["first_plan_at"])
        self.assertEqual({"preserved": [1, 2]}, state["interaction_timing"]["future_field"])

        corrupt = {"interaction_timing": "not-a-dict"}
        self.assertEqual("not-a-dict", interaction_timing.TimingSlice.from_state(corrupt).to_state_value())
        self.assertEqual(
            {
                "launched_at": None,
                "first_question_at": None,
                "first_question_seconds": None,
                "first_plan_at": None,
                "first_plan_seconds": None,
                "first_builder_at": None,
                "first_builder_seconds": None,
            },
            interaction_timing.project({}),
        )

    def test_evidence_export_binding_matches_with_or_without_timing(self):
        base = {
            "task_id": "t1",
            "run_dir": "/tmp/run",
            "created_at": "2026-10-10T08:00:00+00:00",
            "settings": {"workflow": {}},
        }
        b_no_timing = evidence_export.binding(base, {"attempts": [], "totals": {}})
        with_timing = {**base, "interaction_timing": {"launched_at": "2026-10-10T08:00:00+00:00"}}
        b_with_timing = evidence_export.binding(with_timing, {"attempts": [], "totals": {}})
        self.assertNotEqual(b_no_timing, b_with_timing)
        self.assertEqual(
            b_with_timing, evidence_export.binding(copy.deepcopy(with_timing), {"attempts": [], "totals": {}})
        )


class BudgetSliceTests(unittest.TestCase):
    def test_absent_falsy_and_corrupt_round_trip(self):
        absent: dict = {}
        sliced = budget_state.BudgetSlice.from_state(absent)
        self.assertIsNone(sliced.raw_active_seconds)
        self.assertEqual(0, sliced.active_seconds_default_zero)
        self.assertEqual({}, sliced.inherited_spend_dict())

        budget_state.add_active_seconds(absent, 12.5)
        self.assertEqual(12.5, absent["active_seconds"])

        falsy = {"active_seconds": None}
        budget_state.add_active_seconds(falsy, 5, treat_falsy_as_zero=True)
        self.assertEqual(5, falsy["active_seconds"])

        inherited = {"usd": 1.25, "unpriced_stages": 1, "partial_stages": 0, "extra": "keep"}
        state = {"active_seconds": 30, "inherited_spend": copy.deepcopy(inherited)}
        roundtrip: dict = {}
        budget_state.BudgetSlice.from_state(state).write_to_state(roundtrip)
        self.assertEqual(state, roundtrip)

        corrupt = {"inherited_spend": "corrupt"}
        self.assertEqual({}, budget_state.inherited_spend_dict(corrupt))
        corrupt_out: dict = {}
        budget_state.BudgetSlice.from_state(corrupt).write_to_state(corrupt_out)
        self.assertEqual(corrupt, corrupt_out)

    def test_reassertable_time_bound_uses_callable_reader(self):
        time_bound = next(row for row in run_setup.REASSERTABLE_BOUNDS if row[0] == "PAUSED_TIME_LIMIT")
        used_fn = time_bound[3]
        self.assertTrue(callable(used_fn))
        self.assertEqual(0, used_fn({}))
        self.assertEqual(42, used_fn({"active_seconds": 42}))


class BaselineStateSliceTests(unittest.TestCase):
    def test_absent_none_corrupt_and_nested_round_trip(self):
        self.assertTrue(regression_state.is_optional_dict({}))
        self.assertFalse(regression_state.has_value({}))
        self.assertTrue(regression_state.is_optional_dict({"regression_baseline": None}))
        self.assertFalse(regression_state.has_value({"regression_baseline": None}))
        self.assertFalse(regression_state.is_optional_dict({"regression_baseline": "corrupt"}))
        self.assertTrue(regression_state.has_value({"regression_baseline": "corrupt"}))
        self.assertEqual({}, regression_state.mapping({"regression_baseline": "corrupt"}))

        state: dict = {}
        regression_state.write_settled_timeouts(state, {"k1": {"path": "/tmp/a.json", "sha256": "abc"}})
        self.assertIn("k1", state["regression_baseline"]["settled_timeouts"])

        whole = {"command": "pytest", "path": "/tmp/whole.json", "extra": 7}
        regression_state.write_record(state, whole, preserve_settled_timeouts=True)
        self.assertEqual("/tmp/whole.json", state["regression_baseline"]["path"])
        self.assertEqual(7, state["regression_baseline"]["extra"])
        self.assertIn("k1", state["regression_baseline"]["settled_timeouts"])

        regression_state.write_whole(state, {"command": "pytest -q", "path": "/tmp/w2.json"})
        self.assertEqual("pytest -q", state["regression_baseline"]["whole"]["command"])
        serialized = json.loads(json.dumps(state))
        reloaded: dict = {}
        regression_state.BaselineStateSlice.from_state(serialized).write_to_state(reloaded)
        self.assertEqual(serialized, reloaded)
        self.assertFalse(regression_state.BaselineStateSlice.from_state(serialized).has_value() is False)
        _ = Framework("pytest", "pytest")


if __name__ == "__main__":
    unittest.main()
