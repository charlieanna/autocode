"""How much of each run's cost is known, per run, stage and model, read from the usage ledger (#4239)."""

from __future__ import annotations

import unittest

import autocode_cost_coverage as coverage

ABSENT = object()


def attempt(
    *,
    stage="builder",
    model="claude-opus-5-5",
    reported=None,
    historical=None,
    input_tokens=1000,
    output_tokens=100,
    exit_code=0,
    duration=60.0,
    request_ids=("msg_1",),
    resolved=("claude-opus-5-5",),
    **flags,
):
    item = {
        "stage": stage,
        "job": stage.replace("_", " ").title(),
        "model": model,
        "tokens": {
            "input_tokens": input_tokens,
            "cached_input_tokens": None,
            "cache_write_tokens": None,
            "output_tokens": output_tokens,
            "reasoning_output_tokens": None,
            "fresh_input_tokens": None,
        },
        "reported_cost_usd": reported,
        "historical_estimated_cost_usd": historical,
        "started_at": "2026-11-27T10:59:00+00:00",
        "finished_at": "2026-11-27T11:00:00+00:00",
        "duration_seconds": duration,
        "exit_code": exit_code,
        "active": False,
        "rejected": False,
        "timed_out": False,
        "interrupted": False,
        "runner_owned": False,
        "issues": [],
        "unfinished_requests": None,
        **flags,
    }
    if request_ids is not ABSENT:
        # An older ledger row has none of these three fields.
        item.setdefault("usage_truncated", False)
        item["provider_request_ids"] = None if request_ids is None else list(request_ids)
        item["resolved_models"] = None if resolved is None else list(resolved)
    return item


def unseen(**flags):
    """An attempt the provider never reported on: no tokens, no cost, no receipt."""
    return attempt(input_tokens=None, output_tokens=None, request_ids=None, resolved=None, **flags)


def row(*attempts, run="run-1", workflow="build", state="completed", wall=600.0, plan_size="small"):
    return {
        "run": run,
        "workflow": workflow,
        "evidence_provenance": "live",
        "outcome": {"state": state, "wall_seconds": wall, "plan_size": plan_size},
        "accounting": {"attempts": list(attempts)},
    }


def only(entries, **match):
    found = [entry for entry in entries if all(entry.get(key) == value for key, value in match.items())]
    assert len(found) == 1, (match, entries)
    return found[0]


class AttemptTests(unittest.TestCase):
    def test_the_terminal_status_is_what_the_runner_recorded_and_unknown_when_it_recorded_nothing(self):
        self.assertEqual("completed", coverage.terminal_status(attempt()))
        self.assertEqual("failed", coverage.terminal_status(attempt(exit_code=1)))
        self.assertEqual("rejected", coverage.terminal_status(attempt(rejected=True)))
        self.assertEqual("timed_out", coverage.terminal_status(attempt(timed_out=True, exit_code=124)))
        self.assertEqual("interrupted", coverage.terminal_status(attempt(interrupted=True, exit_code=None)))
        self.assertEqual("running", coverage.terminal_status(attempt(active=True, exit_code=None)))
        self.assertEqual("unknown", coverage.terminal_status(attempt(exit_code=None)))

    def test_a_cut_or_missing_completion_event_is_named_from_what_the_ledger_recorded(self):
        self.assertEqual("complete", coverage.completion_event(attempt()))
        self.assertEqual("truncated", coverage.completion_event(attempt(usage_truncated=True)))
        self.assertEqual("truncated", coverage.completion_event(attempt(unfinished_requests=2)))
        # An older ledger row has no usage_truncated field; its cut-stream issue still says so.
        cut = attempt(request_ids=ABSENT, issues=["The stage's event stream was cut; its usage is a lower bound"])
        self.assertEqual("truncated", coverage.completion_event(cut))
        self.assertEqual("missing", coverage.completion_event(unseen()))
        silent = attempt(issues=["No native request finishes; unobserved usage is unknown"])
        self.assertEqual("missing", coverage.completion_event(silent))
        self.assertEqual("running", coverage.completion_event(attempt(active=True)))
        # A provider that reported a charge but no token counts did send its completion.
        self.assertEqual(
            "complete", coverage.completion_event(attempt(input_tokens=None, output_tokens=None, reported=0.2))
        )


class RunCoverageTests(unittest.TestCase):
    def test_a_missing_provider_receipt_is_unknown_and_never_zero(self):
        summary = coverage.summarise([row(attempt(reported=0.5), unseen())])
        run = summary["by_run"][0]
        self.assertEqual("partial", run["coverage"])
        self.assertEqual(0.5, run["reported_usd"])
        self.assertIsNone(run["estimated_usd"])
        self.assertEqual(1, run["unknown_attempts"])
        self.assertEqual(0.5, run["known_usd"])
        self.assertIsNone(run["total_usd"])
        self.assertFalse(run["complete"])
        self.assertEqual({"complete": 1, "truncated": 0, "missing": 1, "running": 0}, run["completion_events"])
        self.assertEqual({"recorded": 1, "none_named": 1, "not_recorded": 0, "resolved_model": 1}, summary["receipts"])

        nothing = coverage.summarise([row(unseen(), unseen())])["by_run"][0]
        self.assertEqual("unknown", nothing["coverage"])
        self.assertIsNone(nothing["known_usd"])
        self.assertIsNone(nothing["total_usd"])
        self.assertIsNone(nothing["reported_usd"])

    def test_a_partially_priced_run_keeps_reported_estimated_and_unknown_amounts_apart(self):
        summary = coverage.summarise(
            [
                row(
                    attempt(reported=1.0),
                    attempt(historical=0.25),
                    attempt(model="no-such-model-9"),
                    run="partly",
                ),
                row(attempt(reported=0.75), attempt(historical=0.5), run="priced"),
            ]
        )
        partly = only(summary["by_run"], run="partly")
        expected = {
            "coverage": "partial",
            "reported_usd": 1.0,
            "reported_attempts": 1,
            "estimated_usd": 0.25,
            "estimated_attempts": 1,
            "unknown_attempts": 1,
            "known_usd": 1.25,
            "total_usd": None,
            "complete": False,
        }
        self.assertEqual(expected, {key: partly[key] for key in expected})
        priced = only(summary["by_run"], run="priced")
        self.assertEqual(("complete", 1.25, 1.25), (priced["coverage"], priced["known_usd"], priced["total_usd"]))
        self.assertEqual(
            {"complete": 1, "partial": 1, "unknown": 0, "no_model_attempts": 0, "not_recorded": 0}, summary["runs"]
        )
        self.assertEqual(1.75, summary["usd"]["reported_usd"])
        self.assertEqual(0.75, summary["usd"]["estimated_usd"])
        self.assertEqual(1, summary["usd"]["unknown_attempts"])
        self.assertIsNone(summary["usd"]["total_usd"])

    def test_a_priced_attempt_whose_stream_was_cut_leaves_its_run_partial(self):
        summary = coverage.summarise([row(attempt(reported=1.0), attempt(reported=0.3, usage_truncated=True))])
        run = summary["by_run"][0]
        self.assertEqual("partial", run["coverage"])
        self.assertEqual(1.3, run["known_usd"])
        self.assertIsNone(run["total_usd"])
        self.assertEqual(1, summary["completion_events"]["truncated"])

    def test_runner_stages_are_left_out_and_a_row_without_accounting_is_not_recorded(self):
        runner = attempt(runner_owned=True, reported=0.0, request_ids=None)
        summary = coverage.summarise(
            [row(runner, run="runner-only"), {"run": "legacy", "outcome": {"state": "completed"}}]
        )
        self.assertEqual(0, summary["attempts"])
        self.assertEqual(
            {"complete": 0, "partial": 0, "unknown": 0, "no_model_attempts": 1, "not_recorded": 1}, summary["runs"]
        )
        self.assertEqual("not_recorded", only(summary["by_run"], run="legacy")["coverage"])


class GroupTests(unittest.TestCase):
    def test_coverage_is_counted_per_stage_and_model_with_the_receipts_each_recorded(self):
        summary = coverage.summarise(
            [
                row(
                    attempt(stage="builder", reported=1.0, duration=30.0),
                    attempt(stage="tester", model="gpt-5.6-sol", historical=0.2, request_ids=None, resolved=None),
                    run="a",
                ),
                row(
                    attempt(stage="builder", request_ids=ABSENT, model="no-such-model-9", duration=None),
                    attempt(stage="builder", resolved=("claude-opus-5-5-20261101",), reported=0.5, duration=45.0),
                    run="b",
                ),
            ]
        )
        builder = only(summary["by_stage"], stage="builder")
        self.assertEqual("Builder", builder["job"])
        self.assertEqual(3, builder["attempts"])
        self.assertEqual((1.5, 1, None), (builder["reported_usd"], builder["unknown_attempts"], builder["total_usd"]))
        self.assertEqual(["claude-opus-5-5", "no-such-model-9"], builder["models"])
        self.assertEqual({"known": 75.0, "unrecorded_attempts": 1}, builder["seconds"])
        self.assertEqual({"recorded": 2, "none_named": 0, "not_recorded": 1, "resolved_model": 2}, builder["receipts"])
        tester = only(summary["by_stage"], stage="tester")
        self.assertEqual((0.2, 0.2, True), (tester["estimated_usd"], tester["total_usd"], tester["complete"]))

        opus = only(summary["by_model"], model="claude-opus-5-5")
        self.assertEqual(2, opus["attempts"])
        self.assertEqual(["claude-opus-5-5", "claude-opus-5-5-20261101"], opus["resolved_models"])
        self.assertEqual(1.5, opus["total_usd"])
        sol = only(summary["by_model"], model="gpt-5.6-sol")
        self.assertIsNone(sol["resolved_models"])
        self.assertEqual({"recorded": 0, "none_named": 1, "not_recorded": 0, "resolved_model": 0}, sol["receipts"])

    def test_delivered_cost_counts_only_complete_runs_and_cost_per_delivery_needs_every_run(self):
        summary = coverage.summarise(
            [
                row(attempt(reported=2.0), run="fixed", workflow="bug_fix", wall=100.0),
                row(attempt(reported=4.0), unseen(), run="fixed-partly", workflow="bug_fix", wall=300.0),
                row(attempt(reported=1.0), run="gave-up", workflow="bug_fix", state="abandoned", wall=50.0),
                row(attempt(reported=3.0), run="built", workflow="build", wall=900.0),
                row(attempt(reported=1.0), run="paused", workflow="build", state="paused", wall=None),
            ]
        )
        bug_fix = only(summary["delivered"], workflow="bug_fix")
        self.assertEqual((3, 2, 1), (bug_fix["runs"], bug_fix["delivered_runs"], bug_fix["incomplete_runs"]))
        self.assertEqual(
            {"priced_runs": 1, "excluded_runs": 1, "median": 2.0, "p90": 2.0, "total": 2.0},
            bug_fix["usd_per_delivered_run"],
        )
        self.assertEqual(
            {"count": 2, "median": 200.0, "p90": 300.0, "unrecorded_runs": 0},
            bug_fix["wall_seconds_per_delivered_run"],
        )
        self.assertIsNone(bug_fix["usd_per_delivery"])
        build = only(summary["delivered"], workflow="build")
        # Every build run's cost is known, so the paused run's cost is charged to the one delivery.
        self.assertEqual(
            (2, 1, 0, 4.0),
            (build["runs"], build["delivered_runs"], build["incomplete_runs"], build["usd_per_delivery"]),
        )
        self.assertEqual(1, build["wall_seconds_per_delivered_run"]["count"])

    def test_models_are_compared_only_on_the_same_stage_and_plan_size(self):
        summary = coverage.summarise(
            [
                row(attempt(reported=1.0, duration=40.0), attempt(reported=3.0, duration=80.0), run="opus-1"),
                row(attempt(model="gpt-5.6-sol", reported=0.5, duration=20.0), run="sol-1"),
                row(attempt(model="gpt-5.6-sol", exit_code=1, input_tokens=None, output_tokens=None), run="sol-2"),
                row(attempt(model="glm-5.3", reported=0.1), run="glm-large", plan_size="large"),
                row(attempt(stage="tester", reported=0.2), run="tester-only"),
            ]
        )
        self.assertEqual(1, len(summary["matched_workloads"]))
        workload = summary["matched_workloads"][0]
        self.assertEqual(("builder", "Builder", "small"), (workload["stage"], workload["job"], workload["plan_size"]))
        self.assertEqual(["claude-opus-5-5", "gpt-5.6-sol"], [item["model"] for item in workload["models"]])
        opus, sol = workload["models"]
        self.assertEqual((2, 2, 2), (opus["attempts"], opus["completed_attempts"], opus["priced_attempts"]))
        self.assertEqual({"count": 2, "median": 2.0, "p90": 3.0}, opus["usd_per_attempt"])
        self.assertEqual({"count": 2, "median": 60.0, "p90": 80.0}, opus["seconds_per_attempt"])
        self.assertEqual((2, 1, 1), (sol["attempts"], sol["completed_attempts"], sol["priced_attempts"]))
        self.assertEqual({"count": 1, "median": 0.5, "p90": 0.5}, sol["usd_per_attempt"])


if __name__ == "__main__":
    unittest.main()
