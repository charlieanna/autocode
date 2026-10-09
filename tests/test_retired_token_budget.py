"""Pure compatibility checks for retired cumulative token budgets."""
import copy
import unittest

from autocode_retired_token_budget import retire_settings, retired_pause


class RetiredTokenBudgetTests(unittest.TestCase):
    def test_settings_retirement_preserves_other_limits_and_reporting(self):
        settings = {"limits": {"max_reported_tokens": 1, "max_seconds": 900},
                    "budget_origins": {"max_reported_tokens": "user_explicit", "max_seconds": "user_explicit"},
                    "roles": {"terra": {"model": "fixture"}}, "context_soft_tokens": 10000}
        expected = copy.deepcopy(settings)
        del expected["limits"]["max_reported_tokens"]
        del expected["budget_origins"]["max_reported_tokens"]
        retire_settings(settings)
        self.assertEqual(expected, settings)
        retire_settings(settings)
        self.assertEqual(expected, settings)

    def test_only_the_retired_operational_guard_matches(self):
        for status in ("PAUSED_BUDGET", "PAUSED_USAGE_UNKNOWN"):
            self.assertTrue(retired_pause({"pause_status": status, "budget": {"kind": "max_reported_tokens"}}))
        for origin in (None, [], {}, {"pause_status": "PAUSED_TIME_LIMIT", "budget": {"kind": "max_seconds"}},
                       {"pause_status": "WAITING_FOR_USER", "budget": {"kind": "max_reported_tokens"}},
                       {"pause_status": "PAUSED_BUDGET", "budget": {"kind": "provider_quota"}},
                       {"pause_status": "PAUSED_BUDGET", "budget": None}):
            with self.subTest(origin=origin):
                self.assertFalse(retired_pause(origin))
