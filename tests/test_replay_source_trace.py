"""Recorded check scope relocates authenticated review evidence, never authority."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_replay_source_trace as binding
import autocode_source_trace as trace


class ReplaySourceTraceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        task = "Build a greeting CLI."
        body = {
            "acceptance_criteria": [
                {"id": "C1", "criterion": task, "sources": [{"source_id": "task:0", "quote": task}]}
            ]
        }
        report = {"source_trace": {"out_of_scope": [], "negation_waivers": []}}
        self.report_bytes = json.dumps(report).encode()
        self.events_bytes = b'{"type":"thread.started","thread_id":"original-session"}\n'
        output, events = self.root / "review.json", self.root / "review.jsonl"
        output.write_bytes(self.report_bytes)
        events.write_text('{"type":"thread.started","thread_id":"fresh-session"}\n')
        record = {
            "stage": "astra_challenge",
            "output": str(output),
            "events": str(events),
            "exit_code": 0,
            "replay": {"dispatch_id": "review", "ordinal": 0, "manifest_sha256": "a" * 64},
        }
        self.state = {"task": task, "stages": [record], "goal_contract": {"body": body}}
        body = trace.reviewed(self.state, body, report["source_trace"], record, binding.obligations.sources(self.state))
        self.state["goal_contract"]["body"] = body
        original_body = copy.deepcopy(body)
        original_body["source_trace"]["review"].update(
            output="/unavailable/original/review.json",
            events="/unavailable/original/review.jsonl",
            events_sha256=hashlib.sha256(self.events_bytes).hexdigest(),
        )
        self.context = {"contract_body": original_body, "task": {"objective": task}, "case_ids": ["B1"]}
        self.payloads = {"review-output": self.report_bytes, "review-events": self.events_bytes}
        artifacts = {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "redacted": False}
            for name, data in self.payloads.items()
        }
        self.tape = SimpleNamespace(
            sha256="a" * 64,
            document={
                "artifacts": artifacts,
                "dispatches": [
                    {
                        "id": "review",
                        "ordinal": 0,
                        "stage": "astra_challenge",
                        "exit_code": 0,
                        "events": "review-events",
                        "report": "review-output",
                    },
                    {"id": "check", "ordinal": 1, "stage": "sol"},
                ],
            },
            artifact=self.artifact,
        )

    def artifact(self, identity):
        data = self.payloads[identity]
        if hashlib.sha256(data).hexdigest() != self.tape.document["artifacts"][identity]["sha256"]:
            raise ValueError("Archived bytes changed")
        return data

    def rebind(self):
        return binding.rebind_context(self.tape, self.state, self.context, "check")

    def test_same_review_relocates_without_opening_original_paths_or_mutating_scope(self):
        original = copy.deepcopy(self.context)
        state = copy.deepcopy(self.state)
        result = self.rebind()
        self.assertEqual(self.state["goal_contract"]["body"], result["contract_body"])
        self.assertEqual(original["task"], result["task"])
        self.assertEqual(original["case_ids"], result["case_ids"])
        self.assertEqual(original, self.context)
        self.assertEqual(state, self.state)

    def test_foreign_missing_ambiguous_late_or_invalid_occurrence_is_refused(self):
        original = copy.deepcopy(self.state)
        original_context = copy.deepcopy(self.context)
        for stage in ([], {}, None, 17):
            with self.subTest(stage=stage):
                self.context = copy.deepcopy(original_context)
                self.context["contract_body"]["source_trace"]["review"]["stage"] = stage
                with self.assertRaises(ValueError):
                    self.rebind()
        self.context = original_context
        changes = [
            {"manifest_sha256": "b" * 64},
            {"dispatch_id": "foreign"},
            {"ordinal": 1},
            {"ordinal": False},
        ]
        for changed in changes:
            with self.subTest(changed=changed):
                self.state = copy.deepcopy(original)
                self.state["stages"][0]["replay"].update(changed)
                with self.assertRaises(ValueError):
                    self.rebind()
        for records in ([], [original["stages"][0]] * 2):
            with self.subTest(records=len(records)):
                self.state = copy.deepcopy(original)
                self.state["stages"] = records
                with self.assertRaises(ValueError):
                    self.rebind()
        self.state = copy.deepcopy(original)
        self.tape.document["dispatches"][1]["ordinal"] = 0
        with self.assertRaises(ValueError):
            self.rebind()

    def test_original_pins_redaction_and_archival_bytes_are_reauthenticated(self):
        for identity in self.payloads:
            with self.subTest(identity=identity):
                artifact = self.tape.document["artifacts"][identity]
                artifact["redacted"] = True
                with self.assertRaises(ValueError):
                    self.rebind()
                artifact["redacted"] = False
                artifact["original_sha256"] = "f" * 64
                with self.assertRaises(ValueError):
                    self.rebind()
                del artifact["original_sha256"]
                self.payloads[identity] += b"changed"
                with self.assertRaises(ValueError):
                    self.rebind()
                self.payloads[identity] = self.payloads[identity][:-7]

    def test_report_only_review_requires_the_declared_original_stage(self):
        row = self.tape.document["dispatches"][0]
        row.update(stage="astra_challenge_report_repair", report_only=True, original_stage="astra_challenge")
        self.state["stages"][0].update(
            stage="astra_challenge_report_repair", report_only=True, original_stage="astra_challenge"
        )
        self.assertEqual(self.state["goal_contract"]["body"], self.rebind()["contract_body"])
        row["original_stage"] = "astra_finalize"
        with self.assertRaises(ValueError):
            self.rebind()

    def test_fresh_review_pin_or_trace_changes_are_refused(self):
        original = copy.deepcopy(self.state)
        for field in ("events_sha256", "output_sha256", "trace_sha256"):
            with self.subTest(field=field):
                self.state = copy.deepcopy(original)
                self.state["goal_contract"]["body"]["source_trace"]["review"][field] = "f" * 64
                with self.assertRaises(ValueError):
                    self.rebind()
        self.state = copy.deepcopy(original)
        for field in ("output", "events"):
            path = Path(self.state["goal_contract"]["body"]["source_trace"]["review"][field])
            raw = path.read_bytes()
            path.write_bytes(raw + b"changed")
            with self.assertRaises(ValueError):
                self.rebind()
            path.write_bytes(raw)

    def test_repinning_an_unrelated_archival_judgment_cannot_replace_the_review(self):
        data = json.dumps(
            {
                "source_trace": {
                    "out_of_scope": [{"source_id": "task:0", "quote": "Build a greeting CLI.", "reason": "Dropped"}],
                    "negation_waivers": [],
                }
            }
        ).encode()
        self.payloads["review-output"] = data
        self.tape.document["artifacts"]["review-output"]["sha256"] = hashlib.sha256(data).hexdigest()
        self.context["contract_body"]["source_trace"]["review"]["output_sha256"] = hashlib.sha256(data).hexdigest()
        with self.assertRaises(ValueError):
            self.rebind()

    def test_legacy_context_stays_exact_and_semantic_changes_are_not_normalized(self):
        legacy = {"contract_body": None, "task": {}, "case_ids": []}
        self.assertEqual(legacy, binding.rebind_context(self.tape, {}, legacy, "unknown"))
        self.context["contract_body"]["constraints"] = ["A different obligation"]
        result = self.rebind()
        self.assertEqual(["A different obligation"], result["contract_body"]["constraints"])
        self.assertNotEqual(self.state["goal_contract"]["body"], result["contract_body"])


if __name__ == "__main__":
    unittest.main()
