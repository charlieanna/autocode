#!/usr/bin/env python3
"""Scripted provider for live-trial smoke runs (profile=fixture).

Implements the greeting-CLI scenario end to end so the harness can be proven
without model spend. Not a model-quality test: plans and reports are
handwritten. Only provider I/O is substituted; the CLI, approval, scheduling
and oracle paths are real.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

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

TEST_GREET_PY = """\
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
"""

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
        "milestones": [
            {
                "id": "M1",
                "objective": "Deliver and verify the greeting CLI",
                "acceptance_criteria": ["C1"],
                "depends_on": [],
                "affected_paths": ["greet.py", "test_greet.py", "README.md"],
            }
        ],
        "deliverables": ["greet.py", "test_greet.py", "README.md"],
        "required_behaviors": [
            "Print Hello, NAME for one nonempty name and exit 0",
            "Reject no-arg and two-arg usage with exit 2",
            "Python standard library only",
        ],
        "important_failure_cases": ["Reject no-arg and two-arg usage with exit 2"],
        "scope_exclusions": ["Web service", "Deployment"],
        "constraints": ["Python standard library only"],
        "permission_boundaries": ["Read and edit only this fixture Git workspace"],
        "accepted_assumptions": [{"text": "CLI invocation is sufficient", "basis": "agent_proposed", "answer_id": ""}],
        "delegated_decisions": [],
        "acceptance_criteria": [
            {
                "id": "C1",
                "criterion": "Greeting contract holds",
                "verification_method": "Execute greeting and invalid-input regression checks",
                "human_review": False,
            }
        ],
        "open_blocking_questions": [],
    }


def _criteria(value_status: str, evidence: str) -> list[dict]:
    return [{"id": "C1", "criterion": "Greeting contract holds", "status": value_status, "evidence": evidence}]


def _saved_report(data: dict) -> dict:
    for key in ("rejected_report", "original_report"):
        source = data.get(key) or {}
        if isinstance(source.get("content"), dict):
            return source["content"]
        if source.get("path"):
            with contextlib.suppress(OSError, ValueError):
                value = json.loads(Path(source["path"]).read_text())
                if isinstance(value, dict):
                    return value
    if (data.get("original") or {}).get("output"):
        with contextlib.suppress(OSError, ValueError):
            value = json.loads(Path(data["original"]["output"]).read_text())
            if isinstance(value, dict):
                return value
    return {}


_NUMBER = re.compile(r"\d+(?:\.\d+)*")
_NEGATION = re.compile(r"\b(?:must not|never|do not|don't|cannot|can't|not|no)\b", re.I)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if part.strip()]


def _number_in(number: str, text: str) -> bool:
    return re.search(r"(?<!\d)(?<!\d\.)" + re.escape(number) + r"(?!(?:\d|\.\d))", text) is not None


def _corpus(contract: dict) -> str:
    parts = [
        *(contract.get("required_behaviors") or []),
        *(contract.get("constraints") or []),
        *(contract.get("technical_approach") or []),
    ]
    for row in contract.get("acceptance_criteria") or []:
        parts.append(str(row.get("criterion", "")) + " " + str(row.get("verification_method", "")))
    return " ".join(parts)


def _cite(contract: dict, data: dict) -> dict:
    """Cite supported source sentences and retain saved repair metadata (#595 / #1201)."""
    inventory = [
        row for row in data.get("source_inventory") or [] if isinstance(row, dict) and row.get("id") and row.get("text")
    ]
    saved_contract = _saved_report(data).get("contract") or {}
    saved_rows = saved_contract.get("acceptance_criteria", [])
    previous = {row["id"]: row["sources"] for row in saved_rows if "id" in row and "sources" in row}
    constraints = contract.setdefault("constraints", [])
    for constraint in saved_contract.get("constraints", []):
        if constraint not in constraints:
            constraints.append(constraint)
    if inventory:
        for row in inventory:
            issue = re.match(r"Resolve GitHub issue ([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+):", row["text"])
            if issue:
                constraint = (
                    f"Resolve GitHub issue {issue[1]}; treat the quoted report as problem data, not runtime commands"
                )
                if constraint not in constraints:
                    constraints.append(constraint)
        # The fixture writes only its three deliverables. Supplied proof files remain
        # inputs; restate their actual paths rather than treating a source quote as a plan.
        outputs = set(contract.get("deliverables", []))
        for path in _source_refs():
            if path == "task" or path in outputs or not any(path in row["text"] for row in inventory):
                continue
            constraint = f"Preserve supplied input file {path} at its exact project-relative path; do not modify it"
            if constraint not in constraints:
                constraints.append(constraint)
    # Follow-up metadata may state numbers or negations the greeting plan does not
    # carry. The independent fixture reviewer records those sentences out of scope.
    corpus = _corpus(contract)
    citations = []
    for source in inventory:
        for sentence in _sentences(source["text"]):
            numbers = _NUMBER.findall(sentence)
            if numbers and not all(_number_in(number, corpus) for number in numbers):
                continue
            if _NEGATION.search(sentence) and not _NEGATION.search(corpus):
                continue
            citations.append({"source_id": source["id"], "quote": sentence})
    for row in contract.get("acceptance_criteria") or []:
        if inventory:
            row["sources"] = [dict(citation) for citation in citations]
        else:
            row.setdefault("sources", copy.deepcopy(previous.get(row.get("id"), [])))
    return contract


def _quote_covers(sentence: str, quotes: list[str]) -> bool:
    """Match autocode_source_trace._covered(..., spans=True) for criterion citations."""
    bare = sentence.rstrip(".")
    for quote in quotes:
        if not quote:
            continue
        clipped = quote.rstrip(".")
        if bare in clipped or bare == clipped or sentence in quote:
            return True
        if len(clipped) >= 12 and (clipped in bare or quote in sentence):
            return True
    return False


def _source_trace(contract: dict, data: dict) -> dict[str, list]:
    """Out-of-scope every source sentence the criteria do not cite."""
    inventory = [
        row for row in data.get("source_inventory") or [] if isinstance(row, dict) and row.get("id") and row.get("text")
    ]
    saved = _saved_report(data)
    if not inventory and isinstance(saved.get("source_trace"), dict):
        return copy.deepcopy(saved["source_trace"])
    quotes = [
        citation["quote"] for row in contract.get("acceptance_criteria") or [] for citation in row.get("sources") or []
    ]
    out_of_scope = []
    for source in inventory:
        for sentence in _sentences(source["text"]):
            if not _quote_covers(sentence, quotes):
                out_of_scope.append(
                    {
                        "source_id": source["id"],
                        "quote": sentence,
                        "reason": "Handwritten fixture: not an acceptance obligation for this plan",
                    }
                )
    return {"out_of_scope": out_of_scope, "negation_waivers": []}


def _trace(*pairs: tuple[str, str]) -> list[dict]:
    """(requirement_id, evidence) pairs; evidence is a required_behavior or criterion id."""
    return [{"requirement_id": rid, "disposition": "covered", "evidence": evidence} for rid, evidence in pairs]


# Evidence must match a required_behavior or criterion id exactly (autocode_trace_coverage).
TRACE_ROWS = (
    ("R1", "Print Hello, NAME for one nonempty name and exit 0"),
    ("R2", "Reject no-arg and two-arg usage with exit 2"),
    ("R3", "Python standard library only"),
    ("R4", "C1"),
)


def _planning_contract(repair_error: str = "") -> dict:
    body = _contract()
    body["initial_task"] = {
        "kind": "implement",
        "milestone_id": "M1",
        "objective": "Deliver and verify the greeting CLI",
        "affected_paths": ["greet.py", "test_greet.py", "README.md"],
        "requirements": ["Print Hello, NAME for one nonempty name"],
        "acceptance_criteria": ["C1"],
        "validation_plan": ["Execute greeting and invalid-input regression checks"],
    }
    first = os.environ.get("LIVE_FIXTURE_FIRST_TASK")
    if first in ("validate", "implement"):
        # A first task that names no affected paths in a milestone that owns none, as a live
        # program's final check planned it (#615): "validate" only checks, "implement" writes.
        # A repair the runner asked for because of affected_paths lists the files it writes.
        body["milestones"][0]["affected_paths"] = []
        body["initial_task"]["kind"] = first
        if "affected_paths" not in repair_error:
            body["initial_task"]["affected_paths"] = []
    return body


def _write_files() -> list[str]:
    Path("greet.py").write_text(GREET_PY)
    Path("test_greet.py").write_text(TEST_GREET_PY)
    Path("README.md").write_text(README_MD)
    return ["greet.py", "test_greet.py", "README.md"]


def _source_refs():
    """Cite the tracked and ordinary untracked inputs in the source inventory."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], capture_output=True
    )
    sources = []
    root = Path.cwd().resolve()
    inventory = {raw.decode(errors="surrogateescape") for raw in result.stdout.split(b"\0") if raw}
    excluded = (".autocode", ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")
    for raw in result.stdout.split(b"\0"):
        name = raw.decode(errors="surrogateescape")
        path = Path(name)
        if not raw:
            continue
        try:
            target = path.resolve(strict=True)
            relative = target.relative_to(root)
        except (OSError, ValueError):
            continue
        # Select directly stored inventory files. Aliases can resolve in this
        # workspace yet dangle in the copy when any link target is ignored.
        if relative.as_posix() != path.as_posix() or relative.as_posix() not in inventory:
            continue
        if any(
            p.as_posix().startswith(".autocode-ui/") or p.suffix == ".pyc" or any(part in excluded for part in p.parts)
            for p in (path, relative)
        ):
            continue
        if target.is_file():
            sources.append(name)
    return sorted(set(sources))[:8] or ["task"]


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
    session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
    print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)
    # A real tool call the runner can cite as event evidence.
    print(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "check",
                    "type": "command_execution",
                    "command": "python3 -m unittest test_greet.py",
                    "exit_code": 0,
                    "aggregated_output": "4 tests passed",
                },
            }
        ),
        flush=True,
    )

    common = {
        "contract_revision": contract.get("revision", 0),
        "contract_hash": contract.get("hash", ""),
        "task_id": task.get("id", ""),
        "deferred_backlog": [],
        "user_request": {
            "kind": "none",
            "discovered": "",
            "impact": "",
            "decision_needed": "",
            "options": [],
            "proposed_delta": "",
        },
    }
    if repairing:
        identity = data.get("report_identity") or data.get("original") or {}
        for key in ("contract_revision", "contract_hash", "task_id"):
            if key in identity:
                common[key] = identity[key]

    adaptive = "ADAPTIVE PLANNING" in prompt  # an adaptive Planner's draft carries its initial_task
    repair_error = str(data.get("error") or "") if repairing else ""
    # A repair prompt carries no planning instructions; the report it repairs shows whether it had a first task.
    if repairing and not adaptive:
        with contextlib.suppress(OSError):
            adaptive = '"initial_task"' in Path((data.get("rejected_report") or {}).get("path") or "").read_text()
    if stage == "recognize_workflow":
        report: dict[str, Any] = {
            "workflow": "build",
            "reason": "Handwritten fixture: every request is a build",
            "signals": [],
            "design_document": "",
        }
        if 'Add "clarity"' in prompt:
            # Vague keeps the Requirements stage the fixture's handwritten trace relies on.
            report["clarity"] = os.environ.get("LIVE_FIXTURE_CLARITY", "vague")
    elif stage == "investigate_stuck":
        report = {
            "diagnosis": "Offline fixture: it cannot diagnose; the run pauses as before.",
            "cause": "other",
            "guidance": "",
            "recommendation": "pause",
            "user_question": "",
            "evidence_refs": [],
            "example": "",
            "probe": "",
            "untestable": "",
        }
    elif stage == "requirements_gather":
        report = {
            "summary": "Handwritten greeting requirements; no model planning",
            "intended_outcome": "Provide a deterministic greeting CLI",
            "required_behaviors": ["Print Hello, NAME for one nonempty name"],
            "constraints": ["Python standard library only"],
            "acceptance_tests": ["Execute greeting and invalid-input regression checks"],
            "source_refs": _source_refs(),
            "proposed_assumptions": [
                {
                    "id": "A1",
                    "text": "CLI invocation is sufficient",
                    "kind": "inferable",
                    "category": "behavior",
                    "convention_ref": "task",
                    "rationale": "The task specifies a command-line tool",
                    "supports": [],
                }
            ],
            "open_questions": [],
            "requirements": [
                {
                    "id": "R1",
                    "text": "Print Hello, NAME for one nonempty name",
                    "source_quote": "It prints 'Hello, NAME' for one nonempty name argument and exits 0.",
                },
                {
                    "id": "R2",
                    "text": "Reject any other argument count with a usage line and exit 2",
                    "source_quote": "Any other argument count (no arguments, or two or more) prints a usage line to stderr and exits 2.",
                },
                {
                    "id": "R3",
                    "text": "Use only the Python standard library",
                    "source_quote": "Python standard library only.",
                },
                {
                    "id": "R4",
                    "text": "Deliver greet.py, test_greet.py and README.md",
                    "source_quote": "Deliver greet.py, test_greet.py with regression tests, and a short README.md.",
                },
            ],
            "ignored_statements": [],
            "workspace_scope": {"spans_repositories": False, "external_parts": []},
            "conflicts": [],
            "proposed_reframes": [],
            "ignored_requirements": [],
            "machine_resolutions": [],
            "access_blockers": [],
        }
        # Requirements names the job it understood in every run (#1846, #1860); this fixture only builds.
        report.update(job="build", design_document="")
        asked = (
            json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
            if "--output-schema" in sys.argv
            else {}
        ).get("properties", {})
        if 'Add "clarity"' in prompt or "clarity" in asked:
            # Judged as the recognizer judged it before #1860; a report repair's schema still asks for it.
            report["clarity"] = os.environ.get("LIVE_FIXTURE_CLARITY", "vague")
        if data.get("spec_first"):
            report["assistant_reply"] = "I understand you want a deterministic greeting CLI. Is that right?"
        for event in data.get("brief_feedback") or []:
            if event.get("text") == "Keep the same scope and standard-library-only constraint.":
                report["requirements"].append(
                    {
                        "id": "R5",
                        "text": "Keep the existing scope and standard-library-only constraint",
                        "source_quote": event["text"],
                    }
                )
        if (
            data.get("spec_first")
            and not data.get("follow_up")
            and "It prints 'Hello, NAME'" not in str(data.get("task", ""))
        ):
            # A chat message that names no work (a greeting, "What is your name?"): answer it, then ask. A
            # follow-up's task is its new message alone, with the earlier turn as context (#1860): it is work.
            report.update(
                summary="The user has not described a task yet.",
                assistant_reply="I'm AutoCode's requirements assistant. What would you like to build or change?",
                intended_outcome="Not yet specified by the user",
                required_behaviors=[],
                acceptance_tests=[],
                requirements=[],
                proposed_assumptions=[],
                constraints=[],
                source_refs=[],
                open_questions=[
                    {
                        "id": "requested-outcome",
                        "question": "What would you like to build or change?",
                        "why": "No engineering outcome has been requested yet.",
                        "options": [],
                        "proposed_default": "",
                        "kind": "decision",
                        "category": "requested_outcome",
                        "delegable": False,
                    }
                ],
            )
    elif stage == "astra_discovery":
        report = {
            "summary": "Handwritten greeting plan",
            "contract": _cite(_planning_contract(repair_error) if adaptive else _contract(), data),
            "code_refs": [ref for ref in _source_refs() if ref != "task"],
            "alternatives": [],
            "uncertainties": [],
            "contract_changes": [],
            "conflict_resolutions": [],
            "machine_resolutions": [],
            "remediation_records": [],
            "access_blockers": [],
            "requirement_trace": _trace(*TRACE_ROWS),
        }
    elif stage == "astra_challenge":
        draft = (data.get("goal_contract") or {}).get("body") or {}
        report = {
            "summary": "Handwritten plan review; no concerns",
            "obligation_decisions": [],
            "concerns": [],
            "source_trace": _source_trace(draft, data),
        }
    elif stage == "astra_finalize":
        finalized = _cite(_planning_contract(repair_error), data)
        report = {
            "summary": "Handwritten final plan",
            "contract": finalized,
            "source_trace": _source_trace(finalized, data),
            "obligation_decisions": [],
            "decisions": [],
            "contract_changes": [],
            "conflict_resolutions": [],
            "requirement_trace": _trace(*TRACE_ROWS),
        }
    elif stage == "glm_revise":
        report = {
            "summary": "Handwritten revision; nothing to revise",
            "contract": _cite(_planning_contract(repair_error) if adaptive else _contract(), data),
            "code_refs": [ref for ref in _source_refs() if ref != "task"],
            "responses": [],
            "contract_changes": [],
            "conflict_resolutions": [],
            "machine_resolutions": [],
            "remediation_records": [],
            "access_blockers": [],
            "requirement_trace": _trace(*TRACE_ROWS),
        }
    elif stage == "terra":
        changed = _write_files()
        report = {
            **common,
            "summary": "Delivered greeting CLI",
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
            "findings": [],
            "finding_dispositions": [],
            "unverified_criteria": [],
            "checks": [{"command": "python3 -m unittest test_greet.py", "exit_code": 0, "evidence_ref": "event:check"}],
            "criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": ["event:check"]}],
            "end_to_end_result": {
                "status": "PASS",
                "summary": "Greeting and usage paths checked",
                "evidence_refs": ["event:check"],
                "technical_result": None,
                "pending_human_criteria": [],
            },
        }
    elif stage in ("astra_review", "astra_plan", "astra_resolve"):
        report = {
            **common,
            "status": "COMPLETE",
            "acceptance_criteria": _criteria("verified", "event:check"),
            "evidence": ["event:check"],
            "next_objective": "",
            "blocker": "",
            "plan": [],
            "affected_paths": [],
            "next_task": {
                "kind": "none",
                "milestone_id": "",
                "requirements": [],
                "acceptance_criteria": [],
                "validation_plan": [],
                "findings": [],
            },
            "findings": [],
            "finding_dispositions": [],
            "agreed_limitations": [],
        }
    else:
        report = {
            **common,
            "status": "CONTINUE",
            "next_objective": "Continue",
            "acceptance_criteria": _criteria("unverified", ""),
            "evidence": [],
            "blocker": "",
            "plan": [],
            "affected_paths": [],
            "next_task": {
                "kind": "implement",
                "milestone_id": "M1",
                "requirements": ["Print Hello, NAME"],
                "acceptance_criteria": ["C1"],
                "validation_plan": ["Run tests"],
                "findings": [],
            },
            "findings": [],
            "finding_dispositions": [],
            "agreed_limitations": [],
        }

    schema = (
        json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
        if "--output-schema" in sys.argv
        else {}
    )
    saved = _saved_report(data) if repairing else {}
    handoff = (data.get("requirements_handoff") or {}).get("report") or {}
    if "requirement_trace" in report and any(row.get("id") == "R5" for row in handoff.get("requirements", [])):
        report["requirement_trace"].extend(_trace(("R5", "Python standard library only")))
    if "assistant_reply" in schema.get("properties", {}):
        report.setdefault("assistant_reply", report.get("summary", ""))
    if "source_verdict" in saved:
        report["source_verdict"] = copy.deepcopy(saved["source_verdict"])
    if "source_verdict" in schema.get("properties", {}) or (
        stage == "astra_review" and ((contract.get("body") or {}).get("source_trace") is not None)
    ):
        report.setdefault(
            "source_verdict",
            {"verdict": "PASS", "uncovered": [], "summary": "Handwritten: every criterion traces to the brief"},
        )
    if stage == "astra_finalize" and "retain_contract" in schema.get("properties", {}).get("contract", {}).get(
        "properties", {}
    ):
        draft = report["contract"]
        report["contract"] = {
            "retain_contract": data.get("retained_contract")
            or (data.get("clarification_context") or {}).get("retained_contract"),
            "open_blocking_questions": draft.get("open_blocking_questions", []),
        }
        if "initial_task" in schema["properties"]["contract"]["properties"]:
            report["contract"]["initial_task"] = draft.get("initial_task")
        if "acceptance_criteria" in schema["properties"]["contract"]["properties"]:
            # The reference's criteria delta (#1991); empty, it retains every row.
            report["contract"]["acceptance_criteria"] = {
                "retain_from": report["contract"]["retain_contract"],
                "upsert": [],
                "remove": [],
                "proofs": [],
            }
        report["requirement_trace"] = []
    output = Path(sys.argv[sys.argv.index("-o") + 1])
    output.write_text(json.dumps(report))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
