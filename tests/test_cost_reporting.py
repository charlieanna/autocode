"""Offline accounting regressions discovered by the canonical tools suite."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import live_token_sampler as sampler
import score_autocode_run as scorer
from tools.providers.opencode import normalized_events


GLM = "zai-coding-plan/glm-5.3"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"


def event(ident="part-1", session="session-1", **changes):
    tokens = {"input": 100, "output": 20, "reasoning": 10,
              "cache": {"read": 40, "write": 5}}
    tokens.update(changes)
    return {"type": "step_finish", "sessionID": session, "timestamp": 1234,
            "part": {"id": ident, "type": "step-finish", "reason": "stop",
                     "cost": 0, "tokens": tokens}}


def record(model=GLM, stage="terra", **changes):
    row = {"stage": stage, "command": ["opencode", "run", "--model", model],
           "metrics": {"provider_tokens": {
               "input_tokens": 145, "cached_input_tokens": 40,
               "output_tokens": 30, "reasoning_output_tokens": 10}},
           "finished_at": "2026-09-28T00:00:00Z"}
    row.update(changes)
    return row


class CostSemanticsTest(unittest.TestCase):
    def test_raw_and_actual_provider_normalization_have_identical_cost(self):
        raw = event()
        usage = normalized_events([raw])[-1]["usage"]
        self.assertEqual(145, usage["input_tokens"])
        self.assertEqual(30, usage["output_tokens"])
        expected = (145 * 0.60 + 30 * 2.20) / 1e6
        self.assertAlmostEqual(expected, scorer.estimate_cost(GLM, raw["part"]["tokens"]))
        self.assertAlmostEqual(expected, scorer.estimate_cost(GLM, usage))

    def test_missing_invalid_or_unknown_data_is_not_free(self):
        for tokens in ({}, {"input_tokens": 1}, {"input_tokens": None, "output_tokens": 0},
                       {"input_tokens": True, "output_tokens": 0},
                       {"input_tokens": -1, "output_tokens": 0},
                       {"input_tokens": 1.5, "output_tokens": 0},
                       {"input_tokens": 1, "output_tokens": float("nan")},
                       {"input_tokens": 1, "cached_input_tokens": 2, "output_tokens": 0},
                       {"input_tokens": 1, "output_tokens": 1, "reasoning_output_tokens": 2},
                       {"input": 100, "output": 20, "reasoning": 0, "cache": {"read": 0}}):
            with self.subTest(tokens=tokens):
                self.assertIsNone(scorer.estimate_cost(GLM, tokens))
        self.assertIsNone(scorer.estimate_cost("unknown-model", {"input_tokens": 1, "output_tokens": 1}))

    def test_explicit_zero_and_optional_normalized_details(self):
        self.assertEqual(0, scorer.estimate_cost(GLM, {"input_tokens": 0, "output_tokens": 0,
                                                      "input": 999, "output": 999}))
        self.assertIsNotNone(scorer.estimate_cost(GLM, {"input_tokens": 1, "output_tokens": 1}))

    def test_normalized_missing_does_not_fall_back_to_conflicting_raw_fields(self):
        self.assertIsNone(scorer.estimate_cost(GLM, {"input_tokens": None, "output_tokens": 2,
                                                    "input": 100, "output": 3}))

    def test_small_calls_keep_precision_before_aggregation(self):
        one = scorer.estimate_cost(GLM, {"input_tokens": 1, "output_tokens": 1})
        self.assertGreater(one, 0)
        self.assertAlmostEqual(0.0028, sum([one] * 1000))

    def test_launch_model_beats_current_settings_and_record_label(self):
        state = {"settings": {"roles": {"terra": {"model": MIMO}}},
                 "stages": [record(model=GLM)]}
        state["stages"][0]["model"] = MIMO
        before = copy.deepcopy(state)
        row = scorer.stage_token_rows(state)[0]
        self.assertEqual(GLM, row["model"])
        self.assertEqual(before, state)

    def test_missing_launch_does_not_guess_from_current_settings(self):
        state = {"settings": {"roles": {"terra": {"model": GLM}}},
                 "stages": [record(command=["opencode", "--model"])]}
        row = scorer.stage_token_rows(state)[0]
        self.assertEqual("", row["model"])
        self.assertIsNone(row["estimated_usd"])

    def test_model_command_variants_and_record_fallback(self):
        for command in (["cmd", "-m", GLM], ["cmd", "--model=" + GLM]):
            self.assertEqual(GLM, scorer.recorded_model({"command": command}))
        self.assertEqual(GLM, scorer.recorded_model({"model": GLM}))


class SavedRunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def save(self, state):
        (self.root / "state.json").write_text(json.dumps(state))

    def log(self, name, rows):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        return str(path)

    def test_failed_attempts_remain_in_total_and_unknown_poisons_complete_total(self):
        failed = record(timed_out=True, abandoned=True, exit_code=1)
        state = {"stages": [failed, record(model=MIMO)]}
        self.save(state)
        report = scorer.score_run(self.root)
        usage = report["token_usage"]
        expected = sum(scorer.estimate_cost(m, failed["metrics"]["provider_tokens"]) for m in (GLM, MIMO))
        self.assertAlmostEqual(expected, usage["estimated_api_equivalent_usd"])
        state["stages"].append(record(metrics={}))
        self.save(state)
        report = scorer.score_run(self.root)
        usage = report["token_usage"]
        self.assertIsNone(usage["estimated_api_equivalent_usd"])
        self.assertAlmostEqual(expected, usage["known_estimated_api_equivalent_usd"])
        self.assertIsNone(usage["totals"]["input_tokens"])
        self.assertEqual(1, usage["unpriced_stages"])
        self.assertEqual("PARTIAL", report["scores"]["token_discipline"]["score"])
        self.assertIn("**Estimated API-equivalent cost:** unknown", scorer.render(report))

    def test_empty_and_active_runs_do_not_claim_complete_cost(self):
        for state in ({}, {"stages": [record()], "active_stage": record()}):
            self.save(state)
            report = scorer.score_run(self.root)
            self.assertIsNone(report["token_usage"]["estimated_api_equivalent_usd"])
            scorer.render(report)

    def test_runner_owned_transition_costs_zero_without_fabricated_model(self):
        self.save({"stages": [record(command=[], engine="runner", runner_owned=True,
                                      metrics={"provider_tokens": {"input_tokens": 0, "output_tokens": 0}})]})
        report = scorer.score_run(self.root)
        self.assertEqual(0, report["token_usage"]["estimated_api_equivalent_usd"])

    def test_duplicate_updates_count_once_and_unregistered_logs_are_ignored(self):
        initial = event(input=50)
        updated = event(input=100)
        path = self.log("events/terra-01.jsonl", [initial, [], "noise", updated])
        self.log("evidence/copied.jsonl", [updated])
        self.save({"stages": [record(events=path)]})
        samples, summary = sampler.sample_run(self.root)
        self.assertEqual(1, len(samples))
        self.assertEqual(100, samples[0]["tokens"]["input"])
        self.assertEqual(1234, samples[0]["ts"])
        self.assertEqual(5, summary["total"]["cache_write"])
        self.assertEqual("observed_steps_only", summary["scope"])

    def test_same_part_id_in_different_sessions_is_not_deduplicated(self):
        path = self.log("terra-01.jsonl", [event(session="a"), event(session="b")])
        self.save({"stages": [record(events=path)]})
        samples, _ = sampler.sample_run(self.root)
        self.assertEqual(2, len(samples))

    def test_retry_models_are_resolved_by_exact_log(self):
        first = self.log("terra-01.jsonl", [event()])
        second = self.log("terra-02.jsonl", [event()])
        self.save({"settings": {"roles": {"terra": {"model": "new-model"}}},
                   "stages": [record(events=first), record(model=MIMO, events=second)]})
        samples, summary = sampler.sample_run(self.root)
        self.assertEqual([GLM, MIMO], [s["model"] for s in samples])
        expected = sum(scorer.estimate_cost(m, event()["part"]["tokens"]) for m in (GLM, MIMO))
        self.assertAlmostEqual(expected, summary["total"]["est_usd"])
        self.assertEqual(sorted([GLM, MIMO]), summary["per_stage"]["terra"]["models"])

    def test_missing_tokens_and_zero_reported_subscription_cost_stay_unknown(self):
        row = event()
        row["part"].pop("tokens")
        path = self.log("terra-01.jsonl", [row])
        self.save({"stages": [record(events=path)]})
        samples, summary = sampler.sample_run(self.root)
        self.assertEqual(0, samples[0]["cost_reported"])
        self.assertIsNone(samples[0]["est_usd"])
        self.assertIsNone(summary["total"]["input"])
        self.assertIsNone(summary["total"]["est_usd"])
        self.assertIn("unknown", sampler.render_stream(samples))
        self.assertIn("unknown", sampler.render_summary(summary))

    def test_conflicting_log_owners_do_not_choose_a_model(self):
        path = self.log("terra-01.jsonl", [event()])
        self.save({"stages": [record(events=path), record(model=MIMO, events=path)]})
        samples, _ = sampler.sample_run(self.root)
        self.assertEqual(1, len(samples))
        self.assertEqual("", samples[0]["model"])
        self.assertIsNone(samples[0]["est_usd"])

    def test_outside_log_and_symlink_are_not_read(self):
        with tempfile.TemporaryDirectory() as external:
            path = Path(external) / "outside.jsonl"
            path.write_text(json.dumps(event()))
            (self.root / "link.jsonl").symlink_to(path)
            self.save({"stages": [record(events=str(path)), record(events="link.jsonl")]})
            samples, summary = sampler.sample_run(self.root)
            self.assertEqual([], samples)
            self.assertIsNone(summary["total"]["est_usd"])

    def test_relative_log_and_truncated_line_are_supported(self):
        path = self.log("events/terra-100.jsonl", [event()])
        with Path(path).open("a") as handle:
            handle.write('{"type":')
        self.save({"stages": [record(events="events/terra-100.jsonl")]})
        samples, _ = sampler.sample_run(self.root)
        self.assertEqual(1, len(samples))
        self.assertEqual("terra", samples[0]["stage"])

    def test_cli_entry_points_emit_unknown_and_leave_saved_run_unchanged(self):
        self.save({"stages": [record(metrics={})]})
        before = (self.root / "state.json").read_bytes()
        root = Path(__file__).resolve().parents[1]
        for module in ("score_autocode_run", "live_token_sampler"):
            for args in ([str(root / "tools" / (module + ".py"))], ["-m", "tools." + module]):
                result = subprocess.run([sys.executable, *args, "--run-dir", str(self.root)],
                                        cwd=root, capture_output=True, text=True, timeout=10)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("unknown", result.stdout)
        self.assertEqual(before, (self.root / "state.json").read_bytes())


class ModelRoutingTests(unittest.TestCase):
    def test_any_recorded_model_is_allowed_and_not_penalized_for_billing_or_ladder(self):
        for model in ('mimo-token-plan/mimo-v2.6-pro', 'opencode/mimo-v2.6-flash-free',
                      'zai-coding-plan/glm-5.2-highspeed', 'new-plan/future-model'):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as temp:
                run = Path(temp)
                state = {'status': 'RUNNING', 'settings': {'roles': {
                    'terra': {'model': model}, 'sol': {'model': 'openai/gpt-6-sol'}}},
                    'stages': [{'stage': 'terra', 'command': ['opencode', 'run', '--model', model]}]}
                (run / 'state.json').write_text(json.dumps(state))
                routing = scorer.model_route_checks(state, run)
                self.assertIn(model, routing['launched_models'])
                self.assertEqual([], routing['forbidden_seen'])
                report = scorer.score_run(run)
                self.assertEqual('PASS', report['scores']['model_routing']['score'])


if __name__ == "__main__":
    unittest.main()
