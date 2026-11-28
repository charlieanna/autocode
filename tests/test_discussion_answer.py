"""A discussion completes only on an answer to what was asked, never on a restatement of it (#4237).

A read-only review reached TASK_COMPLETE after citing seven source files and returning a restatement of the
request: the report was schema-valid and every citation was real, so nothing tied the answer to the outcome
the person asked for. The Requirements acceptance gate (autocode_requirements_handoff.accept) now holds a
discussion it would answer itself to ``findings`` that answer each requirement; report-check and a rejection's
fault list give the same answer (autocode_requirements_faults).
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_discussion_answer as discussion_answer
import autocode_report_check as report_check
import autocode_requirements_answer as requirements_answer
import autocode_requirements_faults as requirements_faults
import autocode_requirements_handoff as handoff

TASK = (
    "Read-only review: determine whether the DK worker stages nameserver writes "
    "or only pulses domain status for reconciliation."
)
OUTCOME = "Determine whether the DK worker stages nameserver writes or only pulses domain status"
SOURCES = [
    "app/worker.py",
    "app/nameservers.py",
    "app/status.py",
    "app/reconcile.py",
    "app/queue.py",
    "tests/test_worker.py",
    "tests/test_reconcile.py",
]
REQUIREMENTS = [
    {
        "id": "R1",
        "text": "Determine whether the DK worker stages nameserver writes or only pulses domain status.",
        "source_quote": TASK,
    },
    {"id": "R2", "text": "Keep the review read-only.", "source_quote": "Read-only review"},
]
FINDING = (
    "The DK worker only pulses domain status: handle() sets PENDING_RECONCILE and never calls "
    "NameserverWriter.stage, which only the API handler invokes."
)
NEGATIVE = (
    "No path in app/worker.py or app/reconcile.py calls NameserverWriter.stage; app/nameservers.py has one caller."
)
GAP = (
    "Not settled: app/reconcile.py hands reconciliation to the external RegAgent service, so whether "
    "RegAgent later writes nameservers cannot be read from this repository."
)
SETTLE = "The RegAgent source or a reconcile log showing the EPP commands it sends."


def row(kind, statement, evidence=(), *, ids=("R1", "R2"), would_settle=""):
    return {
        "requirement_ids": list(ids),
        "kind": kind,
        "statement": statement,
        "evidence": list(evidence),
        "would_settle": would_settle,
    }


def report(**change):
    """A well-formed, cited discussion report: seven source files read, nothing to write."""
    value = {
        "summary": "Review whether the DK worker stages nameserver writes.",
        "intended_outcome": OUTCOME,
        "required_behaviors": [],
        "constraints": ["Read-only"],
        "acceptance_tests": [],
        "source_refs": [*SOURCES, "task"],
        "proposed_assumptions": [],
        "open_questions": [],
        "requirements": copy.deepcopy(REQUIREMENTS),
        "ignored_statements": [],
        "conflicts": [],
        "proposed_reframes": [],
        "job": "discuss",
        "assistant_reply": (
            "You want to know whether the DK worker stages nameserver writes or only pulses domain status."
        ),
        "findings": [],
    }
    value.update(change)
    return value


def restated():
    """The #4237 answer: cited, schema-valid, and only the request said back."""
    return report(findings=[row("finding", TASK, ["app/worker.py"])])


def answered():
    return report(findings=[row("finding", FINDING, ["app/worker.py:40-52"])])


def run_state(workspace, *, kind=None, answers=None):
    return {
        "version": 3,
        "task_id": "discussion-answer",
        "task": TASK,
        "workspace": str(workspace),
        "settings": {},
        "workflow": {"kind": kind, "then": "draft_plan"},
        "stages": [],
        "answers": dict(answers or {}),
        "user_events": [],
    }


def problems(value):
    return discussion_answer.problems(
        value,
        request_texts=[TASK],
        local_refs=requirements_faults.cited_source_refs(value["source_refs"], {"task"}),
    )


class AcceptanceGateTests(unittest.TestCase):
    """The fixture the issue asks for, through the runner's one acceptance path."""

    def accept(self, value, *, kind=None):
        with tempfile.TemporaryDirectory() as temporary:
            state = run_state(Path(temporary), kind=kind)
            handoff.accept(state, value, {"output": "req.json"}, check_code_refs=mock.Mock(), queue=mock.Mock())
            return state

    def assert_not_completed(self, value):
        with tempfile.TemporaryDirectory() as temporary:
            state = run_state(Path(temporary))
            with self.assertRaises(ValueError) as caught:
                handoff.accept(state, value, {"output": "req.json"}, check_code_refs=mock.Mock(), queue=mock.Mock())
        self.assertNotEqual("TASK_COMPLETE", state.get("status"))
        self.assertNotIn("answer", state)
        return str(caught.exception)

    def test_cited_restatement_does_not_complete_the_discussion(self):
        error = self.assert_not_completed(restated())
        self.assertIn("restates the request", error)

    def test_cited_discussion_with_no_findings_does_not_complete(self):
        error = self.assert_not_completed(report())
        self.assertIn("findings is empty", error)

    def test_framed_restatement_does_not_complete(self):
        framed = "You asked whether the DK worker stages nameserver writes or only pulses domain status."
        error = self.assert_not_completed(report(findings=[row("finding", framed, ["app/worker.py"])]))
        self.assertIn("restates the request", error)

    def test_substantive_answer_completes_with_its_findings_in_the_answer(self):
        state = self.accept(answered())
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertIn(FINDING, state["answer"]["answer"])
        self.assertIn(
            {"claim": FINDING, "source": "app/worker.py:40-52", "example": "", "probe": ""}, state["answer"]["evidence"]
        )
        self.assertIn(FINDING, requirements_answer.render(state))

    def test_supported_negative_finding_completes(self):
        state = self.accept(report(findings=[row("negative_finding", NEGATIVE, ["app/worker.py", "app/reconcile.py"])]))
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertIn("Not found (R1, R2): " + NEGATIVE, state["answer"]["answer"])

    def test_explicit_evidence_gap_completes(self):
        state = self.accept(report(findings=[row("evidence_gap", GAP, would_settle=SETTLE)]))
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertIn(GAP, state["answer"]["answer"])
        self.assertIn(SETTLE, state["answer"]["answer"])

    def test_a_pinned_discuss_job_is_held_to_the_same_answer(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = run_state(Path(temporary), kind="discuss")
            with self.assertRaises(ValueError):
                handoff.accept(state, restated(), {"output": "o"}, check_code_refs=mock.Mock(), queue=mock.Mock())
        self.assertNotEqual("TASK_COMPLETE", state.get("status"))


class FindingProblemsTests(unittest.TestCase):
    def test_substantive_finding_has_no_problems(self):
        self.assertEqual([], problems(answered()))

    def test_unsupported_negative_finding_is_refused(self):
        for evidence in ([], ["task"], ["https://example.com/ticket"], ["app/other.py"]):
            with self.subTest(evidence=evidence):
                found = problems(report(findings=[row("negative_finding", NEGATIVE, evidence)]))
                self.assertTrue(any("evidence" in item for item in found), found)

    def test_evidence_gap_needs_what_would_settle_it(self):
        found = problems(report(findings=[row("evidence_gap", GAP)]))
        self.assertTrue(any("would_settle" in item for item in found), found)

    def test_every_requirement_needs_an_answering_row(self):
        found = problems(report(findings=[row("finding", FINDING, ["app/worker.py"], ids=["R1"])]))
        self.assertTrue(any("R2" in item for item in found), found)

    def test_unknown_requirement_id_is_refused(self):
        found = problems(report(findings=[row("finding", FINDING, ["app/worker.py"], ids=["R1", "R2", "R9"])]))
        self.assertTrue(any("R9" in item for item in found), found)

    def test_copied_outcome_and_process_framing_are_restatements(self):
        for statement in (
            OUTCOME,
            "Review: determine whether the DK worker stages nameserver writes.",
            "This review checks whether the DK worker stages nameserver writes or only pulses domain status.",
        ):
            with self.subTest(statement=statement):
                found = problems(report(findings=[row("finding", statement, ["app/worker.py"])]))
                self.assertTrue(any("restates the request" in item for item in found), found)

    def test_a_restatement_in_other_inflections_is_still_a_restatement(self):
        for statement in (
            "Explain whether the DK worker staged nameserver writes or only pulsed domain status.",
            "Determining whether the DK worker is staging nameserver writes or only pulsing domain status.",
        ):
            with self.subTest(statement=statement):
                found = problems(report(findings=[row("finding", statement, ["app/worker.py"])]))
                self.assertTrue(any("restates the request" in item for item in found), found)

    def test_a_direct_answer_in_the_request_words_is_not_a_restatement(self):
        for statement in (
            "The DK worker does not stage nameserver writes; it only pulses domain status.",
            "The DK worker never staged nameserver writes; it only pulsed domain status.",
        ):
            with self.subTest(statement=statement):
                self.assertEqual([], problems(report(findings=[row("finding", statement, ["app/worker.py"])])))


class ScopeTests(unittest.TestCase):
    """Only a discussion Requirements would answer itself is held to findings."""

    def test_due_only_for_a_discussion_answered_by_requirements(self):
        cases = {
            "conversational discuss": (report(), None, {}, True),
            "pinned discuss": (report(), "discuss", {}, True),
            "build job": (report(job="build"), None, {}, False),
            "pinned build": (report(), "build", {}, False),
            "written note": (report(required_behaviors=["Write notes/dk.md"]), None, {}, False),
            "unanswered question": (report(open_questions=[{"id": "Q1"}]), None, {}, False),
            "answered question": (report(open_questions=[{"id": "Q1"}]), None, {"Q1": {}}, True),
        }
        for name, (value, kind, answers, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(expected, requirements_answer.answers_itself(value, known_kind=kind, answered=answers))

    def test_run_without_a_workflow_record_is_not_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = run_state(Path(temporary))
            del state["workflow"]
            self.assertFalse(
                any(
                    "findings" in item
                    for item in requirements_faults.faults(restated(), requirements_faults.context(state))
                )
            )


class ReportCheckParityTests(unittest.TestCase):
    """report-check and a rejection's fault list read the one answer admission gives."""

    def test_faults_and_report_check_name_the_restatement(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = run_state(Path(temporary))
            faults = requirements_faults.faults(restated(), requirements_faults.context(state))
            spec = report_check.build_spec(
                stage="requirements_gather", workspace=Path(temporary), schema={}, state=state
            )
            errors = report_check.check_report(json.dumps(restated()), spec=spec)
            clean = report_check.check_report(json.dumps(answered()), spec=spec)
        self.assertTrue(any("restates the request" in item for item in faults), faults)
        self.assertTrue(any("restates the request" in item for item in errors), errors)
        self.assertFalse(any("findings" in item for item in clean), clean)


class SchemaTests(unittest.TestCase):
    def test_requirements_report_schema_carries_findings(self):
        from units import autoplanner

        properties = autoplanner.SCHEMAS["requirements_gather"]["properties"]
        self.assertEqual(discussion_answer.SCHEMA, properties[discussion_answer.FIELD])


if __name__ == "__main__":
    unittest.main()
