#!/usr/bin/env python3
"""Scripted provider for live-trial smoke runs (profile=fixture).

Implements the greeting-CLI scenario end to end so the harness can be proven
without model spend. Not a model-quality test: plans and reports are
handwritten. Only provider I/O is substituted; the CLI, approval, scheduling
and oracle paths are real.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

GREET_PY = '''\
"""Deterministic greeting CLI."""
import sys

USAGE = "usage: greet.py NAME"


def greet(name: str) -> str:
    return f"Hello, {name}"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not argv[0]:
        print(USAGE, file=sys.stderr)
        return 2
    print(greet(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

TEST_GREET_PY = '''\
import subprocess
import sys
import unittest

from greet import greet


class TestGreet(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(greet("Ada"), "Hello, Ada")

    def test_no_arg(self):
        proc = subprocess.run([sys.executable, "greet.py"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr.lower())

    def test_two_arg(self):
        proc = subprocess.run([sys.executable, "greet.py", "Ada", "Lovelace"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)

    def test_unicode(self):
        self.assertEqual(greet("Zoë"), "Hello, Zoë")


if __name__ == "__main__":
    unittest.main()
'''

README_MD = """# Greeting CLI

Deterministic greeting: `greet.py NAME` prints `Hello, NAME` and exits 0.
Any other argument count prints usage to stderr and exits 2.

Python standard library only.
"""


def _contract() -> dict:
    return {
        "intended_outcome": "Provide a deterministic greeting CLI",
        "intended_user": "A local developer",
        "end_to_end_flow": ["Run greet.py with one name", "Read Hello, NAME or a usage error"],
        "technical_approach": ["A standard-library Python CLI using sys.argv"],
        "milestones": [{
            "id": "M1", "objective": "Deliver and verify the greeting CLI",
            "acceptance_criteria": ["C1"], "depends_on": [],
            "affected_paths": ["greet.py", "test_greet.py", "README.md"],
        }],
        "deliverables": ["greet.py", "test_greet.py", "README.md"],
        "required_behaviors": [
            "Print Hello, NAME for one nonempty name",
            "Reject no-arg and two-arg usage with exit 2",
            "Python standard library only",
        ],
        "important_failure_cases": ["Reject no-arg and two-arg usage with exit 2"],
        "scope_exclusions": ["Web service", "Deployment"],
        "constraints": ["Python standard library only"],
        "permission_boundaries": ["Read and edit only this fixture Git workspace"],
        "accepted_assumptions": [{"text": "CLI invocation is sufficient",
                                  "basis": "agent_proposed", "answer_id": ""}],
        "delegated_decisions": [],
        "acceptance_criteria": [{
            "id": "C1", "criterion": "Greeting contract holds",
            "verification_method": "Execute greeting and invalid-input regression checks",
            "human_review": False,
        }],
        "open_blocking_questions": [],
    }


def _criteria(value_status: str, evidence: str) -> list[dict]:
    return [{"id": "C1", "criterion": "Greeting contract holds",
             "status": value_status, "evidence": evidence}]


def _trace(*pairs: tuple[str, str]) -> list[dict]:
    """(requirement_id, evidence) pairs; evidence is a required_behavior or criterion id."""
    return [{"requirement_id": rid, "disposition": "covered", "evidence": evidence}
            for rid, evidence in pairs]


TRACE_ROWS = (
    ("R1", "Print Hello, NAME for one nonempty name"),
    ("R2", "Reject no-arg and two-arg usage with exit 2"),
    ("R3", "Python standard library only"),
    ("R4", "C1"),
)


def _planning_contract() -> dict:
    body = _contract()
    body["initial_task"] = {
        "kind": "implement", "milestone_id": "M1",
        "objective": "Deliver and verify the greeting CLI",
        "affected_paths": ["greet.py", "test_greet.py", "README.md"],
        "requirements": ["Print Hello, NAME for one nonempty name"],
        "acceptance_criteria": ["C1"],
        "validation_plan": ["Execute greeting and invalid-input regression checks"],
    }
    return body


def _write_files() -> list[str]:
    Path("greet.py").write_text(GREET_PY)
    Path("test_greet.py").write_text(TEST_GREET_PY)
    Path("README.md").write_text(README_MD)
    return ["greet.py", "test_greet.py", "README.md"]


def main() -> int:
    if sys.argv[1:] == ["login", "status"]:
        print("Logged in using ChatGPT (offline live-trial fixture)")
        return 0
    prompt = sys.stdin.read()
    if "CURRENT HANDOFF DATA\n" not in prompt:
        print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}), flush=True)
        print(json.dumps({"error": "no handoff data"}))
        return 0
    data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
    stage = data.get("stage") or data.get("original", {}).get("stage") or ""
    repairing = bool(data.get("report_repair"))
    if repairing:
        stage = data.get("original", {}).get("stage", stage)
    # Branch on the owning stage, not the *_report_repair routing name.
    task = data.get("current_task") or {}
    contract = data.get("goal_contract") or {"revision": 0, "hash": ""}
    session = (sys.argv[sys.argv.index("resume") + 1]
               if "resume" in sys.argv else str(uuid.uuid4()))
    print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)
    # A real tool call the runner can cite as event evidence.
    print(json.dumps({"type": "item.completed", "item": {
        "id": "check", "type": "command_execution",
        "command": "python3 -m unittest test_greet.py",
        "exit_code": 0, "aggregated_output": "4 tests passed",
    }}), flush=True)

    common = {
        "contract_revision": contract.get("revision", 0),
        "contract_hash": contract.get("hash", ""),
        "task_id": task.get("id", ""),
        "deferred_backlog": [],
        "user_request": {"kind": "none", "discovered": "", "impact": "",
                         "decision_needed": "", "options": [], "proposed_delta": ""},
    }

    if stage == "requirements_gather":
        report = {
            "summary": "Handwritten greeting requirements; no model planning",
            "intended_outcome": "Provide a deterministic greeting CLI",
            "required_behaviors": ["Print Hello, NAME for one nonempty name"],
            "constraints": ["Python standard library only"],
            "acceptance_tests": ["Execute greeting and invalid-input regression checks"],
            "source_refs": ["task"],
            "proposed_assumptions": ["CLI invocation is sufficient"],
            "open_questions": [],
            "requirements": [
                {"id": "R1",
                 "text": "Print Hello, NAME for one nonempty name",
                 "source_quote": "It prints 'Hello, NAME' for one nonempty name argument and exits 0."},
                {"id": "R2",
                 "text": "Reject any other argument count with a usage line and exit 2",
                 "source_quote": "Any other argument count (no arguments, or two or more) prints a usage line to stderr and exits 2."},
                {"id": "R3",
                 "text": "Use only the Python standard library",
                 "source_quote": "Python standard library only."},
                {"id": "R4",
                 "text": "Deliver greet.py, test_greet.py and README.md",
                 "source_quote": "Deliver greet.py, test_greet.py with regression tests, and a short README.md."},
            ],
            "ignored_statements": [],
            "conflicts": [],
            "proposed_reframes": [],
        }
    elif stage == "astra_discovery":
        report = {
            "summary": "Handwritten greeting plan",
            "contract": _contract(),
            "code_refs": [], "alternatives": [], "uncertainties": [],
            "contract_changes": [], "conflict_resolutions": [],
            "requirement_trace": _trace(*TRACE_ROWS),
        }
    elif stage == "astra_challenge":
        report = {"summary": "Handwritten plan review; no concerns", "concerns": []}
    elif stage == "astra_finalize":
        report = {
            "summary": "Handwritten final plan",
            "contract": _planning_contract(),
            "decisions": [], "contract_changes": [],
            "conflict_resolutions": [],
            "requirement_trace": _trace(*TRACE_ROWS),
        }
    elif stage == "glm_revise":
        report = {
            "summary": "Handwritten revision; nothing to revise",
            "contract": _contract(),
            "code_refs": [], "responses": [], "contract_changes": [],
            "conflict_resolutions": [],
            "requirement_trace": _trace(*TRACE_ROWS),
        }
    elif stage == "terra":
        changed = _write_files()
        report = {
            **common, "summary": "Delivered greeting CLI",
            "changed_files": changed,
            "commands_run": ["python3 -m unittest test_greet.py"],
            "results": ["4 tests passed"],
            "remaining_risks": [],
            "evidence_refs": ["event:check"],
            "addressed_requirements": ["C1"],
            "untested_behavior": [],
            "recommended_checks": ["Execute greeting and invalid-input regression checks"],
        }
    elif stage in ("sol", "astra_checkpoint"):
        report = {
            **common,
            "verdict": "PASS",
            "checks_run": ["python3 -m unittest test_greet.py"],
            "findings": [], "finding_dispositions": [], "unverified_criteria": [],
            "checks": [{"command": "python3 -m unittest test_greet.py",
                        "exit_code": 0, "evidence_ref": "event:check"}],
            "criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": ["event:check"]}],
            "end_to_end_result": {"status": "PASS",
                                  "summary": "Greeting and usage paths checked",
                                  "evidence_refs": ["event:check"]},
        }
    elif stage in ("astra_review", "astra_plan", "astra_resolve"):
        report = {
            **common, "status": "COMPLETE",
            "acceptance_criteria": _criteria("verified", "event:check"),
            "evidence": ["event:check"], "next_objective": "", "blocker": "",
            "plan": [], "affected_paths": [],
            "next_task": {"kind": "none", "milestone_id": "", "requirements": [],
                          "acceptance_criteria": [], "validation_plan": [], "findings": []},
            "findings": [], "finding_dispositions": [], "agreed_limitations": [],
        }
    else:
        report = {**common, "status": "CONTINUE", "next_objective": "Continue",
                  "acceptance_criteria": _criteria("unverified", ""),
                  "evidence": [], "blocker": "", "plan": [], "affected_paths": [],
                  "next_task": {"kind": "implement", "milestone_id": "M1",
                                "requirements": ["Print Hello, NAME"],
                                "acceptance_criteria": ["C1"],
                                "validation_plan": ["Run tests"], "findings": []},
                  "findings": [], "finding_dispositions": [], "agreed_limitations": []}

    output = Path(sys.argv[sys.argv.index("-o") + 1])
    output.write_text(json.dumps(report))
    print(json.dumps({"type": "turn.completed",
                      "usage": {"input_tokens": 10, "output_tokens": 10}}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
