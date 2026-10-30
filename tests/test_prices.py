"""The dated price table, tools/autocode_prices.json (#721)."""

import copy
import json
import tomllib
import unittest
from pathlib import Path

import autocode_builder_policy as builder_policy
import autocode_configure as configure
import autocode_prices as prices
import autocode_usage as usage
from harness import driver, profiles
from providers import claude, opencode

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = json.loads((ROOT / "tools" / "autocode_prices.json").read_text())


def _flag_models(argv):
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg.endswith("-model")]


def default_profile_models():
    """Every model a default route, default profile or fake catalog run is recorded under."""
    models = {*opencode.DEFAULT_MODELS.values(), *configure.DEFAULT_ROLE_MODELS.values()}
    models |= {builder_policy.DEFAULTS["strong_model"], builder_policy.DEFAULTS["checker_model"]}
    kilo = tomllib.loads((ROOT / "tools/providers/configs/kilocode.toml").read_text())
    models |= {route["model"] for route in kilo["roles"].values()}
    models |= {*claude.DEFAULT_MODELS.values(), *claude.LISTED_MODELS, *claude.BUILDER_RETRY.values()}
    for profile in profiles.PROFILES.values():
        models |= set((profile.get("models") or {}).values()) | set(_flag_models(profile.get("extra") or []))
    return models | set(_flag_models(driver.FAKE_FLAGS))


class PriceTableTests(unittest.TestCase):
    def test_the_shipped_table_is_valid_dated_and_sourced(self):
        table = prices.parse(SHIPPED)
        self.assertRegex(table["as_of"], r"^\d{4}-\d{2}-\d{2}$")
        for model, row in table["models"].items():
            with self.subTest(model=model):
                self.assertTrue(row["source"].startswith("https://"), row["source"])
                self.assertRegex(row["retrieved"], r"^\d{4}-\d{2}-\d{2}$")
                self.assertLessEqual(row["retrieved"], table["as_of"])
                self.assertGreater(row["output"], 0)
                self.assertLessEqual(row.get("cached_input", 0), row["input"])

    def test_selected_gocode_sol_route_has_a_sourced_conservative_budget_rate(self):
        row = prices.rate("gpt-6.1-sol")
        self.assertIsNotNone(row, "The selected GoCode route must not start with unknown spend")
        self.assertEqual((2.0, 10.0), (row["input"], row["output"]))
        self.assertIn("caas-gocode-prod", row["source"])
        self.assertEqual(3.0, prices.estimate("gpt-6.1-sol", {
            "input_tokens": 1000000, "output_tokens": 100000, "cached_input_tokens": 900000,
        }))

    def test_every_default_profile_model_has_a_row(self):
        unpriced = sorted(model for model in default_profile_models() if prices.key(model) is None)
        self.assertEqual([], unpriced, "add a dated row to tools/autocode_prices.json for these")

    def test_a_model_is_matched_as_recorded_without_inventing_a_rate(self):
        self.assertEqual("openai/gpt-5.6-sol", prices.key("gpt-5.6-sol"))  # the Codex engine's bare id
        self.assertEqual("zai-coding-plan/glm-5.3", prices.key("zai-coding-plan/glm-5.3#high"))  # OpenCode 2.x
        self.assertEqual("claude-opus-5-5", prices.key("claude-opus-5-5"))
        for unknown in ("glm-5.3", "cursor-acp/auto", "fixture/live-fixture", "", None, 7):
            with self.subTest(model=unknown):
                self.assertIsNone(prices.key(unknown))
                self.assertIsNone(prices.rate(unknown))
                self.assertIsNone(prices.estimate(unknown, {"input_tokens": 1, "output_tokens": 1}))

    def test_an_estimate_prices_cache_reads_at_the_cached_rate_only_when_their_count_is_known(self):
        row = prices.rate("openai/gpt-6-sol")
        tokens = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cached_input_tokens": 600_000}
        expected = (400_000 * row["input"] + 600_000 * row["cached_input"] + 100_000 * row["output"]) / 1e6
        self.assertAlmostEqual(expected, prices.estimate("openai/gpt-6-sol", tokens))
        flat = (1_000_000 * row["input"] + 100_000 * row["output"]) / 1e6
        for cached in (None, -1, 2_000_000, True):
            with self.subTest(cached=cached):
                self.assertAlmostEqual(flat, prices.estimate("gpt-6-sol", {**tokens, "cached_input_tokens": cached}))
        self.assertIsNone(prices.estimate("gpt-6-sol", {"input_tokens": 10}))  # no output count: unknown

    def test_the_frozen_comparison_rates_are_not_the_price_table(self):
        # Evidence reports and campaign comparisons are bound to REFERENCE_PRICES; updating the
        # dated table must not move them.
        self.assertEqual({"input": 0.60, "output": 2.20}, usage.REFERENCE_PRICES["zai-coding-plan/glm-5.3"])
        self.assertNotEqual(
            usage.REFERENCE_PRICES["zai-coding-plan/glm-5.3"]["input"], prices.rate("zai-coding-plan/glm-5.3")["input"]
        )

    def test_qualified_claude_aliases_share_the_dated_rates_and_provenance(self):
        tokens = {"input_tokens": 1_000_000, "cached_input_tokens": 600_000, "output_tokens": 100_000}
        for alias, existing in (
            ("anthropic/claude-opus-5-5", "claude-opus-5-5"),
            ("kilo/anthropic/claude-opus-5-5", "kilo/anthropic/claude-opus-5.5"),
            ("anthropic/claude-sonnet-5-5", "claude-sonnet-5-5"),
            ("anthropic/claude-haiku-4-5", "claude-haiku-4-5-20251001"),
        ):
            with self.subTest(alias=alias):
                self.assertIsNotNone(prices.rate(alias))
                for key in ("input", "cached_input", "output", "source", "retrieved"):
                    self.assertEqual(prices.rate(existing)[key], prices.rate(alias)[key])
                self.assertAlmostEqual(prices.estimate(existing, tokens), prices.estimate(alias + "#high", tokens))
        self.assertAlmostEqual(3.72, prices.estimate("anthropic/claude-opus-5-5#high", tokens))

    def test_mimo_flash_on_the_token_plan_route_is_priced(self):
        # Live Kilo runs on 2026-10-19 and 10-21 recorded this route's tokens with no rate.
        tokens = {"input_tokens": 1_000_000, "cached_input_tokens": 600_000, "output_tokens": 100_000}
        self.assertAlmostEqual(0.08568, prices.estimate("xiaomi-token-plan-sgp/mimo-v2.6-flash", tokens))

    def test_a_malformed_table_is_refused_with_the_field(self):
        cases = {
            "negative": ("input", -1),
            "nan": ("output", float("nan")),
            "bool": ("input", True),
            "text": ("output", "2.0"),
            "no source": ("source", None),
            "no date": ("retrieved", ""),
        }
        model = "openai/gpt-6-sol"
        for name, (field, value) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, field):
                broken = copy.deepcopy(SHIPPED)
                broken["models"][model][field] = value
                prices.parse(broken)
        with self.assertRaisesRegex(ValueError, "USD"):
            prices.parse({**SHIPPED, "currency": "EUR"})


class PriceHistoryTests(unittest.TestCase):
    """A row keeps the rates it replaced, so a tariff update never reprices an earlier stage (#1867 S1a)."""

    MODEL = "openai/gpt-6-sol"

    def table(self, history):
        document = copy.deepcopy(SHIPPED)
        document["models"][self.MODEL]["history"] = history
        return prices.parse(document)

    def old(self, until="2026-10-15", **changes):
        return {
            "input": 1.0,
            "output": 4.0,
            "source": "https://example.test/old",
            "retrieved": "2026-09-01",
            "until": until,
            **changes,
        }

    def test_a_stage_is_priced_at_the_rate_in_force_on_the_utc_day_it_finished(self):
        from unittest import mock

        with mock.patch.object(prices, "table", return_value=self.table([self.old()])):
            current = SHIPPED["models"][self.MODEL]
            self.assertEqual("2026-10-15", prices.rate(self.MODEL, "2026-10-14T23:59:59+00:00")["until"])
            self.assertEqual(1.0, prices.rate(self.MODEL, "2026-10-14T23:59:59+00:00")["input"])
            # 2026-10-14 20:00 in New York is already 10-15 in UTC: the new rate.
            self.assertEqual(current["input"], prices.rate(self.MODEL, "2026-10-14T20:00:00-04:00")["input"])
            self.assertNotIn("until", prices.rate(self.MODEL, "2026-10-15T00:00:00+00:00"))
            self.assertNotIn("until", prices.rate(self.MODEL))  # no date: today's rate, as before
            self.assertNotIn("until", prices.rate(self.MODEL, "not a date"))
            tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
            self.assertEqual(5.0, prices.estimate(self.MODEL, tokens, "2026-10-01T10:00:00Z"))

    def test_a_history_out_of_order_or_without_its_fields_is_refused(self):
        for name, history in {
            "not a list": {"until": "2026-10-15"},
            "bad date": [self.old(until="15 Oct 2026")],
            "timestamp": [self.old(until="2026-10-15T00:00:00Z")],
            "out of order": [self.old(until="2026-10-15"), self.old(until="2026-10-01")],
            "no source": [self.old(source="")],
            "negative": [self.old(input=-1)],
        }.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, "history"):
                self.table(history)


if __name__ == "__main__":
    unittest.main()
