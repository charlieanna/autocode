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

    def test_escalation_cannot_add_an_account_or_an_unsupported_level(self):
        settings = {"route_selection": {"candidates": {"terra": [{"model": "plan/model", "levels": ["low", "high"]}]}}}
        self.assertTrue(permission.allows(settings, "terra", "plan/model", "high"))
        self.assertFalse(permission.allows(settings, "terra", "plan/model", "max"))
        self.assertFalse(permission.allows(settings, "terra", "other/model", "high"))
        self.assertTrue(permission.allows({}, "terra", "other/model", "high"))
