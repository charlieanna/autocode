#!/usr/bin/env python3
"""Deterministic offline provider for end-to-end tests; never contacts a model."""

import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

from goal_fixtures import (
    body,
    greeting_changed_code_reasons,
    quote_changed_lines,
    scripted_files_changed_code_reasons,
    scripted_files_changed_code_rejections,
    seed_greeting_workspace,
    write_goodbye_source,
    write_greeting_source,
)


def _fixed_file():
    """A workspace file a scripted Tester's probe_reason names: a reason stands only when it names a path
    that exists in the workspace (tools/autocode_tester_probes.py)."""
    root = Path.cwd()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_file() and "." in path.name[1:] and not any(part.startswith(".") for part in relative.parts):
            return relative.as_posix()
    return ""


# The stages that run on the per-task Planner's route (autocode_names.TASK_PLANNER_STAGES). This fake is
# copied out of tools/ to run, so it names them itself.
TASK_PLANNER_STAGES = {
    "draft_plan",
    "review_plan",
    "finalize_plan",
    "plan_task",
    "review_completion",
    "review_checkpoint",
    "resolve",
    "diagnose",
}

if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT (offline fixture)")
    raise SystemExit(0)
if sys.argv[1:] == ["--version"]:
    # Older than the bundled codex provider config needs (0.160.0), so a fixture Claude Code or Gemini CLI run
    # keeps its checks on its own tool; tests/test_codex_transport_cli.py runs fake_codex_config.py for that.
    print(os.environ.get("AUTOCODE_FIXTURE_CODEX_VERSION", "codex-cli 0.159.0"))
    raise SystemExit(0)

prompt = sys.stdin.read()
if os.environ.get("AUTOCODE_FIXTURE_CODEX_LOG"):
    # One line per stage call: its argv and the start of its prompt, never the environment's values.
    with open(os.environ["AUTOCODE_FIXTURE_CODEX_LOG"], "a") as _log:
        _log.write(json.dumps({"argv": sys.argv[1:], "prompt_head": prompt[:120]}) + "\n")
data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
schema = (
    json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
    if "--output-schema" in sys.argv
    else {}
)
if "acceptance_criteria_ref" in data:
    data["acceptance_criteria"] = [
        {k: c[k] for k in ("id", "criterion")} for c in data["goal_contract"]["body"]["acceptance_criteria"]
    ]


def open_finding_id(source, text):
    for row in data.get("open_findings") or []:
        if row.get("source") == source and row.get("finding") == text:
            return row["id"]
    return None


_NUMBER = re.compile(r"\d+(?:\.\d+)*")
_NEGATION = re.compile(r"\b(?:must not|never|do not|don't|cannot|can't|not|no)\b", re.I)


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
        row["sources"] = [dict(citation) for citation in citations]
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
                        "reason": "Scripted fixture: not an acceptance obligation for this plan",
                    }
                )
    return {"out_of_scope": out_of_scope, "negation_waivers": []}


SOURCE_TRACE: dict[str, list] = {"out_of_scope": [], "negation_waivers": []}


def source_verdict(result):
    if "source_verdict" in schema.get("properties", {}):
        result.setdefault(
            "source_verdict",
            {"verdict": "PASS", "uncovered": [], "summary": "Scripted: every criterion traces to the brief"},
        )


PLANNING_STAGES = {
    "requirements_gather",
    "draft_plan",
    "revise_plan",
    "review_plan",
    "finalize_plan",
    "requirements",
    "plan",
    "plan_review",
    "plan_revise",
    "plan_finalize",
}


def _workspace_sources():
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], capture_output=True
    ).stdout.split(b"\0")
    excluded = {".autocode", ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    found = []
    for name in ("greet.py", "bye.py") + tuple(raw.decode(errors="surrogateescape") for raw in tracked if raw):
        path = Path(name)
        if name in found or path.suffix == ".pyc" or any(part in excluded for part in path.parts):
            continue
        if path.is_file():
            found.append(name)
    return found[:8]


def _default_ambiguity_audit(payload):
    qids = [
        str(q["id"])
        for q in (payload.get("open_questions") or [])
        if isinstance(q, dict) and q.get("id") and q.get("kind") == "decision" and q.get("category") != "technical"
    ]
    first = (
        {"status": "open_question", "refs": qids}
        if qids
        else (
            {"status": "not_applicable", "refs": []}
            if payload.get("job") == "none" and not payload.get("requirements")
            else {"status": "specified_in_source", "refs": ["task"]}
        )
    )
    rest = {"status": "not_applicable", "refs": []}
    return {
        "outcome_scope": first,
        "inputs_and_edge_cases": dict(rest),
        "error_and_failure_behavior": dict(rest),
        "compatibility_and_callers": dict(rest),
        "ux_and_output_contract": dict(rest),
        "state_and_side_effects": dict(rest),
    }


def _default_findings(payload):
    """#4237: a discussion Requirements answers itself answers in findings. The scripted report read
    nothing that settles it, so it says so as one evidence gap answering every requirement."""
    if payload.get("required_behaviors") or payload.get("acceptance_tests"):
        return []
    ids = [str(r["id"]) for r in payload.get("requirements") or [] if isinstance(r, dict) and r.get("id")]
    return [
        {
            "requirement_ids": ids,
            "kind": "evidence_gap",
            "statement": "The scripted provider read no source that settles this.",
            "evidence": [],
            "would_settle": "A real model reading the repository.",
        }
    ]


def _conform_ledger(payload, schema_obj, default_ref):
    if not isinstance(payload, dict) or not isinstance(schema_obj, dict):
        return
    props = schema_obj.get("properties", {})
    if "repair_report" in props and "repair_report" not in payload:
        branches = (props.get("repair_report") or {}).get("anyOf") or []
        if len(branches) == 2:
            props = branches[1].get("properties", {})
    if "ambiguity_audit" in props and "ambiguity_audit" not in payload:
        payload["ambiguity_audit"] = _default_ambiguity_audit(payload)
    if "approach" in props and "approach" not in payload:
        # #4171: no sketch, so Requirements asks nothing more about the approach.
        payload["approach"] = {"summary": "", "pieces": [], "band": "", "alternatives": [], "out_of_scope": []}
    if "approach" in props and "findings" in props and "findings" not in payload:
        payload["findings"] = _default_findings(payload)
    q_props = ((props.get("open_questions") or {}).get("items") or {}).get("properties") or {}
    if "convention_ref" in q_props:
        for q in payload.get("open_questions") or []:
            if isinstance(q, dict):
                q.setdefault("convention_ref", "")
    for box_key in ("contract", "requirements"):
        box = payload.get(box_key)
        box_props = ((props.get(box_key) or {}).get("properties")) or {}
        if not isinstance(box, dict) or not box_props:
            continue
        bq_props = ((box_props.get("open_blocking_questions") or {}).get("items") or {}).get("properties") or {}
        if "convention_ref" in bq_props:
            for q in box.get("open_blocking_questions") or []:
                if isinstance(q, dict):
                    q.setdefault("convention_ref", "")
        for dec_key in ("accepted_assumptions", "delegated_decisions"):
            d_props = ((box_props.get(dec_key) or {}).get("items") or {}).get("properties") or {}
            if not ("category" in d_props or "convention_ref" in d_props):
                continue
            for row in box.get(dec_key) or []:
                if not isinstance(row, dict):
                    continue
                if row.get("basis") == "agent_proposed":
                    row.setdefault("category", "technical")
                    row.setdefault("convention_ref", default_ref)
                else:
                    row.setdefault("category", "requested_outcome")
                    row.setdefault("convention_ref", "")


def record_launch(stage):
    probe = os.environ.get("AUTOCODE_REGISTRY_LAUNCH_PROBE")
    if probe:
        registry_path = Path(os.environ["AUTOCODE_HOME"]) / "registry.json"
        try:
            registry = json.loads(registry_path.read_text())
            observed = sorted(item["run_dir"] for item in registry.get("runs", {}).values())
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            observed = {"error": str(error)}
        with Path(probe).open("a") as stream:
            stream.write(json.dumps({"stage": stage, "runs": observed}) + "\n")


if data.get("report_repair"):
    record_launch(data["original"]["stage"] + "_report_repair")
    # This branch only reformats a saved report; never executes the original task.
    loss_cache = os.environ.get("AUTOCODE_FIXTURE_REPORT_LOSS")
    if loss_cache and data["original"].get("stage") == "tester":
        result = json.loads(Path(loss_cache).read_text())
    elif data["original"].get("truncated_output"):
        result = json.loads(data["rejected_report"]["content"]["partial_text"] + "}")
    else:
        result = json.loads(Path(data["original"]["output"]).read_text())
    if os.environ.get("AUTOCODE_FIXTURE_INVALID_INVESTIGATOR") and data["original"].get("stage") == "investigate_stuck":
        result["diagnosis"] = "Offline fixture: retain the interrupted report repair and pause."
    if "summary" not in result and data["original"].get("stage", "").startswith(("builder", "draft_plan")):
        result["summary"] = "Repaired fixture report"
    if os.environ.get("AUTOCODE_FIXTURE_MODE") == "human-pending":
        original_stage = data["original"].get("stage")
        if original_stage == "review_completion":
            for row in result["acceptance_criteria"]:
                if not row["evidence"].strip():
                    row["evidence"] = "Current Validator executed CLI checks; human acceptance is still pending"
        elif original_stage == "tester" and not result["checks"]:
            original_events = [json.loads(line) for line in Path(data["original"]["events"]).read_text().splitlines()]
            result["checks"] = [
                {
                    "command": event["item"]["command"],
                    "exit_code": event["item"]["exit_code"],
                    "evidence_ref": "event:" + event["item"]["id"],
                }
                for event in original_events
                if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "command_execution"
            ]
        if original_stage == "tester" and os.environ.get("AUTOCODE_FIXTURE_INCOMPLETE_HUMAN_PROOF"):
            # A report-only repair cites the original execution and shapes the human gap (#1851).
            result["criterion_results"][0]["evidence_refs"] = ["event:check"]
            result["end_to_end_result"].update(
                status="NOT_VERIFIED",
                summary="Both CLI flows executed; C1 human acceptance pending",
                evidence_refs=["event:check"],
                technical_result={
                    "status": "PASS",
                    "summary": "Both CLI flows executed",
                    "evidence_refs": ["event:check"],
                },
                pending_human_criteria=["C1"],
            )
    source_verdict(result)
    if "progressive_checkpoint" in schema.get("properties", {}):
        result.setdefault("progressive_checkpoint", False)
    _repair_sources = _workspace_sources()
    _conform_ledger(result, schema, f"{_repair_sources[0]}:1" if _repair_sources else "")
    session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
    print(json.dumps({"type": "thread.started", "thread_id": session}))
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 10}}))
    raise SystemExit(0)
stage = data["stage"]
if os.environ.get("AUTOCODE_FIXTURE_CAPTURE_HOLD") == stage:
    # #4028: what an `autocode capture --compare-base` this stage's tools ran leaves when it cannot
    # confirm its command stopped: its private custody journal, with no result, in the store this
    # process's own environment names. The scripted stage then finishes normally.
    _state = os.environ.get("XDG_STATE_HOME")
    _store = Path(_state) if _state and Path(_state).is_absolute() else Path(os.environ["HOME"]) / ".local/state"
    _store = _store / "autocode/metadata"
    _store.mkdir(mode=0o700, parents=True, exist_ok=True)
    _member = uuid.uuid4().hex
    _journal = {
        "schema": 2,
        "group_nonce": uuid.uuid4().hex,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip(),
        "uid": os.geteuid(),
        "attempts": [{"nonce": _member, "result_file": f"result-{_member}.json"}],
    }
    for _name, _body in (("admission.lock", b""), ("pending.json", json.dumps(_journal).encode())):
        _descriptor = os.open(_store / _name, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        os.write(_descriptor, _body)
        os.close(_descriptor)
if stage == "builder" and os.environ.get("AUTOCODE_CONSUMER_BARRIER"):
    import time

    barrier = Path(os.environ["AUTOCODE_CONSUMER_BARRIER"])
    if barrier.exists():
        barrier.with_suffix(".entered").write_text("stage active")
        # Long enough for the consumer test's CLI calls on a contended host (#314);
        # the test releases the barrier as soon as they finish.
        deadline = time.monotonic() + 120
        while barrier.exists():
            if time.monotonic() > deadline:
                raise SystemExit("fixture barrier timed out")
            time.sleep(0.02)
mode = os.environ.get("AUTOCODE_FIXTURE_MODE", "standard")
# Bug-fix fixtures: the job type the planners propose, and the exact files the Builder writes.
task_kind = os.environ.get("AUTOCODE_FIXTURE_TASK_KIND", "build")
scripted_files = (
    json.loads(Path(os.environ["AUTOCODE_FIXTURE_FILES"]).read_text())
    if os.environ.get("AUTOCODE_FIXTURE_FILES")
    else None
)
# A list scripts a correction: the Builder writes the first entry while it has no actionable finding and the
# last once the runner or a reviewer has given it one; the plan names every path any entry writes, and the
# other stages read the entry whose exact bytes are in the workspace.
builder_rounds = scripted_files if isinstance(scripted_files, list) else [scripted_files] if scripted_files else []


def _scripted_round(rounds):
    if stage == "builder":
        return rounds[-1 if data.get("actionable_findings") else 0]
    for files in reversed(rounds):
        if all(Path(name).is_file() and Path(name).read_text() == content for name, content in files.items()):
            return files
    return rounds[0]


builder_paths = sorted({name for files in builder_rounds for name in files})
builder_files = _scripted_round(builder_rounds) if builder_rounds else None
contract = data["goal_contract"] or {"revision": 0, "hash": ""}
record_launch(stage)
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
session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
NO_PROPOSAL = {
    "version": 0,
    "needed_because": "",
    "shared_decisions": [],
    "outstanding_criteria": [],
    "done_slices": [],
    "slices": [],
}
if os.environ.get("AUTOCODE_FIXTURE_SESSION_DRIFT"):
    session = str(uuid.uuid4())
print(json.dumps({"type": "thread.started", "thread_id": session}))
_quota_model = os.environ.get("AUTOCODE_FIXTURE_QUOTA_MODEL")  # only this model's quota is used up, when set
if os.environ.get("AUTOCODE_FIXTURE_QUOTA_STAGE") == stage and (
    not _quota_model or ("--model" in sys.argv and sys.argv[sys.argv.index("--model") + 1] == _quota_model)
):
    # AUTOCODE_FIXTURE_QUOTA_MESSAGE swaps in another provider error at the same point (a non-quota stop);
    # AUTOCODE_FIXTURE_QUOTA_ERROR, a JSON object, replaces the whole error (OpenCode's APIError shape).
    error = json.loads(os.environ.get("AUTOCODE_FIXTURE_QUOTA_ERROR") or "null") or {
        "message": os.environ.get("AUTOCODE_FIXTURE_QUOTA_MESSAGE", "subscription usage limit reached")
    }
    print(json.dumps({"type": "error", "error": error}))
    raise SystemExit(3)
_sources = _workspace_sources()
if stage in PLANNING_STAGES and "ready" not in schema.get("properties", {}) and _sources:
    print(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "read-sources",
                    "type": "command_execution",
                    "command": shlex.join(["head", "-n", "1", *_sources]),
                    "exit_code": 0,
                    "aggregated_output": "",
                },
            }
        )
    )


def adaptive_task(draft, implement):
    """An adaptive-planning Planner draft carries its initial_task: kind none while a blocking question is open."""
    if "ADAPTIVE PLANNING" not in prompt:
        return
    draft["initial_task"] = (
        dict(implement)
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


IMPLEMENT_TASK = {
    "objective": "Implement greeting CLI",
    "affected_paths": builder_paths if builder_files else ["greet.py", "test_greeting.py"],
    "kind": "implement",
    "milestone_id": "M1",
    "requirements": ["Greet names; reject empty/whitespace input"],
    "acceptance_criteria": ["C1"],
    "validation_plan": ["Execute valid, empty and whitespace input"],
}
if stage == "investigate_stuck":
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
        "project_notes": [],
    }
    if failure := data.get("builder_failure"):
        # A Builder-failure classification must cite the pinned failure and its evidence.
        result.update(
            failure_class="unknown",
            example="Offline fixture: it cannot diagnose this failure; it is classified unknown.",
            untestable="An offline fixture runs no command that could show the cause.",
            failure_id=failure.get("failure_id", ""),
            evidence_refs=list(failure.get("evidence_refs") or [])[:1],
        )
elif stage == "resolve_repair":
    result = {
        "diagnosis": "Offline fixture: it cannot resolve; the run pauses as before.",
        "action": "hold",
        "repaired_report_json": "",
        "guidance": "",
        "next_objective": "",
        "affected_paths": [],
        "user_question": "",
        "user_options": [],
        "evidence_refs": [],
    }
elif stage == "requirements_gather":
    draft = body(questions=not data["saved_answers"], task_kind=task_kind)
    if "ready" in schema.get("properties", {}):
        # The triage pass's small contract (#2940): the wire schema asks only for the blocking
        # questions or ready, so answer exactly that; the full report runs on the ordinary
        # schema once the answers are saved.
        result = {
            "summary": "Triage: ask the blocking question before drafting requirements",
            "intended_outcome": draft["intended_outcome"],
            "ready": not draft["open_blocking_questions"],
            "open_questions": draft["open_blocking_questions"],
        }
    else:
        result = {
            "summary": "Requirements for a local greeting CLI, without an implementation plan",
            "intended_outcome": draft["intended_outcome"],
            "required_behaviors": draft["required_behaviors"],
            "constraints": draft["constraints"],
            "acceptance_tests": ["Valid and invalid CLI input have the requested outcomes"],
            "source_refs": [f"{name}:1" for name in ("greet.py", "bye.py") if Path(name).is_file()],
            "proposed_assumptions": [],
            "open_questions": draft["open_blocking_questions"],
            "requirements": [],
            "ignored_statements": [],
            "conflicts": [],
            "proposed_reframes": [],
            "ignored_requirements": [],
            "machine_resolutions": [],
            "access_blockers": [],
            "task_kind": task_kind,
            # Requirements names the job (#1860); this offline fixture treats every request as a build.
            "job": "build",
            "design_document": "",
            # This offline fixture's requests belong to this repository alone (#2066), unless a test names a part
            # that belongs elsewhere as TARGET|WORK|QUOTE, the quote copied from its task.
            "workspace_scope": {"spans_repositories": False, "external_parts": []},
        }
    external = os.environ.get("AUTOCODE_FIXTURE_EXTERNAL_PART")
    if external:
        target, work, quote = external.split("|", 2)
        result["workspace_scope"] = {
            "spans_repositories": True,
            "external_parts": [{"target": target, "work": work, "source_quote": quote, "evidence": ""}],
        }
    if 'Add "clarity"' in prompt or "clarity" in schema.get("properties", {}):
        # Vague: the review allowance counts per planning cycle as before. A report
        # repair's prompt has no rule, but its schema still asks for clarity.
        result["clarity"] = os.environ.get("AUTOCODE_FIXTURE_CLARITY", "vague")
elif stage == "draft_plan":
    draft = body(questions=not data["saved_answers"], human=mode in ("standard", "human-pending"), task_kind=task_kind)
    if os.environ.get("AUTOCODE_FIXTURE_TECHNICAL_FLOW_PENDING"):
        draft["end_to_end_flow"].append("Install the CLI entry point and invoke it from another working directory")
    if os.environ.get("AUTOCODE_FIXTURE_FLOW_AWAITS_REVIEW"):
        draft["end_to_end_flow"].append("User approves C1")
    if builder_files:
        draft["milestones"][0]["affected_paths"] = builder_paths
    if mode == "milestones":
        draft["acceptance_criteria"].append(
            {
                "id": "C2",
                "criterion": "Goodbye CLI prints Goodbye, NAME",
                "verification_method": "Execute bye.py with Ada",
                "human_review": False,
            }
        )
        draft["milestones"].append(
            {
                "id": "M2",
                "objective": "Deliver goodbye CLI",
                "acceptance_criteria": ["C2"],
                "depends_on": ["M1"],
                "affected_paths": ["bye.py", "test_goodbye.py"],
            }
        )
        draft["deliverables"].append("bye.py")
        draft["required_behaviors"].append("Print Goodbye, NAME from bye.py")
    if data["saved_answers"]:
        draft["accepted_assumptions"] = [{"text": "User selected CLI", "basis": "user_answer", "answer_id": "Q1"}]
    for feedback in data["brief_feedback"]:
        draft["constraints"].append(feedback["text"])
        draft["accepted_assumptions"].append(
            {"text": feedback["text"], "basis": "user_feedback", "answer_id": feedback["id"]}
        )
    if os.environ.get("AUTOCODE_FIXTURE_CURRENT_BEHAVIOR"):
        # #2133: C1 is a regression criterion. Its current_behavior box is left for the runner to fill from the
        # bound diagnosis, or, when AUTOCODE_FIXTURE_CURRENT_BEHAVIOR_DRAFT is set, written wrongly first and
        # corrected by the revision to AUTOCODE_FIXTURE_CURRENT_BEHAVIOR (the Investigator's observed text).
        draft["acceptance_criteria"][0]["verification_method"] = "test: test_c1_blank_name_rejected"
        if os.environ.get("AUTOCODE_FIXTURE_CURRENT_BEHAVIOR_DRAFT"):
            draft["acceptance_criteria"][0]["current_behavior"] = os.environ["AUTOCODE_FIXTURE_CURRENT_BEHAVIOR_DRAFT"]
    cite(draft)
    result = {"contract": draft, "summary": "Build a small local greeting CLI with a clear invalid-input failure"}
    if data.get("joint_planning"):
        if draft["open_blocking_questions"]:
            draft["milestones"] = []
            draft["technical_approach"] = []
        adaptive_task(draft, IMPLEMENT_TASK)
        source = next((name for name in ("greet.py", "bye.py") if Path(name).is_file()), None)
        result.update(
            code_refs=[f"{source}:1"] if source else ["goal_contract.body"],
            alternatives=["A web endpoint would need deployment"],
            uncertainties=[],
            contract_changes=[],
            requirement_trace=[],
            conflict_resolutions=[],
            machine_resolutions=[],
            access_blockers=[],
            remediation_records=[],
            progressive_proposal=dict(NO_PROPOSAL),
        )
elif stage == "review_plan":
    result = {
        "summary": "Check whitespace-only input",
        "obligation_decisions": [],
        "source_trace": source_trace_for(contract.get("body") or {}),
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
    }
elif stage == "revise_plan":
    draft = {
        key: value
        for key, value in contract["body"].items()
        if key not in ("brief_acceptance", "risk_acceptance", "source_trace")
    }
    draft.pop("initial_task", None)
    if os.environ.get("AUTOCODE_FIXTURE_CURRENT_BEHAVIOR_DRAFT"):
        draft["acceptance_criteria"][0]["current_behavior"] = os.environ["AUTOCODE_FIXTURE_CURRENT_BEHAVIOR"]
    draft["important_failure_cases"] = [*draft["important_failure_cases"], "Reject whitespace-only input"]
    adaptive_task(draft, IMPLEMENT_TASK)
    source = next((name for name in ("greet.py", "bye.py") if Path(name).is_file()), None)
    result = {
        "contract": draft,
        "summary": "Added whitespace case",
        "code_refs": [f"{source}:1"] if source else ["goal_contract.body"],
        "contract_changes": [],
        "requirement_trace": [],
        "conflict_resolutions": [],
        "machine_resolutions": [],
        "remediation_records": [],
        "access_blockers": [],
        "progressive_proposal": dict(NO_PROPOSAL),
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
elif stage == "finalize_plan":
    draft = {
        key: value
        for key, value in contract["body"].items()
        if key not in ("brief_acceptance", "risk_acceptance", "source_trace")
    }
    draft["initial_task"] = {
        "objective": "Implement greeting CLI",
        "affected_paths": builder_paths if builder_files else ["greet.py", "test_greeting.py"],
        "kind": "implement",
        "milestone_id": "M1",
        "requirements": ["Greet names; reject empty/whitespace input"],
        "acceptance_criteria": ["C1"],
        "validation_plan": ["Execute valid, empty and whitespace input"],
    }
    blocked = mode == "planning-blocked"
    if blocked:
        draft["open_blocking_questions"] = [
            {
                "id": "P2",
                "question": "Should whitespace be rejected?",
                "why": "Unresolved input semantics",
                "options": ["Reject", "Accept"],
                "proposed_default": "",
                "kind": "decision",
                "category": "behavior",
                "delegable": False,
            }
        ]
    cite(draft)
    result = {
        "contract": draft,
        "summary": "Ready for approval" if not blocked else "User decision required",
        "contract_changes": [],
        "requirement_trace": [],
        "conflict_resolutions": [],
        "obligation_decisions": [],
        "source_trace": source_trace_for(draft),
        "progressive_proposal": dict(NO_PROPOSAL),
        "decisions": [
            {
                "concern_id": "P1",
                "decision": "Reject whitespace" if not blocked else "Ask the user",
                "rationale": "Consistent invalid-input contract",
                "acceptance_test": "Whitespace input exits 2",
                "resolved": not blocked,
            }
        ],
    }
    if mode == "planning-invalid":
        result["decisions"] = []
elif stage.removesuffix("_report_repair") in TASK_PLANNER_STAGES and stage != "review_checkpoint":
    rework = data.get("validation", {}).get("verdict") == "FAIL"
    complete = stage == "review_completion" and not rework
    advance = mode == "milestones" and complete and data["current_task"]["milestone_id"] == "M1"
    if advance:
        complete = False
    result = {
        **common,
        "status": "COMPLETE" if complete else "REWORK" if rework else "CONTINUE",
        "acceptance_criteria": [
            {**c, "status": "verified" if complete else "unverified", "evidence": "Validator executed both CLI cases"}
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
        "findings": [
            {
                "id": "",
                "severity": "high",
                "finding": "Empty names are accepted by greet.py",
                "evidence": "Validator events",
                "blocking": True,
            }
        ]
        if rework
        else [],
        "agreed_limitations": ["Local command-line use only"] if complete else [],
        "finding_dispositions": (
            [
                {
                    "id": open_finding_id("task_planner", "Empty names are accepted by greet.py"),
                    "disposition": "resolved",
                    "evidence": "Validator events",
                }
            ]
            if complete and open_finding_id("task_planner", "Empty names are accepted by greet.py")
            else []
        ),
        "evidence": ["Validator events"],
        "blocker": "",
        "plan": ["Implement greeting", "Run both cases"],
        "affected_paths": ["greet.py", "test_greeting.py"],
    }
    if advance:
        result.update(next_objective="Implement goodbye CLI", affected_paths=["bye.py", "test_goodbye.py"])
        result["next_task"].update(
            milestone_id="M2",
            requirements=["Print Goodbye, NAME"],
            acceptance_criteria=["C2"],
            validation_plan=["Execute both greeting and goodbye"],
        )
    if mode == "stalled" and ((data.get("milestone_checkpoint") or {}).get("current") or {}).get("needs_replan"):
        result["next_objective"] = "Isolate empty input with a focused reproduction before repair"
    if mode == "human-pending" and stage == "review_completion" and not data.get("human_reviews"):
        result.update(status="CONTINUE", next_objective="Obtain human acceptance of the validated artifact")
        result["acceptance_criteria"][0]["status"] = "unverified"
        if os.environ.get("AUTOCODE_FIXTURE_EMPTY_HUMAN_EVIDENCE"):
            result["acceptance_criteria"][0]["evidence"] = ""
        result["next_task"].update(
            kind="validate",
            milestone_id="M1",
            requirements=["Obtain human acceptance"],
            acceptance_criteria=["C1"],
            validation_plan=["Ask the user to accept the current artifact"],
        )
    redundant = os.environ.get("AUTOCODE_FIXTURE_REDUNDANT_COMPLETION")
    if (
        stage == "review_completion"
        and complete
        and redundant
        and (redundant == "always" or not data.get("checkpoint_reason"))
    ):
        result.update(status="CONTINUE", next_objective="Re-run the already passing validation")
        result["next_task"].update(
            kind="validate",
            milestone_id="M1",
            requirements=["Validate the greeting CLI"],
            acceptance_criteria=["C1"],
            validation_plan=["Run greet.py with Ada and an empty name"],
        )
    if stage == "resolve":
        result["diagnosis"] = "Empty names are accepted by the CLI; add input validation and retest both cases."
    if stage == "review_completion" and rework and os.environ.get("AUTOCODE_FIXTURE_INCOMPLETE_REWORK"):
        # Exercise Resolver routing with a valid rejection that still needs a repair plan.
        result["next_task"]["validation_plan"] = []
elif stage == "builder":
    # A rework fixture's Builder corrects its work only for a reviewer's finding: the runner's own proof
    # finding (source runner), which comes back before any Tester, leaves the first attempt as it was.
    reviewer_findings = [row for row in data["actionable_findings"] if row.get("source") != "runner"]
    # Each implement attempt must produce a real tree delta; the runner
    # measures changed files from the workspace snapshot, not the report.
    batch = str(uuid.uuid4())
    milestone = data.get("current_task", {}).get("milestone_id")
    if mode == "milestones" and milestone == "M2":
        write_goodbye_source(Path.cwd(), revision=batch)
    elif mode == "stalled" or mode == "rework" and not reviewer_findings:
        Path("greet.py").write_text("import sys\nprint('Hello, ' + sys.argv[1])\n# attempt " + batch + "\n")
    elif builder_files:
        for name, content in builder_files.items():
            Path(name).write_text(content)
    else:
        seed_greeting_workspace(Path.cwd())
        write_greeting_source(Path.cwd(), revision=batch)
    if os.environ.get("AUTOCODE_CONSUMER_BARRIER"):
        with Path("greet.py").open("a") as fixture_output:
            fixture_output.write("# revision " + str(uuid.uuid4()) + "\n")
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
    if mode == "milestones" and milestone == "M2":
        result.update(
            changed_files=["bye.py", "test_goodbye.py"],
            evidence_refs=["bye.py", "test_goodbye.py"],
            summary="Goodbye written",
        )
    elif builder_files:
        result.update(changed_files=sorted(builder_files), evidence_refs=sorted(builder_files))
    elif mode != "stalled" and (mode != "rework" or reviewer_findings):
        # The seeded workspace above: the stalled and first rework attempts write greet.py alone.
        result.update(changed_files=["greet.py", "test_greeting.py"], evidence_refs=["greet.py", "test_greeting.py"])
else:
    valid = subprocess.run([sys.executable, "greet.py", "Ada"], capture_output=True, text=True)
    invalid = subprocess.run([sys.executable, "greet.py", ""], capture_output=True, text=True)
    passed = valid.returncode == 0 and valid.stdout == "Hello, Ada\n" and invalid.returncode == 2
    goodbye_passed = False
    if mode == "milestones" and data["current_task"]["milestone_id"] == "M2":
        goodbye = subprocess.run([sys.executable, "bye.py", "Ada"], capture_output=True, text=True)
        goodbye_passed = goodbye.returncode == 0 and goodbye.stdout == "Goodbye, Ada\n"
        passed = passed and goodbye_passed
    # A real command the runner can re-run in a clean copy (autocode_check_replay): the same checks.
    script = (
        "import subprocess, sys\n"
        "run = lambda *a: subprocess.run([sys.executable, *a], capture_output=True, text=True)\n"
        "ok = run('greet.py', 'Ada').stdout == 'Hello, Ada\\n' and run('greet.py', '').returncode == 2\n"
        + (
            "ok = ok and run('bye.py', 'Ada').stdout == 'Goodbye, Ada\\n'\n"
            if mode == "milestones" and data["current_task"]["milestone_id"] == "M2"
            else ""
        )
        + "sys.exit(0 if ok else 1)\n"
    )
    command = shlex.join([sys.executable, "-c", script])
    unreproducible = os.environ.get("AUTOCODE_FIXTURE_UNREPRODUCIBLE_CHECK")
    # "once": only the first Validator attempt cites it; the one the runner starts again cites the real check.
    if unreproducible and not (unreproducible == "once" and Path(".autocode/validator-only").exists()):
        # Passes only in the Validator's own session: it reads a file the Validator made outside the source.
        Path(".autocode/validator-only").write_text("set up by the Validator\n")
        command = "test -f .autocode/validator-only"
    if not os.environ.get("AUTOCODE_FIXTURE_NO_CHECK_EVENT"):
        print(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "check",
                        "type": "command_execution",
                        "command": command,
                        "exit_code": 0 if passed else 1,
                        "aggregated_output": json.dumps(
                            {"valid": [valid.returncode, valid.stdout], "invalid": invalid.returncode}
                        ),
                    },
                }
            )
        )
    findings = (
        []
        if passed
        else [
            {
                "id": "",
                "severity": "high",
                "blocking": True,
                "finding": "Empty names are accepted",
                "evidence": "event:check",
                "reproduction_steps": ["Run greet.py with an empty argument"],
                "expected": "Exit 2",
                "actual": f"Exit {invalid.returncode}",
                "why_it_matters": "Required invalid-input behavior",
                "suggested_correction": "Reject an empty or whitespace-only name",
            }
        ]
    )
    result = {
        **common,
        "verdict": "PASS" if passed else "FAIL",
        "findings": findings,
        "checks_run": [command],
        "unverified_criteria": [],
        "checks": [{"command": command, "exit_code": 0 if passed else 1, "evidence_ref": "event:check"}],
        "end_to_end_result": {
            "status": "PASS" if passed else "FAIL",
            "summary": "Executed both CLI user flows",
            "evidence_refs": ["event:check"],
            "technical_result": None,
            "pending_human_criteria": [],
        },
        "criterion_results": [{"id": "C1", "status": "PASS" if passed else "FAIL", "evidence_refs": ["event:check"]}],
        "finding_dispositions": (
            [
                {
                    "id": open_finding_id("tester", "Empty names are accepted"),
                    "disposition": "resolved",
                    "evidence": "event:check",
                }
            ]
            if passed and open_finding_id("tester", "Empty names are accepted")
            else []
        ),
    }
    if mode == "milestones":
        result["criterion_results"].append(
            {
                "id": "C2",
                "status": "PASS" if goodbye_passed else "NOT_VERIFIED",
                "evidence_refs": ["event:check"] if goodbye_passed else [],
            }
        )
    if mode == "human-pending" and passed:
        result.update(
            verdict=os.environ.get("AUTOCODE_FIXTURE_HUMAN_VERDICT", "BLOCKED"),
            unverified_criteria=["C1 human acceptance pending"],
        )
        result["criterion_results"][0]["status"] = "NOT_VERIFIED"
        if os.environ.get("AUTOCODE_FIXTURE_FLOW_AWAITS_REVIEW"):
            # The approved flow ends in the person's acceptance, so the flow itself stays unverified.
            result["end_to_end_result"].update(
                status="NOT_VERIFIED", summary=os.environ["AUTOCODE_FIXTURE_FLOW_AWAITS_REVIEW"]
            )
            if not os.environ.get("AUTOCODE_FIXTURE_LEGACY_FLOW_GAP"):
                unfinished = bool(os.environ.get("AUTOCODE_FIXTURE_TECHNICAL_FLOW_PENDING"))
                result["end_to_end_result"].update(
                    pending_human_criteria=["C1"],
                    technical_result={
                        "status": "NOT_VERIFIED" if unfinished else "PASS",
                        "summary": "Installation and invocation were not executed"
                        if unfinished
                        else "Both CLI flows executed",
                        "evidence_refs": ["check:1"],
                    },
                )
        if os.environ.get("AUTOCODE_FIXTURE_INCOMPLETE_HUMAN_PROOF"):
            # The live #1851 shape: the human row cites nothing and the flow proves no technical result.
            result["criterion_results"][0]["evidence_refs"] = []
            result["end_to_end_result"].update(
                status="NOT_VERIFIED", summary="", evidence_refs=[], technical_result=None, pending_human_criteria=[]
            )
        if os.environ.get("AUTOCODE_FIXTURE_OMIT_CHECKS"):
            result["checks"] = []
        if os.environ.get("AUTOCODE_FIXTURE_NO_CHECK_EVENT"):
            result.update(checks=[], checks_run=[])
            result["criterion_results"][0]["evidence_refs"] = ["greet.py"]
            result["end_to_end_result"]["evidence_refs"] = ["greet.py"]
    if stage == "review_checkpoint":
        result = {
            "validation": result,
            "consult_sol": {"requested": False, "question": "", "reason": ""},
            "decision": {
                **common,
                "status": "COMPLETE" if passed else "REWORK",
                "acceptance_criteria": [
                    {
                        **c,
                        "status": "verified" if passed else "unverified",
                        "evidence": "Independent Plan Reviewer executed CLI cases; event:check",
                    }
                    for c in data["acceptance_criteria"]
                ],
                "next_objective": "" if passed else "Reject empty input and rerun the CLI tests",
                "next_task": {
                    "kind": "none" if passed else "implement",
                    "milestone_id": "" if passed else "M1",
                    "requirements": [] if passed else ["Reject empty input"],
                    "acceptance_criteria": [] if passed else ["C1"],
                    "validation_plan": [] if passed else ["Execute valid and empty input"],
                    "findings": [],
                },
                "findings": [],
                "finding_dispositions": [],
                "agreed_limitations": [],
                "evidence": ["event:check"],
                "blocker": "",
                "plan": ["Implement and independently verify"],
                "affected_paths": ["greet.py", "test_greeting.py"],
            },
        }
if stage == "review_completion" and os.environ.get("AUTOCODE_FIXTURE_PERMISSION_LOOP"):
    number = len(data.get("saved_answers") or {})
    result.update(
        status="BLOCKED",
        blocker="The unchanged evaluator needs a separate maintainer correction",
        user_request={
            "kind": "permission",
            "discovered": "No source or validation progress",
            "impact": "The unchanged evaluator still blocks completion",
            "decision_needed": f"No new consent needed; execute the authorized correction (request {number})",
            "options": ["Execute the correction", "Leave paused"],
            "proposed_delta": f"Keep the plan unchanged; maintainer delivery remains absent ({number})",
        },
    )
if os.environ.get("AUTOCODE_FIXTURE_REPORT_REPAIR_STAGE") == stage:
    result.pop("summary", None)
if stage == "builder" and (data.get("workflow") or {}).get("mode") == "glm_final_audit_v2":
    valid = subprocess.run([sys.executable, "greet.py", "Ada"], capture_output=True, text=True)
    invalid = subprocess.run([sys.executable, "greet.py", ""], capture_output=True, text=True)
    passed = valid.returncode == 0 and valid.stdout == "Hello, Ada\n" and invalid.returncode == 2
    command = shlex.join(
        [
            sys.executable,
            "-c",
            "import subprocess, sys\nrun = lambda *a: subprocess.run([sys.executable, *a], capture_output=True, text=True)\nsys.exit(0 if run('greet.py', 'Ada').stdout == 'Hello, Ada\\n' and run('greet.py', '').returncode == 2 else 1)\n",
        ]
    )
    print(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "self-check",
                    "type": "command_execution",
                    "command": command,
                    "exit_code": 0 if passed else 1,
                    "aggregated_output": "fixture checks",
                },
            }
        )
    )
    assessment = {
        **common,
        "verdict": "PASS" if passed else "FAIL",
        "findings": [],
        "finding_dispositions": [],
        "checks_run": [command],
        "unverified_criteria": [],
        "checks": [{"command": command, "exit_code": 0 if passed else 1, "evidence_ref": "event:self-check"}],
        "end_to_end_result": {
            "status": "PASS" if passed else "FAIL",
            "summary": "Builder self-check",
            "evidence_refs": ["event:self-check"],
            "technical_result": None,
            "pending_human_criteria": [],
        },
        "criterion_results": [
            {"id": "C1", "status": "PASS" if passed else "FAIL", "evidence_refs": ["event:self-check"]}
        ],
    }
    second = data.get("next_action") == "Second Builder batch"
    consulted = bool(data.get("consultation_reports"))
    action = (
        ("REQUEST_FINAL_AUDIT" if consulted else "ESCALATE_SOL")
        if mode == "sol-escalation"
        else ("REQUEST_FINAL_AUDIT" if second else "CONTINUE")
    )
    result["untested_behavior"] = []
    result["continuation"] = {
        "action": action,
        "plan": ["Finish approved greeting"],
        "next_task": {
            "kind": "implement",
            "objective": "Second Builder batch",
            "affected_paths": ["greet.py", "test_greeting.py"],
            "milestone_id": "M1",
            "requirements": ["Verify the final greeting"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["Run valid and invalid CLI cases"],
        },
        "reason": "Fixture-specific debugging question" if action == "ESCALATE_SOL" else "Continue approved work",
        "question": "Check the empty input boundary" if action == "ESCALATE_SOL" else "",
        "self_assessment": assessment,
    }
validation_schema = schema.get("properties", {})
validation_report = result
if stage == "review_checkpoint":
    validation_schema = validation_schema.get("validation", {}).get("properties", {})
    validation_report = result.get("validation", {})
if stage in ("tester", "review_checkpoint") and "changed_code_exceptions" in validation_schema:
    # A report repair's schema may allow no reasons (maxItems 0); a schema-following Tester then gives none.
    cap = validation_schema["changed_code_exceptions"].get("maxItems")
    reasons = list(
        {
            row["gap_id"]: row
            for row in (
                scripted_files_changed_code_rejections(data, builder_files)
                + greeting_changed_code_reasons(data)
                + scripted_files_changed_code_reasons(data, builder_files)
            )
        }.values()
    )  # one reason per gap: the gate refuses a duplicate gap_id
    validation_report["changed_code_exceptions"] = quote_changed_lines(data, reasons[:cap])
criterion_row = validation_schema.get("criterion_results", {}).get("items", {}).get("properties", {})
if stage in ("tester", "review_checkpoint") and (not schema or "probe_reason" in criterion_row):
    # A scripted Tester runs no probe of its own beyond its one scripted CLI check, so it records why for
    # each PASS criterion (autocode_tester_probes), on either Tester route.
    for row in validation_report.get("criterion_results") or []:
        if row.get("status") == "PASS":
            row.setdefault(
                "probe_reason",
                f"Scripted fixture Tester: {row['id']} is checked by its fixed scripted CLI flow only; {_fixed_file()} holds the input",
            )
if "--output-schema" in sys.argv:
    if "workspace_scope" in schema.get("properties", {}):
        result.setdefault("workspace_scope", {"spans_repositories": False, "external_parts": []})
    if "progressive_checkpoint" in schema.get("properties", {}):
        result.setdefault("progressive_checkpoint", False)
    source_verdict(result)
# Legacy direct fixture launches may omit --output-schema; a sealed source_trace
# still needs a Completion Reviewer verdict (#595 / #1201).
elif stage == "review_completion" and ((contract.get("body") or {}).get("source_trace") is not None):
    result.setdefault(
        "source_verdict",
        {"verdict": "PASS", "uncovered": [], "summary": "Scripted: every criterion traces to the brief"},
    )
# New final reviews are verdicts on the exact current plan; old saved schemas
# continue to use the full fixture report. Keep deliberate invalid-task faults.
if stage == "finalize_plan" and "retain_contract" in schema.get("properties", {}).get("contract", {}).get(
    "properties", {}
):
    draft = result["contract"]
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
    result["contract"] = retained
    result["requirement_trace"] = []
# A planner records each part the person left out of this run, as its packet says (#2066).
if isinstance(result.get("contract"), dict) and "scope_exclusions" in result["contract"]:
    for entry in data.get("scope_exclusions_required") or []:
        if entry not in result["contract"]["scope_exclusions"]:
            result["contract"]["scope_exclusions"] = [*result["contract"]["scope_exclusions"], entry]
if "failure_class" in schema.get("properties", {}):
    # Builder-failure investigation reports gained an explicit classification
    # contract. Keep both the first scripted report and its report-only repair
    # schema-compliant so these fixtures test recovery rather than malformed
    # fake-provider output.
    result.setdefault("failure_class", "unknown")
    result.setdefault("failure_id", "offline-fixture-failure")
_conform_ledger(result, schema, f"{_sources[0]}:1" if _sources else "")
Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(result))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}))
