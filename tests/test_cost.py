"""A run's spend against its money cap (#721): the pure projection. End to end: tests/test_cost_cap.py."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_cost as cost
import autocode_prices as prices
import autocode_run_view as run_view
import autocode_support as support
import autocode_usage as usage

TOKENS = {"input_tokens": 1000, "output_tokens": 200}


def stage(model="gpt-5.6-sol", tokens=TOKENS, **fields):
    return {"stage": "tester", "role": "tester", "model": model, "metrics": {"provider_tokens": dict(tokens)}, **fields}


def run(*stages, cap=None, **fields):
    limits = {} if cap is None else {"max_cost_usd": cap}
    return {"settings": {"limits": limits}, "stages": list(stages), **fields}


class RequestCostCompletenessTests(unittest.TestCase):
    def record(self, costs, complete=True):
        record = stage("unknown/model")
        record["metrics"]["request_context"] = {
            "accounting": {
                "complete": complete,
                "requests": [
                    {
                        "identity": ["session", str(index)],
                        "reported_cost_usd": amount,
                        "tokens": dict.fromkeys(usage.request_usage.TOKEN_KEYS, 1),
                    }
                    for index, amount in enumerate(costs)
                ],
            }
        }
        return record

    def test_missing_request_price_keeps_the_known_subtotal_but_never_claims_a_complete_bill(self):
        state = run(self.record([1.25, None]), cap=1)
        view = cost.view(state)
        self.assertEqual(1.25, view["spent_usd"])
        self.assertEqual(1.25, view["reported_usd"])
        self.assertFalse(view["complete"])
        self.assertIn("at least $1.25", cost.line(view))
        self.assertEqual((1.25, 1), cost.exceeded(state))
        self.assertEqual(1, cost.carried(state)["partial_stages"])
        totals = usage.summary(state)
        self.assertFalse(totals["cost_usd"]["complete"])
        self.assertEqual(1, totals["partial_stages"])
        self.assertFalse(view["breakdown"]["by_stage"][0]["complete"])

    def test_missing_request_coverage_is_partial_even_with_complete_aggregate_tokens(self):
        for complete in (False, None):
            with self.subTest(complete=complete):
                state = run(self.record([1.25, 0.75], complete=complete))
                view = cost.view(state)
                self.assertEqual(2, view["spent_usd"])
                self.assertFalse(view["complete"])
                self.assertEqual(1, usage.summary(state)["partial_stages"])

    def test_all_prices_and_request_coverage_produce_a_complete_total(self):
        state = run(self.record([1.25, 0.75]))
        view = cost.view(state)
        self.assertEqual(2, view["spent_usd"])
        self.assertTrue(view["complete"])
        self.assertEqual(0, usage.summary(state)["partial_stages"])

    def test_an_authoritative_attempt_amount_supersedes_incomplete_request_costs(self):
        for observation in (
            {"provider_cost_usd": 3.0},
            {"cost_state": {"session_id": "session", "total_cost_usd": 3.0}},
        ):
            with self.subTest(observation=observation):
                record = self.record([1.25, None], complete=False)
                record["metrics"].update(observation)
                view = cost.view(run(record))
                self.assertEqual(3, view["spent_usd"])
                self.assertTrue(view["complete"])

    def test_no_request_price_remains_unknown(self):
        view = cost.view(run(self.record([None, None])))
        self.assertIsNone(view["spent_usd"])
        self.assertFalse(view["complete"])
        self.assertEqual(1, view["unpriced_stages"])


MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"


def step_finish(index, cost_usd=0, fresh=800, read=22400, output=112, reasoning=151):
    """A Kilo request finish as a live Requirements stage on a token plan writes it: cost 0."""
    part = {
        "id": f"prt_{index}",
        "sessionID": "ses_live",
        "messageID": f"msg_{index}",
        "type": "step-finish",
        "cost": cost_usd,
        "tokens": {"input": fresh, "output": output, "reasoning": reasoning, "cache": {"read": read, "write": 0}},
    }
    return {"type": "step_finish", "sessionID": "ses_live", "part": part}


class RunningStageSpendTests(unittest.TestCase):
    """While the first stage runs, nothing has finished, but its tokens have a price (Ankur, 2026-11-11:
    the VS Code header showed "$0+ · 1.9M+ tokens" during Requirements on Kilo with MiMo)."""

    def running(self, model=MIMO, rows=(), **fields):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        events = Path(folder.name) / "requirements-gather-01.jsonl"
        events.write_text("".join(json.dumps(row) + "\n" for row in rows))
        active = {
            "stage": "requirements_gather",
            "engine": "opencode",
            "output_mode": "opencode_events",
            "events": str(events),
            "launch_route": {"engine": "opencode", "model": model, "reasoning_effort": "medium"},
            "started_at": "2026-11-11T01:24:28+00:00",
        }
        return {"status": "RUNNING", "settings": {"limits": {}}, "stages": [], "active_stage": active, **fields}

    def test_a_running_stage_on_a_zero_reporting_plan_is_priced_from_its_tokens_so_far(self):
        state = self.running(rows=[step_finish(index) for index in range(3)])
        shown = run_view.view(state)["cost"]
        tokens = {"input_tokens": 3 * 23200, "cached_input_tokens": 3 * 22400, "output_tokens": 3 * 263}
        expected = round(prices.estimate(MIMO, tokens), 6)
        self.assertGreater(expected, 0)
        self.assertEqual(expected, shown["running_usd"])
        # Nothing has finished: the money-cap ledger stays the finished stages' alone, and a floor.
        self.assertEqual(0.0, shown["spent_usd"])
        self.assertFalse(shown["complete"])
        self.assertEqual(0, cost.spent(state))
        # Without the live accounting (the build loop's boundary checks) nothing reads the event file.
        self.assertIsNone(cost.view(state)["running_usd"])

    def test_a_running_stage_whose_provider_prices_every_request_uses_those_prices(self):
        state = self.running(rows=[step_finish(0, cost_usd=0.25), step_finish(1, cost_usd=0.5)])
        self.assertEqual(0.75, run_view.view(state)["cost"]["running_usd"])

    def test_an_unpriced_running_stage_or_none_running_is_unknown_not_zero(self):
        unpriced = self.running(model="mystery/model", rows=[step_finish(0)])
        self.assertIsNone(run_view.view(unpriced)["cost"]["running_usd"])
        finished = run_view.view(run(stage()))["cost"]
        self.assertIsNone(finished["running_usd"])
        self.assertIsNone(cost.running({"attempts": [{"active": False, "model": MIMO}]}))
        self.assertIsNone(cost.running("damaged"))

    def test_a_stopped_retained_attempt_counts_toward_spend_and_the_cap(self):
        for status in ("WAITING_FOR_USER", "PAUSED_UNCERTAIN_STAGE", "RESOLVER_PENDING", "RUNNING"):
            with self.subTest(status=status):
                state = self.running(rows=[step_finish(0)], status=status)
                in_flight_accounting = usage.accounting(state)
                active = state["active_stage"]
                active.update(
                    finished_at="2026-11-11T01:25:28+00:00",
                    exit_code=-15,
                    interrupted=True,
                    metrics=support.event_metrics(active["events"]),
                )
                active["metrics"]["provider_tokens_partial"] = True
                state["stages"] = [stage(metrics={"provider_cost_usd": 0.431212})]
                expected = 0.431212 + prices.estimate(MIMO, active["metrics"]["provider_tokens"])
                state["settings"]["limits"]["max_cost_usd"] = expected - 0.000001
                before = copy.deepcopy(state)
                shown = run_view.view(state)
                self.assertIsNone(shown["cost"]["running_usd"])
                self.assertIsNone(cost.view(state, accounting=in_flight_accounting)["running_usd"])
                self.assertEqual(round(expected, 6), shown["cost"]["spent_usd"])
                self.assertFalse(shown["cost"]["complete"])
                self.assertIn("at least", cost.line(shown["cost"]))
                self.assertAlmostEqual(expected, cost.exceeded(state)[0])
                self.assertAlmostEqual(expected, cost.crossing(state, MIMO, active)["spent"])
                self.assertEqual(0, shown["usage"]["accounting"]["active_attempts"])
                self.assertIsNone(shown["usage"]["active_stage"])
                self.assertEqual(2, shown["usage"]["accounting"]["attempt_count"])
                groups = shown["cost"]["breakdown"]["by_stage"]
                self.assertAlmostEqual(round(expected, 6), sum(row["spent_usd"] for row in groups))
                self.assertEqual(before, state, "reading spend must not reconcile or mutate the attempt")

    def test_retained_spend_is_counted_once_before_and_after_archival(self):
        state = self.running(rows=[step_finish(0, cost_usd=0.75)], status="WAITING_FOR_USER")
        active = state["active_stage"]
        # Reconciliation can account a stopped legacy attempt without a finish timestamp.
        active.update(accounted=True, metrics=support.event_metrics(active["events"]))
        held = run_view.view(state)["cost"]
        self.assertEqual(0.75, held["spent_usd"])
        self.assertIsNone(held["running_usd"])
        self.assertFalse(held["complete"], "the cut stream's spend remains a floor after it stops")
        self.assertEqual(0.75, cost.carried(state)["usd"])
        state["stages"].append(copy.deepcopy(active))
        self.assertEqual(held, run_view.view(state)["cost"], "a saved copy is the same attempt")
        state.pop("active_stage")
        self.assertEqual(held, run_view.view(state)["cost"])
        self.assertEqual(0.75, cost.spent(state))

        # A fully recorded terminal amount can be complete while its report awaits reconciliation.
        state["active_stage"] = state["stages"].pop()
        state["active_stage"]["metrics"] = {"provider_cost_usd": 0.75}
        self.assertTrue(cost.view(state)["complete"])
        self.assertTrue(usage.summary(state)["cost_usd"]["complete"])

    def test_a_retained_unpriced_attempt_is_unknown_and_never_running(self):
        state = self.running(model="mystery/model", rows=[step_finish(0)], status="PAUSED_UNCERTAIN_STAGE")
        active = state["active_stage"]
        active.update(finished_at="2026-11-11T01:25:28+00:00", metrics=support.event_metrics(active["events"]))
        state["settings"]["limits"]["max_cost_usd"] = 0.000001
        shown = run_view.view(state)["cost"]
        self.assertIsNone(shown["running_usd"])
        self.assertIsNone(shown["spent_usd"])
        self.assertEqual(1, shown["unpriced_stages"])
        self.assertFalse(shown["complete"])
        self.assertIsNone(cost.exceeded(state))

    def test_paused_status_alone_does_not_claim_the_active_process_finished(self):
        state = self.running(rows=[step_finish(0, cost_usd=0.75)], status="PAUSED_PROCESS_CLEANUP")
        state["active_stage"]["accounted"] = "false"
        shown = run_view.view(state)["cost"]
        self.assertEqual(0.75, shown["running_usd"])
        self.assertEqual(0, shown["spent_usd"])
        self.assertFalse(shown["complete"])

    def test_public_cli_reports_retained_spend_without_changing_the_checkpoint(self):
        state = self.running(rows=[step_finish(0, cost_usd=0.75)], status="WAITING_FOR_USER")
        active = state["active_stage"]
        active.update(
            finished_at="2026-11-11T01:25:28+00:00",
            exit_code=-15,
            interrupted=True,
            metrics=support.event_metrics(active["events"]),
        )
        root = Path(active["events"]).parent.resolve()
        active.update(iteration=1, role="requirements", output=str(root / "requirements-gather-01.json"))
        workspace = root / "project"
        run_dir = workspace / ".autocode/runs/stopped-attempt"
        run_dir.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(workspace)], check=True, capture_output=True)
        state.update(
            workspace=str(workspace),
            run_dir=str(run_dir),
            task="Verify retained attempt spend",
            iteration=1,
            sessions={},
            history=[],
            next_stage="requirements_gather",
            phase="DISCOVERING",
        )
        state["settings"]["limits"]["max_cost_usd"] = 0.5
        checkpoint = run_dir / "state.json"
        checkpoint.write_text(json.dumps(state))
        before = checkpoint.read_bytes()
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "tools/autocode.py"),
                "--workspace",
                str(workspace),
                "--run-dir",
                str(run_dir),
                "--status",
                "--json",
            ],
            env={**os.environ, "AUTOCODE_HOME": str(root / "home")},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        shown = json.loads(result.stdout)["view"]
        self.assertEqual(
            (0.75, None, 0.5), tuple(shown["cost"][key] for key in ("spent_usd", "running_usd", "cap_usd"))
        )
        self.assertFalse(shown["cost"]["complete"])
        self.assertEqual(0, shown["usage"]["accounting"]["active_attempts"])
        self.assertIn("Cost: at least $0.75 so far; cap $0.50.", result.stderr)
        self.assertEqual(before, checkpoint.read_bytes())


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
        for state in (run(partial), run(stage(), active_stage={"stage": "builder"})):
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
        attempt = stage("gpt-5.6-luna", stage="builder")
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
