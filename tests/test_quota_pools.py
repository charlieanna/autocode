"""Where subscription-plan quota went: pool, burn and waste read from the usage ledger (#4229)."""

from __future__ import annotations

import datetime as dt
import unittest

import autocode_quota_pools as pools

NOW = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.UTC)


def attempt(
    *,
    provider="claude",
    model="claude-opus-5-5",
    role="plan_reviewer",
    effort="medium",
    input_tokens=1_000_000,
    output_tokens=0,
    reported=None,
    historical=None,
    finished_at="2026-11-27T11:00:00+00:00",
    **flags,
):
    tokens = {
        "input_tokens": input_tokens,
        "cached_input_tokens": None,
        "cache_write_tokens": None,
        "output_tokens": output_tokens,
    }
    return {
        "model": model,
        "role": role,
        "route_role": None,
        "engine": "opencode" if provider == "claude" else provider,
        "execution_identity": {"provider": provider, "model": model, "reasoning_effort": effort, "role": role},
        "reasoning_effort": effort,
        "tokens": tokens,
        "reported_cost_usd": reported,
        "historical_estimated_cost_usd": historical,
        "started_at": finished_at,
        "finished_at": finished_at,
        "rejected": False,
        "timed_out": False,
        "interrupted": False,
        "runner_owned": False,
        **flags,
    }


def row(*attempts, provenance="live", run="run-1"):
    return {"run": run, "evidence_provenance": provenance, "accounting": {"attempts": list(attempts)}}


class PoolOfTests(unittest.TestCase):
    def test_the_plan_tool_that_ran_the_stage_names_the_pool(self):
        self.assertEqual(pools.pool_of(attempt(provider="claude")), "claude")
        self.assertEqual(pools.pool_of(attempt(provider="codex", model="gpt-5.6-sol")), "codex")

    def test_a_codex_engine_attempt_without_a_recorded_identity_is_the_codex_pool(self):
        legacy = {"engine": "codex", "model": "gpt-5.6-sol"}
        self.assertEqual(pools.pool_of(legacy), "codex")

    def test_any_other_route_is_not_a_plan_pool(self):
        self.assertEqual(pools.pool_of(attempt(provider="gocode", model="glm-5.3")), "other")
        self.assertEqual(pools.pool_of({}), "other")


class BurnTests(unittest.TestCase):
    def test_the_providers_reported_cost_is_used_first(self):
        self.assertEqual(pools.burn(attempt(reported=1.25, historical=9.0)), (1.25, "reported"))

    def test_a_recorded_historical_estimate_comes_before_repricing(self):
        self.assertEqual(pools.burn(attempt(historical=0.5)), (0.5, "estimated"))

    def test_tokens_are_priced_at_the_models_api_equivalent_rate_when_nothing_was_recorded(self):
        # 1M input tokens of claude-opus-5-5 at $4 per million.
        self.assertEqual(pools.burn(attempt()), (4.0, "estimated"))

    def test_unobserved_usage_is_unknown_never_zero(self):
        unseen = attempt(input_tokens=None, output_tokens=None)
        self.assertEqual(pools.burn(unseen), (None, "unknown"))

    def test_an_unpriced_model_is_unknown_never_zero(self):
        self.assertEqual(pools.burn(attempt(model="no-such-model-9")), (None, "unknown"))


class SummariseTests(unittest.TestCase):
    def summary(self, *rows):
        return pools.summarise(list(rows), now=NOW)

    def test_attempts_are_grouped_by_pool_role_model_and_level(self):
        result = self.summary(
            row(
                attempt(),
                attempt(),
                attempt(provider="codex", model="gpt-5.6-sol", role="tester", effort="high", input_tokens=500_000),
            )
        )
        claude, codex = result["pools"]["claude"], result["pools"]["codex"]
        self.assertEqual((claude["attempts"], claude["burn_usd"]), (2, 8.0))
        self.assertEqual(
            claude["routes"],
            [
                {
                    "role": "plan_reviewer",
                    "model": "claude-opus-5-5",
                    "effort": "medium",
                    "attempts": 2,
                    "accepted_attempts": 2,
                    "burn_usd": 8.0,
                    "unknown_attempts": 0,
                    "wasted_usd": 0.0,
                    "wasted_attempts": 0,
                }
            ],
        )
        self.assertEqual((codex["attempts"], codex["burn_usd"]), (1, 2.0))

    def test_unknown_burn_makes_the_pool_total_a_floor_and_is_counted(self):
        result = self.summary(row(attempt(), attempt(input_tokens=None, output_tokens=None)))
        claude = result["pools"]["claude"]
        self.assertEqual((claude["burn_usd"], claude["unknown_attempts"], claude["burn_complete"]), (4.0, 1, False))

    def test_a_rejected_timed_out_or_interrupted_attempt_is_wasted_burn(self):
        result = self.summary(
            row(attempt(), attempt(rejected=True), attempt(timed_out=True), attempt(interrupted=True))
        )
        claude = result["pools"]["claude"]
        self.assertEqual((claude["wasted_attempts"], claude["wasted_usd"], claude["burn_usd"]), (3, 12.0, 16.0))
        self.assertEqual(claude["routes"][0]["accepted_attempts"], 1)

    def test_fake_provider_runs_and_runner_owned_stages_spend_no_quota(self):
        result = self.summary(
            row(attempt(), provenance="fake"),
            row(attempt(runner_owned=True), run="run-2"),
        )
        self.assertEqual(result["pools"], {})
        self.assertEqual(result["attempts"], 0)

    def test_rolling_windows_count_only_attempts_that_finished_inside_them(self):
        result = self.summary(
            row(
                attempt(finished_at="2026-11-27T10:00:00+00:00"),
                attempt(finished_at="2026-11-26T12:00:00+00:00"),
                attempt(finished_at="2026-11-01T12:00:00+00:00"),
            )
        )
        windows = result["pools"]["claude"]["windows"]
        self.assertEqual(windows, {"5h": 4.0, "7d": 8.0})

    def test_an_attempt_with_no_usable_time_is_in_the_total_but_in_no_window(self):
        result = self.summary(row(attempt(finished_at=None, started_at=None)))
        claude = result["pools"]["claude"]
        self.assertEqual((claude["burn_usd"], claude["windows"]), (4.0, {"5h": 0.0, "7d": 0.0}))


if __name__ == "__main__":
    unittest.main()
