"""Accounting oracles: exact quantities, negative controls, no model spending."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

import autocode_efficiency as efficiency
import autocode_request_usage as requests
import autocode_run_view as run_view
import autocode_usage as usage


def finish(identity="p1", *, session="session", **changes):
    row = {"type": "step_finish", "sessionID": session, "timestamp": 5000,
           "part": {"id": identity, "sessionID": session, "cost": 0.3,
                    "tokens": {"input": 10, "output": 4, "reasoning": 6, "cache": {"read": 20, "write": 3}}}}
    row["part"]["tokens"].update(changes)
    return row


def attempt(name="terra", identity="one", *, start=0, end=10, **extra):
    rows = [{"type": "step_start", "sessionID": "session", "timestamp": 1000}, finish(identity)]
    return {"stage": name, "role": name, "output": f"/owned/run/iterations/001/{identity}.json",
            "iteration": 1, "started_at": start, "finished_at": end, "duration_seconds": end - start,
            "model": "unpriced-model", "task_id": "T1", "exit_code": 0,
            "metrics": {"request_context": {"accounting": requests.accounting(rows)}}, **extra}


def design_state():
    cases = [{"id": f"case{n}", "file_key": "File", "node_id": f"{n}:1", "state": "default",
              "viewport": {"width": 320, "height": 240, "device_scale_factor": 1}} for n in (1, 2)]
    return {"status": "TASK_COMPLETE", "settings": {"design_manifest": {
        "manifest_hash": "manifest1", "body": {"files": [{"key": "File", "nodes": ["1:1", "2:1"]}], "cases": cases}}},
        "validation": {"verdict": "PASS", "source_revision": "source1", "design_manifest_hash": "manifest1",
                       "design_results": [{"id": "case1", "status": "PASS", "criterion_ids": ["C1"]},
                                          {"id": "case2", "status": "FAIL", "criterion_ids": ["C2"]}]}}


class NativeAccountingTests(unittest.TestCase):
    def test_cache_and_reasoning_are_inclusive_once_with_raw_components_retained(self):
        measured = usage.accounting({"stages": [attempt()]})
        expected = {"input_tokens": 33, "cached_input_tokens": 20, "cache_write_tokens": 3,
                    "fresh_input_tokens": 10, "output_tokens": 10, "reasoning_output_tokens": 6,
                    "visible_output_tokens": 4}
        self.assertEqual(expected, {key: value["value"] for key, value in measured["tokens"].items()})
        self.assertEqual(1, measured["provider_requests"]["value"])
        self.assertEqual(0.3, measured["cost"]["reported_usd"]["value"])
        self.assertIsNone(measured["cost"]["api_equivalent_usd"])
        self.assertIsNone(measured["cost"]["subscription_invoice_usd"])

    def test_missing_each_counter_is_unknown_not_zero_and_other_quantities_survive(self):
        for field in ("input", "output", "reasoning", "cache"):
            with self.subTest(field=field):
                event = finish()
                del event["part"]["tokens"][field]
                record = attempt()
                record["metrics"]["request_context"]["accounting"] = requests.accounting([event])
                measured = usage.accounting({"stages": [record]})
                missing = "input_tokens" if field in ("input", "cache") else "output_tokens"
                other = "output_tokens" if missing == "input_tokens" else "input_tokens"
                self.assertIsNone(measured["tokens"][missing]["value"])
                self.assertIsNotNone(measured["tokens"][other]["value"])
                self.assertFalse(measured["complete"])

    def test_duplicate_finish_and_cross_attempt_replay_are_counted_once(self):
        first, second = attempt(), attempt("sol", "two", start=10, end=20)
        events = [finish(), finish(), finish("new")]
        first["metrics"]["request_context"]["accounting"] = requests.accounting([finish()])
        second["metrics"]["request_context"]["accounting"] = requests.accounting(events)
        measured = usage.accounting({"stages": [first, second]})
        self.assertEqual(2, measured["provider_requests"]["value"])
        self.assertEqual(66, measured["tokens"]["input_tokens"]["value"])
        self.assertEqual([33, 33], [row["attributed_tokens"]["input_tokens"]["value"] for row in measured["attempts"]])
        result = efficiency.summary({"stages": [first, second]})
        self.assertEqual(66, sum(row["tokens"]["input_tokens"]["known"] for row in result["by_category"].values()))

    def test_conflicting_duplicate_stays_unknown_after_original_is_replayed(self):
        events = [finish(), finish(input=50), finish()]
        result = requests.accounting(events)
        self.assertTrue(result["requests"][0]["conflict"])
        self.assertIsNone(result["requests"][0]["tokens"]["input_tokens"])
        self.assertFalse(result["complete"])
        record = attempt()
        record["metrics"]["request_context"]["accounting"] = result
        self.assertIsNone(usage.accounting({"stages": [record]})["tokens"]["input_tokens"]["value"])

    def test_old_finish_cannot_close_newer_unfinished_request(self):
        result = requests.accounting([finish(), {"type": "step_start", "timestamp": 6000}, finish()])
        self.assertTrue(result["unfinished_request"])
        self.assertFalse(result["complete"])
        self.assertEqual(1, result["observed_requests"])

    def test_interleaved_sessions_keep_independent_pending_requests_and_intervals(self):
        first, second = finish(session="s1"), finish("p2", session="s2")
        first["timestamp"], second["timestamp"] = 3000, 4000
        starts = [{"type": "step_start", "sessionID": "s1", "timestamp": 1000, "part": {"id": "start1"}},
                  {"type": "step_start", "sessionID": "s2", "timestamp": 2000, "part": {"id": "start2"}}]
        partial = requests.accounting([*starts, first])
        self.assertFalse(partial["complete"])
        self.assertTrue(partial["unfinished_request"])
        self.assertEqual(1, partial["unfinished_requests"])
        self.assertEqual(1000, partial["requests"][0]["started_at_ms"])
        complete = requests.accounting([*starts, second, first])
        self.assertTrue(complete["complete"])
        self.assertEqual({"s1": (1000, 3000), "s2": (2000, 4000)}, {
            row["identity"][0]: (row["started_at_ms"], row["finished_at_ms"]) for row in complete["requests"]})

    def test_exact_start_replay_is_idempotent_but_unidentified_starts_remain_unresolved(self):
        start = {"type": "step_start", "sessionID": "session", "timestamp": 1000, "part": {"id": "start"}}
        complete = requests.accounting([start, start, finish(), start, finish()])
        self.assertTrue(complete["complete"])
        unknown = requests.accounting([{"type": "step_start", "timestamp": 1}, start, finish()])
        self.assertFalse(unknown["complete"])
        self.assertTrue(unknown["unfinished_request"])
        repeated_unknown = requests.accounting([{"type": "step_start", "sessionID": "session"},
            {"type": "step_start", "sessionID": "session"}, finish()])
        self.assertFalse(repeated_unknown["complete"])
        self.assertEqual(1, repeated_unknown["unfinished_requests"])

    def test_invalid_finish_identity_does_not_manufacture_zero_requests(self):
        event = finish()
        event["part"]["sessionID"] = "wrong-session"
        measured = requests.accounting([event])
        self.assertEqual([], measured["requests"])
        self.assertFalse(measured["complete"])
        self.assertTrue(measured["issues"])

    def test_provider_error_after_finish_does_not_hide_unreported_failed_request(self):
        measured = requests.accounting([finish(), {"type": "error", "error": {"name": "APIError"}}])
        self.assertFalse(measured["complete"])
        self.assertEqual(1, measured["observed_requests"])
        self.assertTrue(any("unreported request" in issue for issue in measured["issues"]))

    def test_model_attribution_conflict_does_not_erase_identical_known_token_quantities(self):
        first = attempt(model="model-one")
        second = attempt("sol", "two", model="model-two")
        second["metrics"] = deepcopy(first["metrics"])
        measured = usage.accounting({"stages": [first, second]})
        self.assertEqual(33, measured["tokens"]["input_tokens"]["value"])
        self.assertEqual(1, measured["provider_requests"]["value"])
        self.assertTrue(any("model attribution" in issue for issue in measured["issues"]))

    def test_active_partial_and_unparsed_native_logs_preserve_known_subtotals(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            path.write_text(json.dumps(finish()) + '\n{"type":"step_start"}\n{"partial":')
            record = attempt(events=str(path), engine="opencode")
            result = usage.accounting({"active_stage": record})
            self.assertEqual(1, result["active_attempts"])
            self.assertEqual(33, result["tokens"]["input_tokens"]["known"])
            self.assertIsNone(result["tokens"]["input_tokens"]["value"])
            self.assertIsNone(result["provider_requests"]["value"])
            self.assertTrue(any("truncated" in issue for issue in result["issues"]))

    def test_missing_event_file_does_not_turn_unseen_usage_into_zero(self):
        result = usage.accounting({"active_stage": attempt(events="/does-not-exist/events", engine="opencode")})
        self.assertIsNone(result["tokens"]["input_tokens"]["value"])
        self.assertIsNone(result["provider_requests"]["value"])
        self.assertFalse(result["complete"])

    def test_finished_pinned_request_receipt_is_not_reparsed_on_status_poll(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            path.write_text(json.dumps(finish()) + "\n")
            record = attempt(events=str(path), engine="opencode")
            record["metrics"]["request_context"] = requests.read(path)
            with mock.patch.object(requests, "read", side_effect=AssertionError("unnecessary full-log reread")):
                self.assertEqual(33, usage.accounting({"stages": [record]})["tokens"]["input_tokens"]["value"])
            path.write_text(json.dumps(finish(input=15)) + "\n")
            self.assertEqual(38, usage.accounting({"stages": [record]})["tokens"]["input_tokens"]["known"])

    def test_failed_interrupted_and_report_repair_attempts_all_count(self):
        records = [attempt(identity="fail", exit_code=1, rejected=True),
                   attempt("sol_report_repair", "repair", start=10, end=20, report_only=True),
                   attempt("sol", "interrupt", start=20, end=25, interrupted=True)]
        measured = usage.accounting({"stages": records})
        self.assertEqual(3, measured["attempt_count"])
        self.assertEqual(99, measured["tokens"]["input_tokens"]["value"])
        self.assertAlmostEqual(0.9, measured["cost"]["reported_usd"]["value"])
        self.assertEqual(1, efficiency.summary({"stages": records})["by_category"]["report_repair"]["attempts"])

    def test_unknown_price_does_not_hide_known_tokens_or_claim_subscription_zero(self):
        record = attempt()
        record["metrics"]["request_context"]["accounting"]["requests"][0]["reported_cost_usd"] = None
        measured = usage.accounting({"stages": [record]})
        self.assertEqual(33, measured["tokens"]["input_tokens"]["value"])
        self.assertIsNone(measured["cost"]["reported_usd"]["value"])
        self.assertIsNone(measured["cost"]["historical_estimated_usd"]["value"])
        self.assertIsNone(measured["cost"]["subscription_invoice_usd"])

    def test_conflicting_saved_attempt_is_not_added_twice_or_silently_selected(self):
        first, conflict = attempt(), attempt()
        conflict["metrics"]["request_context"]["accounting"] = requests.accounting([finish("one", input=99)])
        measured = usage.accounting({"stages": [first, conflict, first]})
        self.assertEqual(1, measured["attempt_count"])
        self.assertIsNone(measured["tokens"]["input_tokens"]["value"])
        self.assertTrue(any("Conflicting attempt" in issue for issue in measured["issues"]))


class OutcomeTests(unittest.TestCase):
    def test_multiframe_green_tests_do_not_accept_visual_mismatch_or_uninspected_pass(self):
        result = efficiency.summary(design_state(), completion_current=True)
        self.assertTrue(result["delivery"]["taskrun_done"])
        self.assertIsNone(result["delivery"]["verified_deliveries"])
        visual = result["visual"]
        self.assertEqual((2, 2), (visual["requested_frames"], visual["requested_states"]))
        self.assertIsNone(visual["accepted_frames"])
        self.assertIsNone(visual["accepted_states"])
        self.assertIsNone(visual["cases"][0]["accepted"])
        self.assertIs(visual["cases"][1]["accepted"], False)
        self.assertIn("#250/#251", visual["cases"][0]["reason"])
        self.assertIsNone(result["unit_metrics"]["input_tokens"]["value"])

    def test_all_failed_visual_cases_have_zero_accepted_and_undefined_unit_cost(self):
        state = design_state()
        state["validation"]["design_results"][0]["status"] = "FAIL"
        result = efficiency.summary(state, completion_current=True)
        self.assertEqual(0, result["visual"]["accepted_states"])
        self.assertEqual(0, result["delivery"]["verified_deliveries"])
        self.assertIsNone(result["unit_metrics"]["reported_usd"]["value"])

    def test_produced_capture_and_model_boolean_cannot_become_acceptance(self):
        state = design_state()
        for row in state["validation"]["design_results"]:
            row.update(status="PASS", candidate_ref="real.png", comparison_ref="inspection.txt", accepted=True)
        state["current_visual_acceptance"] = True
        state["visual_acceptance"] = {"verified": True, "accepted_states": 2}
        result = efficiency.summary(state, completion_current=True)
        self.assertIsNone(result["visual"]["accepted_states"])
        self.assertTrue(all(row["status"] == "UNKNOWN" for row in result["visual"]["cases"]))
        self.assertIsNone(result["visual"]["first_independently_accepted_screen"]["at"])

    def test_reference_or_source_change_never_launders_history_or_rekeys_cases(self):
        state = design_state()
        original = deepcopy(state)
        before = efficiency.summary(state, completion_current=True)
        state["validation"]["source_revision"] = "source2"
        state["settings"]["design_manifest"]["manifest_hash"] = "new-reference"
        after = efficiency.summary(state, completion_current=False)
        self.assertEqual(before["visual"]["cases"][0]["key"], after["visual"]["cases"][0]["key"])
        self.assertTrue(all(row["accepted"] is None for row in after["visual"]["cases"]))
        self.assertEqual(original["validation"]["design_results"], state["validation"]["design_results"])
        self.assertIsNone(after["visual"]["cases"][0]["first_accepted_at"])

    def test_generic_completion_is_available_but_pass_review_is_not_taskrun_done(self):
        for status, expected in (("PASS", False), ("RUNNING", False), ("TASK_COMPLETE", True), ("COMPLETE", True)):
            with self.subTest(status=status):
                view = run_view.view({"status": status, "stages": [attempt()]}, completion_current=True)
                self.assertEqual(expected, view["done"])
                self.assertEqual(expected, view["efficiency"]["delivery"]["taskrun_done"])
                self.assertEqual(int(expected), view["efficiency"]["delivery"]["verified_deliveries"])
                self.assertEqual(33 if expected else None, view["efficiency"]["unit_metrics"]["input_tokens"]["value"])

    def test_criteria_need_completion_not_hidden_machine_claims(self):
        state = {"status": "RUNNING", "acceptance_criteria": [{"id": "C1"}, {"id": "C2"}],
                 "last_decision": {"acceptance_criteria": [{"id": "C1", "status": "verified"}, {"id": "C2", "status": "unverified"}]}}
        result = efficiency.summary(state)
        self.assertEqual(0, result["criteria"]["accepted"])
        state["status"] = "TASK_COMPLETE"
        result = efficiency.summary(state, completion_current=True)
        self.assertEqual(1, result["criteria"]["accepted"])
        self.assertEqual(["C2"], result["criteria"]["gaps"])

    def test_visual_mapped_criteria_do_not_inherit_green_completion_acceptance(self):
        state = design_state()
        state["acceptance_criteria"] = [{"id": "C1"}, {"id": "C2"}, {"id": "functional"}]
        state["last_decision"] = {"acceptance_criteria": [{"id": identity, "status": "verified"}
                                                         for identity in ("C1", "C2", "functional")]}
        result = efficiency.summary(state, completion_current=True)
        self.assertEqual([None, False, True], [row["accepted"] for row in result["criteria"]["rows"]])
        self.assertIsNone(result["criteria"]["accepted"])
        self.assertEqual(1, result["criteria"]["known_accepted"])
        self.assertIsNone(result["unit_metrics"]["per_accepted_frame"]["input_tokens"]["value"])

    def test_missing_denominators_are_undefined_not_zero_or_a_percentage(self):
        result = efficiency.summary({"status": "RUNNING"})
        self.assertIsNone(result["visual"]["requested_states"])
        self.assertIsNone(result["criteria"]["requested"])
        self.assertIsNone(result["unit_metrics"]["reported_usd"]["value"])


class CurrentCompletionTests(unittest.TestCase):
    def state(self, run="/root", *, status="TASK_COMPLETE", identity="current"):
        return {"run_dir": run, "status": status, "stages": [attempt(identity=identity)],
                "acceptance_criteria": [{"id": "C1"}, {"id": "C2"}],
                "last_decision": {"acceptance_criteria": [{"id": "C1", "status": "verified"},
                                                          {"id": "C2", "status": "PASS"}]}}

    def test_saved_completion_without_trusted_fresh_check_is_unknown_not_accepted(self):
        state = self.state()
        state["completion_current"] = True  # Saved/model-owned data is not the trusted caller argument.
        original = deepcopy(state)
        for arguments in ({}, {"completion_current": None}, {"completion_current": 1}, {"completion_current": "True"}):
            with self.subTest(arguments=arguments), mock.patch.object(Path, "open", side_effect=AssertionError("view must not run a proof check")):
                view = run_view.view(state, **arguments)
                result = view["efficiency"]
                self.assertTrue(view["done"])
                self.assertEqual(1, result["delivery"]["completed_tasks"])
                self.assertIsNone(result["delivery"]["current_completion"])
                self.assertIsNone(result["delivery"]["verified_deliveries"])
                self.assertIsNone(result["criteria"]["accepted"])
                self.assertEqual([None, None], [row["accepted"] for row in result["criteria"]["rows"]])
                self.assertEqual(["verified", "PASS"], [row["reported_status"] for row in result["criteria"]["rows"]])
                self.assertIsNone(result["unit_metrics"]["input_tokens"]["denominator"])
                self.assertIsNone(result["unit_metrics"]["input_tokens"]["value"])
                self.assertIsNone(result["unit_metrics"]["reported_usd"]["value"])
                self.assertIsNone(result["unit_metrics"]["wall_seconds"]["value"])
                self.assertTrue(view["usage"]["accounting"]["complete"])
                self.assertEqual(33, view["usage"]["accounting"]["tokens"]["input_tokens"]["value"])
                self.assertEqual(10, result["time"]["wall_union_seconds"])
        self.assertEqual(original, state)

    def test_current_true_or_stale_false_change_only_acceptance_not_history_or_usage(self):
        state = self.state()
        fresh = run_view.view(state, completion_current=True)
        stale = run_view.view(state, completion_current=False)
        for view, expected in ((fresh, True), (stale, False)):
            result = view["efficiency"]
            self.assertTrue(view["done"])
            self.assertEqual(1, result["delivery"]["completed_tasks"])
            self.assertIs(result["delivery"]["current_completion"], expected)
            self.assertEqual(int(expected), result["delivery"]["verified_deliveries"])
            self.assertEqual(2 if expected else 0, result["criteria"]["accepted"])
            self.assertEqual([expected, expected], [row["accepted"] for row in result["criteria"]["rows"]])
            self.assertEqual(int(expected), result["unit_metrics"]["input_tokens"]["denominator"])
            self.assertEqual(33 if expected else None, result["unit_metrics"]["input_tokens"]["value"])
            self.assertEqual(0.3 if expected else None, result["unit_metrics"]["reported_usd"]["value"])
            self.assertEqual(10 if expected else None, result["unit_metrics"]["wall_seconds"]["value"])
        self.assertEqual(fresh["usage"], stale["usage"])
        self.assertEqual(fresh["efficiency"]["time"], stale["efficiency"]["time"])
        self.assertEqual(fresh["evidence"], stale["evidence"])

    def test_noncomplete_status_is_known_not_delivered_even_with_a_positive_hint(self):
        for status in ("RUNNING", "PASS", "PAUSED_BUDGET"):
            for current in (None, False, True):
                with self.subTest(status=status, current=current):
                    view = run_view.view(self.state(status=status), completion_current=current)
                    result = view["efficiency"]
                    self.assertFalse(view["done"])
                    self.assertIs(result["delivery"]["current_completion"], False)
                    self.assertEqual(0, result["delivery"]["verified_deliveries"])
                    self.assertEqual(0, result["criteria"]["accepted"])
                    self.assertTrue(all(row["accepted"] is False for row in result["criteria"]["rows"]))
                    self.assertIsNone(result["unit_metrics"]["input_tokens"]["value"])

    def test_current_generic_gate_does_not_supply_visual_authority(self):
        state = {**self.state(), **design_state()}
        for current, accepted in ((None, None), (False, 0), (True, None)):
            with self.subTest(current=current):
                result = efficiency.summary(state, completion_current=current)
                self.assertEqual(accepted, result["delivery"]["verified_deliveries"])
                self.assertIsNone(result["visual"]["accepted_frames"])
                self.assertIsNone(result["visual"]["accepted_states"])
                self.assertIsNone(result["unit_metrics"]["input_tokens"]["value"])
                self.assertIsNone(result["unit_metrics"]["per_accepted_state"]["reported_usd"]["value"])
                if current is False:
                    self.assertEqual(0, result["criteria"]["accepted"])
                    self.assertEqual([False, False], [row["accepted"] for row in result["criteria"]["rows"]])
                elif current is None:
                    self.assertEqual([None, None], [row["accepted"] for row in result["criteria"]["rows"]])
                else:
                    self.assertEqual([None, False], [row["accepted"] for row in result["criteria"]["rows"]])

    def test_aggregate_mixed_current_proof_retains_all_usage_but_not_a_partial_denominator(self):
        states = [self.state(f"/root{n}", identity=str(n)) for n in range(4)]
        states[3]["status"] = "RUNNING"
        views = [run_view.view(state, completion_current=current) for state, current in zip(states, (True, False, None, True), strict=False)]
        result = efficiency.aggregate(views)
        self.assertEqual(3, result["completed_tasks"])
        self.assertEqual({"verified": 1, "not_verified": 2, "unknown": 1}, result["current_completion_counts"])
        self.assertEqual(1, result["known_verified_deliveries"])
        self.assertIsNone(result["verified_deliveries"])
        self.assertIsNone(result["unit_metrics"]["input_tokens"]["value"])
        self.assertTrue(result["usage"]["complete"])
        self.assertEqual(132, result["usage"]["tokens"]["input_tokens"]["value"])
        self.assertEqual(10, result["time"]["wall_union_seconds"])
        for current, delivered in ((False, 1), (True, 2)):
            views[2] = run_view.view(states[2], completion_current=current)
            measured = efficiency.aggregate(views)
            self.assertEqual(3, measured["completed_tasks"])
            self.assertEqual(delivered, measured["verified_deliveries"])
            self.assertEqual(132 / delivered, measured["unit_metrics"]["input_tokens"]["value"])
            self.assertAlmostEqual(1.2 / delivered, measured["unit_metrics"]["reported_usd"]["value"])

    def test_aggregate_does_not_accept_an_old_positive_count_without_current_proof(self):
        view = run_view.view(self.state(), completion_current=True)
        del view["efficiency"]["delivery"]["current_completion"]
        result = efficiency.aggregate([view])
        self.assertEqual(1, result["completed_tasks"])
        self.assertEqual(0, result["known_verified_deliveries"])
        self.assertIsNone(result["verified_deliveries"])
        self.assertIsNone(result["unit_metrics"]["reported_usd"]["value"])
        self.assertTrue(result["usage"]["complete"])
        view["efficiency"]["delivery"]["current_completion"] = False
        stale = efficiency.aggregate([view])
        self.assertEqual(0, stale["verified_deliveries"])
        self.assertEqual(0, stale["unit_metrics"]["reported_usd"]["denominator"])
        self.assertIsNone(stale["unit_metrics"]["reported_usd"]["value"])

    def test_worker_completion_never_supplies_missing_root_acceptance_or_missing_usage(self):
        parent_state = {"run_dir": "/parent", "status": "TASK_COMPLETE", "orchestration_batch": {
            "workers": [{"run_dir": "/worker", "milestone_id": "M1", "status": "RUNNING"}]}}
        parent = run_view.view(parent_state)
        worker = run_view.view({**self.state("/worker", identity="worker"), "parent_run": "/parent"}, completion_current=True)
        other = run_view.view(self.state("/other", identity="other"), completion_current=True)
        result = efficiency.aggregate([parent, worker, other])
        self.assertEqual((2, 1), (result["completed_tasks"], result["worker_views"]))
        self.assertEqual(1, result["known_verified_deliveries"])
        self.assertIsNone(result["verified_deliveries"])
        self.assertIsNone(result["unit_metrics"]["input_tokens"]["value"])
        self.assertTrue(result["usage"]["complete"])
        self.assertEqual(66, result["usage"]["tokens"]["input_tokens"]["value"])
        checked_parent = run_view.view(parent_state, completion_current=True)
        missing_worker = efficiency.aggregate([checked_parent, other])
        self.assertEqual(2, missing_worker["verified_deliveries"])
        self.assertFalse(missing_worker["usage"]["complete"])
        self.assertIsNone(missing_worker["unit_metrics"]["input_tokens"]["value"])
        complete = efficiency.aggregate([checked_parent, worker, other])
        self.assertEqual(2, complete["verified_deliveries"])
        self.assertEqual(33, complete["unit_metrics"]["input_tokens"]["value"])


class InjectedVisualProjectionTests(unittest.TestCase):
    """Consumer-contract fixtures, not image inspection or acceptance authority."""
    def state(self, run="/visual", *, two_frames=False):
        state = design_state()
        if not two_frames:
            state["settings"]["design_manifest"]["body"]["cases"][1].update(node_id="1:1", state="hover")
            state["settings"]["design_manifest"]["body"]["files"][0]["nodes"] = ["1:1"]
        state.update(run_dir=run, created_at="1970-01-01T00:00:00+00:00",
                     acceptance_criteria=[{"id": cid} for cid in ("C1", "C2", "shared-visual", "functional")],
                     last_decision={"acceptance_criteria": [{"id": cid, "status": "verified"}
                         for cid in ("C1", "C2", "shared-visual", "functional")]})
        failed = attempt(identity=run.strip("/") + "-failed", start=0, end=6, exit_code=1, rejected=True)
        review = attempt("sol", run.strip("/") + "-review", start=8, end=20)
        request = review["metrics"]["request_context"]["accounting"]["requests"][0]
        request.update(started_at_ms=9000, finished_at_ms=19000)
        state["stages"] = [failed, review]
        return state

    def projection(self, state, verdicts=("PASS", "PASS")):
        return {"cases": [{"id": case["id"], "file_key": case["file_key"], "node_id": case["node_id"],
                           "criterion_ids": [f"C{index + 1}", "shared-visual"], "verdict": verdict,
                           "current_accepted": {"PASS": True, "FAIL": False, "NOT_VERIFIED": None}[verdict],
                           "accepted_at": "1970-01-01T00:00:20+00:00" if verdict == "PASS" else None,
                           "historical_accepted_at": "1970-01-01T00:00:10+00:00"}
                          for index, (case, verdict) in enumerate(zip(state["settings"]["design_manifest"]["body"]["cases"], verdicts, strict=False))],
                # These deliberately misleading precomputed values must be ignored.
                "current_all_accepted": True, "accepted_cases": 999, "accepted_frames": 999, "coverage_complete": True}

    def measure(self, state, projection, *, current=True):
        return efficiency.summary(state, now=40, completion_current=current, visual_acceptance=projection)

    def test_two_states_one_pass_one_fail_never_accept_the_frame_or_whole_delivery(self):
        state = self.state()
        result = self.measure(state, self.projection(state, ("PASS", "FAIL")))
        visual = result["visual"]
        self.assertEqual((1, 0), (visual["accepted_states"], visual["accepted_frames"]))
        self.assertEqual((2, 1), (visual["requested_states"], visual["requested_frames"]))
        self.assertTrue(visual["coverage_complete"])
        self.assertFalse(visual["current_all_accepted"])
        self.assertEqual(0, result["delivery"]["verified_deliveries"])
        self.assertEqual([True, False, False, True], [row["accepted"] for row in result["criteria"]["rows"]])
        self.assertEqual(66, result["unit_metrics"]["per_accepted_state"]["input_tokens"]["value"])
        self.assertAlmostEqual(0.6, result["unit_metrics"]["per_accepted_state"]["reported_usd"]["value"])
        self.assertIsNone(result["unit_metrics"]["per_accepted_frame"]["reported_usd"]["value"])
        self.assertEqual(["case2"], [row["id"] for row in visual["gaps"]])

    def test_all_states_pass_accept_one_frame_and_use_all_failed_and_successful_attempts(self):
        state = self.state()
        result = self.measure(state, self.projection(state))
        visual = result["visual"]
        self.assertEqual((2, 1), (visual["accepted_states"], visual["accepted_frames"]))
        self.assertEqual(1, result["delivery"]["verified_deliveries"])
        self.assertTrue(visual["current_all_accepted"])
        self.assertTrue(visual["projection_valid"])
        self.assertEqual([], visual["gaps"])
        self.assertEqual(4, result["criteria"]["accepted"])
        self.assertEqual(33, result["unit_metrics"]["per_accepted_state"]["input_tokens"]["value"])
        self.assertEqual(66, result["unit_metrics"]["per_accepted_frame"]["input_tokens"]["value"])
        self.assertAlmostEqual(0.3, result["unit_metrics"]["per_accepted_state"]["reported_usd"]["value"])

    def test_partial_unknown_coverage_exposes_subtotals_without_a_partial_unit_denominator(self):
        for two_frames in (False, True):
            with self.subTest(two_frames=two_frames):
                state = self.state(two_frames=two_frames)
                result = self.measure(state, self.projection(state, ("PASS", "NOT_VERIFIED")))
                visual = result["visual"]
                self.assertFalse(visual["coverage_complete"])
                self.assertEqual(1, visual["known_accepted_states"])
                self.assertEqual(int(two_frames), visual["known_accepted_frames"])
                self.assertIsNone(visual["accepted_states"])
                self.assertIsNone(visual["accepted_frames"])
                self.assertIsNone(result["delivery"]["verified_deliveries"])
                self.assertEqual([True, None, None, True], [row["accepted"] for row in result["criteria"]["rows"]])
                self.assertIsNone(result["unit_metrics"]["per_accepted_state"]["reported_usd"]["value"])
                self.assertIsNone(result["unit_metrics"]["per_accepted_frame"]["input_tokens"]["value"])

    def test_distinct_frames_are_derived_from_required_states_not_supplied_frame_count(self):
        state = self.state(two_frames=True)
        result = self.measure(state, self.projection(state, ("PASS", "FAIL")))
        self.assertEqual(1, result["visual"]["accepted_frames"])
        self.assertEqual([True, False], [row["accepted"] for row in result["visual"]["frames"]])
        self.assertEqual(66, result["unit_metrics"]["per_accepted_frame"]["input_tokens"]["value"])

    def test_invalid_projection_inventory_decisions_and_mappings_fail_closed(self):
        state = self.state()
        valid = self.projection(state)
        variants = [None, {**valid, "cases": valid["cases"][:1]}, {**valid, "cases": valid["cases"] * 2},
                    {**valid, "cases": [*valid["cases"], None]}]
        for change in ({"id": "foreign"}, {"file_key": "foreign"}, {"node_id": "wrong-frame"},
                       {"state": "wrong-state"}, {"viewport": {"width": 999}}, {"criterion_ids": ["unknown-cid"]},
                       {"criterion_ids": []}, {"current_accepted": 1}, {"current_accepted": False},
                       {"verdict": "UNKNOWN"}):
            variant = deepcopy(valid)
            variant["cases"][0].update(change)
            variants.append(variant)
        for index, projection in enumerate(variants):
            with self.subTest(index=index):
                # An empty mapping is malformed injection; None exercises the unchanged no-argument path separately.
                result = self.measure(state, {} if projection is None else projection)
                self.assertFalse(result["visual"]["projection_valid"])
                self.assertIsNone(result["visual"]["accepted_states"])
                self.assertIsNone(result["visual"]["accepted_frames"])
                self.assertIsNone(result["delivery"]["verified_deliveries"])
                self.assertTrue(all(row["accepted"] is None for row in result["visual"]["cases"]))
                self.assertIsNone(result["unit_metrics"]["input_tokens"]["value"])
                self.assertTrue(result["visual"]["issues"])
                self.assertTrue(result["criteria"]["rows"][-1]["accepted"])

    def test_invalid_approved_inventory_cannot_be_completed_by_projected_counts(self):
        state = self.state()
        projection = self.projection(state)
        state["settings"]["design_manifest"]["body"]["files"][0]["nodes"].append("uncovered-frame")
        result = self.measure(state, projection)
        self.assertFalse(result["visual"]["projection_valid"])
        self.assertIsNone(result["delivery"]["verified_deliveries"])
        self.assertTrue(any("inventory" in issue for issue in result["visual"]["issues"]))

    def test_raw_saved_projection_and_builder_pass_do_not_enter_the_trusted_seam(self):
        state = self.state()
        for row in state["validation"]["design_results"]:
            row.update(status="PASS", current_accepted=True, accepted_at="1970-01-01T00:00:20Z")
        state["visual_acceptance"] = self.projection(state)
        state["visual_acceptance_receipts"] = [self.projection(state)]
        state["current_visual_acceptance"] = True
        result = self.measure(state, None)
        self.assertIsNone(result["visual"]["projection_valid"])
        self.assertIsNone(result["visual"]["accepted_states"])
        self.assertIsNone(result["delivery"]["verified_deliveries"])
        self.assertIsNone(result["visual"]["first_independently_accepted_screen"]["at"])
        self.assertTrue(all(row["accepted"] is None for row in result["visual"]["cases"]))

    def test_current_invalidation_preserves_supplied_history_without_reusing_it_as_acceptance(self):
        state = self.state()
        current = self.projection(state)
        original = deepcopy(current)
        before = self.measure(state, current)
        invalidated = self.projection(state, ("NOT_VERIFIED", "NOT_VERIFIED"))
        state["validation"]["source_revision"] = "changed-source"
        after = self.measure(state, invalidated)
        self.assertEqual(2, before["visual"]["accepted_states"])
        self.assertIsNone(after["visual"]["accepted_states"])
        self.assertIsNone(after["delivery"]["verified_deliveries"])
        self.assertEqual(0, after["visual"]["known_accepted_states"])
        for prior, row in zip(before["visual"]["cases"], after["visual"]["cases"], strict=False):
            self.assertEqual(prior["key"], row["key"])
            self.assertEqual(prior["first_accepted_at"], row["first_accepted_at"])
            self.assertIsNone(row["current_accepted_at"])
        first = after["visual"]["first_independently_accepted_screen"]
        self.assertIsNone(first["at"])
        self.assertEqual("1970-01-01T00:00:10+00:00", first["historical_at"])
        self.assertEqual(10, first["historical_elapsed_seconds"])
        self.assertIsNone(first["usage"])
        self.assertIsNone(self.measure(state, None)["delivery"]["verified_deliveries"])
        self.assertEqual(original, current)

    def test_first_current_time_and_native_cutoff_are_separate_from_history_and_later_cost(self):
        state = self.state()
        later = attempt("astra_resolve", "later-failed", start=25, end=30, exit_code=1)
        later["metrics"]["request_context"]["accounting"]["requests"][0].update(started_at_ms=26000, finished_at_ms=29000)
        state["stages"].append(later)
        result = self.measure(state, self.projection(state))
        first = result["visual"]["first_independently_accepted_screen"]
        self.assertEqual("1970-01-01T00:00:20+00:00", first["at"])
        self.assertEqual(20, first["elapsed_seconds"])
        self.assertEqual(10, first["historical_elapsed_seconds"])
        self.assertEqual(66, first["usage"]["tokens"]["input_tokens"]["value"])
        self.assertEqual(2, first["usage"]["provider_requests"]["value"])
        self.assertAlmostEqual(0.6, first["usage"]["reported_usd"]["value"])
        self.assertEqual(49.5, result["unit_metrics"]["per_accepted_state"]["input_tokens"]["value"])

    def test_incomplete_timestamps_do_not_invent_first_acceptance_time_or_usage(self):
        for missing in ("accepted_at", "naive_time", "created_at", "negative_elapsed"):
            with self.subTest(missing=missing):
                state = self.state()
                projection = self.projection(state)
                if missing == "accepted_at":
                    projection["cases"][0]["accepted_at"] = None
                elif missing == "naive_time":
                    projection["cases"][0]["accepted_at"] = "1970-01-01T00:00:20"
                elif missing == "created_at":
                    state.pop("created_at")
                else:
                    state["created_at"] = "1970-01-01T00:00:30+00:00"
                result = self.measure(state, projection)
                self.assertEqual(2, result["visual"]["accepted_states"])
                first = result["visual"]["first_independently_accepted_screen"]
                self.assertIsNone(first["elapsed_seconds"])
                self.assertIsNone(first["usage"])

    def test_inflight_request_or_missing_native_finish_time_keeps_first_usage_unknown(self):
        for change in ({"started_at_ms": 15000, "finished_at_ms": 25000}, {"finished_at_ms": None}):
            with self.subTest(change=change):
                state = self.state()
                state["stages"][1]["metrics"]["request_context"]["accounting"]["requests"][0].update(change)
                result = self.measure(state, self.projection(state))
                self.assertEqual(20, result["visual"]["first_independently_accepted_screen"]["elapsed_seconds"])
                self.assertIsNone(result["visual"]["first_independently_accepted_screen"]["usage"])
                self.assertEqual(33, result["unit_metrics"]["per_accepted_state"]["input_tokens"]["value"])

    def test_unfinished_or_absent_usage_does_not_make_accepted_screens_free(self):
        for missing in ("active", "absent"):
            with self.subTest(missing=missing):
                state = self.state()
                if missing == "active":
                    state["active_stage"] = attempt("astra_resolve", "unfinished", start=25, end=30)
                else:
                    state["stages"] = []
                result = self.measure(state, self.projection(state))
                self.assertEqual(2, result["visual"]["accepted_states"])
                self.assertIsNone(result["unit_metrics"]["per_accepted_state"]["input_tokens"]["value"])
                self.assertIsNone(result["unit_metrics"]["per_accepted_frame"]["reported_usd"]["value"])
                self.assertIsNone(result["visual"]["first_independently_accepted_screen"]["usage"])
                combined = efficiency.aggregate([run_view.view(state, completion_current=True,
                                                                 visual_acceptance=self.projection(state))])
                self.assertEqual(1, combined["verified_deliveries"])
                self.assertIsNone(combined["unit_metrics"]["reported_usd"]["value"])
                self.assertIsNone(combined["unit_metrics"]["input_tokens"]["value"])
                self.assertFalse(combined["usage"]["complete"])

    def test_current_completion_and_zero_denominators_remain_independent_guards(self):
        state = self.state()
        for current in (False, None):
            result = self.measure(state, self.projection(state), current=current)
            self.assertEqual(2, result["visual"]["accepted_states"])
            self.assertEqual(0 if current is False else None, result["delivery"]["verified_deliveries"])
            self.assertIsNone(result["unit_metrics"]["per_accepted_state"]["input_tokens"]["value"])
            self.assertEqual([current] * 4, [row["accepted"] for row in result["criteria"]["rows"]])
        failed = self.measure(state, self.projection(state, ("FAIL", "FAIL")))
        self.assertEqual(0, failed["visual"]["accepted_states"])
        self.assertEqual(0, failed["delivery"]["verified_deliveries"])
        self.assertIsNone(failed["unit_metrics"]["reported_usd"]["value"])

    def test_view_injection_is_detached_and_aggregate_requires_both_current_authorities(self):
        state = self.state()
        projection = self.projection(state)
        original_state, original_projection = deepcopy(state), deepcopy(projection)
        valid = run_view.view(state, completion_current=True, visual_acceptance=projection)
        baseline_keys = set(run_view.view(state))
        self.assertEqual(baseline_keys, set(valid))
        self.assertEqual(1, efficiency.aggregate([valid])["verified_deliveries"])
        without_visual = run_view.view(self.state("/missing-visual"), completion_current=True)
        self.assertIsNone(efficiency.aggregate([valid, without_visual])["verified_deliveries"])
        without_current = run_view.view(self.state("/missing-current"), visual_acceptance=projection)
        self.assertIsNone(efficiency.aggregate([valid, without_current])["verified_deliveries"])
        # Old/incomplete public metrics cannot create acceptance from a positive precomputed count.
        without_visual["efficiency"]["delivery"]["verified_deliveries"] = 1
        self.assertIsNone(efficiency.aggregate([without_visual])["verified_deliveries"])
        valid["efficiency"]["visual"]["cases"][0]["criterion_ids"].append("changed")
        self.assertEqual(original_state, state)
        self.assertEqual(original_projection, projection)


class TimingAndProvenanceTests(unittest.TestCase):
    def replay(self, action):
        schedule = {"action": action, "reason": "new_obligation" if action == "execute" else "same_obligation_complete_proof",
                    "receipt": "/owned/receipt.json", "receipt_sha256": "sha", "identity": "obligation",
                    "attempt_id": "runner1", "started_at": 20, "finished_at": 25}
        if action == "reuse":
            schedule.update(started_at=30, finished_at=31, original_started_at=20, original_finished_at=25,
                            original_reason="new_obligation")
        return {"checks": [{"command": "tests", "exit_code": 0, "timed_out": False,
                            "results": {"passed": ["T1"]}, "scheduling": schedule}]}

    def test_replay_observer_imports_original_crash_execution_once_without_reusing_elapsed_time(self):
        uninterrupted, restarted = {}, {}
        efficiency.observe_replay(uninterrupted, self.replay("execute"), attempt_id="validator-events")
        efficiency.observe_replay(uninterrupted, self.replay("reuse"), attempt_id="validator-events")
        efficiency.observe_replay(restarted, self.replay("reuse"), attempt_id="validator-events")
        efficiency.observe_replay(restarted, self.replay("reuse"), attempt_id="validator-events")
        self.assertEqual(uninterrupted, restarted)
        measured = efficiency.summary(restarted)
        self.assertEqual(5, measured["time"]["summed_execution_seconds"])
        self.assertEqual(5, measured["time"]["wall_union_seconds"])
        self.assertEqual(1, measured["repeats"]["reused_observed"])
        self.assertEqual(0, measured["repeats"]["redundant_checks_observed"])
        self.assertEqual(5, measured["by_category"]["functional_proof"]["summed_execution_seconds"])

    def test_waiting_is_only_recorded_when_observed_not_inferred_from_stage_gaps(self):
        state = {"stages": [attempt(start=0, end=10), attempt(identity="two", start=20, end=30)]}
        before = efficiency.summary(state)
        self.assertIn("waiting", before["attribution"]["uninstrumented_categories"])
        efficiency.record_observation(state, event_id="operator-wait", kind="activity", category="waiting",
                                      started_at=10, finished_at=20, provenance={"reason": "user approval"})
        after = efficiency.summary(state)
        self.assertEqual(30, after["time"]["wall_union_seconds"])
        self.assertEqual(20, after["time"]["summed_execution_seconds"])
        self.assertEqual(10, after["time"]["exclusive_wall_seconds_by_category"]["waiting"])

    def test_missing_original_replay_interval_is_unknown_not_zero(self):
        state, replay = {}, self.replay("reuse")
        del replay["checks"][0]["scheduling"]["original_started_at"]
        efficiency.observe_replay(state, replay, attempt_id="v")
        self.assertIsNone(efficiency.summary(state)["time"]["wall_union_seconds"])

    def test_parallel_union_and_summed_durations_are_distinct_without_double_counting(self):
        result = efficiency.summary({"stages": [attempt(start=0, end=10), attempt("sol", "two", start=5, end=15)]})
        timing = result["time"]
        self.assertEqual(15, timing["wall_union_seconds"])
        self.assertEqual(20, timing["summed_execution_seconds"])
        self.assertEqual(15, sum(timing["exclusive_wall_seconds_by_category"].values()))
        self.assertEqual(5, timing["exclusive_wall_seconds_by_category"]["parallel_overlap"])
        self.assertEqual(4, timing["provider_request_union_seconds"])

    def test_active_elapsed_uses_fake_clock_without_waiting_or_finished_estimates(self):
        record = attempt()
        record.pop("finished_at")
        record.pop("duration_seconds")
        result = efficiency.summary({"active_stage": record}, now=12)
        self.assertEqual(12, result["time"]["wall_union_seconds"])
        self.assertEqual(12, result["time"]["summed_execution_seconds"])
        self.assertIsNone(result["time"]["provider_request_union_seconds"])

    def test_missing_intervals_do_not_turn_summed_parallel_durations_into_wall_time(self):
        record = attempt()
        record.pop("started_at")
        record.pop("finished_at")
        result = efficiency.summary({"stages": [record]})
        self.assertIsNone(result["time"]["wall_union_seconds"])
        self.assertEqual(10, result["time"]["summed_execution_seconds"])
        self.assertEqual(1, result["time"]["missing_intervals"])

    def test_observations_idempotent_conflicts_visible_and_decisions_not_timed_as_work(self):
        state = {}
        args = dict(event_id="event1", kind="reuse", category="functional_proof", reason="duplicate_no_new_information",
                    provenance={"receipt": "r"}, started_at=0, finished_at=100)
        self.assertTrue(efficiency.record_observation(state, **args))
        self.assertTrue(efficiency.record_observation(state, **args))
        result = efficiency.summary(state)
        self.assertEqual(1, result["repeats"]["reused_observed"])
        self.assertEqual(0, result["time"]["wall_union_seconds"])
        with self.assertWarns(RuntimeWarning):
            self.assertFalse(efficiency.record_observation(state, **{**args, "kind": "repeat"}))
        self.assertEqual(args["kind"], state["efficiency_observations"][0]["kind"])
        self.assertTrue(efficiency.summary(state)["issues"])

    def test_every_repeat_reason_is_kept_and_independent_checks_are_not_waste(self):
        state = {}
        for reason in efficiency.REASONS:
            efficiency.record_observation(state, event_id=reason, kind="repeat", category="functional_proof",
                                          reason=reason, provenance={"source": "pinned"})
        efficiency.record_observation(state, event_id="suppressed", kind="suppressed", category="diagnosis")
        result = efficiency.summary(state)
        self.assertEqual(6, result["repeats"]["observed"])
        self.assertEqual(dict.fromkeys(efficiency.REASONS, 1), result["repeats"]["by_reason"])
        self.assertEqual(1, result["repeats"]["redundant_checks_observed"])
        self.assertEqual(1, result["repeats"]["suppressed_observed"])

    def test_stage_categories_and_visual_observation_usage_are_disjoint(self):
        names = ["astra_discovery", "terra", "sol", "task_preflight", "astra_resolve", "sol_report_repair"]
        state = {"stages": [attempt(name, str(n), start=n * 10, end=(n + 1) * 10) for n, name in enumerate(names)]}
        efficiency.record_observation(state, event_id="inspection", kind="activity", category="visual_capture_review",
                                      attempt_id="001/2", provenance={"actual_activity": "image review, not acceptance"})
        result = efficiency.summary(state)
        self.assertEqual(1, result["by_category"]["visual_capture_review"]["attempts"])
        self.assertEqual(0, result["by_category"]["functional_proof"]["attempts"])
        self.assertEqual(198, sum(row["tokens"]["input_tokens"]["known"] for row in result["by_category"].values()))

    def test_repair_attempts_join_finding_assignments_not_duplicate_observations(self):
        state = {"stages": [attempt(identity="repair1"), attempt(identity="repair2")],
                 "findings_ledger": [{"id": "F1", "status": "open", "assigned_history": [{"task_id": "T1"}, {"task_id": "T1"}]}]}
        efficiency.record_observation(state, event_id="repair", kind="repeat", category="build", attempt_id="001/repair1", finding_ids=["F1"])
        self.assertEqual(2, efficiency.summary(state)["repair_attempts_per_finding"][0]["repair_attempts"])

    def test_public_view_is_additive_and_deeply_detached_from_state(self):
        state = {"status": "RUNNING", "stages": [attempt(launch_route={"model": "m"})]}
        original = deepcopy(state)
        view = run_view.view(state)
        view["usage"]["accounting"]["attempts"][0]["launch_route"]["model"] = "changed"
        view["usage"]["accounting"]["attempts"][0]["requests"][0]["tokens"]["input_tokens"] = 999
        self.assertEqual(original, state)
        self.assertIn("cost_usd", view["usage"])
        self.assertIn("efficiency", view)

    def test_public_replay_projection_retains_nested_receipts_without_exposing_mutable_state(self):
        replay = self.replay("reuse")
        replay.update(verdict="PASS", scheduling={"reused_count": 1})
        state = {"validation": {"check_replay": replay}}
        projected = run_view.view(state)["evidence"]["check_replay"]
        self.assertEqual("reuse", projected["checks"][0]["scheduling"]["action"])
        self.assertEqual({"passed": ["T1"]}, projected["checks"][0]["results"])
        self.assertEqual(1, projected["scheduling"]["reused_count"])
        projected["checks"][0]["scheduling"]["receipt_sha256"] = "tampered"
        self.assertEqual("sha", replay["checks"][0]["scheduling"]["receipt_sha256"])


class ObservationConflictTests(unittest.TestCase):
    def record(self, state, **changes):
        return efficiency.record_observation(state, **{
            "event_id": "runner-proof", "kind": "activity", "category": "functional_proof",
            "started_at": 0, "finished_at": 10, **changes})

    def test_exact_observation_replay_keeps_measured_time_and_one_immutable_record(self):
        state = {"status": "TASK_COMPLETE"}
        self.assertTrue(self.record(state))
        original = deepcopy(state["efficiency_observations"][0])
        self.assertTrue(self.record(state))
        self.assertEqual([original], state["efficiency_observations"])
        result = efficiency.summary(state, completion_current=True)
        self.assertEqual(10, result["time"]["wall_union_seconds"])
        self.assertEqual(10, result["time"]["summed_execution_seconds"])
        self.assertEqual(10, result["unit_metrics"]["wall_seconds"]["value"])
        self.assertEqual(0, result["time"]["missing_intervals"])

    def test_activity_start_end_category_or_kind_conflict_makes_affected_time_unknown(self):
        for changes in ({"started_at": 1}, {"finished_at": 20}, {"category": "diagnosis"},
                        {"kind": "repeat"}, {"kind": "reuse"}):
            with self.subTest(changes=changes):
                state = {"status": "TASK_COMPLETE"}
                self.record(state)
                with self.assertWarns(RuntimeWarning):
                    self.assertFalse(self.record(state, **changes))
                result = efficiency.summary(state, completion_current=True)
                self.assertIsNone(result["time"]["wall_union_seconds"])
                self.assertIsNone(result["time"]["summed_execution_seconds"])
                self.assertEqual(1, result["time"]["missing_intervals"])
                self.assertEqual(0, result["time"]["known_summed_execution_seconds"])
                self.assertIsNone(result["by_category"]["functional_proof"]["summed_execution_seconds"])
                if changes.get("category"):
                    self.assertIsNone(result["by_category"][changes["category"]]["summed_execution_seconds"])
                self.assertEqual(1, result["delivery"]["verified_deliveries"])
                self.assertEqual(1, result["unit_metrics"]["wall_seconds"]["denominator"])
                self.assertIsNone(result["unit_metrics"]["wall_seconds"]["value"])
                self.assertEqual("Timing coverage is incomplete", result["unit_metrics"]["wall_seconds"]["reason"])
                self.assertTrue(any("Conflicting observation" in issue for issue in result["issues"]))

    def test_known_other_intervals_and_native_usage_remain_independently_known(self):
        state = {"status": "TASK_COMPLETE", "stages": [attempt(start=30, end=40)]}
        self.record(state)
        with self.assertWarns(RuntimeWarning):
            self.record(state, finished_at=20)
        self.record(state, event_id="known-setup", category="deterministic_setup", started_at=50, finished_at=55)
        view = run_view.view(state, completion_current=True)
        result, measured = view["efficiency"], view["usage"]["accounting"]
        self.assertEqual(15, result["time"]["observed_wall_union_seconds"])
        self.assertEqual(15, result["time"]["known_summed_execution_seconds"])
        self.assertIsNone(result["time"]["wall_union_seconds"])
        self.assertIsNone(result["time"]["summed_execution_seconds"])
        self.assertEqual(10, result["by_category"]["build"]["summed_execution_seconds"])
        self.assertEqual(5, result["by_category"]["deterministic_setup"]["summed_execution_seconds"])
        self.assertIsNone(result["by_category"]["functional_proof"]["summed_execution_seconds"])
        self.assertEqual(1, result["by_category"]["functional_proof"]["conflicted_activity_observations"])
        self.assertTrue(all(value is None for value in result["time"]["exclusive_wall_seconds_by_category"].values()))
        self.assertEqual(15, sum(result["time"]["observed_exclusive_wall_seconds_by_category"].values()))
        self.assertEqual(4, result["time"]["provider_request_union_seconds"])
        self.assertTrue(measured["complete"])
        self.assertEqual(33, measured["tokens"]["input_tokens"]["value"])
        self.assertEqual(0.3, measured["cost"]["reported_usd"]["value"])
        self.assertEqual(33, result["unit_metrics"]["input_tokens"]["value"])
        self.assertEqual(0, result["by_category"]["functional_proof"]["tokens"]["input_tokens"]["value"])
        self.assertEqual(0, result["by_category"]["functional_proof"]["reported_usd"]["value"])

    def test_reuse_suppression_only_conflict_is_not_executed_time_but_later_activity_is_unknown(self):
        state = {"status": "TASK_COMPLETE"}
        self.record(state, kind="reuse")
        with self.assertWarns(RuntimeWarning):
            self.record(state, kind="suppressed", category="diagnosis", finished_at=20)
        result = efficiency.summary(state, completion_current=True)
        self.assertEqual(0, result["time"]["wall_union_seconds"])
        self.assertEqual(0, result["time"]["summed_execution_seconds"])
        self.assertEqual(0, result["time"]["missing_intervals"])
        self.assertEqual(0, result["unit_metrics"]["wall_seconds"]["value"])
        self.assertFalse(result["observations"][0]["execution_possible"])
        self.assertTrue(result["issues"])
        with self.assertWarns(RuntimeWarning):
            self.record(state, category="build", finished_at=30)
        changed = efficiency.summary(state, completion_current=True)
        self.assertIsNone(changed["time"]["wall_union_seconds"])
        self.assertIsNone(changed["time"]["summed_execution_seconds"])
        self.assertIsNone(changed["by_category"]["build"]["summed_execution_seconds"])
        self.assertEqual(0, changed["by_category"]["diagnosis"]["summed_execution_seconds"])

    def test_conflicts_retain_all_alternatives_and_cannot_be_cleared_by_exact_replay(self):
        state = {"status": "TASK_COMPLETE"}
        self.record(state)
        original = deepcopy(state["efficiency_observations"][0])
        for changes in ({"finished_at": 20}, {"finished_at": 20}, {"category": "diagnosis"}):
            with self.assertWarns(RuntimeWarning):
                self.assertFalse(self.record(state, **changes))
        self.assertTrue(self.record(state))
        self.assertEqual(original, state["efficiency_observations"][0])
        self.assertEqual(3, len(state["efficiency_observations"]))
        result = efficiency.summary(state, completion_current=True)
        self.assertIsNone(result["time"]["wall_union_seconds"])
        self.assertEqual(1, result["time"]["missing_intervals"])
        self.assertEqual(3, len(result["observations"][0]["candidates"]))
        self.assertEqual(["diagnosis", "functional_proof"], result["observations"][0]["affected_categories"])
        result["observations"][0]["candidates"][0]["finished_at"] = 999
        self.assertEqual(original, state["efficiency_observations"][0])

    def test_legacy_marker_missing_conflicting_kind_cannot_claim_no_execution(self):
        state = {"status": "TASK_COMPLETE"}
        self.record(state, kind="reuse")
        state["efficiency_observations"].append({"event_id": "runner-proof", "kind": "conflict",
                                                "reason": "Conflicting observation replay"})
        result = efficiency.summary(state, completion_current=True)
        self.assertIsNone(result["time"]["wall_union_seconds"])
        self.assertIsNone(result["time"]["summed_execution_seconds"])
        self.assertTrue(all(row["summed_execution_seconds"] is None for row in result["by_category"].values()))
        self.assertEqual(1, result["time"]["missing_intervals"])

    def test_public_aggregate_preserves_conflict_across_root_outcomes_and_worker_coverage(self):
        for status in ("TASK_COMPLETE", "PAUSED_BUDGET"):
            with self.subTest(status=status):
                state = {"run_dir": "/conflicted", "status": status}
                self.record(state)
                with self.assertWarns(RuntimeWarning):
                    self.record(state, finished_at=20)
                known = run_view.view({"run_dir": "/known", "status": "TASK_COMPLETE", "stages": [attempt(start=30, end=40)]},
                                      completion_current=True)
                combined = efficiency.aggregate([run_view.view(state, completion_current=True), known])
                expected_deliveries = 2 if status == "TASK_COMPLETE" else 1
                self.assertEqual(expected_deliveries, combined["verified_deliveries"])
                self.assertEqual(expected_deliveries, combined["unit_metrics"]["wall_seconds"]["denominator"])
                self.assertIsNone(combined["unit_metrics"]["wall_seconds"]["value"])
                self.assertIsNone(combined["time"]["wall_union_seconds"])
                self.assertIsNone(combined["time"]["summed_execution_seconds"])
                self.assertEqual(10, combined["time"]["observed_wall_union_seconds"])
                self.assertTrue(combined["usage"]["complete"])
                self.assertIsNone(combined["by_category"]["functional_proof"]["summed_execution_seconds"])
                self.assertTrue(any("Conflicting observation" in issue for issue in combined["issues"]))
                state["orchestration_batch"] = {"workers": [{"run_dir": "/worker", "milestone_id": "M1", "status": "RUNNING"}]}
                parent = run_view.view(state, completion_current=True)
                incomplete = efficiency.aggregate([parent, known])
                self.assertIsNone(incomplete["time"]["wall_union_seconds"])
                self.assertFalse(incomplete["usage"]["complete"])
                worker = run_view.view({"run_dir": "/worker", "parent_run": "/conflicted", "status": "TASK_COMPLETE",
                                        "stages": [attempt(identity="worker", start=40, end=50)]}, completion_current=True)
                resolved = efficiency.aggregate([parent, known, worker])
                self.assertEqual(expected_deliveries, resolved["verified_deliveries"])
                self.assertEqual(66, resolved["usage"]["tokens"]["input_tokens"]["value"])
                self.assertTrue(resolved["usage"]["complete"])
                self.assertIsNone(resolved["time"]["wall_union_seconds"])
                self.assertIsNone(resolved["unit_metrics"]["wall_seconds"]["value"])
                self.assertEqual(20, resolved["time"]["known_summed_execution_seconds"])

    def test_replay_observer_conflicting_original_time_cannot_be_laundered_by_reuse(self):
        state = {"status": "TASK_COMPLETE"}
        replay = {"checks": [{"command": "tests", "exit_code": 0, "scheduling": {
            "action": "reuse", "reason": "same_obligation_complete_proof", "original_reason": "new_obligation",
            "receipt": "/receipt", "receipt_sha256": "sha", "identity": "proof", "attempt_id": "runner",
            "started_at": 30, "finished_at": 31, "original_started_at": 0, "original_finished_at": 10}}]}
        efficiency.observe_replay(state, replay, attempt_id="validator")
        altered = deepcopy(replay)
        altered["checks"][0]["scheduling"]["original_finished_at"] = 20
        with self.assertWarns(RuntimeWarning):
            efficiency.observe_replay(state, altered, attempt_id="validator")
        efficiency.observe_replay(state, replay, attempt_id="validator")
        result = efficiency.summary(state, completion_current=True)
        self.assertIsNone(result["time"]["wall_union_seconds"])
        self.assertIsNone(result["time"]["summed_execution_seconds"])
        self.assertEqual(0, result["time"]["known_summed_execution_seconds"])
        self.assertEqual(1, result["repeats"]["reused_observed"])
        self.assertEqual(1, result["time"]["missing_intervals"])


class PublicAggregationTests(unittest.TestCase):
    def test_legacy_public_view_missing_accounting_is_unknown_not_free(self):
        legacy = {"status": "TASK_COMPLETE", "done": True, "usage": {
            "stages": 3, "tokens": {"input_tokens": 1000}, "cost_usd": {"reported": 0.5}}}
        result = efficiency.aggregate([legacy])
        self.assertFalse(result["usage"]["complete"])
        self.assertIsNone(result["usage"]["tokens"]["input_tokens"]["value"])
        self.assertIsNone(result["usage"]["provider_requests"]["value"])
        self.assertIsNone(result["usage"]["cost"]["reported_usd"]["value"])
        self.assertTrue(result["missing_accounting_views"])

    def test_parent_import_and_worker_attempt_are_charged_once(self):
        record = attempt()
        parent = run_view.view({"run_dir": "/parent", "status": "TASK_COMPLETE", "stages": [{**record, "worker_attempt": "b:M:1"}]},
                               completion_current=True)
        worker = run_view.view({"run_dir": "/worker", "parent_run": "/parent", "status": "TASK_COMPLETE", "stages": [record]},
                               completion_current=True)
        result = efficiency.aggregate([parent, worker, parent])
        self.assertEqual((1, 1, 1), (result["runs"], result["worker_views"], result["completed_tasks"]))
        self.assertEqual(1, result["usage"]["attempt_count"])
        self.assertEqual(33, result["usage"]["tokens"]["input_tokens"]["value"])

    def test_final_import_supersedes_active_snapshot_without_double_charge(self):
        record = attempt()
        parent = run_view.view({"run_dir": "/parent", "status": "RUNNING", "stages": [record]})
        worker = run_view.view({"run_dir": "/worker", "parent_run": "/parent", "status": "RUNNING", "active_stage": record})
        result = efficiency.aggregate([worker, parent])
        self.assertEqual(1, result["usage"]["attempt_count"])
        self.assertEqual(0, result["usage"]["active_attempts"])
        self.assertEqual(33, result["usage"]["tokens"]["input_tokens"]["value"])

    def test_parent_alone_does_not_claim_active_worker_usage_is_complete(self):
        result = usage.accounting({"stages": [attempt()], "orchestration_batch": {
            "workers": [{"milestone_id": "M1", "status": "RUNNING"}]}})
        self.assertEqual(["M1"], result["parallel_workers_pending"])
        self.assertIsNone(result["tokens"]["input_tokens"]["value"])
        self.assertFalse(result["complete"])

    def test_aggregate_cannot_restore_false_completeness_by_dropping_worker_warning(self):
        parent = run_view.view({"run_dir": "/parent", "stages": [attempt()], "orchestration_batch": {
            "workers": [{"milestone_id": "M1", "run_dir": "/worker", "status": "RUNNING"}]}})
        result = efficiency.aggregate([parent])
        self.assertFalse(result["usage"]["complete"])
        self.assertIsNone(result["usage"]["tokens"]["input_tokens"]["value"])
        self.assertIsNone(result["usage"]["cost"]["reported_usd"]["value"])
        worker = run_view.view({"run_dir": "/worker", "parent_run": "/parent", "stages": [attempt(identity="two")]})
        complete = efficiency.aggregate([parent, worker])
        self.assertEqual(66, complete["usage"]["tokens"]["input_tokens"]["value"])
        self.assertEqual([], complete["missing_worker_views"])

    def test_all_failed_runs_remain_in_campaign_cost_and_outcome_denominator(self):
        views = [run_view.view({"run_dir": f"/run{n}", "status": status, "stages": [attempt(identity=str(n))]}, completion_current=True)
                 for n, status in enumerate(("TASK_COMPLETE", "PAUSED_BUDGET", "PAUSED_NO_PROGRESS"))]
        result = efficiency.aggregate(views)
        self.assertEqual(3, result["runs"])
        self.assertEqual(1, result["completed_tasks"])
        self.assertEqual(99, result["usage"]["tokens"]["input_tokens"]["value"])
        self.assertAlmostEqual(0.9, result["usage"]["cost"]["reported_usd"]["value"])
        self.assertEqual(99, result["unit_metrics"]["input_tokens"]["value"])

    def test_conflicting_public_snapshots_require_an_explicit_latest_snapshot(self):
        first = run_view.view({"run_dir": "/run", "status": "RUNNING"})
        second = run_view.view({"run_dir": "/run", "status": "TASK_COMPLETE"})
        with self.assertRaisesRegex(ValueError, "latest view"):
            efficiency.aggregate([first, second])


if __name__ == "__main__":
    unittest.main()
