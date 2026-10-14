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
        self.assertIn("Cost: unknown; 1 stage without a rate", cost.line(unknown))
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

    def test_the_line_names_every_basis_and_the_cap(self):
        reported = stage(metrics={"provider_tokens": TOKENS, "provider_cost_usd": 0.5})
        line = cost.line(cost.view(run(stage(), reported, cap=12.5)))
        self.assertRegex(
            line, r"^Cost: \$0\.5\d* so far \(\$0\.0\d+ estimated, \$0\.50 reported by the provider\); cap \$12\.50\.$"
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


if __name__ == "__main__":
    unittest.main()
