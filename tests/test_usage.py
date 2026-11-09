"""Tokens and cost of every task: the totals, the status-view field and the per-project ledger."""

import json
import tempfile
import unittest
from pathlib import Path

import autocode_run_view as run_view
import autocode_status as status
import autocode_usage as usage

GLM = "zai-coding-plan/glm-5.3"


def stage(name, role, model, tokens=(100, 40, 10), cost=None, **extra):
    inp, out = tokens[0], tokens[2]
    metrics = {
        "provider_tokens": {
            "input_tokens": inp,
            "cached_input_tokens": tokens[1],
            "output_tokens": out,
            "reasoning_output_tokens": 0,
        }
    }
    if cost is not None:
        metrics["provider_cost_usd"] = cost
    return {"stage": name, "role": role, "command": ["x", "--model", model], "metrics": metrics, **extra}


class TotalsTests(unittest.TestCase):
    def test_reported_estimated_and_unknown_costs_stay_apart(self):
        state = {
            "stages": [
                stage("terra", "terra", "claude-haiku", cost=0.5),
                stage("sol", "sol", GLM, tokens=(1_000_000, 0, 1_000_000)),
                stage("astra_review", "astra", "claude-opus-5-5"),
                {"stage": "regression_proof", "role": "runner", "runner_owned": True, "engine": "runner"},
            ]
        }
        totals = usage.summary(state)
        self.assertEqual({"reported": 0.5, "estimated": 2.8, "complete": False}, totals["cost_usd"])
        self.assertEqual(1, totals["unknown_stages"])  # unknown is not zero; runner-only work is free
        self.assertEqual(4, totals["stages"])
        self.assertEqual({"astra", "runner", "sol", "terra"}, set(totals["by_role"]))
        self.assertEqual(0.5, totals["by_role"]["terra"]["reported_usd"])

    def test_a_run_whose_every_stage_has_a_cost_is_complete_until_a_stage_is_active(self):
        state = {"stages": [stage("terra", "terra", "claude-haiku", cost=1.25), stage("sol", "sol", "m", cost=0.75)]}
        self.assertEqual({"reported": 2.0, "estimated": 0.0, "complete": True}, usage.summary(state)["cost_usd"])
        state["active_stage"] = {"stage": "astra_review"}
        self.assertFalse(usage.summary(state)["cost_usd"]["complete"])
        self.assertEqual("astra_review", usage.summary(state)["active_stage"])

    def test_partial_tokens_remain_a_lower_bound_after_the_attempt_is_archived(self):
        interrupted = stage("glm_revise_report_repair", "glm", GLM, tokens=(1_000_000, 0, 1_000_000))
        interrupted["metrics"]["provider_tokens_partial"] = True
        state = {"status": "WAITING_FOR_USER", "stages": [interrupted]}
        totals = run_view.view(state)["usage"]
        self.assertEqual(1_000_000, totals["tokens"]["input_tokens"])
        self.assertEqual(1_000_000, totals["tokens"]["output_tokens"])
        self.assertEqual({"reported": 0.0, "estimated": 2.8, "complete": False}, totals["cost_usd"])
        self.assertEqual(1, totals["partial_stages"])
        self.assertEqual(0, totals["unknown_stages"])

    def test_a_reported_cost_needs_every_turn_to_report_and_a_zero_is_not_a_price(self):
        turn = {"type": "turn.completed", "cost_usd": 0.25}
        self.assertEqual(0.75, usage.reported_cost([turn, {"type": "turn.failed", "cost_usd": 0.5}]))
        self.assertIsNone(usage.reported_cost([turn, {"type": "turn.completed"}]))  # one turn unreported
        self.assertIsNone(usage.reported_cost([{"type": "turn.completed", "cost_usd": 0}]))  # subscription zero
        self.assertIsNone(usage.reported_cost([{"type": "turn.completed", "cost_usd": True}, {"type": "item"}]))
        self.assertIsNone(usage.reported_cost([{"type": "item.completed"}]))

    def test_the_model_comes_from_the_command_however_it_spells_the_flag_else_the_launch_route(self):
        tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        for record in (
            {"command": ["x", "--model", GLM]},
            {"command": ["x", "--model=" + GLM]},
            {"command": ["x"], "launch_route": {"model": GLM}},
            {"command": ["x", "--model", GLM], "launch_route": {"model": "claude-opus-5-5"}},
        ):
            with self.subTest(record=record):
                state = {"stages": [{"stage": "terra", "metrics": {"provider_tokens": tokens}, **record}]}
                self.assertEqual(
                    {"reported": 0.0, "estimated": 2.8, "complete": True}, usage.summary(state)["cost_usd"]
                )
        state = {"stages": [{"stage": "terra", "command": ["x"], "metrics": {"provider_tokens": tokens}}]}
        self.assertEqual(1, usage.summary(state)["unknown_stages"])  # no model recorded anywhere

    def test_a_damaged_state_still_gives_a_status_view(self):
        for state in (
            {"active_stage": "terra"},
            {"stages": "none"},
            {"stages": ["x", None, {"metrics": "m"}]},
            {"stages": [{"stage": "terra", "metrics": {"provider_tokens": ["a"]}, "role": {"x": 1}}]},
        ):
            with self.subTest(state=state):
                self.assertIn("usage", run_view.view({"status": "RUNNING", **state}))

    def test_the_status_view_carries_the_totals(self):
        view = run_view.view({"status": "RUNNING", "stages": [stage("terra", "terra", "m", cost=0.3)]})
        self.assertEqual(0.3, view["usage"]["cost_usd"]["reported"])

    def test_public_status_counts_native_tools_without_exposing_payloads(self):
        import autocode_request_usage as request_usage

        with tempfile.TemporaryDirectory() as root:
            events = Path(root) / "native.jsonl"
            private = "PRIVATE-command-path-input-output-error"
            rows = [{"type": "step_start", "sessionID": "session", "part": {"id": "start"}}]
            for identity, code in (("one", 0), ("one", 0), ("two", 7)):
                rows.append(
                    {
                        "type": "tool_use",
                        "sessionID": "session",
                        "part": {
                            "id": identity,
                            "tool": "bash",
                            "state": {
                                "status": "completed",
                                "input": {"command": private},
                                "output": private,
                                "metadata": {"exit": code},
                            },
                        },
                    }
                )
            rows.append({"type": "step_finish", "sessionID": "session", "part": {"id": "end", "reason": "stop"}})
            events.write_text("".join(json.dumps(row) + "\n" for row in rows))
            legacy = request_usage.read(events)
            legacy["accounting"].pop("tool_activity", None)
            record = {"stage": "terra", "events": str(events), "metrics": {"request_context": legacy}}
            shown = run_view.view({"status": "RUNNING", "stages": [record]})
            result = shown["usage"]["accounting"]["attempts"][0]["tool_activity"]
            self.assertEqual(2, result["observed_calls"])
            self.assertEqual(1, result["exact_argument_repeats"])
            self.assertEqual({"nonzero_exit": 1, "tool_error": 0}, result["error_classes"])
            self.assertTrue(result["complete"])
            self.assertNotIn(private, json.dumps(result))
            self.assertNotIn("tool_activity", legacy["accounting"])
            self.assertEqual("RUNNING", shown["status"], "Diagnostics grant no task authority")

    def test_actual_models_keep_stage_jobs_and_launch_variants_after_a_route_change(self):
        state = {
            "status": "RUNNING",
            "settings": {"workflow": {"mode": "glm_first_v1"}, "roles": {"completion": {"model": "next-model"}}},
            "stages": [
                stage("astra_challenge", "astra", "review-model", launch_route={"reasoning_effort": "high"}),
                stage(
                    "astra_checkpoint_report_repair", "astra", "test-model", launch_route={"reasoning_effort": "medium"}
                ),
            ],
        }
        view = run_view.view(state)
        attempts = view["usage"]["accounting"]["attempts"]
        self.assertEqual(["Plan Reviewer", "Tester"], [row["job"] for row in attempts])
        self.assertEqual(["review-model", "test-model"], [row["model"] for row in attempts])
        self.assertEqual(["high", "medium"], [row["launch_route"]["reasoning_effort"] for row in attempts])
        self.assertEqual("next-model", view["routes"]["completion"]["model"])

    def test_an_attempt_carries_the_level_it_ran_at_and_its_effort_choice(self):
        # A review under a stage-choice ceiling (owner decision 2026-11-05): /details shows both.
        choice = {"level": "medium", "ceiling": "high", "lowered": True, "reason": "small_clear"}
        state = {
            "status": "RUNNING",
            "stages": [
                stage(
                    "astra_challenge",
                    "astra",
                    "review-model",
                    launch_route={"reasoning_effort": "high"},
                    reasoning_effort="medium",
                    effort_choice=choice,
                ),
                stage("terra", "terra", "build-model", launch_route={"reasoning_effort": "high"}),
            ],
        }
        reviewed, built = run_view.view(state)["usage"]["accounting"]["attempts"]
        self.assertEqual(("medium", choice), (reviewed["reasoning_effort"], reviewed["effort_choice"]))
        self.assertNotIn("effort_choice", built)

    def test_an_attempt_names_the_route_slot_its_stage_launched(self):
        # #2114: `astra` runs other slots' stages; qualification must credit the slot that launched.
        state = {
            "status": "RUNNING",
            "stages": [
                stage("astra_review", "astra", "review-model", route_role="completion"),
                stage("astra_discovery", "glm", "plan-model"),
            ],
        }
        rows = run_view.view(state)["usage"]["accounting"]["attempts"]
        self.assertEqual(
            [("astra", "completion"), ("glm", None)],
            [(row["role"], row["route_role"]) for row in rows],
        )

    def test_request_level_reported_costs_are_counted_for_unpriced_models(self):
        """#2996: a model missing from the price table is never zero-cost when the provider reported amounts."""
        record = {
            "stage": "terra",
            "role": "terra",
            "command": ["x", "--model", "some/unknown-model-v9"],
            "metrics": {
                "provider_tokens": {
                    "input_tokens": 5000,
                    "cached_input_tokens": 0,
                    "output_tokens": 200,
                    "reasoning_output_tokens": 0,
                },
                "request_context": {
                    "accounting": {
                        "requests": [
                            {"reported_cost_usd": 1.50},
                            {"reported_cost_usd": 0.75},
                            {"reported_cost_usd": 0.25},
                        ]
                    }
                },
            },
        }
        rows = usage.stage_rows({"stages": [record]}, estimator=lambda m, t, f: None)
        self.assertEqual(2.50, rows[0]["cost_usd"], "request-reported costs are summed")
        self.assertEqual("reported", rows[0]["basis"])


class StageMetricsTests(unittest.TestCase):
    def test_a_stage_records_the_cost_its_provider_reported(self):
        import autocode_support as support

        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "terra-01.jsonl"
            log.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in (
                        {
                            "type": "turn.failed",
                            "usage": {
                                "input_tokens": 5,
                                "cached_input_tokens": 1,
                                "output_tokens": 2,
                                "reasoning_output_tokens": 0,
                            },
                            "cost_usd": 0.25,
                        },
                        {
                            "type": "turn.completed",
                            "usage": {
                                "input_tokens": 7,
                                "cached_input_tokens": 3,
                                "output_tokens": 4,
                                "reasoning_output_tokens": 0,
                            },
                            "cost_usd": 0.5,
                        },
                    )
                )
                + "\n"
            )
            metrics = support.event_metrics(log)
            self.assertEqual(0.75, metrics["provider_cost_usd"])
            self.assertEqual(12, metrics["provider_tokens"]["input_tokens"])
            log.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 1,
                            "cached_input_tokens": 0,
                            "output_tokens": 1,
                            "reasoning_output_tokens": 0,
                        },
                    }
                )
                + "\n"
            )
            self.assertIsNone(support.event_metrics(log)["provider_cost_usd"])  # a provider that reports none


class LedgerTests(unittest.TestCase):
    def run_dir(self, root, name="20260930-run-a"):
        run = Path(root) / ".autocode" / "runs" / name
        run.mkdir(parents=True)
        return run

    def test_every_checkpoint_keeps_one_current_row_per_run(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = self.run_dir(temp), self.run_dir(temp, "20260930-run-b")
            state = {"status": "RUNNING", "stages": [stage("terra", "terra", "m", cost=0.5)]}
            status.persist(first / "state.json", state)
            status.persist(first / "state.json", state)  # unchanged: nothing more to write
            state["stages"].append(stage("sol", "sol", "m", cost=0.25))
            state["status"] = "TASK_COMPLETE"
            status.persist(first / "state.json", state)
            status.persist(
                second / "state.json", {"status": "PAUSED_BUDGET", "stages": [stage("terra", "terra", "m", cost=1.0)]}
            )
            rows = [json.loads(line) for line in (Path(temp) / ".autocode" / "usage.jsonl").read_text().splitlines()]
            self.assertEqual(["20260930-run-a", "20260930-run-b"], [row["run"] for row in rows])
            self.assertEqual(
                ("TASK_COMPLETE", 0.75, 2), (rows[0]["status"], rows[0]["cost_usd"]["reported"], rows[0]["stages"])
            )
            self.assertEqual("PAUSED_BUDGET", rows[1]["status"])
            text = usage.report(temp)
            self.assertIn("total (2 runs)", text)
            self.assertIn("$1.75", text)

    def test_a_run_with_no_stage_yet_writes_nothing_and_a_failure_never_fails_the_save(self):
        with tempfile.TemporaryDirectory() as temp:
            run = self.run_dir(temp)
            status.persist(run / "state.json", {"status": "RUNNING"})
            self.assertFalse((Path(temp) / ".autocode" / "usage.jsonl").exists())
            (Path(temp) / ".autocode" / "usage.jsonl").mkdir()  # unwritable ledger
            status.persist(
                run / "state.json", {"status": "RUNNING", "stages": [stage("terra", "terra", "m", cost=1.0)]}
            )
            self.assertTrue((run / "state.json").is_file())

    def test_a_parallel_builders_attempts_are_charged_to_its_parent_not_to_a_ledger_of_its_own(self):
        with tempfile.TemporaryDirectory() as temp:
            worker = Path(temp) / ".autocode" / "builders" / "b1" / "1" / ".autocode" / "runs" / "builder-b1-1"
            worker.mkdir(parents=True)
            status.persist(
                worker / "state.json",
                {"status": "RUNNING", "parent_run": "/p", "stages": [stage("terra", "terra", "m", cost=1.0)]},
            )
            self.assertFalse((worker.parent.parent / "usage.jsonl").exists())
            parent = self.run_dir(temp)
            account = {**stage("terra", "terra", "m", cost=1.0), "worker_milestone": "M2", "batch_id": "b1"}
            status.persist(
                parent / "state.json", {"status": "RUNNING", "stages": [account]}
            )  # as account_workers copies it
            self.assertIn("$1.00", usage.report(temp))

    def test_an_empty_project_says_so(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIn("No usage recorded", usage.report(temp))


if __name__ == "__main__":
    unittest.main()
