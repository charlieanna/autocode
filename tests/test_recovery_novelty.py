"""Causal novelty policy and authenticated evidence packets, without live models."""
import base64
import copy
import dataclasses
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import autocode_recovery_novelty as novelty
import autocode_recovery_inputs as inputs
import autocode_resolver_recovery as recovery
import autocode_rework_policy as rework
import autocode_util as util
import autocode as runner
import autocode_builder_recovery as builder_recovery


class NoveltyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.incident = novelty.Incident("python -m unittest test_app", "AssertionError: 2 != 3", "C1", "M1")
        self.prior = [{"incident_ids": [self.incident.id], "action": "repair", "change_id": "old"}]

    def test_instruction_keeps_the_prohibition_in_one_sentence(self):
        # The "Never ..." clause must name its objects together, not be split by
        # the source-novelty note (#417).
        self.assertIn(
            "Never weaken tests, change model pins, permissions, limits or the approved contract;",
            novelty.INSTRUCTION)
        self.assertLess(novelty.INSTRUCTION.index("permissions, limits or the approved contract"),
                        novelty.INSTRUCTION.index("Automatic source novelty"))

    def test_only_proven_wrappers_are_removed(self):
        a = novelty.normalize("/tmp/owned-a/tests/a.py: failure at customer_deadbeef123456", [("/tmp/owned-a", "<workspace>")])
        b = novelty.normalize("/tmp/owned-b/tests/a.py: failure at customer_deadbeef123456", [("/tmp/owned-b", "<workspace>")])
        self.assertEqual(a, b)
        self.assertNotEqual(a, b.replace("a.py", "b.py"))
        self.assertIn("customer_deadbeef123456", a)
        self.assertEqual("/tmp/other/tests/a.py", novelty.normalize("/tmp/other/tests/a.py", [("/tmp/owned-a", "<workspace>")]))
        self.assertEqual("/tmp/owned-ab/test", novelty.normalize("/tmp/owned-ab/test", [("/tmp/owned-a", "<workspace>")]))

    def test_distinct_causes_tests_invariants_and_tasks_do_not_share_identity(self):
        original = asdict(self.incident)
        for key in ("operation", "failure", "invariant", "affected_task"):
            with self.subTest(key=key):
                self.assertNotEqual(self.incident.id, novelty.Incident(**{**original, key: original[key] + " different"}).id)
        self.assertEqual(self.incident.id, novelty.Incident(**{**original, "cause": "implementation"}).id)

    def test_restart_new_session_and_generic_retry_do_not_buy_another_call(self):
        for _ in range(8):
            self.assertEqual("hold", novelty.decide(self.incident, self.prior,
                action="diagnosis", unresolved_question="Try again in a fresh session").action)
        self.assertEqual("hold", novelty.decide(self.incident, self.prior, action="repair", change_id="old",
                                                expected_check=self.incident.operation).action)

    def test_first_unclear_failure_gets_only_a_specific_question(self):
        self.assertEqual("hold", novelty.decide(self.incident, [], action="diagnosis").action)
        self.assertEqual("diagnosis", novelty.decide(self.incident, [], action="diagnosis",
                         unresolved_question="Which app branch produces 2 rather than the required 3?").action)

    def test_known_cause_permission_and_product_need_no_classifier_call(self):
        self.assertEqual("hold", novelty.decide(self.incident, [], action="diagnosis",
                         known_correction=True, unresolved_question="Why?").action)
        for cause in ("permission", "product"):
            incident = novelty.Incident(**{**asdict(self.incident), "cause": cause})
            self.assertEqual("request", novelty.decide(incident, [], action="repair", explicit_grant="unrelated").action)

    def test_healthy_and_uncertain_workers_are_held_without_mutation(self):
        for workers in ("healthy", "uncertain"):
            self.assertEqual("hold", novelty.decide(self.incident, [], action="repair", workers=workers).action)

    def test_attested_change_and_discriminating_check_are_both_required(self):
        for fields, expected in (({"change_id": "new"}, "hold"),
                                 ({"expected_check": self.incident.operation}, "hold"),
                                 ({"change_id": "new", "expected_check": self.incident.operation}, "repair"),
                                 ({"changed_input": "runtime-proof", "expected_check": self.incident.operation}, "repair")):
            self.assertEqual(expected, novelty.decide(self.incident, self.prior, action="repair", **fields).action)

    def test_exact_explicit_retry_is_one_use_and_does_not_clear_history(self):
        before = copy.deepcopy(self.prior)
        self.assertEqual("repair", novelty.decide(self.incident, self.prior, action="repair", explicit_grant="g1").action)
        self.assertEqual(before, self.prior)
        self.prior.append({"incident_ids": [self.incident.id], "grant_id": "g1"})
        self.assertEqual("hold", novelty.decide(self.incident, self.prior, action="repair", explicit_grant="g1").action)

    def test_source_change_identity_rejects_cosmetic_unscoped_and_unrelated_checks(self):
        source = "def answer():\n    return 2\n"
        change = {"hypothesis": "Wrong return value", "target": "app.py", "before": "return 2", "after": "return 3",
                  "expected_check": self.incident.operation, "expected_result": "exit 0", "evidence_refs": ["log"]}
        args = {"sources": {"app.py": source}, "allowed_paths": ["app.py"], "operations": {self.incident.operation}}
        identity = novelty.change_identity(change, **args)
        self.assertTrue(identity)
        self.assertEqual(identity, novelty.change_identity({**change, "hypothesis": "Different prose, same edit"}, **args))
        self.assertEqual(identity, novelty.change_identity({**change, "expected_result": "The same tests succeed"}, **args))
        for bad in ({"after": "return 2 # new session"}, {"target": "test_other.py"},
                    {"expected_check": "python -m unittest unrelated"}, {"before": "return 4"}, {"expected_result": ""}):
            self.assertIsNone(novelty.change_identity({**change, **bad}, **args), bad)

    def test_unittest_trace_presentation_is_not_a_new_cause(self):
        output = ('FAIL: test_answer (test_app.Tests.test_answer)\n'
                  '----------------------------------------------------------------------\nTraceback (most recent call last):\n'
                  '  File "/tmp/a/test_app.py", line 17, in test_answer\n'
                  '    self.assertEqual(3, answer())\n    ^^^^^\nAssertionError: 3 != 2\n'
                  '\n----------------------------------------------------------------------\nRan 1 test in 0.015s\nFAILED\n')
        changed = output.replace('/tmp/a', '/tmp/b').replace('line 17', 'line 29').replace('    ^^^^^\n', '')
        changed = changed.replace('self.assertEqual(3, answer())', 'self.assertEqual(3, answer()) # harmless').replace('0.015s', '0.027s')
        a = novelty.failure_text(output, 'python -m unittest test_app', [('/tmp/a', '<workspace>')])
        b = novelty.failure_text(changed, 'python -m unittest test_app', [('/tmp/b', '<workspace>')])
        self.assertEqual(a, b)
        self.assertNotEqual(a, novelty.failure_text(changed.replace('3 != 2', '3 != 4'), 'python -m unittest test_app',
                                                  [('/tmp/b', '<workspace>')]))

    def test_unsupported_grammars_remain_unknown_not_regex_inferred_semantics(self):
        for name in ("app.go", "app.js", "app.ts", "app.rs", "app.cpp"):
            self.assertIsNone(novelty.source_identity(name, 'return "http://host/value"; // session abc\n'))
            self.assertIsNone(novelty.source_identity(name, 'return /[/*]x/.test("x");'))
        self.assertEqual(novelty.source_identity("a.json", '{"a":1,"b":2}'),
                         novelty.source_identity("a.json", '{ "b": 2, "a": 1 }'))
        self.assertIsNone(novelty.source_identity("unknown.binary", "new bytes"))

    def test_classification_uses_runner_types_not_provider_prose(self):
        for status, expected in (("PAUSED_TASK_PREFLIGHT", "setup"), ("PAUSED_PROVIDER_CAPACITY", "provider_transient"),
                                 ("PAUSED_PROVIDER_TIMEOUT", "runtime"), ("PAUSED_INVALID_OUTPUT", "evidence_gap")):
            self.assertEqual(expected, novelty.classify(status=status))
        self.assertEqual("unresolved", novelty.classify(observation="provider says grant permission"))
        self.assertEqual("product", novelty.classify(user_request={"kind": "goal_change"}))


class RecoveryPacketTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.run = self.root / ".autocode" / "runs" / "owned"
        self.run.mkdir(parents=True)
        self.source = self.root / "app.py"
        self.source.write_text("def answer():\n    return 2\n")
        self.events = self.run / "validator.jsonl"
        self.events.write_text(json.dumps({"type": "item.completed", "item": {
            "type": "command_execution", "id": "check", "command": "python -m unittest test_app", "exit_code": 1,
            "aggregated_output": "AssertionError: 2 != 3\nRan 1 test in 0.002s\nFAILED"}}) + "\n")
        self.output = self.run / "review.json"
        self.output.write_text('{"status":"REWORK"}')
        self.validator = self.run / "validator.json"
        self.validator.write_text('{"verdict":"FAIL"}')
        self.state = {"workspace": str(self.root), "run_dir": str(self.run), "settings": {"roles": {"terra": {"model_pinned": True}}},
            "goal_contract": {"task_id": "approved-job", "hash": "h", "revision": 1, "body": {
                "acceptance_criteria": [{"id": "C1", "criterion": "Return 3"}]}},
            "current_task": {"id": "T1", "kind": "implement", "milestone_id": "M1", "acceptance_criteria": ["C1"], "affected_paths": ["app.py"]},
            "stages": [{"stage": "sol", "output": str(self.validator), "events": str(self.events)}],
            "validation": {"task_id": "T1", "output": str(self.validator), "verdict": "FAIL", "checks": [{
                "command": "python -m unittest test_app", "exit_code": 1, "evidence_ref": "event:check"}]}}
        self.record = {"stage": "astra_review", "output": str(self.output), "source_revision": "s1"}
        self.request = {"source_revision": "s1", "source_output": str(self.output), "evidence_hashes": {str(self.output): util.file_hash(self.output),
                                                                 str(self.events): util.file_hash(self.events)}}
        self.state["resolution_request"] = self.request
        self.snapshot = patch.object(util, "snapshot", return_value={"revision": "s1", "files": {"app.py": "hash"}})
        self.snapshot.start()
        self.addCleanup(self.snapshot.stop)
        recovery.prepare_resolution(self.state, {"summary": "Wrong answer"}, self.record)

    def packet(self):
        return recovery.load_packet(self.request["recovery_packet"], self.run)

    def legacy_failure(self):
        state = copy.deepcopy(self.state)
        state.update(status="PAUSED_REPEATED_FAILURE", next_stage="astra_resolve")
        for index in range(3):
            output = self.run / f"legacy-failure-{index}.json"
            output.write_text("{}")
            row = {"stage": "astra_resolve", "iteration": 1, "role": "resolver", "task_id": "T1",
                   "output": str(output), "source_revision": "s1", "rejected": True, "exit_code": 1}
            recovery.failures.record(state, row, ValueError("Missing summary"), util.now())
            state["stages"].append(row)
        source = {key: value for key, value in self.record.items() if key != "recovery_packet"}
        state["resolution_request"] = {"source_output": str(self.output), "source_revision": "s1",
                                       "evidence_hashes": {str(self.output): util.file_hash(self.output)}}
        recovery.prepare_resolution(state, {"summary": "Wrong answer"}, source)
        packet = recovery.load_packet(state["resolution_request"]["recovery_packet"], self.run)
        state["stages"].append({"stage": "terra", "recovery_novelty": {
            "dispatch_id": "earlier-repair", "incident_ids": [novelty.Incident(**packet["incidents"][0]).id],
            "action": "repair", "change_id": None}})
        return state

    def authorize_legacy(self, state):
        with patch.object(runner.support, "snapshot", util.snapshot):
            authorization = runner.authorize_failure_retry(state, self.run, self.root)
            runner.repeated_failure_resume_guard(state, self.root, authorization=authorization)
        self.assertTrue(authorization["consumed"])
        return authorization

    def test_consumed_legacy_audit_without_novelty_marker_cannot_buy_a_dispatch(self):
        state = self.legacy_failure()
        self.authorize_legacy(state)
        state["stages"].append({"stage": "astra_resolve", "output": str(self.run / "old-success.json"),
                                "source_revision": "s1", "exit_code": 0})
        state.update(status="PAUSED_NO_PROGRESS", next_stage="astra_resolve")
        restarted = json.loads(json.dumps(state))
        history = copy.deepcopy(restarted["failure_history"])
        audit = copy.deepcopy(restarted["failure_retry_authorizations"])
        attempt = {"stage": "astra_resolve", "output": str(self.run / "must-not-launch.json")}
        with self.assertRaisesRegex(util.Paused, "No causal progress"):
            recovery.admit_dispatch(restarted, attempt, self.root, self.run)
        self.assertNotIn("recovery_novelty", attempt)
        self.assertEqual(history, restarted["failure_history"])
        self.assertEqual(audit, restarted["failure_retry_authorizations"])

    def test_actual_current_legacy_authorization_crosses_guard_once_not_a_restart(self):
        state = self.legacy_failure()
        authorization = self.authorize_legacy(state)
        audit = copy.deepcopy(state["failure_retry_authorizations"])
        attempt = {"stage": "astra_resolve", "output": str(self.run / "authorized.json")}
        recovery.admit_dispatch(state, attempt, self.root, self.run, retry_authorization=authorization)
        self.assertEqual("invocation", attempt["recovery_novelty"]["grant_kind"])
        self.assertEqual("explicit_retry", attempt["recovery_novelty"]["reason"])
        self.assertEqual(audit, state["failure_retry_authorizations"])
        state["stages"].append(attempt)
        self.assertTrue(authorization["_novelty_consumed"])
        with self.assertRaisesRegex(util.Paused, "No causal progress"):
            recovery.admit_dispatch(state, {"stage": "astra_resolve", "output": str(self.run / "second.json")},
                                    self.root, self.run, retry_authorization=authorization)
        with self.assertRaisesRegex(util.Paused, "No causal progress"):
            recovery.admit_dispatch(state, {"stage": "astra_resolve", "output": str(self.run / "new-process.json")},
                                    self.root, self.run, retry_authorization=audit[-1])

    def test_current_legacy_retry_requires_exact_stage_source_count_and_pending_frontier(self):
        for mutation in ("stage", "source", "count", "pending", "frontier", "unconsumed"):
            with self.subTest(mutation=mutation):
                state = self.legacy_failure()
                authorization = self.authorize_legacy(state)
                if mutation == "stage":
                    authorization["stage"] = "terra"
                elif mutation == "source":
                    authorization["source_revision"] = "not-the-current-source"
                elif mutation == "count":
                    authorization["count"] += 1
                if mutation == "pending":
                    state["pending_report_repair"] = {"stage": "astra_resolve"}
                elif mutation == "frontier":
                    state["next_stage"] = "terra"
                elif mutation == "unconsumed":
                    authorization.pop("consumed")
                attempt = {"stage": "astra_resolve", "output": str(self.run / (mutation + ".json"))}
                with self.assertRaisesRegex(util.Paused, "No causal progress"):
                    recovery.admit_dispatch(state, attempt, self.root, self.run,
                                            retry_authorization=authorization)
                self.assertNotIn("recovery_novelty", attempt)

    def test_new_novelty_grant_is_live_only_and_its_audit_is_not_credit(self):
        state = self.legacy_failure()
        state.update(status="PAUSED_NO_PROGRESS", next_stage="astra_resolve")
        with self.assertRaises(util.Paused):
            recovery.admit_dispatch(state, {"stage": "astra_resolve", "output": "held"}, self.root, self.run)
        settings = copy.deepcopy(state["settings"])
        authorization = recovery.authorize_retry(runner, state, self.run, self.root)
        retained = copy.deepcopy(state["failure_retry_authorizations"][-1])
        with self.assertRaisesRegex(util.Paused, "No causal progress"):
            recovery.admit_dispatch(state, {"stage": "astra_resolve", "output": "audit-argument"}, self.root, self.run,
                                    retry_authorization=retained)
        attempt = {"stage": "astra_resolve", "output": str(self.run / "present-cli-grant.json")}
        recovery.admit_dispatch(state, attempt, self.root, self.run, retry_authorization=authorization)
        self.assertEqual("explicit_retry", attempt["recovery_novelty"]["reason"])
        self.assertEqual(retained, state["failure_retry_authorizations"][-1])
        self.assertEqual(settings, state["settings"])
        state["stages"].append(attempt)
        with self.assertRaisesRegex(util.Paused, "No causal progress"):
            recovery.admit_dispatch(state, {"stage": "astra_resolve", "output": "audit-is-not-credit"},
                                    self.root, self.run, retry_authorization=retained)

    def capture_resolution(self, state, decision):
        self.output.write_text(json.dumps(decision))
        source = {key: value for key, value in self.record.items() if key != "recovery_packet"}
        source.update(task_id="T1", contract_hash="h", contract_revision=1, finished_at=util.now())
        state["resolution_request"] = {"source_revision": "s1", "source_task_id": "T1", "source_output": str(self.output),
            "contract_hash": "h", "contract_revision": 1,
            "review": copy.deepcopy(decision), "evidence_hashes": {
                str(self.output): util.file_hash(self.output), str(self.events): util.file_hash(self.events)}}
        recovery.prepare_resolution(state, decision, source)
        state["stages"].append(source)
        return source

    def validation_request(self):
        return {"task_id": "T1", "contract_hash": "h", "contract_revision": 1, "status": "REWORK",
                "user_request": {"kind": "none"}, "acceptance_criteria": [
                    {"id": "C1", "criterion": "Return 3", "status": "unverified", "evidence": str(self.events)}],
                "next_task": {"kind": "validate", "milestone_id": "M1", "requirements": ["Return 3"],
                    "acceptance_criteria": ["C1"], "validation_plan": ["python -m unittest test_app"], "findings": []}}

    def builder_retry_state(self):
        state = copy.deepcopy(self.state)
        state["settings"]["roles"]["terra"].update(model="pinned-builder", reasoning_effort="high")
        state["settings"]["builder_retry"] = {"enabled": True, "ordinary_retries": 0, "strong_model": "strong",
                                               "strong_reasoning_effort": "high"}
        incident = novelty.Incident(**self.packet()["incidents"][0])
        state["stages"].append({"stage": "terra", "recovery_novelty": {
            "dispatch_id": "earlier-repair", "incident_ids": [incident.id], "action": "repair", "change_id": None}})
        decision = self.validation_request()
        decision["next_task"]["kind"] = "implement"
        self.capture_resolution(state, decision)
        self.assertEqual("pause", recovery.builder_policy.failure(state, str(self.output), "Independent failure"))
        state["next_stage"] = "terra"
        builder_recovery.request_serial_retry(state, ["M1"])
        return state

    def test_current_builder_retry_covers_one_planning_diagnosis_and_one_builder(self):
        state = self.builder_retry_state()
        settings = copy.deepcopy(state["settings"])
        diagnosis = {"stage": "astra_resolve", "started_at": util.now(), "output": str(self.run / "new-diagnosis.json")}
        recovery.admit_dispatch(state, diagnosis, self.root, self.run)
        self.assertEqual("builder", diagnosis["recovery_novelty"]["grant_kind"])
        state["stages"].append(diagnosis)
        state["current_task"]["id"] = "T2"
        plan = {"source_output": str(self.output), "tasks": [copy.deepcopy(state["current_task"])]}
        recovery.finish_resolution_packet(state, state.pop("resolution_request"), plan)
        state.update(repair_plan=plan, next_stage="terra")
        attempt = {"stage": "terra", "started_at": util.now(), "output": str(self.run / "granted-builder.json")}
        recovery.admit_dispatch(state, attempt, self.root, self.run)
        self.assertEqual(diagnosis["recovery_novelty"]["grant_id"], attempt["recovery_novelty"]["grant_id"])
        state["stages"].append(attempt)
        with self.assertRaisesRegex(util.Paused, "No causal progress"):
            recovery.admit_dispatch(state, {"stage": "terra", "output": "second-builder"}, self.root, self.run)
        self.assertEqual(settings, state["settings"])

    def test_legacy_builder_grant_consumed_without_novelty_receipt_stays_spent(self):
        for stage, timestamp in (("terra", True), ("astra_resolve", True), ("terra", False)):
            with self.subTest(stage=stage, known_clock=timestamp):
                state = self.builder_retry_state()
                spent = {"stage": stage, "output": str(self.run / "legacy-consumed.json"), "exit_code": 0}
                if timestamp:
                    spent["started_at"] = util.now()
                state["stages"].append(spent)
                attempt = {"stage": "astra_resolve", "output": str(self.run / "must-not-launch.json")}
                with self.assertRaisesRegex(util.Paused, "No causal progress"):
                    recovery.admit_dispatch(state, attempt, self.root, self.run)
                self.assertNotIn("recovery_novelty", attempt)

    def test_builder_credit_requires_current_lane_count_nonce_pending_and_source(self):
        for mutation in ("count", "nonce", "lane", "pending", "source", "newer_decision"):
            with self.subTest(mutation=mutation):
                state = self.builder_retry_state()
                grant = state["builder_retry_decisions"][-1]
                if mutation == "count":
                    grant["attempt"] += 1
                elif mutation == "nonce":
                    grant["at"] = "2000-01-01T00:00:00+00:00"
                elif mutation == "lane":
                    state["builder_retry_key"] = "different-lane"
                elif mutation == "pending":
                    state["resolution_request"]["source_output"] = "different-report"
                elif mutation == "source":
                    self.output.write_text("changed after the user grant")
                else:
                    state["builder_retry_decisions"].append({**grant, "owner": "autoresolver"})
                attempt = {"stage": "astra_resolve", "output": str(self.run / (mutation + "-not-launched.json"))}
                with self.assertRaises(util.Paused):
                    recovery.admit_dispatch(state, attempt, self.root, self.run)
                self.assertNotIn("recovery_novelty", attempt)

    def test_bound_validation_only_resolution_does_not_consult_or_consume_builder_budget(self):
        state = copy.deepcopy(self.state)
        state["settings"]["builder_retry"] = {"enabled": True, "ordinary_retries": 0, "strong_model": "strong"}
        source = self.capture_resolution(state, self.validation_request())
        before = copy.deepcopy(state)
        attempt = {"stage": "astra_resolve", "output": str(self.run / "validation-planning.json")}
        with patch.object(recovery.builder_policy, "failure", side_effect=AssertionError("No Builder work requested")):
            recovery.admit_dispatch(state, attempt, self.root, self.run)
        self.assertEqual("diagnosis", attempt["recovery_novelty"]["action"])
        self.assertEqual(before, state)
        decision = copy.deepcopy(state["resolution_request"]["review"])
        decision["recovery_change"] = {"hypothesis": "A real but unrequested code edit", "target": "app.py",
            "before": "return 2", "after": "return 3", "expected_check": "python -m unittest test_app",
            "expected_result": "exit 0", "question": "", "evidence_refs": [str(self.events)]}
        runtime, policy = Mock(), Mock()
        self.assertFalse(recovery.route_known_change(runtime, state, decision, source, run_dir=self.run, retry_policy=policy))
        self.assertEqual([], runtime.mock_calls)
        self.assertEqual([], policy.mock_calls)

    def test_validation_label_tampering_cannot_bypass_bound_decision_or_scope(self):
        for mutation in ("mutable_label", "current_task_label", "source", "criteria", "empty_plan", "unaccepted"):
            with self.subTest(mutation=mutation):
                state = copy.deepcopy(self.state)
                state["settings"]["roles"]["terra"].update(model="pinned-builder")
                state["settings"]["builder_retry"] = {"enabled": True, "ordinary_retries": 0, "strong_model": "strong"}
                decision = self.validation_request()
                if mutation in ("mutable_label", "current_task_label"):
                    decision["next_task"]["kind"] = "implement"
                elif mutation == "criteria":
                    decision["next_task"]["acceptance_criteria"] = ["UNAPPROVED"]
                elif mutation == "empty_plan":
                    decision["next_task"]["validation_plan"] = []
                self.capture_resolution(state, decision)
                if mutation == "mutable_label":
                    state["resolution_request"]["review"]["next_task"]["kind"] = "validate"
                elif mutation == "current_task_label":
                    state["current_task"]["kind"] = "validate"
                elif mutation == "source":
                    self.output.write_text("altered after acceptance")
                elif mutation == "unaccepted":
                    state["resolution_request"]["source_report_not_accepted"] = True
                attempt = {"stage": "astra_resolve", "output": str(self.run / "no-bypass.json")}
                with self.assertRaises(util.Paused):
                    recovery.admit_dispatch(state, attempt, self.root, self.run)
                self.assertNotIn("recovery_novelty", attempt)

    def test_validation_can_cover_another_approved_criterion_of_the_same_milestone(self):
        state = copy.deepcopy(self.state)
        body = state["goal_contract"]["body"]
        body["acceptance_criteria"].append({"id": "C2", "criterion": "Reject unsupported input"})
        body["milestones"] = [{"id": "M1", "acceptance_criteria": ["C1", "C2"]}]
        decision = self.validation_request()
        decision["next_task"]["acceptance_criteria"] = ["C2"]
        self.capture_resolution(state, decision)
        with patch.object(recovery.builder_policy, "failure", side_effect=AssertionError("Validation is not Builder work")):
            attempt = {"stage": "astra_resolve", "output": str(self.run / "approved-other-check.json")}
            recovery.admit_dispatch(state, attempt, self.root, self.run)
        self.assertEqual("diagnosis", attempt["recovery_novelty"]["action"])

    def test_originals_remain_byte_exact_after_legitimate_source_changes(self):
        packet = self.packet()
        original = next(row for row in packet["originals"] if row["original_path"] == str(self.source))
        self.source.write_text("def answer():\n    return 3\n")
        self.assertEqual(packet, self.packet())
        blob = util.read(original["path"])
        self.assertEqual(b"def answer():\n    return 2\n", base64.b64decode(blob["base64"]))
        self.assertEqual(self.state["goal_contract"]["body"], packet["protected_obligations"])

    def test_draft_and_original_tampering_fail_closed_without_repinning(self):
        pointer = copy.deepcopy(self.request["recovery_packet"])
        path = Path(pointer["path"])
        original = path.read_bytes()
        for replacement in ('{"version":1}', "broken"):
            path.write_text(replacement)
            with self.assertRaises(util.Paused):
                self.packet()
            self.assertEqual(pointer, self.request["recovery_packet"])
        path.write_bytes(original)
        archived = Path(self.packet()["originals"][0]["path"])
        archived.write_text("tampered")
        with self.assertRaises(util.Paused):
            self.packet()

    def test_foreign_run_packet_cannot_be_replayed(self):
        other = self.root / ".autocode" / "runs" / "other"
        other.mkdir()
        with self.assertRaises(util.Paused):
            recovery.load_packet(self.request["recovery_packet"], other)

    def test_first_diagnosis_is_reserved_once_and_restart_cannot_dispatch_again(self):
        record = {"stage": "astra_resolve", "output": str(self.run / "diagnosis.json"), "started_at": "first"}
        recovery.admit_dispatch(self.state, record, self.root, self.run)
        saved = copy.deepcopy(record)
        recovery.admit_dispatch(self.state, record, self.root, self.run)
        self.assertEqual(saved, record)
        self.state["stages"].append(record)
        restarted = json.loads(json.dumps(self.state))
        before = copy.deepcopy(restarted)
        with self.assertRaisesRegex(util.Paused, "No causal progress"):
            recovery.admit_dispatch(restarted, {"stage": "astra_resolve", "output": str(self.run / "new-session.json")}, self.root, self.run)
        self.assertEqual(before["stages"], restarted["stages"])
        self.assertNotIn("resolver", restarted)

    def test_only_an_attempt_archived_without_a_report_leaves_its_incident_untried(self):
        # #422: automatic recovery accounts and archives a stopped attempt that ended without a
        # completed turn (timeout, capacity, startup). It returned no report, so its relaunch is
        # admitted. A timeout whose late terminal turn was reconciled, an operator-abandoned
        # uncertain attempt, a rejected report and a truncated report may each carry a result,
        # so each still holds (#254).
        archived = {"timed_out": True, "accounted": True, "automatic_recovery": True, "abandoned": True, "rejected": True}
        for flags, admitted in ((archived, True), ({"timed_out": True}, False),
                                ({"abandoned": True, "rejected": True}, False),
                                ({"rejected": True, "exit_code": 0}, False),
                                ({**archived, "timed_out": False, "exit_code": 0, "truncated_output": True}, False),
                                ({**archived, "accounted": False}, False)):
            with self.subTest(flags=flags):
                state = copy.deepcopy(self.state)
                first = {"stage": "astra_resolve", "output": str(self.run / "first.json"), "started_at": "first"}
                recovery.admit_dispatch(state, first, self.root, self.run)
                first.update(flags)
                state["stages"].append(first)
                relaunch = {"stage": "astra_resolve", "output": str(self.run / "relaunch.json"), "started_at": "second"}
                if not admitted:
                    with self.assertRaisesRegex(util.Paused, "No causal progress"):
                        recovery.admit_dispatch(state, relaunch, self.root, self.run)
                    continue
                recovery.admit_dispatch(state, relaunch, self.root, self.run)
                self.assertEqual("first_incident", relaunch["recovery_novelty"]["reason"])
                state["stages"].append(relaunch)
                # The relaunch returned a result, so the same experiment again needs new information.
                with self.assertRaisesRegex(util.Paused, "No causal progress"):
                    recovery.admit_dispatch(state, {"stage": "astra_resolve", "output": str(self.run / "third.json")},
                                            self.root, self.run)

    def test_task_reassignment_and_comment_change_do_not_rename_incident(self):
        initial = novelty.Incident(**self.packet()["incidents"][0]).id
        self.state["current_task"].update(id="T9", requirements=["Different repair wording"])
        self.state["validation"]["task_id"] = "T9"
        self.state["resolution_request"] = {**self.request}
        self.state["resolution_request"].pop("recovery_packet")
        self.source.write_text(self.source.read_text() + "\n# Changed timestamp 2026-10-03\n")
        record = {key: value for key, value in self.record.items() if key != "recovery_packet"}
        recovery.prepare_resolution(self.state, {"summary": "Same failure"}, record)
        packet = recovery.load_packet(self.state["resolution_request"]["recovery_packet"], self.run)
        self.assertEqual(initial, novelty.Incident(**packet["incidents"][0]).id)

    def test_changed_packet_scope_or_settings_refused_before_provider(self):
        for key in ("settings", "current_task"):
            state = copy.deepcopy(self.state)
            if key == "settings":
                state[key]["roles"]["terra"]["model_pinned"] = False
            else:
                state[key]["acceptance_criteria"] = ["C2"]
            with self.assertRaises(util.Paused):
                recovery.admit_dispatch(state, {"stage": "astra_resolve"}, self.root, self.run)

    def test_admission_ceiling_change_preserves_packet_and_assigned_repair_not_novelty(self):
        pointer = copy.deepcopy(self.request["recovery_packet"])
        original = Path(pointer["path"]).read_bytes()
        plan = {"tasks": [copy.deepcopy(self.state["current_task"])]}
        recovery.finish_resolution_packet(self.state, self.request, plan)
        self.state["repair_plan"] = plan
        self.state["settings"].update(limits={"iteration_ceiling": 3, "max_seconds": 900},
                                      transport_identities={},
                                      budget_origins={"iteration_ceiling": "user_explicit", "max_seconds": "user_explicit"})
        record = {"stage": "terra", "output": str(self.run / "assigned-repair.json")}
        recovery.admit_dispatch(self.state, record, self.root, self.run)
        self.assertEqual("first_incident", record["recovery_novelty"]["reason"])
        self.state["stages"].append(record)
        self.state["settings"]["limits"].update(iteration_ceiling=None, max_seconds=0)
        with self.assertRaisesRegex(util.Paused, "No causal progress"):
            recovery.admit_dispatch(self.state, {"stage": "astra_resolve", "output": str(self.run / "no-new-input.json")},
                                    self.root, self.run)
        self.assertEqual(original, Path(pointer["path"]).read_bytes())
        self.assertEqual(pointer, self.request["recovery_packet"])
        self.assertNotIn("limits", self.packet()["settings"])
        self.assertEqual({"iteration_ceiling": None, "max_seconds": 0}, self.state["settings"]["limits"])

    def two_criteria_failure(self):
        """T1 owns C1 and C2 of the approved C1-C3 and fails; its recovery packet is captured."""
        state = copy.deepcopy(self.state)
        state["goal_contract"]["body"]["acceptance_criteria"] += [
            {"id": "C2", "criterion": "Reject a blank name"}, {"id": "C3", "criterion": "Document usage"}]
        state["current_task"]["acceptance_criteria"] = ["C1", "C2"]
        state["resolution_request"] = {key: value for key, value in self.request.items() if key != "recovery_packet"}
        record = {key: value for key, value in self.record.items() if key != "recovery_packet"}
        recovery.prepare_resolution(state, {"summary": "Blank name accepted"}, record)
        return state

    def test_repair_admission_accepts_only_a_narrowing_of_the_failed_scope(self):
        # #423: the Resolver's repair task may keep only the failed criterion, but
        # it may not add one, move to another milestone, or change the contract.
        cases = {"narrowed": ({"acceptance_criteria": ["C2"]}, {}, None),
                 "unchanged": ({}, {}, None),
                 "widened": ({"acceptance_criteria": ["C2", "C3"]}, {}, "changed before admission"),
                 "other milestone": ({"milestone_id": "M2", "acceptance_criteria": ["C2"]}, {}, "changed before admission"),
                 "joined wave": ({"milestone_ids": ["M1", "M2"], "acceptance_criteria": ["C2"]}, {}, "changed before admission"),
                 "no criteria": ({"acceptance_criteria": []}, {}, "changed before admission"),
                 "other contract": ({"acceptance_criteria": ["C2"]}, {"hash": "h2"}, "changed before admission"),
                 "other job": ({"acceptance_criteria": ["C2"]}, {"task_id": "other-job"}, "changed before admission")}
        for name, (task, contract, refused) in cases.items():
            with self.subTest(name):
                state = self.two_criteria_failure()
                pointer = copy.deepcopy(state["resolution_request"]["recovery_packet"])
                # The repair task is assigned before its admission receipt is written.
                state["current_task"].update(id="T2", **task)
                state["goal_contract"].update(contract)
                plan = {"tasks": [copy.deepcopy(state["current_task"])]}
                recovery.finish_resolution_packet(state, state.pop("resolution_request"), plan)
                state.update(repair_plan=plan, next_stage="terra")
                attempt = {"stage": "terra", "output": str(self.run / "repair-builder.json")}
                if refused:
                    with self.assertRaisesRegex(util.Paused, refused) as caught:
                        recovery.admit_dispatch(state, attempt, self.root, self.run)
                    self.assertEqual("PAUSED_STALE_HANDOFF", caught.exception.status)
                    self.assertNotIn("recovery_novelty", attempt)
                    continue
                recovery.admit_dispatch(state, attempt, self.root, self.run)
                self.assertEqual("repair", attempt["recovery_novelty"]["action"])
                self.assertEqual(pointer, attempt["recovery_novelty"]["packet"])
                # Narrowing buys no novelty: the same incident still needs a new change.
                state["stages"].append(attempt)
                with self.assertRaisesRegex(util.Paused, "No causal progress"):
                    recovery.admit_dispatch(state, {"stage": "terra", "output": str(self.run / "again.json")},
                                            self.root, self.run)

    def test_execution_timeout_model_and_permission_changes_still_invalidate_binding(self):
        for settings in ({"limits": {"stage_timeout_seconds": 0}}, {"allow_no_changes": True},
                         {"transport_identities": {"opencode": {"base_url": "different-endpoint"}}},
                         {"roles": {"terra": {"model": "different", "model_pinned": True}}}):
            state = copy.deepcopy(self.state)
            state["settings"].update(settings)
            with self.assertRaisesRegex(util.Paused, "settings"):
                recovery.admit_dispatch(state, {"stage": "astra_resolve"}, self.root, self.run)

    def test_integrated_parent_keeps_existing_handoff_without_opening_worker_artifacts(self):
        state = copy.deepcopy(self.state)
        state["current_task"]["milestone_ids"] = ["M1", "M2"]
        state["resolution_request"] = {"source_revision": "s1", "evidence_hashes": {
            str(self.root / ".autocode" / "workers" / "owned-child" / "proof.json"): "already-validated-parent-pin"}}
        pins = copy.deepcopy(state["resolution_request"]["evidence_hashes"])
        record = {key: value for key, value in self.record.items() if key != "recovery_packet"}
        with patch.object(recovery, "_archive", side_effect=AssertionError("must not open a worker artifact")):
            recovery.prepare_resolution(state, {"summary": "Combined integration failed"}, record)
        self.assertEqual(pins, state["resolution_request"]["evidence_hashes"])
        self.assertNotIn("recovery_packet", state["resolution_request"])
        self.assertNotIn("recovery_packet", record)
        self.assertIn("existing_resolver_bounds", state["resolution_request"]["recovery_novelty_skipped"])
        # The fallback never makes the same foreign path valid for a serial packet.
        state["current_task"].pop("milestone_ids")
        with self.assertRaisesRegex(util.Paused, "another run"):
            recovery.prepare_resolution(state, {"summary": "Serial scope cannot own that artifact"}, record)

    def test_existing_builder_exhaustion_precedes_diagnosis_and_is_idempotent(self):
        state = copy.deepcopy(self.state)
        state["settings"]["roles"]["terra"].update(model="pinned-builder")
        state["settings"]["builder_retry"] = {"enabled": True, "ordinary_retries": 0, "strong_model": "strong",
                                               "strong_reasoning_effort": "high"}
        record = {key: value for key, value in self.record.items() if key != "recovery_packet"}
        state["resolution_request"] = {"source_revision": "s1", "source_output": str(self.output),
                                       "evidence_hashes": {str(self.output): util.file_hash(self.output)}}
        recovery.prepare_resolution(state, {"summary": "Actual failed candidate"}, record)
        settings = copy.deepcopy(state["settings"])
        for _ in range(2):
            attempt = {"stage": "astra_resolve", "output": str(self.run / "must-not-launch.json")}
            with self.assertRaises(util.Paused) as caught:
                recovery.admit_dispatch(state, attempt, self.root, self.run)
            self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", caught.exception.status)
            self.assertEqual(settings, state["settings"])
            self.assertNotIn("recovery_novelty", attempt)
        self.assertEqual(["pause"], [row["action"] for row in state["builder_retry_decisions"]])
        self.assertEqual("terra", state["next_stage"])
        self.assertNotIn("resolver", state)
        self.assertTrue(recovery.known_builder_pause(state))
        state["phase"] = "PAUSED_OR_BLOCKED"
        self.assertTrue(recovery.known_builder_pause(state))
        state["builder_retry_decisions"][-1]["owner"] = "user_cli"
        self.assertFalse(recovery.known_builder_pause(state))

    def test_parallel_operational_diagnosis_keeps_its_existing_limits_without_a_packet(self):
        state = copy.deepcopy(self.state)
        state["current_task"]["milestone_ids"] = ["M1", "M2"]
        state["resolver"] = {"diagnostic_calls": 1}
        state["settings"]["operational_diagnosis"] = {"max_calls_per_run": 2}
        original = copy.deepcopy(state)
        request = {"source_revision": "s1", "description": "Repeated report failure", "evidence_hashes": {
            str(self.root / ".autocode" / "workers" / "owned-child" / "report.json"): "existing-parent-pin"}}
        record = {key: value for key, value in self.record.items() if key != "recovery_packet"}
        with patch.object(recovery, "_archive", side_effect=AssertionError("must not open a worker artifact")):
            recovery.prepare_diagnosis(state, request, record, self.run)
        self.assertIn("existing_resolver_bounds", request["recovery_novelty_skipped"])
        self.assertNotIn("recovery_packet", request)
        self.assertEqual(original, state)

    def test_available_builder_preview_never_spends_or_escalates(self):
        state = copy.deepcopy(self.state)
        state["settings"]["roles"]["terra"].update(model="builder", model_pinned=False)
        state["settings"]["builder_retry"] = {"enabled": True, "ordinary_retries": 1, "strong_model": "strong",
                                               "strong_reasoning_effort": "high"}
        record = {key: value for key, value in self.record.items() if key != "recovery_packet"}
        state["resolution_request"] = {"source_revision": "s1", "source_output": str(self.output),
                                       "evidence_hashes": {str(self.output): util.file_hash(self.output)}}
        recovery.prepare_resolution(state, {"summary": "Actual failed candidate"}, record)
        settings = copy.deepcopy(state["settings"])
        attempt = {"stage": "astra_resolve", "output": str(self.run / "first-diagnosis.json")}
        recovery.admit_dispatch(state, attempt, self.root, self.run)
        self.assertEqual("first_incident", attempt["recovery_novelty"]["reason"])
        self.assertNotIn("builder_retry_decisions", state)
        self.assertNotIn("builder_retries", state)
        self.assertEqual(settings, state["settings"])

    def test_claimed_builder_grant_without_matching_operator_receipt_cannot_dispatch(self):
        packet = self.packet()
        self.state["stages"].append({"stage": "terra", "recovery_novelty": {
            "dispatch_id": "previous", "incident_ids": [novelty.Incident(**packet["incidents"][0]).id],
            "action": "repair", "change_id": None}})
        self.state["builder_retry_decisions"] = [{"owner": "user_cli", "action": "retry", "at": "claimed",
            "milestone_key": util.digest(["h", ["M1"]]), "failure": str(self.output)}]
        self.state["user_events"] = [{"kind": "builder_retry", "actor": "provider", "at": "claimed",
                                     "milestone_ids": ["M1"], "failure": str(self.output)}]
        attempt = {"stage": "astra_resolve", "output": str(self.run / "unauthorized.json")}
        with self.assertRaisesRegex(util.Paused, "No causal progress"):
            recovery.admit_dispatch(self.state, attempt, self.root, self.run)
        self.assertNotIn("recovery_novelty", attempt)
        self.assertNotIn("resolver", self.state)

    def test_saved_active_workers_are_not_removed_or_restarted(self):
        for active in ({"active_stage": {"output": "live", "pid": 12}}, {"uncertain_artifacts": ["unknown"]}):
            state = {**copy.deepcopy(self.state), **active}
            with self.assertRaises(util.Paused):
                recovery.admit_dispatch(state, {"stage": "astra_resolve", "output": "replacement"}, self.root, self.run)
            for key, value in active.items():
                self.assertEqual(value, state[key])

    def test_diagnosis_attests_only_a_change_its_packet_attests(self):
        # #422: a diagnosis's accepted retry does not depend on its proposed change. One the
        # packet cannot attest is passed on as unattested advice with the reason, under a key
        # admit_dispatch does not read, instead of voiding the retry or vanishing.
        change = {"hypothesis": "Wrong answer return branch", "target": "app.py", "before": "return 2",
                  "after": "return 3", "expected_check": "python -m unittest test_app", "expected_result": "exit 0",
                  "evidence_refs": [str(self.events)], "question": ""}
        self.assertEqual((change, None), recovery.diagnosis_change(self.request, change, self.run))
        for mutation in ({"expected_check": "ruby test.rb"}, {"target": "source.rb"},
                         {"evidence_refs": ["invented"]}, {"before": "return 9"}, "not an object"):
            with self.subTest(mutation=mutation):
                proposed = {**change, **mutation} if isinstance(mutation, dict) else mutation
                attested, unattested = recovery.diagnosis_change(self.request, proposed, self.run)
                self.assertIsNone(attested)
                self.assertEqual(proposed, unattested["change"])
                self.assertIn("Recovery packet: proposed change", unattested["reason"])
        self.assertEqual((None, None), recovery.diagnosis_change(self.request, None, self.run))
        # Without a packet (parallel or integrated scope) nothing attests a proposal.
        attested, unattested = recovery.diagnosis_change({}, change, self.run)
        self.assertEqual((None, change), (attested, unattested["change"]))
        # A stale packet is not a refused proposal: it raises, as finish_resolution_packet does.
        stale = {**self.request, "recovery_packet": {**self.request["recovery_packet"], "sha256": "0" * 64}}
        with self.assertRaisesRegex(util.Paused, "draft hash changed"):
            recovery.diagnosis_change(stale, change, self.run)

    def test_specific_changed_repair_is_admitted_but_not_marked_accepted(self):
        self.request["recovery_change"] = {"hypothesis": "Wrong answer return branch", "target": "app.py",
            "before": "return 2", "after": "return 3", "expected_check": "python -m unittest test_app",
            "expected_result": "exit 0", "evidence_refs": [str(self.events)], "question": ""}
        recovery.validate_decision(self.state, {"recovery_change": self.request["recovery_change"]}, self.record)
        packet = self.packet()
        self.state["stages"].append({"stage": "terra", "recovery_novelty": {
            "dispatch_id": "prior", "incident_ids": [novelty.Incident(**packet["incidents"][0]).id],
            "action": "repair", "change_id": None}})
        plan = {"tasks": [self.state["current_task"]]}
        recovery.finish_resolution_packet(self.state, self.request, plan)
        self.state["repair_plan"] = plan
        attempt = {"stage": "terra", "output": str(self.run / "builder.json")}
        recovery.admit_dispatch(self.state, attempt, self.root, self.run)
        self.assertEqual("bounded_change", attempt["recovery_novelty"]["reason"])
        self.assertNotIn("accepted", attempt["recovery_novelty"])
        self.assertEqual("FAIL", self.state["validation"]["verdict"])

    def test_completion_proposal_foreign_or_empty_refs_cannot_admit_diagnosis(self):
        change = {"hypothesis": "Wrong answer", "target": "app.py", "before": "return 2", "after": "return 3",
                  "expected_check": "python -m unittest test_app", "expected_result": "exit 0", "question": "Which branch?"}
        for refs in ([], ["/foreign/receipt"], ["event:invented"]):
            with self.subTest(refs=refs):
                self.request["recovery_change"] = {**change, "evidence_refs": refs}
                attempt = {"stage": "astra_resolve", "output": str(self.run / "not-launched.json")}
                with self.assertRaisesRegex(util.Paused, "pinned originals"):
                    recovery.admit_dispatch(self.state, attempt, self.root, self.run)
                self.assertNotIn("recovery_novelty", attempt)

    def test_current_validator_event_alias_resolves_only_to_its_pinned_stream(self):
        change = {"hypothesis": "Wrong answer", "target": "app.py", "before": "return 2", "after": "return 3",
                  "expected_check": "python -m unittest test_app", "expected_result": "exit 0",
                  "question": "Which branch?", "evidence_refs": ["event:check"]}
        self.request["recovery_change"] = change
        attempt = {"stage": "astra_resolve", "output": str(self.run / "alias-diagnosis.json")}
        recovery.admit_dispatch(self.state, attempt, self.root, self.run)
        self.assertEqual("diagnosis", attempt["recovery_novelty"]["action"])

    def test_attested_dependency_change_is_not_an_unverified_environment_claim(self):
        original = {"files": {"lib.py": "old"}, "consumer_contract": "h"}
        changed = {"files": {"lib.py": "new"}, "consumer_contract": "h"}
        old, new = json.dumps(original, sort_keys=True), json.dumps(changed, sort_keys=True)
        change = {"target": inputs.TARGET, "hypothesis": "The registered dependency supplies the missing export",
            "before": old, "after": new, "expected_check": "python -m unittest test_app", "expected_result": "exit 0"}
        kwargs = {"operations": {"python -m unittest test_app"}}
        ident = inputs.change_identity(change, {inputs.TARGET: new}, {inputs.TARGET: old}, **kwargs)
        self.assertTrue(ident)
        for bad in ({"target": "input:arbitrary_environment"}, {"before": "claimed old input"},
                    {"after": "claimed new input"}, {"expected_check": "unrelatedcheck"}):
            self.assertIsNone(inputs.change_identity({**change, **bad}, {inputs.TARGET: new}, {inputs.TARGET: old}, **kwargs))
        self.assertIsNone(inputs.change_identity(change, {inputs.TARGET: new}, {inputs.TARGET: new}, **kwargs))

    def test_dependency_capture_requires_the_existing_user_receipt_and_live_pins(self):
        root = self.run / "evidence" / "dependency"
        (root / "source").mkdir(parents=True)
        (root / "source" / "lib.py").write_text("answer = 3\n")
        files = {"lib.py": util.file_hash(root / "source" / "lib.py")}
        contract = {"task_id": "producer", "revision": 1, "body": {"acceptance_criteria": []}}
        contract.update(hash=util.digest(contract), approval_status="approved")
        snapshot = {"head": "saved-producer-head", "files": files}
        revision = util.digest(snapshot)
        for name, value in (("contract.json", contract), ("source-snapshot.json", snapshot),
                            ("validator.json", {"verdict": "PASS", "contract_hash": contract["hash"]}),
                            ("reviewer.json", {"status": "TASK_COMPLETE", "contract_hash": contract["hash"]})):
            util.atomic_json(root / name, value)
        producer = str(self.root / "producer")
        receipt = {"producer_workspace": producer, "producer_run": producer + "/.autocode/runs/one",
            "consumer_contract": "h", "producer_contract": contract["hash"], "source_revision": revision,
            "verified_complete": True, "files": files,
            "pins": {name: util.file_hash(root / name) for name in ("contract.json", "source-snapshot.json")},
            "proofs": {role: {"path": role + ".json", "sha256": util.file_hash(root / (role + ".json")),
                               "source_revision": revision} for role in ("validator", "reviewer")}}
        manifest = root / "manifest.json"
        util.atomic_json(manifest, receipt)
        wait = {"status": "delivered", "authorization": "user_cli_dependency_binding", "consumer_contract": "h",
            "consumer_source": "s1", "destination": "evidence/dependency", "files": ["lib.py"],
            "producer_run": receipt["producer_run"], "producer_workspace": producer, "request_id": "registered-request",
            "manifest": str(manifest), "manifest_sha256": util.file_hash(manifest), "next_stage": "terra"}
        self.state["dependency_wait"] = wait
        with self.assertRaisesRegex(ValueError, "matching user binding"):
            inputs.capture(self.state, self.run)
        self.state["user_events"] = [{"kind": "dependency_binding", "actor": "user_cli", **wait}]
        before = copy.deepcopy(self.state)
        observed, pins = inputs.capture(self.state, self.run)
        self.assertIn(inputs.TARGET, observed)
        self.assertEqual(6, len(pins))
        self.assertEqual(before, self.state)
        record = {key: value for key, value in self.record.items() if key != "recovery_packet"}
        self.state["resolution_request"] = {"source_revision": "s1", "source_output": str(self.output), "evidence_hashes": {
            str(self.output): util.file_hash(self.output), str(self.events): util.file_hash(self.events)}}
        recovery.prepare_resolution(self.state, {"summary": "Same failure after delivery"}, record)
        (root / "source" / "lib.py").write_text("tampered = True\n")
        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            inputs.capture(self.state, self.run)
        attempt = {"stage": "astra_resolve", "output": str(self.run / "not-admitted.json")}
        with self.assertRaisesRegex(util.Paused, "source hash mismatch"):
            recovery.admit_dispatch(self.state, attempt, self.root, self.run)
        self.assertNotIn("recovery_novelty", attempt)

    def scratch(self, control=None):
        """Where tool containment tells a contained stage to capture (#419)."""
        return self.root / ".autocode" / (control or "tool-containment-" + uuid.uuid4().hex) / "scratch"

    def contained_capture(self, scratch, *, recorded=None):
        """Pin a failed check's receipt and log captured in `scratch`; record `recorded` as the Validator's scratch."""
        scratch.mkdir(parents=True)
        raw = scratch / "evidence-check.log"
        raw.write_text("AssertionError: 2 != 3\n")
        receipt = scratch / "evidence-check.json"
        receipt.write_text(json.dumps({"full_output": str(raw)}))
        state = copy.deepcopy(self.state)
        if recorded:
            state["stages"][-1]["tool_containment"] = {"version": 1, "scratch": str(recorded)}
        state["resolution_request"] = {"source_revision": "s1", "source_output": str(self.output), "evidence_hashes": {
            str(path): util.file_hash(path) for path in (self.output, self.events, receipt, raw)}}
        return state, receipt, raw

    def test_contained_validator_scratch_capture_is_archived_as_this_runs_evidence(self):
        scratch = self.scratch()
        state, receipt, raw = self.contained_capture(scratch, recorded=scratch)
        record = {key: value for key, value in self.record.items() if key != "recovery_packet"}
        recovery.prepare_resolution(state, {"summary": "Wrong answer"}, record)
        packet = recovery.load_packet(state["resolution_request"]["recovery_packet"], self.run)
        self.assertLessEqual({str(receipt), str(raw)}, {row["original_path"] for row in packet["originals"]})

    def test_containment_scratch_is_owned_only_through_this_runs_own_launch_record(self):
        record = {key: value for key, value in self.record.items() if key != "recovery_packet"}
        scratch = self.scratch()
        state, _, _ = self.contained_capture(scratch, recorded=scratch)
        recovery.prepare_resolution(state, {"summary": "Recorded scratch"}, copy.deepcopy(record))
        sibling, misnamed, control = self.scratch(), self.scratch("tool-containment-" + "g" * 32), self.scratch().parent
        foreign = self.root / ".autocode" / "runs" / "foreign" / ("tool-containment-" + "a" * 32) / "scratch"
        cases = {"unrecorded": (self.scratch(), None),
                 "beside the recorded scratch": (sibling.with_name("scratch-copy"), sibling),
                 "not a runner-made name": (misnamed, misnamed),
                 "the control directory, not its scratch": (control, control),
                 "inside another run": (foreign, foreign)}
        for label, (evidence, recorded) in cases.items():
            with self.subTest(label=label):
                state, _, _ = self.contained_capture(evidence, recorded=recorded)
                with self.assertRaisesRegex(util.Paused, "another run"):
                    recovery.prepare_resolution(state, {"summary": label}, copy.deepcopy(record))
                self.assertNotIn("recovery_packet", state["resolution_request"])
        scratch = self.scratch()
        elsewhere = self.run / "elsewhere"
        state, _, _ = self.contained_capture(elsewhere, recorded=scratch)
        scratch.parent.mkdir()
        scratch.symlink_to(elsewhere)
        state["resolution_request"]["evidence_hashes"] = {
            str(scratch / Path(path).name) if Path(path).parent == elsewhere else path: digest
            for path, digest in state["resolution_request"]["evidence_hashes"].items()}
        with self.assertRaisesRegex(util.Paused, "symlink"):
            recovery.prepare_resolution(state, {"summary": "Symlinked scratch"}, copy.deepcopy(record))

    def known_correction(self, evidence):
        """A sealed Completion REWORK with an attested change, over a failed check captured in `evidence`."""
        self.state["iteration"] = 2
        self.state["settings"]["roles"] = {"terra": {"model": "builder"}, "sol": {"model": "reviewer"}}
        identity = {"task_id": "T1", "contract_hash": "h", "contract_revision": 1}
        change = {"hypothesis": "Wrong answer", "target": "app.py", "before": "return 2", "after": "return 3",
                  "expected_check": "python -m unittest test_app", "expected_result": "exit 0",
                  "question": "", "evidence_refs": [str(self.events)]}
        decision = {**identity, "status": "REWORK", "user_request": {"kind": "none"},
                    "next_objective": "Return the accepted answer", "recovery_change": change,
                    "next_task": {"kind": "implement"}}
        completion_events = self.run / "completion.jsonl"
        completion_events.write_text('{"type":"turn.completed"}\n')
        reported = self.run / "completion.reported.json"
        reported.write_text(json.dumps(decision))
        self.output.write_text(json.dumps(decision))
        record = {**identity, "stage": "astra_review", "source_revision": "s1", "exit_code": 0,
                  "output": str(self.output), "events": str(completion_events), "reported_output": str(reported)}
        accepted = {**identity, "stage": "sol", "role": "sol", "source_revision": "s1", "exit_code": 0,
                    "output": str(self.validator), "events": str(self.events)}
        report = {**identity, "verdict": "FAIL", "checks": self.state["validation"]["checks"]}
        self.validator.write_text(json.dumps(report))
        rework.capture(record, decision)
        rework.capture(accepted, report)
        evidence.mkdir(parents=True, exist_ok=True)
        raw = evidence / "executed-check.log"
        raw.write_text("AssertionError: 2 != 3\n")
        receipt = evidence / "executed-check.receipt.json"
        receipt.write_text(json.dumps({"full_output": str(raw)}))
        self.state["validation"] = {**report, "source_revision": "s1", "reviewer_role": "sol", "output": str(self.validator),
            "evidence_hashes": {str(path): util.file_hash(path) for path in (self.events, raw, receipt)}}
        self.state["stages"] = [{"stage": "terra", "role": "terra", "task_id": "T1"}, accepted]
        runtime = SimpleNamespace(lifecycle=SimpleNamespace(assign_task=Mock()), support=SimpleNamespace(
            snapshot=util.snapshot, verify_checks=Mock()), check_evidence_options=lambda _: {})
        retry = SimpleNamespace(enabled=lambda _: True, failure=Mock(return_value="retry"))
        paths = [self.output, completion_events, reported, self.validator, self.events, raw, receipt]
        return decision, record, raw, paths, runtime, retry

    def known_correction_prepared(self, decision, record, paths):
        state, current_record = copy.deepcopy(self.state), copy.deepcopy(record)
        state["resolution_request"] = {"source_revision": "s1", "evidence_hashes": {
            str(path): util.file_hash(path) for path in paths}}
        current_record.update(finished_at="2026-10-03T00:00:00Z", processes=[{"pid": 123, "birth_identity": 42}])
        state["active_stage"] = copy.deepcopy(current_record)
        recovery.prepare_resolution(state, decision, current_record)
        return state, current_record

    def test_known_correction_accepts_a_failed_check_its_contained_validator_captured(self):
        # Under tool containment the Validator's prompt says to capture into its own scratch (#419).
        scratch = self.scratch()
        decision, record, raw, paths, runtime, retry = self.known_correction(scratch)
        self.state["stages"][-1]["tool_containment"] = {"version": 1, "scratch": str(scratch)}
        state, current_record = self.known_correction_prepared(decision, record, paths)
        runtime.goals = SimpleNamespace(record_decision=Mock())
        runtime.dispatch = SimpleNamespace(build_stage=lambda _: "terra")
        with patch.object(recovery.processes, "recorded_worker_state", return_value={"checked": True, "alive": False}):
            self.assertTrue(recovery.route_known_change(runtime, state, decision, current_record,
                                                        run_dir=self.run, retry_policy=retry))
        self.assertEqual("known-correction", state["repair_plan"]["kind"])
        self.assertIn(str(raw), state["repair_plan"]["evidence_hashes"])

    def test_known_correction_is_held_only_by_a_repair_that_returned_a_result(self):
        # #422: the same change's earlier Builder dispatch, archived without a report, did not
        # try it, so it is routed again rather than through a paid diagnosis; a returned one holds.
        decision, record, _, paths, runtime, retry = self.known_correction(self.run)
        runtime.goals = SimpleNamespace(record_decision=Mock())
        runtime.dispatch = SimpleNamespace(build_stage=lambda _: "terra")
        state, current_record = self.known_correction_prepared(decision, record, paths)
        packet = recovery.load_packet(state["resolution_request"]["recovery_packet"], self.run)
        recovery.validate_decision(state, decision, current_record)
        earlier = {"stage": "terra", "role": "terra", "output": str(self.run / "earlier-repair.json"),
                   "recovery_novelty": {"dispatch_id": "earlier-repair", "action": "repair",
                                        "incident_ids": [novelty.Incident(**row).id for row in packet["incidents"]],
                                        "change_id": state["resolution_request"]["recovery_change_id"]}}
        for flags, routed in (({"timed_out": True, "accounted": True, "automatic_recovery": True,
                                "abandoned": True, "rejected": True}, True), ({"exit_code": 0}, False)):
            with self.subTest(flags=flags):
                trial, trial_record = copy.deepcopy(state), copy.deepcopy(current_record)
                trial["stages"].append({**earlier, **flags})
                with patch.object(recovery.processes, "recorded_worker_state", return_value={"checked": True, "alive": False}):
                    self.assertIs(routed, recovery.route_known_change(runtime, trial, decision, trial_record,
                                                                       run_dir=self.run, retry_policy=retry))

    def test_known_correction_cannot_repin_artifacts_changed_during_queue(self):
        decision, record, raw, paths, runtime, retry = self.known_correction(self.run)
        immutable = copy.deepcopy(record["rework_evidence"])
        saved = {path: path.read_bytes() for path in paths}
        for changed in paths:
            with self.subTest(changed=changed.name):
                changed.write_bytes(saved[changed] + b"\n")
                current_record = copy.deepcopy(record)
                state = copy.deepcopy(self.state)
                # Simulate an ineligible first-repair path queueing current hashes
                # after an edit. The original sealed reports/pins remain unchanged.
                state["resolution_request"] = {"source_revision": "s1", "evidence_hashes": {
                    str(path): util.file_hash(path) for path in paths}}
                recovery.prepare_resolution(state, decision, current_record)
                with self.assertRaises(util.Paused):
                    recovery.route_known_change(runtime, state, decision, current_record, run_dir=self.run, retry_policy=retry)
                runtime.lifecycle.assign_task.assert_not_called()
                retry.failure.assert_not_called()
                self.assertEqual(immutable, current_record["rework_evidence"])
                changed.write_bytes(saved[changed])
        for missing in paths:
            with self.subTest(missing=missing.name):
                state, current_record = copy.deepcopy(self.state), copy.deepcopy(record)
                state["resolution_request"] = {"source_revision": "s1", "evidence_hashes": {
                    str(path): util.file_hash(path) for path in paths}}
                recovery.prepare_resolution(state, decision, current_record)
                missing.unlink()
                with self.assertRaises(util.Paused) as caught:
                    recovery.route_known_change(runtime, state, decision, current_record, run_dir=self.run, retry_policy=retry)
                self.assertEqual("PAUSED_STALE_HANDOFF", caught.exception.status)
                runtime.lifecycle.assign_task.assert_not_called()
                retry.failure.assert_not_called()
                missing.write_bytes(saved[missing])

        def prepared():
            return self.known_correction_prepared(decision, record, paths)

        for worker in ({"checked": True, "alive": True}, {"checked": False, "alive": None}):
            state, current_record = prepared()
            active = copy.deepcopy(state["active_stage"])
            with patch.object(recovery.processes, "recorded_worker_state", return_value=worker):
                with self.assertRaisesRegex(util.Paused, "descendants"):
                    recovery.route_known_change(runtime, state, decision, current_record, run_dir=self.run, retry_policy=retry)
            self.assertEqual(active, state["active_stage"])
            runtime.lifecycle.assign_task.assert_not_called()
            retry.failure.assert_not_called()
        state, current_record = prepared()
        state["active_stage"]["output"] = "different-still-running-worker"
        with self.assertRaisesRegex(util.Paused, "active or uncertain"):
            recovery.route_known_change(runtime, state, decision, current_record, run_dir=self.run, retry_policy=retry)
        runtime.lifecycle.assign_task.assert_not_called()
        retry.failure.assert_not_called()

        for mutation in ("evidence", "source"):
            state, current_record = prepared()
            def preflight(candidate, *_):
                if mutation == "evidence":
                    raw.write_text("changed during preflight")
                else:
                    runtime.support.snapshot.return_value = {"revision": "s2", "files": {"app.py": "changed"}}
            runtime.lifecycle.assign_task.side_effect = preflight
            with patch.object(recovery.processes, "recorded_worker_state", return_value={"checked": True, "alive": False}):
                with self.assertRaises(util.Paused):
                    recovery.route_known_change(runtime, state, decision, current_record, run_dir=self.run, retry_policy=retry)
            self.assertEqual(1, runtime.lifecycle.assign_task.call_count)
            retry.failure.assert_not_called()
            self.assertEqual("T1", state["current_task"]["id"])
            runtime.lifecycle.assign_task.reset_mock(side_effect=True)
            runtime.support.snapshot.return_value = {"revision": "s1", "files": {"app.py": "hash"}}
            raw.write_bytes(saved[raw])
        state, current_record = prepared()
        runtime.goals = SimpleNamespace(record_decision=Mock())
        runtime.dispatch = SimpleNamespace(build_stage=lambda _: "terra")
        with patch.object(recovery.processes, "recorded_worker_state", return_value={"checked": True, "alive": False}):
            self.assertTrue(recovery.route_known_change(runtime, state, decision, current_record, run_dir=self.run, retry_policy=retry))
        self.assertEqual(2, runtime.lifecycle.assign_task.call_count)
        retry.failure.assert_called_once()
        self.assertEqual("known-correction", state["repair_plan"]["kind"])


class RecoveryNoveltyCLI(unittest.TestCase):
    """User-visible recurrence, explicit retry and independent corrected acceptance."""
    def setUp(self):
        from scenarios import run  # Establish the scenario harness import root.
        from harness import catalog
        results = Path(__file__).resolve().parents[1] / ".scenario-runs"
        results.mkdir(exist_ok=True)
        scratch = tempfile.TemporaryDirectory(prefix="novelty-cli-", dir=results)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.scenario = catalog.load("recovery-novelty")
        for name in ("config", "codex-home"):
            (self.root / name).mkdir()

    def driver(self, fault):
        from harness.driver import Driver, default_autocode, fake_setup
        from harness.project import materialize
        scenario = dataclasses.replace(self.scenario, fake_fault="recovery_novelty_" + fault)
        if fault == "nonpython":
            scenario = dataclasses.replace(scenario, dir=scenario.dir / "nonpython",
                                          brief=scenario.brief.replace("Repair greet.py", "Repair greet.js"))
        self.scenario = scenario
        self.project = materialize(scenario.seed, self.root / "project")
        flags, env = fake_setup(scenario, self.root, scenario.reference)
        if fault == "nonpython":
            flags += ["--builder-strong-model", "gpt-5.4", "--pin-model-role", "sol"]
        env.update(XDG_CONFIG_HOME=str(self.root / "config"), CODEX_HOME=str(self.root / "codex-home"),
                   AUTOCODE_PROVIDER="opencode")
        class RecoveryDriver(Driver):
            def call(self, *args, **kwargs):
                if self.run_dir:
                    initial = self.flags
                    self.flags = [value for index, value in enumerate(initial) if value != "--builder-strong-model"
                                  and not (index and initial[index - 1] == "--builder-strong-model")]
                return super().call(*args, **kwargs)
        return RecoveryDriver(self.project, self.root, [*flags, "--max-iterations", "6"], env,
                              autocode=default_autocode(), max_steps=30, timeout_seconds=240)

    def trace(self):
        return [json.loads(line) for line in (self.root / "rework-trace.jsonl").read_text().splitlines()]

    def test_same_ineffective_edit_holds_before_another_diagnosis_and_plain_resume(self):
        driver = self.driver("hold")
        view = driver.drive(self.scenario.brief)
        self.assertFalse(view["done"], view.get("status"))
        self.assertIn("No causal progress", view.get("stop_reason", ""))
        stages = [row["stage"] for row in self.trace()]
        self.assertEqual(["terra", "sol", "astra_review", "terra", "sol", "astra_review"], stages)
        source_hash = util.file_hash(self.project / "greet.py")
        driver.call("plain-resume", "--resume-paused")
        self.assertEqual(stages, [row["stage"] for row in self.trace()])
        self.assertEqual(source_hash, util.file_hash(self.project / "greet.py"))
        driver.call("authorized-retry", "--resume-paused", "--retry-failed-stage", "--pause-after-stage")
        self.assertEqual(stages + ["astra_resolve"], [row["stage"] for row in self.trace()])

    def test_discriminating_fix_executes_then_requires_fresh_independent_acceptance(self):
        driver = self.driver("fix")
        view = driver.drive(self.scenario.brief)
        self.assertTrue(view["done"], (view.get("status"), view.get("stop_reason"), [row["stage"] for row in self.trace()]))
        checks = self.scenario.oracle()(self.project, self.scenario)
        self.assertTrue(all(check.ok for check in checks), checks)
        self.assertEqual(["terra", "sol", "astra_review"] * 3, [row["stage"] for row in self.trace()])
        validators = [row for row in self.trace() if row["stage"] == "sol"]
        self.assertEqual([1, 1, 0], [row["exit_code"] for row in validators])
        self.assertEqual(3, len({row["task_id"] for row in validators}))
        self.assertEqual("PASS", view["evidence"]["check_replay"]["verdict"])
        self.assertEqual(validators[-1]["source_revision"], view["evidence"]["check_replay"]["source_revision"])

    def test_foreign_proof_does_not_buy_a_diagnosis_or_repair(self):
        driver = self.driver("bad_refs")
        view = driver.drive(self.scenario.brief)
        self.assertFalse(view["done"], view.get("status"))
        self.assertIn("pinned originals", view.get("stop_reason", ""))
        self.assertEqual(["terra", "sol", "astra_review"] * 2, [row["stage"] for row in self.trace()])

    def test_specific_new_experiment_may_use_one_bounded_diagnosis(self):
        driver = self.driver("question")
        view = driver.drive(self.scenario.brief)
        stages = [row["stage"] for row in self.trace()]
        self.assertTrue(view["done"], (view.get("status"), view.get("stop_reason"), stages))
        self.assertEqual(["terra", "sol", "astra_review"] * 2 + ["astra_resolve", "terra", "sol", "astra_review"], stages)
        self.assertEqual("PASS", view["evidence"]["check_replay"]["verdict"])

    def test_resolver_repair_narrowed_to_the_failed_criterion_reaches_the_builder(self):
        # #423: the failed task owns C1 and C2; the Resolver's repair keeps only C2.
        driver = self.driver("narrow")
        view = driver.drive(self.scenario.brief)
        trace = self.trace()
        stages = [row["stage"] for row in trace]
        self.assertTrue(view["done"], (view.get("status"), view.get("stop_reason"), stages))
        self.assertEqual(["terra", "sol", "astra_review"] * 2 + ["astra_resolve", "terra", "sol", "astra_review"], stages)
        # The first Builder owned both criteria; the Builder after the Resolver owned only C2.
        self.assertEqual([["C1", "C2"], ["C2"]], [row["task_criteria"] for row in (trace[0], trace[7])])
        self.assertTrue(all(check.ok for check in self.scenario.oracle()(self.project, self.scenario)))

    @unittest.skipUnless(shutil.which("node"), "JavaScript public control requires Node")
    def test_nonpython_unproven_novelty_supports_scoped_user_retries_not_a_dead_end(self):
        driver = self.driver("nonpython")
        try:
            view = driver.drive(self.scenario.brief)
        except Exception as error:
            details = [(step["kind"], step["stdout_tail"], step["stderr_tail"]) for step in driver.steps[-3:]]
            self.fail((str(error), details))
        self.assertFalse(view["done"])
        self.assertIn("unsupported grammar", view.get("stop_reason", ""))
        original = ["terra", "sol", "astra_review"] * 2
        self.assertEqual(original, [row["stage"] for row in self.trace()])
        driver.call("authorize-diagnosis", "--resume-paused", "--retry-failed-stage", "--pause-after-stage")
        self.assertEqual(original + ["astra_resolve"], [row["stage"] for row in self.trace()])
        # Resume the saved configured escalation, rather than reapplying the
        # harness's initial model flags and legitimately invalidating its pins.
        flags = driver.flags
        driver.flags = [value for index, value in enumerate(flags) if not value.endswith("-model")
                        and not (index and flags[index - 1].endswith("-model"))]
        ordinary = driver.call("ordinary-resume", "--resume-paused")
        self.assertEqual(original + ["astra_resolve"], [row["stage"] for row in self.trace()])
        authorized = driver.call("authorize-builder", "--resume-paused", "--retry-failed-stage")
        view = driver.until_stopped()
        self.assertTrue(view["done"], (view.get("status"), view.get("stop_reason"), ordinary.stdout[-1200:],
                                       ordinary.stderr[-1200:], authorized.stdout[-1200:], authorized.stderr[-1200:]))
        self.assertEqual(original + ["astra_resolve", "terra", "sol", "astra_review"], [row["stage"] for row in self.trace()])
        self.assertTrue(all(row.ok for row in self.scenario.oracle()(self.project, self.scenario)))

    def test_oracle_rejects_broken_reference_controls(self):
        from scenarios import run
        rows = run.self_test(self.scenario)
        self.assertTrue(all(ok for _, ok, _ in rows), rows)


if __name__ == "__main__":
    unittest.main()
