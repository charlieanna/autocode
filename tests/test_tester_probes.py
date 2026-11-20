"""A Tester PASS needs a probe of its own or a recorded probe_reason (#3937, #3923, #3922, #3902, #3900).

Saved-report tests, no model call: the runner (autopilot.apply_review_result) and `autocode report-check`
refuse a PASS criterion that cites only approved required commands, route the refusal to the existing
bounded Tester rerun or report-only repair, and record which cited checks were the Tester's own.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import shlex
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import autocode_repair_triage as repair_triage
import autocode_report_check as report_check
import autocode_report_repair_queue as report_repair_queue
import autocode_report_schema as report_schema
import autocode_resolve_repair_job as resolve_repair_job
import autocode_support as support
import autocode_util as util
import autocode_workflow as workflow
import autopilot

from . import test_autocode as base

APPROVED = "python3 -m unittest tests.test_store"
PROBE = "python3 probe_store.py"
PYTEST = 'python3 -m pytest -k "store and not slow" tests/test_store.py'
PYTEST_ARGV = ["python3", "-m", "pytest", "-k", "store and not slow", "tests/test_store.py"]
PROBE_ARGV = ["python3", "probe_store.py"]
REASON = "Documentation only: README.md names the --strict flag; no input can vary"
MISSING = "cites only approved required commands with neither a Tester probe nor a probe_reason"
SCHEMA_DIR = Path(support.__file__).resolve().parent / "autocode-schemas"
NONE_REQUEST = {
    "kind": "none",
    "discovered": "",
    "impact": "",
    "decision_needed": "",
    "options": [],
    "proposed_delta": "",
}


def check(command, ref, code=0):
    return {"command": command, "exit_code": code, "evidence_ref": ref}


def row(cid, *refs, status="PASS", **extra):
    return {"id": cid, "status": status, "evidence_refs": list(refs), **extra}


def make_tester_report(checks, rows, *, verdict="PASS", findings=()):
    """A Tester report valid against the Tester stage's generation schema for the fixture state."""
    return {
        "verdict": verdict,
        "checks_run": [c["command"] or c["evidence_ref"] for c in checks],
        "findings": list(findings),
        "unverified_criteria": [r["id"] for r in rows if r["status"] != "PASS"],
        "checks": checks,
        "criterion_results": rows,
        "contract_revision": 1,
        "contract_hash": "approved",
        "task_id": "task",
        "user_request": dict(NONE_REQUEST),
        "deferred_backlog": [],
        "end_to_end_result": {"status": "NOT_VERIFIED", "summary": "Later milestones remain", "evidence_refs": []},
    }


def ac2_report():
    return make_tester_report([check(APPROVED, "event:item_1")], [row("C1", "check:1")])


def ac3_checks():
    return [check(APPROVED, "event:item_1"), check(PROBE, "event:item_2")]


def ac4_report():
    return make_tester_report(ac3_checks(), [row("C1", "check:1", "check:2"), row("C2", status="NOT_VERIFIED")])


def ac5_report(reason=REASON):
    return make_tester_report([check(APPROVED, "event:item_1")], [row("C1", "check:1", probe_reason=reason)])


def event_copy(report):
    """The form a Tester without capture receipts writes: exact commands, bare event:, unbound exits."""
    result = copy.deepcopy(report)
    result["checks"] = [check(c["command"], "event:", None) for c in result["checks"]]
    return result


class TesterProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test"]
            + ["commit", "--allow-empty", "-qm", "fixture"],
            check=True,
        )
        self.run = self.root / ".autocode" / "runs" / "fixture"
        self.run.mkdir(parents=True)
        self.state = self.make_state([APPROVED])

    def make_state(self, validation_plan):
        criteria = [
            {"id": cid, "criterion": f"Store keeps record {cid}", "status": "unverified", "evidence": ""}
            for cid in ("C1", "C2")
        ]
        body = {
            "acceptance_criteria": [
                {
                    "id": cid,
                    "criterion": f"Store keeps record {cid}",
                    "verification_method": "Validator reads the store",
                    "human_review": False,
                }
                for cid in ("C1", "C2")
            ],
            "milestones": [{"id": "M1", "affected_paths": ["store.py"], "acceptance_criteria": ["C1", "C2"]}],
        }
        return {
            "version": 3,
            "workspace": str(self.root),
            "task": "Keep the store",
            "iteration": 1,
            "status": "RUNNING",
            "next_stage": "tester",
            "goal_contract": {"hash": "approved", "revision": 1, "body": body, "approval_status": "approved"},
            "current_task": {
                "id": "task",
                "contract_hash": "approved",
                "contract_revision": 1,
                "kind": "implement",
                "milestone_id": "M1",
                "acceptance_criteria": ["C1", "C2"],
                "validation_plan": list(validation_plan),
                "affected_paths": ["store.py"],
            },
            "acceptance_criteria": criteria,
            "criteria_revision": util.digest(util.criteria_definition(criteria)),
            "stages": [],
            "history": [],
            "settings": {
                "report_repair": {"max_attempts": 1},
                "roles": {
                    "builder": {"model": "model-builder", "reasoning_effort": "high"},
                    "tester": {"model": "model-tester", "reasoning_effort": "high"},
                    "task_planner": {"model": "model-planner", "reasoning_effort": "high"},
                },
            },
            "sessions": {},
        }

    def record(self, rows=None, report=None):
        """A completed Tester stage record whose events hold the given command executions."""
        if rows is None:
            rows = [("item_1", APPROVED, 0), ("item_2", PROBE, 0)]
        directory = self.run / "iterations" / "001"
        directory.mkdir(parents=True, exist_ok=True)
        events = directory / "tester-01.jsonl"
        events.write_text(
            "".join(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"id": ident, "type": "command_execution", "command": command, "exit_code": code},
                    }
                )
                + "\n"
                for ident, command, code in rows
            )
        )
        output = directory / "tester-01.json"
        output.write_text(json.dumps(report or {}))
        for suffix in (".before.json", ".after.json", ".schema.json"):
            Path(str(directory / "tester-01") + suffix).write_text("{}")
        return {
            "role": "tester",
            "stage": "tester",
            "iteration": 1,
            "exit_code": 0,
            "duration_seconds": 1,
            "source_revision": support.snapshot(self.root)["revision"],
            "events": str(events),
            "output": str(output),
            "before_ref": str(directory / "tester-01.before.json"),
            "after_ref": str(directory / "tester-01.after.json"),
            "schema": str(directory / "tester-01.schema.json"),
        }

    def admit(self, report, record=None):
        """Admit a Tester report. The clean-copy replay of its checks is its own gate (tests.test_check_replay)
        and runs nothing here: this fixture has no project to replay."""
        record = record or self.record()
        with patch.object(autopilot.check_replay, "replay", return_value=None):
            autopilot.apply_review_result(
                base.runner, self.state, "tester", copy.deepcopy(report), record, self.root, self.run
            )
        return record

    def rejection(self, report, record=None):
        before = copy.deepcopy(self.state)
        with self.assertRaises(ValueError) as raised:
            self.admit(report, record)
        self.assertNotIn("validation", self.state)
        self.assertEqual(before.get("validation"), self.state.get("validation"))
        return raised.exception

    def stage_schema(self):
        role = support.read(SCHEMA_DIR / "v2" / "sol-report.schema.json")
        return report_schema.review_generation_schema(
            base.runner.goals.role_schema(role, "tester"), self.state, "tester"
        )

    def spec_path(self):
        spec = report_check.build_spec(
            stage="tester", workspace=self.root, schema=self.stage_schema(), state=self.state
        )
        path = self.run / "tester-spec.json"
        path.write_text(json.dumps(spec))
        return path

    def report_check(self, report, spec_path=None):
        """`autocode report-check --spec SPEC -` reading the report on standard input."""
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(report))), redirect_stdout(out), redirect_stderr(err):
            code = report_check.cli(["--spec", str(spec_path or self.spec_path()), "-"])
        return code, out.getvalue(), err.getvalue()

    def receipt(self, name, argv):
        """A genuine capture receipt, printed by an executed capture command in the Tester's events."""
        evidence = self.root / ".autocode" / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        log = evidence / (name + ".log")
        log.write_text("ok\n")
        receipt = {
            "command": argv,
            "exit_code": 0,
            "full_output": str(log),
            "full_output_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
        }
        path = evidence / (name + ".json")
        path.write_text(json.dumps(receipt))
        return ".autocode/evidence/" + name + ".json", {
            "type": "tool_use",
            "sessionID": "s",
            "part": {
                "id": "capture-" + name,
                "sessionID": "s",
                "messageID": "m",
                "tool": "shell",
                "state": {
                    "status": "completed",
                    "input": {
                        "command": f"autocode capture --output .autocode/evidence/{name}.json -- {shlex.join(argv)}"
                    },
                    "metadata": {"exit": 0},
                    "output": json.dumps(receipt) + "\n",
                },
            },
        }

    def receipt_record(self, *receipts):
        record = self.record(rows=[])
        Path(record["events"]).write_text("".join(json.dumps(event) + "\n" for _, event in receipts))
        return record

    def test_ac1_tester_prompt_requires_probes_along_generic_axes(self):
        text = " ".join(support.STABLE["tester"].split())
        for phrase in (
            "probe of its own",
            "documented subclasses",
            "public constructor",
            "character set and encoding",
            "size limits",
            "combinations of failures",
            "technical approach",
            "finds a defect",
            "probe_reason",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        for case in ("SemVerWithVPrefix", "_csv.Error", "131,072", "stockroom", "#3937"):
            with self.subTest(case=case):
                self.assertNotIn(case, text)

    def test_ac2_pass_citing_only_approved_commands_is_sent_back(self):
        record = self.record(rows=[("item_1", APPROVED, 0), ("item_9", "python3 explore.py", 1)])
        error = self.rejection(ac2_report(), record)
        self.assertIsInstance(error, util.ExecutionEvidenceRequired)
        self.assertIn("C1", str(error))
        self.assertIn(MISSING, str(error))
        self.assertNotIn("C2", str(error))
        self.assertEqual("missing_execution", repair_triage.classify_rejection(self.state, record, error))

    def test_ac3_uncited_listed_probe_goes_to_report_repair(self):
        report = make_tester_report(ac3_checks(), [row("C1", "check:1")])
        record = self.record()
        error = self.rejection(report, record)
        self.assertNotIsInstance(error, util.ExecutionEvidenceRequired)
        self.assertIn("C1", str(error))
        self.assertIn("check:2", str(error))
        self.assertEqual("report_defect", repair_triage.classify_rejection(self.state, record, error))

    def test_ac4_cited_tester_probe_is_accepted_and_recorded(self):
        self.admit(ac4_report())
        self.assertEqual(
            {"C1": {"tester_checks": [PROBE], "approved_checks": [APPROVED], "reason": ""}},
            self.state["validation"]["tester_probes"],
        )

    def test_ac5_recorded_reason_is_accepted_instead_of_probe(self):
        util.validate_schema(ac5_report(), self.stage_schema())
        self.admit(ac5_report())
        self.assertEqual(
            {"tester_checks": [], "approved_checks": [APPROVED], "reason": REASON},
            self.state["validation"]["tester_probes"]["C1"],
        )
        self.state.pop("validation")
        error = self.rejection(ac5_report("   "))
        self.assertIsInstance(error, util.ExecutionEvidenceRequired)
        self.assertIn("C1", str(error))
        self.assertIn(MISSING, str(error))

    def test_ac6_report_check_reports_missing_tester_probe(self):
        spec = self.spec_path()
        code, _out, err = self.report_check(ac2_report(), spec)
        self.assertEqual(1, code)
        runner_error = self.rejection(ac2_report())
        self.assertIn(str(runner_error), err)
        for name, report in (("AC4", ac4_report()), ("AC5", ac5_report())):
            with self.subTest(report=name):
                code, out, err = self.report_check(report, spec)
                self.assertEqual((0, ""), (code, err))
                self.assertIn("OK (tester)", out)
        code, out, err = self.report_check(event_copy(ac4_report()), spec)
        self.assertEqual((0, ""), (code, err))
        self.assertIn("OK (tester)", out)
        code, _out, err = self.report_check(event_copy(ac2_report()), spec)
        self.assertEqual(1, code)
        self.assertIn("C1", err)
        self.assertIn(MISSING, err)

    def test_ac15_respelled_approved_command_is_not_a_probe(self):
        self.state = self.make_state([PYTEST])
        approved_ref, approved_event = self.receipt("pytest", PYTEST_ARGV)
        probe_ref, probe_event = self.receipt("probe", PROBE_ARGV)
        # (a) the approved command as a receipt, its command null.
        receipt_only = make_tester_report([check(None, approved_ref, None)], [row("C1", "check:1")])
        error = self.rejection(receipt_only, self.receipt_record((approved_ref, approved_event)))
        self.assertIsInstance(error, util.ExecutionEvidenceRequired)
        self.assertIn("C1", str(error))
        self.assertIn(MISSING, str(error))
        # (b) the approved command typed, bound to the runner's wrapped spelling of it.
        wrapped = f"cd {shlex.quote(str(self.root))} && {PYTEST} 2>&1"
        typed = make_tester_report([check(PYTEST, "event:item_1")], [row("C1", "check:1")])
        error = self.rejection(typed, self.record(rows=[("item_1", wrapped, 0)]))
        self.assertIsInstance(error, util.ExecutionEvidenceRequired)
        self.assertIn("C1", str(error))
        self.assertIn(MISSING, str(error))
        # (c) the same receipt and a probe receipt, both cited.
        both = make_tester_report(
            [check(None, approved_ref, None), check(None, probe_ref, None)], [row("C1", "check:1", "check:2")]
        )
        self.admit(both, self.receipt_record((approved_ref, approved_event), (probe_ref, probe_event)))
        self.assertEqual([PROBE], self.state["validation"]["tester_probes"]["C1"]["tester_checks"])
        spec = self.spec_path()
        code, _out, err = self.report_check(receipt_only, spec)
        self.assertEqual(1, code)
        self.assertIn("C1", err)
        code, out, err = self.report_check(both, spec)
        self.assertEqual((0, ""), (code, err))
        self.assertIn("OK (tester)", out)

    def test_ac16_partial_probe_coverage_reruns_tester(self):
        report = make_tester_report(ac3_checks(), [row("C1", "check:1"), row("C2", "check:1", "check:2")])
        record = self.record()
        error = self.rejection(report, record)
        self.assertIsInstance(error, util.ExecutionEvidenceRequired)
        self.assertIn("C1", str(error))
        self.assertNotIn("C2", str(error))
        self.assertEqual("missing_execution", repair_triage.classify_rejection(self.state, record, error))
        state = copy.deepcopy(self.state)
        self.assertTrue(resolve_repair_job.queue_missing_execution(state, record, error))
        code, _out, err = self.report_check(report)
        self.assertEqual(1, code)
        self.assertIn("C1", err)
        self.assertNotIn("C2", err)

        self.state["resolve_repair_history"] = [
            {
                "kind": "missing_execution",
                "stage": "tester",
                "source_revision": record["source_revision"],
                "contract_hash": "approved",
            }
        ]
        error = self.rejection(report, record)
        self.assertNotIsInstance(error, util.ExecutionEvidenceRequired)
        self.assertIn("C1", str(error))
        self.assertNotIn("C2", str(error))
        self.assertEqual("report_defect", repair_triage.classify_rejection(self.state, record, error))
        self.assertFalse(resolve_repair_job.queue_missing_execution(copy.deepcopy(self.state), record, error))
        self.assertTrue(report_repair_queue.queue(self.state, record, error, completed=lambda: True))

    def test_ac17_fail_verdict_report_is_admitted_without_probe_rejection(self):
        finding = {
            "id": "",
            "severity": "high",
            "finding": "An empty store file raises a traceback",
            "evidence": "check:1",
            "blocking": True,
            "reproduction_steps": ["printf '' > store.json", "python3 store.py list"],
            "expected": "exit 2 with a store error",
            "actual": "traceback, exit 1",
            "why_it_matters": "A damaged store hides every other error",
            "suggested_correction": "Load the store inside the error handler",
        }
        report = make_tester_report(
            [check(APPROVED, "event:item_1")], [row("C1", "check:1")], verdict="FAIL", findings=[finding]
        )
        self.admit(report)
        self.assertEqual("FAIL", self.state["validation"]["verdict"])
        code, out, err = self.report_check(report)
        self.assertEqual((0, ""), (code, err))
        self.assertIn("OK (tester)", out)

    def test_ac20_probe_reason_offered_only_in_tester_stage_schema(self):
        item = self.stage_schema()["properties"]["criterion_results"]["items"]
        self.assertEqual({"type": "string"}, item["properties"]["probe_reason"])
        self.assertNotIn("probe_reason", item["required"])
        implementation = workflow.implementation_schema(SCHEMA_DIR)
        assessment = implementation["properties"]["continuation"]["properties"]["self_assessment"]
        self.assertNotIn("probe_reason", assessment["properties"]["criterion_results"]["items"]["properties"])
        checkpoint = report_schema.review_generation_schema(
            workflow.checkpoint_schema(SCHEMA_DIR), self.state, "review_checkpoint"
        )
        validation = checkpoint["properties"]["validation"]
        self.assertNotIn("probe_reason", validation["properties"]["criterion_results"]["items"]["properties"])

    def test_a_combined_review_checkpoint_validation_is_not_gated(self):
        # review_checkpoint applies its validation as "tester", but its schema offers no probe_reason.
        record = {**self.record(), "stage": "review_checkpoint"}
        self.admit(ac2_report(), record)
        self.assertEqual("PASS", self.state["validation"]["verdict"])
        self.assertNotIn("tester_probes", self.state["validation"])

    def test_a_saved_run_without_a_contract_rerun_goes_to_report_repair(self):
        self.state["version"] = 2  # queue_missing_execution never reruns such a run
        error = self.rejection(ac2_report())
        self.assertNotIsInstance(error, util.ExecutionEvidenceRequired)
        self.assertIn(MISSING, str(error))

    def test_a_shell_wrapped_approved_command_is_not_a_probe(self):
        wrapped = "sh -c " + shlex.quote(APPROVED)
        report = make_tester_report([check(wrapped, "event:item_1")], [row("C1", "check:1")])
        self.assertIsInstance(
            self.rejection(report, self.record(rows=[("item_1", wrapped, 0)])), util.ExecutionEvidenceRequired
        )

    def test_report_check_skips_an_older_spec_and_never_counts_an_unreadable_receipt(self):
        spec = report_check.build_spec(
            stage="tester", workspace=self.root, schema=self.stage_schema(), state=self.state
        )
        older = {key: value for key, value in spec.items() if key != "approved_commands"}
        self.assertEqual([], report_check.check_report(json.dumps(ac2_report()), spec=older))
        missing = make_tester_report(
            [check(APPROVED, "event:"), check(None, ".autocode/evidence/missing.json", None)],
            [row("C1", "check:1", "check:2")],
        )
        errors = report_check.check_report(json.dumps(missing), spec=spec)
        self.assertTrue(any(MISSING in error for error in errors), errors)

    def test_a_tester_spec_whose_approved_commands_cannot_be_read_names_none(self):
        with patch.object(report_check.verification_plan, "approved_commands", side_effect=ValueError("no checklist")):
            spec = report_check.build_spec(
                stage="tester", workspace=self.root, schema=self.stage_schema(), state=self.state
            )
        self.assertEqual([], spec["approved_commands"])


if __name__ == "__main__":
    unittest.main()
