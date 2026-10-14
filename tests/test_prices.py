"""The dated price table, tools/autocode_prices.json (#721)."""

import copy
import json
import tomllib
import unittest
from pathlib import Path

import autocode_builder_policy as builder_policy
import autocode_configure as configure
import autocode_prices as prices
import autocode_stuck_job as stuck_job
import autocode_usage as usage
from harness import driver, profiles
from providers import opencode

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = json.loads((ROOT / "tools" / "autocode_prices.json").read_text())


def _flag_models(argv):
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg.endswith("-model")]


def default_profile_models():
    """Every model a default route, default profile or fake catalog run is recorded under."""
    models = {*opencode.DEFAULT_MODELS.values(), *configure.DEFAULT_ROLE_MODELS.values()}
    models |= {builder_policy.DEFAULTS["strong_model"], builder_policy.DEFAULTS["checker_model"], stuck_job.CLAUDE}
    kilo = tomllib.loads((ROOT / "tools/providers/configs/kilocode.toml").read_text())
    models |= {route["model"] for route in kilo["roles"].values()}
    claude = tomllib.loads((ROOT / "examples/claude-provider/claude.toml").read_text())
    models |= {route["model"] for route in claude["roles"].values()}
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


if __name__ == "__main__":
    unittest.main()
