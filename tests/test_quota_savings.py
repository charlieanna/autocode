"""The cheaper-route what-if built from a plan pool's own ledger evidence (#4229)."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path

import autocode_quota_pools as pools
import autocode_quota_report as quota_report
import autocode_quota_savings as savings
import autocode_usage_report as usage_report

NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)


def _attempt(
    provider: str,
    model: str,
    usd: float | None,
    *,
    role: str = "builder",
    effort: str = "high",
    rejected: bool = False,
) -> dict:
    return {
        "model": model,
        "role": role,
        "route_role": role,
        "reasoning_effort": effort,
        "execution_identity": {"provider": provider},
        "reported_cost_usd": usd,
        "tokens": {},
        "rejected": rejected,
        "finished_at": (NOW - dt.timedelta(hours=1)).isoformat(),
    }


def _many(count: int, provider: str, model: str, usd: float | None, **kwargs) -> list[dict]:
    return [_attempt(provider, model, usd, **kwargs) for _ in range(count)]


def _row(attempts: list[dict], run: str = "run-a") -> dict:
    return {
        "run": run,
        "status": "TASK_COMPLETE",
        "started_at": "2026-11-19T10:00:00+00:00",
        "updated_at": "2026-11-19T10:00:00+00:00",
        "evidence_provenance": "live",
        "accounting": {"attempts": attempts},
    }


def _suggest(attempts: list[dict]) -> dict:
    return savings.suggest(pools.summarise([_row(attempts)], now=NOW)["pools"])


class SuggestTests(unittest.TestCase):
    def test_a_cheaper_route_with_enough_evidence_is_priced_per_attempt(self):
        result = _suggest(
            _many(6, "claude", "claude-opus-5-5", 2.0) + _many(5, "claude", "claude-sonnet-5-5", 0.5, effort="medium")
        )

        found = result["pools"]["claude"]["opportunities"]
        self.assertEqual(1, len(found))
        self.assertEqual("builder", found[0]["role"])
        self.assertEqual("claude-opus-5-5", found[0]["from"]["model"])
        self.assertEqual("claude-sonnet-5-5", found[0]["to"]["model"])
        self.assertEqual("medium", found[0]["to"]["effort"])
        self.assertEqual(2.0, found[0]["from"]["usd_per_attempt"])
        self.assertEqual(0.5, found[0]["to"]["usd_per_attempt"])
        self.assertEqual(9.0, found[0]["saving_usd"])
        self.assertEqual(9.0, result["pools"]["claude"]["saving_usd"])
        self.assertEqual(9.0, result["saving_usd"])

    def test_the_same_model_at_a_lower_effort_is_a_cheaper_route(self):
        result = _suggest(
            _many(5, "codex", "gpt-6-astra", 4.0, role="plan_reviewer", effort="high")
            + _many(5, "codex", "gpt-6-astra", 1.0, role="plan_reviewer", effort="medium")
        )

        found = result["pools"]["codex"]["opportunities"]
        self.assertEqual(1, len(found))
        self.assertEqual("high", found[0]["from"]["effort"])
        self.assertEqual("medium", found[0]["to"]["effort"])
        self.assertEqual(15.0, found[0]["saving_usd"])

    def test_a_route_with_too_few_attempts_is_not_evidence(self):
        result = _suggest(
            _many(6, "claude", "claude-opus-5-5", 2.0)
            + _many(savings.MIN_SAMPLE - 1, "claude", "claude-sonnet-5-5", 0.5)
        )

        self.assertEqual([], result["pools"]["claude"]["opportunities"])
        self.assertEqual(0.0, result["saving_usd"])

    def test_a_route_that_is_rejected_more_often_is_not_a_substitute(self):
        cheaper = _many(3, "claude", "claude-sonnet-5-5", 0.5) + _many(
            3, "claude", "claude-sonnet-5-5", 0.5, rejected=True
        )

        result = _suggest(_many(6, "claude", "claude-opus-5-5", 2.0) + cheaper)

        self.assertEqual([], result["pools"]["claude"]["opportunities"])

    def test_an_equal_accept_rate_qualifies(self):
        expensive = _many(2, "claude", "claude-opus-5-5", 2.0, rejected=True) + _many(
            2, "claude", "claude-opus-5-5", 2.0
        )
        cheaper = _many(5, "claude", "claude-sonnet-5-5", 0.5, rejected=True) + _many(
            5, "claude", "claude-sonnet-5-5", 0.5
        )

        result = _suggest(expensive + cheaper)

        self.assertEqual(1, len(result["pools"]["claude"]["opportunities"]))

    def test_a_route_that_costs_more_is_never_suggested(self):
        result = _suggest(_many(6, "claude", "claude-sonnet-5-5", 0.5) + _many(6, "claude", "claude-opus-5-5", 2.0))

        found = result["pools"]["claude"]["opportunities"]
        self.assertEqual(["claude-opus-5-5"], [item["from"]["model"] for item in found])

    def test_a_cheaper_model_in_another_role_is_not_a_substitute(self):
        result = _suggest(
            _many(6, "claude", "claude-opus-5-5", 2.0, role="builder")
            + _many(6, "claude", "claude-sonnet-5-5", 0.5, role="requirements")
        )

        self.assertEqual([], result["pools"]["claude"]["opportunities"])

    def test_a_cheaper_model_in_another_pool_is_not_a_substitute(self):
        result = _suggest(_many(6, "claude", "claude-opus-5-5", 2.0) + _many(6, "codex", "gpt-6-sol", 0.5))

        self.assertEqual([], result["pools"]["claude"]["opportunities"])
        self.assertEqual([], result["pools"]["codex"]["opportunities"])

    def test_the_cheapest_qualifying_route_is_the_one_named(self):
        result = _suggest(
            _many(6, "claude", "claude-opus-5-5", 2.0)
            + _many(5, "claude", "claude-sonnet-5-5", 0.8)
            + _many(5, "claude", "claude-haiku-4-5", 0.3)
        )

        found = result["pools"]["claude"]["opportunities"]
        self.assertEqual(
            ["claude-haiku-4-5"], [item["to"]["model"] for item in found if item["from"]["usd_per_attempt"] == 2.0]
        )

    def test_a_substitute_that_would_check_itself_is_not_suggested(self):
        attempts = (
            _many(6, "claude", "claude-opus-5-5", 2.0, role="builder")
            + _many(5, "claude", "claude-sonnet-5-5", 0.5, role="builder")
            + _many(5, "claude", "claude-sonnet-5-5", 0.5, role="tester")
        )

        found = _suggest(attempts)["pools"]["claude"]["opportunities"]

        self.assertEqual([], [item for item in found if item["role"] == "builder"])

    def test_a_substitute_of_another_family_than_the_checker_is_allowed(self):
        attempts = (
            _many(6, "claude", "claude-opus-5-5", 2.0, role="builder")
            + _many(5, "claude", "claude-sonnet-5-5", 0.5, role="builder")
            + _many(5, "codex", "gpt-6-sol", 1.0, role="tester")
        )

        found = _suggest(attempts)["pools"]["claude"]["opportunities"]

        self.assertEqual(["claude-sonnet-5-5"], [item["to"]["model"] for item in found])

    def test_unknown_burn_is_never_counted_as_a_saving(self):
        unknown_cheaper = _many(6, "claude", "claude-sonnet-5-5", None)
        unknown_expensive = _many(6, "claude", "claude-opus-5-5", None)

        first = _suggest(_many(6, "claude", "claude-opus-5-5", 2.0) + unknown_cheaper)
        second = _suggest(unknown_expensive + _many(6, "claude", "claude-sonnet-5-5", 0.5))

        self.assertEqual([], first["pools"]["claude"]["opportunities"])
        self.assertEqual([], second["pools"]["claude"]["opportunities"])

    def test_a_route_priced_on_part_of_its_attempts_uses_only_those(self):
        partly_known = _many(5, "claude", "claude-opus-5-5", 2.0) + _many(3, "claude", "claude-opus-5-5", None)

        result = _suggest(partly_known + _many(5, "claude", "claude-sonnet-5-5", 0.5))

        found = result["pools"]["claude"]["opportunities"]
        self.assertEqual(7.5, found[0]["saving_usd"])
        self.assertEqual(5, found[0]["from"]["priced_attempts"])

    def test_a_pool_other_than_a_plan_is_not_analysed(self):
        result = _suggest(_many(6, "gocode", "claude-opus-5-5", 2.0) + _many(6, "gocode", "claude-sonnet-5-5", 0.5))

        self.assertEqual({}, result["pools"])

    def test_it_states_what_it_is_and_what_it_is_not(self):
        result = _suggest(_many(6, "claude", "claude-opus-5-5", 2.0))

        self.assertIn("what-if", result["basis"])
        self.assertIn("does not change", result["basis"])
        self.assertEqual(savings.MIN_SAMPLE, result["min_attempts"])


class ReportTests(unittest.TestCase):
    def _payload(self, attempts: list[dict]) -> dict:
        return quota_report.build([_row(attempts)], scope="workspace", now=NOW)

    def test_the_payload_carries_the_what_if(self):
        payload = self._payload(
            _many(6, "claude", "claude-opus-5-5", 2.0) + _many(5, "claude", "claude-sonnet-5-5", 0.5)
        )

        self.assertEqual(9.0, payload["savings"]["saving_usd"])
        self.assertEqual(9.0, payload["savings"]["pools"]["claude"]["saving_usd"])

    def test_the_text_names_the_route_the_saving_and_its_basis(self):
        payload = self._payload(
            _many(6, "claude", "claude-opus-5-5", 2.0) + _many(5, "claude", "claude-sonnet-5-5", 0.5)
        )

        text = quota_report.format_text(payload)

        self.assertIn("what-if", text)
        self.assertIn("builder: claude-opus-5-5 high -> claude-sonnet-5-5 high", text)
        self.assertIn("$2.00 -> $0.50 per attempt", text)
        self.assertIn("saves $9.00 of $12.00", text)

    def test_the_text_says_so_when_no_cheaper_route_has_evidence(self):
        payload = self._payload(_many(6, "claude", "claude-opus-5-5", 2.0))

        text = quota_report.format_text(payload)

        self.assertIn(f"no cheaper route has {savings.MIN_SAMPLE} priced attempts", text)

    def test_nothing_is_said_when_no_plan_pool_has_attempts(self):
        text = quota_report.format_text(self._payload(_many(3, "gocode", "claude-opus-5-5", 2.0)))

        self.assertNotIn("what-if", text)

    def test_the_cli_prints_the_what_if_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / ".autocode"
            ledger.mkdir()
            attempts = _many(6, "claude", "claude-opus-5-5", 2.0) + _many(5, "claude", "claude-sonnet-5-5", 0.5)
            (ledger / "usage.jsonl").write_text(json.dumps(_row(attempts)) + "\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = usage_report.cli(["quota", "--workspace", tmp, "--json"])

        self.assertEqual(0, code)
        self.assertEqual(9.0, json.loads(out.getvalue())["savings"]["saving_usd"])


if __name__ == "__main__":
    unittest.main()
