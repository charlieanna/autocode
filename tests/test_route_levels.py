"""Local metadata must distinguish unknown reasoning from no supported variants."""

import unittest

import autocode_route_levels as levels
import autocode_route_permission as permission


class RouteLevelsTests(unittest.TestCase):
    def test_verbose_listing_keeps_only_advertised_levels(self):
        self.assertEqual(
            {"openai/example": ["high", "low"], "zai/example": []},
            levels.parse(
                'openai/example\n{"variants":{"low":{},"high":{}}}\n'
                'zai/example\n{"variants":{}}\nother/example\n{"name":"unknown"}'
            ),
        )

    def test_missing_metadata_stays_unknown(self):
        self.assertEqual({}, levels.parse("openai/example\nzai/example\n"))
        self.assertEqual({}, levels.parse("openai/example\n{broken"))

    def test_metadata_keeps_the_facts_selection_checks_and_never_a_price(self):
        # #2049: family (verifier independence), tool calling, reasoning, context and release date;
        # a listed price is deliberately not read, and a field the listing lacks is None.
        listing = (
            "opencode/big-pickle\n"
            '{"id":"big-pickle","family":"big-pickle","cost":{"input":0,"output":0},'
            '"limit":{"context":200000,"output":32000},'
            '"capabilities":{"toolcall":true,"reasoning":true},"release_date":"2025-10-17","variants":{}}\n'
            "zai-coding-plan/glm-5.3\n"
            '{"family":"","capabilities":{"toolcall":"yes"},"limit":{"context":-1}}\n'
            "header without a model\n"
        )
        self.assertEqual(
            {
                "opencode/big-pickle": {
                    "family": "big-pickle",
                    "toolcall": True,
                    "reasoning": True,
                    "context": 200000,
                    "release_date": "2025-10-17",
                },
                "zai-coding-plan/glm-5.3": {
                    "family": None,
                    "toolcall": None,
                    "reasoning": None,
                    "context": None,
                    "release_date": None,
                },
            },
            levels.metadata(listing),
        )
        self.assertEqual({"opencode/big-pickle": []}, levels.parse(listing))
        self.assertEqual({}, levels.metadata("openai/example\n{broken"))

    def test_escalation_cannot_add_an_account_or_an_unsupported_level(self):
        settings = {"route_selection": {"candidates": {"terra": [{"model": "plan/model", "levels": ["low", "high"]}]}}}
        self.assertTrue(permission.allows(settings, "terra", "plan/model", "high"))
        self.assertFalse(permission.allows(settings, "terra", "plan/model", "max"))
        self.assertFalse(permission.allows(settings, "terra", "other/model", "high"))
        self.assertTrue(permission.allows({}, "terra", "other/model", "high"))
