"""A run's spend against its money cap (#721): the pure projection. End to end: tests/test_cost_cap.py."""

import unittest

import autocode_cost as cost
import autocode_prices as prices

TOKENS = {"input_tokens": 1000, "output_tokens": 200}


def stage(model="gpt-5.6-sol", tokens=TOKENS, **fields):
    return {"stage": "sol", "role": "sol", "model": model, "metrics": {"provider_tokens": dict(tokens)}, **fields}


def run(*stages, cap=None, **fields):
    limits = {} if cap is None else {"max_cost_usd": cap}
    return {"settings": {"limits": limits}, "stages": list(stages), **fields}


class CostProjectionTests(unittest.TestCase):
    def test_each_stage_has_one_basis_and_unknown_is_never_zero(self):
        priced = prices.estimate("gpt-5.6-sol", TOKENS)
        reported = stage(metrics={"provider_tokens": TOKENS, "provider_cost_usd": 0.5})
        runner = {"stage": "regression", "runner_owned": True, "engine": "runner", "metrics": {}}
        view = cost.view(run(stage(), reported, runner, stage("mystery-model"), cap=2))
        self.assertAlmostEqual(priced, view["estimated_usd"])
        self.assertEqual(0.5, view["reported_usd"])
        self.assertAlmostEqual(priced + 0.5, view["spent_usd"])
        self.assertEqual((2.0, 1, False), (view["cap_usd"], view["unpriced_stages"], view["complete"]))
        self.assertEqual(prices.table()["as_of"], view["prices_as_of"])
        self.assertEqual(set(cost.FIELDS), set(view))

    def test_nothing_known_is_null_and_nothing_spent_is_zero(self):
        unknown = cost.view(run(stage("mystery-model")))
        self.assertEqual((None, None, None), (unknown["estimated_usd"], unknown["reported_usd"], unknown["spent_usd"]))
        self.assertIn(
            "Cost: unknown; 1 stage not priced: a model without a rate or no token counts", cost.line(unknown)
        )
        # A priced model whose provider recorded no token counts is unpriced too.
        silent = cost.view(run(stage(tokens={})))
        self.assertEqual((None, 1), (silent["spent_usd"], silent["unpriced_stages"]))
        fresh = cost.view(run())
        self.assertEqual(
            (None, None, 0.0, True),
            (fresh["estimated_usd"], fresh["reported_usd"], fresh["spent_usd"], fresh["complete"]),
        )
        self.assertEqual("Cost: $0.00 so far; no cap.", cost.line(fresh))

    def test_a_partial_or_running_stage_makes_the_spend_a_floor(self):
        partial = stage(metrics={"provider_tokens": TOKENS, "provider_tokens_partial": True})
        for state in (run(partial), run(stage(), active_stage={"stage": "terra"})):
            with self.subTest(state=state):
                view = cost.view(state)
                self.assertFalse(view["complete"])
                self.assertTrue(cost.line(view).startswith("Cost: at least $"))

    def test_running_parallel_builders_keep_the_total_a_floor(self):
        # Workers' spend reaches the run only when their batch is accounted (autocode_dispatch.account_workers).
        batch = {"status": "BUILDING", "workers": [{"milestone_id": "M2", "status": "RUNNING"}]}
        view = cost.view(run(stage(), orchestration_batch=batch))
        self.assertFalse(view["complete"])
        self.assertTrue(cost.line(view).startswith("Cost: at least $"))
        done = {"status": "BUILT", "workers": [{"milestone_id": "M2", "status": "COMPLETE"}]}
        self.assertTrue(cost.view(run(stage(), orchestration_batch=done))["complete"])

    def test_only_a_positive_finite_saved_cap_is_a_cap(self):
        for saved in (None, 0, -1, float("nan"), float("inf"), "5", True):
            with self.subTest(saved=saved):
                self.assertIsNone(cost.cap({"limits": {"max_cost_usd": saved}}))
                self.assertIsNone(cost.exceeded(run(stage(), cap=saved)))
        self.assertIsNone(cost.cap("damaged"))
        self.assertEqual(3.0, cost.cap({"limits": {"max_cost_usd": 3}}))

    def test_the_cap_is_reached_at_or_above_it_and_counts_only_known_spend(self):
        spent = prices.estimate("gpt-5.6-sol", TOKENS)
        self.assertEqual((spent, spent), cost.exceeded(run(stage(), cap=spent)))
        self.assertIsNone(cost.exceeded(run(stage(), cap=spent * 1.01)))
        # A stage without a rate cannot trip it: the line says the spend is a floor.
        self.assertIsNone(cost.exceeded(run(stage("mystery-model"), cap=0.000001)))

    def test_claude_aliases_reach_the_cap_and_reported_cost_still_wins(self):
        for alias in ("anthropic/claude-opus-5-5", "kilo/anthropic/claude-opus-5-5"):
            with self.subTest(alias=alias):
                state = run(stage(alias), cap=0.008)
                self.assertEqual((0.008, 0.008), cost.exceeded(state))
                self.assertEqual(0, cost.view(state)["unpriced_stages"])
                state = run(stage(alias, metrics={"provider_cost_usd": 0.01, "provider_tokens": TOKENS}), cap=0.01)
                self.assertEqual((0.01, 0.01), cost.exceeded(state))
                self.assertIsNone(cost.view(state)["estimated_usd"])

    def test_a_climb_is_priced_from_the_last_attempts_tokens_at_the_stronger_models_rate(self):
        attempt = stage("gpt-5.6-luna", stage="terra")
        spent = prices.estimate("gpt-5.6-luna", TOKENS)
        projected = prices.estimate("gpt-5.6-sol", TOKENS)
        self.assertEqual(
            {"usd": projected, "input_tokens": 1000, "output_tokens": 200}, cost.attempt("gpt-5.6-sol", attempt)
        )
        over = cost.crossing(run(attempt, cap=spent + projected / 2), "gpt-5.6-sol", attempt)
        self.assertEqual(
            {"projected": projected, "spent": spent, "total": spent + projected, "cap": spent + projected / 2},
            {key: over[key] for key in ("projected", "spent", "total", "cap")},
        )
        # It fits exactly at the cap; no cap, no rate or no token counts never pause.
        self.assertIsNone(cost.crossing(run(attempt, cap=spent + projected), "gpt-5.6-sol", attempt))
        self.assertIsNone(cost.crossing(run(attempt), "gpt-5.6-sol", attempt))
        self.assertIsNone(cost.crossing(run(attempt, cap=0.000001), "mystery-model", attempt))
        silent = stage("gpt-5.6-luna", tokens={})
        self.assertIsNone(cost.crossing(run(silent, cap=0.000001), "gpt-5.6-sol", silent))

    def test_a_refused_climb_names_the_attempt_the_arithmetic_and_the_cap_to_resume_with(self):
        over = {
            "projected": 0.42,
            "spent": 4.8,
            "total": 5.22,
            "cap": 5.0,
            "input_tokens": 120000,
            "output_tokens": 30000,
        }
        self.assertEqual(
            "The Tester's climb to GPT-6 Sol Max (gpt-6-sol at max effort) did not launch: projected at $0.42, "
            "the last Tester attempt's 120,000 input and 30,000 output tokens at gpt-6-sol's price, it would take "
            "the spend from $4.80 to $5.22, past the $5.00 cost cap. AutoCode never raises the cap; resume with "
            "--max-cost-usd 5.22 or more (0 for no cap) to make this attempt",
            cost.climb_stop(
                over,
                attempt="The Tester's climb to GPT-6 Sol Max",
                basis="the last Tester attempt",
                model="gpt-6-sol",
                effort="max",
            ),
        )
        # A route without an effort is named by its model; the cap to resume with rounds up to the next cent.
        over.update(total=5.2201)
        self.assertIn(
            "The Builder's stronger attempt (claude-sonnet-5-5) did not launch",
            cost.climb_stop(over, attempt="The Builder's stronger attempt", basis="b", model="claude-sonnet-5-5"),
        )
        self.assertIn("--max-cost-usd 5.23 or more", cost.climb_stop(over, attempt="A", basis="b", model="m"))

    def test_the_line_names_every_basis_and_the_cap(self):
        reported = stage(metrics={"provider_tokens": TOKENS, "provider_cost_usd": 0.5})
        line = cost.line(cost.view(run(stage(), reported, cap=12.5)))
        self.assertRegex(
            line,
            r"^Cost: \$0\.5\d* so far \(\$0\.0\d+ estimated, \$0\.50 reported by the provider\); cap \$12\.50\. Stage costs: Tester 2 attempts: \$0\.5\d*\.$",
        )
        self.assertEqual("$0.00123", cost.usd(0.00123))
        self.assertEqual("$0.000001", cost.usd(0.000001))
        self.assertEqual("under $0.000001", cost.usd(0.0000001))
        self.assertEqual("$1,234.50", cost.usd(1234.5))

    def test_a_damaged_state_still_gives_a_cost_view(self):
        for state in ("garbage", {"settings": "x", "stages": "y"}, {"stages": [None, 3, {"metrics": "z"}]}):
            with self.subTest(state=state):
                view = cost.view(state)
                self.assertEqual(set(cost.FIELDS), set(view))
                cost.line(view)

    def test_a_copied_cost_block_is_checked_field_by_field(self):
        good = {"estimated_usd": 0.1, "reported_usd": None, "spent_usd": 0.1, "cap_usd": 5, "extra": "dropped"}
        self.assertEqual({"estimated_usd": 0.1, "reported_usd": None, "spent_usd": 0.1, "cap_usd": 5}, cost.check(good))
        for field, value in (
            ("spent_usd", -1),
            ("cap_usd", float("nan")),
            ("estimated_usd", True),
            ("reported_usd", "1"),
        ):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                cost.check({**good, field: value})
        with self.assertRaises(ValueError):
            cost.check(["not", "an", "object"])


class TariffUpdateTests(unittest.TestCase):
    """A rate change does not reprice what earlier stages cost, or flip a resumed run's cap (#1867 S1a)."""

    def test_a_tariff_update_keeps_earlier_spend_and_the_cap_decision(self):
        import copy
        import json
        from pathlib import Path
        from unittest import mock

        shipped = json.loads((Path(prices.__file__).with_name("autocode_prices.json")).read_text())
        model = "openai/gpt-5.6-sol"
        tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        earlier = stage(model, tokens, finished_at="2026-10-10T12:00:00+00:00")
        state = run(earlier, cap=None)
        before = cost.view(state)["spent_usd"]
        updated = copy.deepcopy(shipped)
        row = updated["models"][model]
        updated["models"][model] = {
            **row,
            "input": row["input"] * 3,
            "output": row["output"] * 3,
            "retrieved": "2026-10-20",
            "history": [
                {key: row[key] for key in ("input", "output", "source", "retrieved")} | {"until": "2026-10-20"}
            ],
        }
        cap = round(before * 2, 6)
        with mock.patch.object(prices, "table", return_value=prices.parse(updated)):
            self.assertEqual(before, cost.view(state)["spent_usd"])
            self.assertFalse(cost.exceeded(run(earlier, cap=cap)))
            later = stage(model, tokens, finished_at="2026-10-21T09:00:00+00:00")
            both = cost.view(run(earlier, later))
            self.assertAlmostEqual(before * 4, both["spent_usd"])
            self.assertEqual(
                [("openai/gpt-5.6-sol", "2026-10-20", 1), ("openai/gpt-5.6-sol", None, 1)],
                [(base["model"], base["until"], base["stages"]) for base in both["breakdown"]["price_bases"]],
            )
        # Without the history entry, the same update would have tripled the earlier stage.
        updated["models"][model].pop("history")
        with mock.patch.object(prices, "table", return_value=prices.parse(updated)):
            self.assertAlmostEqual(before * 3, cost.view(state)["spent_usd"])


if __name__ == "__main__":
    unittest.main()
