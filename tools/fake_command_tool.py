#!/usr/bin/env python3
"""Offline config-tool fixture; never calls a model.

By default it writes the report file Autocode names. With --events it prints
OpenCode-format JSON events instead, as ``kilo run --format json`` does, and
reuses the session passed with --session.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from goal_fixtures import body

_NUMBER = re.compile(r"\d+(?:\.\d+)*")
_NEGATION = re.compile(r"\b(?:must not|never|do not|don't|cannot|can't|not|no)\b", re.I)
SOURCE_VERDICT = {
    "verdict": "PASS",
    "uncovered": [],
    "summary": "Offline fixture: every criterion traces to the brief",
}


def report_path():
    return Path(sys.argv[sys.argv.index("--report") + 1])


def argument(name):
    return Path(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else None


def source_criteria_declared():
    return "sources" in (
        schema.get("properties", {})
        .get("contract", {})
        .get("properties", {})
        .get("acceptance_criteria", {})
        .get("items", {})
        .get("properties", {})
    )


if "--version" in sys.argv:
    print("fixture-tool 1")
    raise SystemExit(0)

EVENTS = "--events" in sys.argv
MODEL = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else ""
SESSION = sys.argv[sys.argv.index("--session") + 1] if "--session" in sys.argv else "ses_" + uuid.uuid4().hex
MESSAGE = "msg_" + uuid.uuid4().hex


def event(kind, **part):
    print(
        json.dumps(
            {
                "type": kind,
                "sessionID": SESSION,
                "part": {"id": "prt_" + uuid.uuid4().hex, "sessionID": SESSION, "messageID": MESSAGE, **part},
            }
        ),
        flush=True,
    )


def tool_event(part_id, command, code, output):
    print(
        json.dumps(
            {
                "type": "tool_use",
                "sessionID": SESSION,
                "part": {
                    "id": part_id,
                    "sessionID": SESSION,
                    "messageID": MESSAGE,
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": command},
                        "output": output,
                        "metadata": {"exit": code},
                    },
                },
            }
        ),
        flush=True,
    )


def deliver(value):
    if (
        stage == "astra_finalize"
        and "retain_contract" not in value.get("contract", {})
        and "retain_contract" in schema.get("properties", {}).get("contract", {}).get("properties", {})
    ):
        draft = value["contract"]
        retained = {
            "retain_contract": data.get("retained_contract")
            or (data.get("clarification_context") or {}).get("retained_contract"),
            "open_blocking_questions": draft.get("open_blocking_questions", []),
        }
        if "initial_task" in schema["properties"]["contract"]["properties"]:
            retained["initial_task"] = draft.get("initial_task")
        if "acceptance_criteria" in schema["properties"]["contract"]["properties"]:
            # The reference's criteria delta (#1991); empty, it retains every row.
            retained["acceptance_criteria"] = {
                "retain_from": retained["retain_contract"],
                "upsert": [],
                "remove": [],
                "proofs": [],
            }
        value["contract"] = retained
        value["requirement_trace"] = []
    properties = schema.get("properties", {})
    if data.get("report_repair") and "summary" in schema.get("required", []) and "summary" in properties:
        value.setdefault("summary", "Repaired report")
    draft = value.get("contract")
    if isinstance(draft, dict):
        # Provenance is sealed by the runner, never echoed as a model proposal.
        for field in ("brief_acceptance", "risk_acceptance", "source_trace"):
            draft.pop(field, None)
        cite(draft)
    if "source_trace" in properties:
        value.setdefault("source_trace", {"out_of_scope": [], "negation_waivers": []})
    if "source_verdict" in properties:
        value.setdefault("source_verdict", dict(SOURCE_VERDICT))
    if not EVENTS:
        report_path().write_text(json.dumps(value))
        return
    event("text", text=json.dumps(value))
    event(
        "step_finish",
        reason="stop",
        tokens={"input": 100, "output": 20, "reasoning": 0, "cache": {"read": 40, "write": 0}},
    )


sessions_log = argument("--sessions-log")
if sessions_log:
    with sessions_log.open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "role": sys.argv[sys.argv.index("--role") + 1] if "--role" in sys.argv else "",
                    "resumed": "--session" in sys.argv,
                    "session": SESSION,
                }
            )
            + "\n"
        )
if EVENTS:
    event("step_start")

prompt_file = argument("--prompt-file")
stdin = sys.stdin.read()
prompt = prompt_file.read_text() if prompt_file else stdin
observation = argument("--stdin-observation")
if observation:
    observation.write_text(json.dumps({"stdin": stdin, "prompt": prompt}))
invocations = argument("--invocations")
data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
schema: dict[str, Any] = {}
prefix = prompt.split("\nCURRENT HANDOFF DATA\n", 1)[0]
marker = "\nOPENCODE OUTPUT CONTRACT\n" if EVENTS else "\nTOOL OUTPUT CONTRACT\n"
if marker in prefix:
    # The configured provider carries the actual current schema in its prompt.
    # Report-file delivery has a footer after that JSON; events do not.
    try:
        contract_text = prefix.rsplit(marker, 1)[1]
        start = contract_text.index("\n{") + 1
        schema, end = json.JSONDecoder().raw_decode(contract_text[start:])
        tail = contract_text[start + end :].strip()
        if not isinstance(schema, dict) or (tail and (EVENTS or not tail.startswith("FINAL REPORT DELIVERY\n"))):
            raise ValueError("Invalid schema contract")
    except (IndexError, ValueError):
        raise SystemExit("fake_command_tool: invalid current output schema") from None
if "acceptance_criteria_ref" in data:
    data["acceptance_criteria"] = [
        {k: c[k] for k in ("id", "criterion")} for c in data["goal_contract"]["body"]["acceptance_criteria"]
    ]


def _sentences(text):
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if part.strip()]


def _number_in(number, text):
    return re.search(r"(?<!\d)(?<!\d\.)" + re.escape(number) + r"(?!(?:\d|\.\d))", text) is not None


def _corpus(draft):
    parts = [
        *(draft.get("required_behaviors") or []),
        *(draft.get("constraints") or []),
        *(draft.get("technical_approach") or []),
    ]
    for row in draft.get("acceptance_criteria") or []:
        parts.append(str(row.get("criterion", "")) + " " + str(row.get("verification_method", "")))
    return " ".join(parts)


def cite(draft):
    """Trace criteria to source sentences the contract can still carry (#595 / #1201)."""
    if not source_criteria_declared():
        return draft
    inventory = [
        row for row in data.get("source_inventory") or [] if isinstance(row, dict) and row.get("id") and row.get("text")
    ]
    if not inventory:
        for row in draft.get("acceptance_criteria") or []:
            row.setdefault("sources", [])
        return draft
    corpus = _corpus(draft)
    citations = []
    for source in inventory:
        for sentence in _sentences(source["text"]):
            numbers = _NUMBER.findall(sentence)
            if numbers and not all(_number_in(number, corpus) for number in numbers):
                continue
            if _NEGATION.search(sentence) and not _NEGATION.search(corpus):
                continue
            citations.append({"source_id": source["id"], "quote": sentence})
    for row in draft.get("acceptance_criteria") or []:
        row.setdefault("sources", [dict(citation) for citation in citations])
    return draft


def _quote_covers(sentence, quotes):
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


def source_trace_for(draft):
    inventory = [
        row for row in data.get("source_inventory") or [] if isinstance(row, dict) and row.get("id") and row.get("text")
    ]
    quotes = [
        citation["quote"] for row in draft.get("acceptance_criteria") or [] for citation in row.get("sources") or []
    ]
    out_of_scope = []
    for source in inventory:
        for sentence in _sentences(source["text"]):
            if not _quote_covers(sentence, quotes):
                out_of_scope.append(
                    {
                        "source_id": source["id"],
                        "quote": sentence,
                        "reason": "Offline fixture: not an acceptance obligation for this plan",
                    }
                )
    return {"out_of_scope": out_of_scope, "negation_waivers": []}


def adaptive_task(draft):
    """An adaptive-planning Planner draft carries its initial_task: kind none while a blocking question is open."""
    if "ADAPTIVE PLANNING" not in prompt:
        return
    draft["initial_task"] = (
        {
            "objective": "Implement greeting CLI",
            "affected_paths": ["greet.py"],
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": ["Greet names; reject empty/whitespace input"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["Execute valid, empty and whitespace input"],
        }
        if not draft.get("open_blocking_questions")
        else {
            "objective": "",
            "affected_paths": [],
            "kind": "none",
            "milestone_id": "",
            "requirements": [],
            "acceptance_criteria": [],
            "validation_plan": [],
        }
    )


if invocations:
    with invocations.open("a") as handle:
        handle.write(data.get("stage", "report_repair") + "\n")
if data.get("report_repair"):
    original = data["original"]
    stage = original.get("stage", "terra")
    retained = (data.get("rejected_report") or {}).get("content")
    if retained is None and original.get("output") and Path(original["output"]).is_file():
        retained = Path(original["output"]).read_text()
    retained_supplied = retained is not None
    if isinstance(retained, str):
        try:
            retained = json.loads(retained)
        except json.JSONDecodeError:
            # The legacy synthetic fixture also repairs unparseable output.
            retained, retained_supplied = None, False
    if retained_supplied and not isinstance(retained, dict):
        raise SystemExit("fake_command_tool: report-only repair needs an object report")
    if isinstance(retained, dict) and retained:
        # A report-only reply supplies metadata without replaying execution or
        # discarding the report's citations, waivers, failed verdicts or checks.
        deliver(retained)
        raise SystemExit(0)
    if stage == "requirements_gather":
        draft = body(questions=False, human=False)
        deliver(
            {
                "summary": "Repaired requirements",
                "intended_outcome": draft["intended_outcome"],
                "required_behaviors": draft["required_behaviors"],
                "constraints": draft["constraints"],
                "acceptance_tests": ["Valid and invalid CLI input have the requested outcomes"],
                "source_refs": ["task"],
                "proposed_assumptions": [
                    {
                        "id": "A1",
                        "text": "Use a local CLI",
                        "kind": "inferable",
                        "category": "behavior",
                        "convention_ref": "task",
                        "rationale": "The task asks for a local tool",
                        "supports": [],
                    }
                ],
                "open_questions": [],
                "requirements": [],
                "ignored_statements": [],
                "workspace_scope": {"spans_repositories": False, "external_parts": []},
                "conflicts": [],
                "proposed_reframes": [],
                "ignored_requirements": [],
                "machine_resolutions": [],
                "access_blockers": [],
            }
        )
    elif stage in ("astra_discovery", "glm_revise"):
        draft = dict((original.get("contract") or {}).get("body") or body(questions=True, human=False))
        if draft.get("open_blocking_questions"):
            draft["technical_approach"] = []
            draft["milestones"] = []
            draft.pop("initial_task", None)
        adaptive_task(draft)
        cite(draft)
        deliver(
            {
                "contract": draft,
                "summary": "Repaired planning report",
                "code_refs": ["goal_contract.body"],
                "alternatives": [],
                "uncertainties": [],
                "contract_changes": [],
                "conflict_resolutions": [],
                "requirement_trace": [],
                "machine_resolutions": [],
                "remediation_records": [],
                "access_blockers": [],
                **({"responses": []} if stage == "glm_revise" else {}),
            }
        )
    elif stage == "investigate_stuck":
        # A report-only repair preserves the original diagnosis and its bound ID.
        deliver(json.loads(Path(original["output"]).read_text()))
    elif stage == "astra_challenge":
        draft = (data.get("goal_contract") or {}).get("body") or {}
        deliver(
            {
                "summary": "Repaired challenge",
                "obligation_decisions": [],
                "concerns": [],
                **({"source_trace": source_trace_for(draft)} if "source_trace" in schema.get("properties", {}) else {}),
            }
        )
    elif stage == "astra_finalize":
        draft = body(questions=False, human=False)
        draft["initial_task"] = {
            "objective": "Implement greeting CLI",
            "affected_paths": ["greet.py"],
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": ["Greet names"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["Execute valid and empty input"],
        }
        cite(draft)
        deliver(
            {
                "contract": draft,
                "summary": "Repaired finalize",
                **({"source_trace": source_trace_for(draft)} if "source_trace" in schema.get("properties", {}) else {}),
                "obligation_decisions": [],
                "decisions": [],
                "contract_changes": [],
                "conflict_resolutions": [],
                "requirement_trace": [],
            }
        )
    else:
        deliver(
            {
                "summary": "Repaired report",
                "changed_files": ["greet.py"],
                "commands_run": [],
                "results": ["Repaired"],
                "remaining_risks": [],
                "evidence_refs": ["greet.py"],
                "contract_revision": original.get("contract_revision"),
                "contract_hash": original.get("contract_hash"),
                "task_id": original.get("task_id", ""),
                "user_request": {
                    "kind": "none",
                    "discovered": "",
                    "impact": "",
                    "decision_needed": "",
                    "options": [],
                    "proposed_delta": "",
                },
                "deferred_backlog": [],
                "addressed_requirements": [],
                "untested_behavior": [],
                "recommended_checks": [],
            }
        )
    raise SystemExit(0)
stage = data.get("stage", "terra")
if stage == "sol" and os.environ.get("AUTOCODE_FIXTURE_PLAIN_ERROR"):
    # A human-readable CLI (``codex exec`` without --json): tool output, then the provider's own error.
    print(
        "exec\nbash -lc 'python -m unittest' in .\n exited 1 in 120ms:\n"
        "ERROR: test_retry_after_429 (tests.test_client.RetryTests.test_retry_after_429)\nFAILED (errors=1)",
        flush=True,
    )
    print(os.environ["AUTOCODE_FIXTURE_PLAIN_ERROR"], file=sys.stderr, flush=True)
    raise SystemExit(1)
contract = data["goal_contract"] or {"revision": 0, "hash": ""}
common = {
    "contract_revision": contract["revision"],
    "contract_hash": contract["hash"],
    "task_id": (data.get("current_task") or {}).get("id", ""),
    "deferred_backlog": ["Optional web UI"],
    "user_request": {
        "kind": "none",
        "discovered": "",
        "impact": "",
        "decision_needed": "",
        "options": [],
        "proposed_delta": "",
    },
}


if stage == "recognize_workflow":
    result: dict[str, Any] = {
        "workflow": "build",
        "reason": "Offline fixture: every request is treated as a build",
        "signals": [],
        "design_document": "",
    }
    if 'Add "clarity"' in prompt:
        # Vague keeps the Requirements stage, so a default (adaptive) run plans in the same stages as before.
        result["clarity"] = os.environ.get("AUTOCODE_FIXTURE_CLARITY", "vague")
elif stage == "investigate_stuck" and data.get("builder_failure"):
    failure = data["builder_failure"]
    report = Path(failure["record"]["output"])
    original = json.loads(report.read_text())
    execution = (
        bool(os.environ.get("AUTOCODE_FIXTURE_IDLE_BUILDER_MODELS"))
        and data["current_task"]["affected_paths"] == ["greet.py"]
        and original.get("summary") == "No change made"
        and original.get("results") == ["Nothing written"]
        and not Path("greet.py").exists()
    )
    diagnosis = (
        "The approved greeting CLI is absent after the idle Builder attempt."
        if execution
        else "The fixture cannot establish the cause of this Builder failure."
    )
    code = (
        "import json; from pathlib import Path; r=json.load(open(" + repr("run/" + report.name) + ")); "
        "assert r['summary']=='No change made' and r['results']==['Nothing written']; "
        "assert not Path('greet.py').exists()"
    )
    result = dict(
        diagnosis=diagnosis,
        cause="other",
        guidance=diagnosis if execution else "",
        recommendation="retry" if execution else "pause",
        user_question="",
        evidence_refs=[str(report)],
        example=diagnosis,
        probe=shlex.join([sys.executable, "-c", code]) if execution else "",
        counterexample={
            "file": "run/" + report.name if execution else "",
            "find": json.dumps("No change made") if execution else "",
            "replace": json.dumps("Implementation written") if execution else "",
        },
        untestable="" if execution else "This is not the configured missing-output fault.",
        failure_class="execution" if execution else "unknown",
        failure_id=failure["failure_id"],
    )
elif stage == "investigate_stuck":
    result = {
        "diagnosis": "Offline fixture: it cannot diagnose; the run pauses as before.",
        "cause": "other",
        "guidance": "",
        "recommendation": "pause",
        "user_question": "",
        "evidence_refs": [],
        "example": "",
        "probe": "",
        "counterexample": {"file": "", "find": "", "replace": ""},
        "untestable": "",
    }
elif stage == "requirements_gather":
    draft = body(questions=not data["saved_answers"], human=False)
    result: dict[str, Any] = {
        "summary": "Requirements for the local greeting CLI, without a plan",
        "intended_outcome": draft["intended_outcome"],
        "required_behaviors": draft["required_behaviors"],
        "constraints": draft["constraints"],
        "acceptance_tests": ["Valid and invalid CLI input have the requested outcomes"],
        "source_refs": ["task"],
        "proposed_assumptions": [
            {
                "id": "A1",
                "text": "Use a local CLI if the user chooses that interface",
                "kind": "inferable",
                "category": "behavior",
                "convention_ref": "task",
                "rationale": "The task asks for a local tool",
                "supports": [],
            }
        ],
        "open_questions": draft["open_blocking_questions"],
        "requirements": [],
        "ignored_statements": [],
        "workspace_scope": {"spans_repositories": False, "external_parts": []},
        "conflicts": [],
        "proposed_reframes": [],
        "ignored_requirements": [],
        "machine_resolutions": [],
        "access_blockers": [],
        # Requirements names the job (#1860); this offline fixture treats every request as a build.
        "job": "build",
        "design_document": "",
    }
    if 'Add "clarity"' in prompt or "clarity" in schema.get("properties", {}):
        # Vague, as the recognizer answered: the review allowance counts per planning cycle as before. A report
        # repair's prompt has no rule, but its schema still asks for clarity.
        result["clarity"] = os.environ.get("AUTOCODE_FIXTURE_CLARITY", "vague")
elif stage == "astra_discovery":
    draft = body(questions=not data["saved_answers"], human=False)
    if data["saved_answers"]:
        draft["accepted_assumptions"] = [{"text": "User selected CLI", "basis": "user_answer", "answer_id": "Q1"}]
    else:
        # Unresolved blocking questions require a clarification-only draft.
        draft["technical_approach"] = []
        draft["milestones"] = []
        draft.pop("initial_task", None)
    adaptive_task(draft)
    cite(draft)
    result = {
        "contract": draft,
        "summary": "Build a small local greeting CLI with a clear invalid-input failure",
        "code_refs": ["greet.py:1"] if Path("greet.py").is_file() else ["goal_contract.body"],
        "alternatives": ["A web endpoint would need deployment"],
        "uncertainties": [],
        "contract_changes": [],
        "conflict_resolutions": [],
        "requirement_trace": [],
        "machine_resolutions": [],
        "remediation_records": [],
        "access_blockers": [],
    }
elif stage == "astra_challenge":
    draft = contract.get("body") or {}
    result = {
        "summary": "Check whitespace-only input",
        "obligation_decisions": [],
        "concerns": [
            {
                "id": "P1",
                "concern": "Empty includes whitespace",
                "evidence_refs": ["goal_contract.body.important_failure_cases"],
                "requested_change": "Specify whitespace rejection",
                "acceptance_test": "Whitespace input exits 2",
                "blocking": True,
            }
        ],
        **({"source_trace": source_trace_for(draft)} if "source_trace" in schema.get("properties", {}) else {}),
    }
elif stage == "glm_revise":
    draft = dict(contract["body"])
    draft.pop("initial_task", None)
    draft["important_failure_cases"] = [*draft["important_failure_cases"], "Reject whitespace-only input"]
    adaptive_task(draft)
    cite(draft)
    result = {
        "contract": draft,
        "summary": "Added whitespace case",
        "code_refs": ["greet.py:1"] if Path("greet.py").is_file() else ["goal_contract.body"],
        "contract_changes": [],
        "conflict_resolutions": [],
        "requirement_trace": [],
        "machine_resolutions": [],
        "remediation_records": [],
        "access_blockers": [],
        "responses": [
            {
                "concern_id": "P1",
                "response": "Whitespace is invalid",
                "evidence_refs": ["goal_contract.body"],
                "change": "Added whitespace case",
                "acceptance_test": "Whitespace input exits 2",
            }
        ],
    }
elif stage == "astra_finalize":
    draft = dict(contract["body"])
    draft["initial_task"] = {
        "objective": "Implement greeting CLI",
        "affected_paths": ["greet.py"],
        "kind": "implement",
        "milestone_id": "M1",
        "requirements": ["Greet names; reject empty/whitespace input"],
        "acceptance_criteria": ["C1"],
        "validation_plan": ["Execute valid, empty and whitespace input"],
    }
    cite(draft)
    result = {
        "contract": draft,
        "summary": "Ready for approval",
        **({"source_trace": source_trace_for(draft)} if "source_trace" in schema.get("properties", {}) else {}),
        "contract_changes": [],
        "conflict_resolutions": [],
        "requirement_trace": [],
        "obligation_decisions": [],
        "decisions": [
            {
                "concern_id": "P1",
                "decision": "Reject whitespace",
                "rationale": "Consistent invalid-input contract",
                "acceptance_test": "Whitespace input exits 2",
                "resolved": True,
            }
        ],
    }
elif stage == "terra" and MODEL and MODEL in os.environ.get("AUTOCODE_FIXTURE_IDLE_BUILDER_MODELS", "").split(","):
    # This model's Builder changes nothing, so the run's Builder retry policy picks the next attempt.
    note = Path(".autocode/evidence/idle-builder.txt").resolve()
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("No source change was made\n")
    result = {
        **common,
        "summary": "No change made",
        "changed_files": [],
        "commands_run": [],
        "results": ["Nothing written"],
        "remaining_risks": [],
        "evidence_refs": [str(note)],
        "addressed_requirements": [],
        "untested_behavior": [],
        "recommended_checks": [],
    }
elif stage == "terra":
    Path("greet.py").write_text(
        "import sys\nif len(sys.argv) != 2 or not sys.argv[1].strip():\n    raise SystemExit(2)\nprint('Hello, ' + sys.argv[1])\n"
    )
    result = {
        **common,
        "summary": "Greeting written",
        "changed_files": ["greet.py"],
        "commands_run": [],
        "results": ["Written"],
        "remaining_risks": [],
        "evidence_refs": ["greet.py"],
        "addressed_requirements": data["current_task"]["requirements"],
        "untested_behavior": ["CLI execution"],
        "recommended_checks": ["Execute valid and invalid input"],
    }
elif stage == "sol":
    valid_cmd = [sys.executable, "greet.py", "Ada"]
    invalid_cmd = [sys.executable, "greet.py", ""]
    valid = subprocess.run(valid_cmd, capture_output=True, text=True)
    invalid = subprocess.run(invalid_cmd, capture_output=True, text=True)
    passed = valid.returncode == 0 and valid.stdout == "Hello, Ada\n" and invalid.returncode == 2
    command = shlex.join(valid_cmd)
    if EVENTS:
        tool_event("prt_check", command, 0 if passed else 1, valid.stdout)
        evidence: str | Path = "event:prt_check"
    else:
        evidence = Path(".autocode/evidence/sol-greet.json").resolve()
        captured = subprocess.run(
            shlex.split(data["capture_command"]) + ["--output", str(evidence), "--", *valid_cmd],
            capture_output=True,
            text=True,
        )
        if captured.returncode:
            raise SystemExit(captured.returncode)
    result = {
        **common,
        "verdict": "PASS" if passed else "FAIL",
        "findings": [],
        "checks_run": [command],
        "unverified_criteria": [],
        "checks": [{"command": command, "exit_code": 0 if passed else 1, "evidence_ref": str(evidence)}],
        "end_to_end_result": {
            "status": "PASS" if passed else "FAIL",
            "summary": "Executed the greeting CLI",
            "evidence_refs": [str(evidence)],
            "technical_result": None,
            "pending_human_criteria": [],
        },
        "criterion_results": [{"id": "C1", "status": "PASS" if passed else "FAIL", "evidence_refs": [str(evidence)]}],
        "finding_dispositions": [],
    }
else:
    complete = stage == "astra_review"
    result = {
        **common,
        "status": "COMPLETE" if complete else "CONTINUE",
        "acceptance_criteria": [
            {
                **c,
                "status": "verified" if complete else "unverified",
                "evidence": "Validator receipt executed the greeting CLI",
            }
            for c in data["acceptance_criteria"]
        ],
        "next_objective": "" if complete else "Implement a greeting CLI and reject empty input",
        "next_task": {
            "kind": "none" if complete else "implement",
            "milestone_id": "" if complete else "M1",
            "requirements": [] if complete else ["Print a greeting for valid input and reject empty input"],
            "acceptance_criteria": [] if complete else ["C1"],
            "validation_plan": [] if complete else ["Run greet.py with Ada and an empty name"],
            "findings": [],
        },
        "findings": [],
        "finding_dispositions": [],
        "agreed_limitations": ["Local command-line use only"] if complete else [],
        "evidence": ["Validator receipt"],
        "blocker": "",
        "plan": ["Implement greeting", "Run both cases"],
        "affected_paths": ["greet.py"],
    }

if stage == "sol" and os.environ.get("AUTOCODE_FIXTURE_MISSING_CHECK_EXIT"):
    result["checks"][0].pop("exit_code")
deliver(result)
if os.environ.get("AUTOCODE_FIXTURE_SKIP_REPORT") and not EVENTS:
    report_path().unlink()
