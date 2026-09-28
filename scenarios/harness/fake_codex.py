#!/usr/bin/env python3
"""Scripted stand-in for the ``codex`` CLI, used by ``run --fake``.

It plans from the scenario brief, "builds" by copying the scenario's reference
solution into the workspace, and validates by really running the scenario's
check command. Only the model is fake: AutoCode's CLI, state machine, approval
gates and evidence checks run for real. This proves the harness and AutoCode's
plumbing for a scenario; it says nothing about model quality.

Configuration comes from the JSON file named by SCENARIO_FAKE_CONFIG:
{"title", "brief", "reference", "check", "paths"}.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

CONFIG = json.loads(Path(os.environ["SCENARIO_FAKE_CONFIG"]).read_text())
PROMPT = ""
DATA: dict = {}
CHECK = CONFIG["check"]
PATHS = CONFIG["paths"]


def requirements() -> list[dict]:
    """One requirement per sentence, quoted verbatim, as AutoCode's planner rules demand."""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", CONFIG["brief"].strip()) if part.strip()]
    return [{"id": f"R{number}", "text": sentence, "source_quote": sentence}
            for number, sentence in enumerate(sentences, start=1)]


def source_refs() -> list[str]:
    """AutoCode requires planning to cite real files once the workspace has any."""
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
    return tracked[:8] or ["task"]


def trace() -> list[dict]:
    return [{"requirement_id": row["id"], "disposition": "covered", "evidence": row["text"]}
            for row in requirements()]


def outcome() -> str:
    """What the request asks for, in its own words (its first sentence)."""
    first = re.split(r"(?<=[.!?])\s+", CONFIG["brief"].strip(), maxsplit=1)[0]
    return first[:240]


def approach() -> list[str]:
    """The fix the saved diagnosis proposes when there is one; otherwise the request itself."""
    notes = sorted((Path(CONFIG["reference"]) / "docs" / "bugs").glob("*.json"))
    fix = json.loads(notes[0].read_text()).get("fix", "") if notes else ""
    return [fix or f"Implement the requested change: {outcome()}"]


def contract(final: bool = False) -> dict:
    body = {
        "intended_outcome": outcome(),
        "intended_user": "The person who made the request",
        "end_to_end_flow": ["Make the change", f"Run {CHECK}"],
        "technical_approach": approach(),
        "milestones": [{"id": "M1", "objective": outcome(), "acceptance_criteria": ["C1"],
                        "depends_on": [], "affected_paths": PATHS}],
        "deliverables": PATHS,
        "required_behaviors": [row["text"] for row in requirements()],
        "important_failure_cases": ["The scenario check command fails"],
        "scope_exclusions": ["Anything outside the scenario brief"],
        "constraints": ["Change only the paths the reference solution touches"],
        "permission_boundaries": ["Read and edit only this scenario workspace"],
        "accepted_assumptions": [{"text": "The request describes the intended behavior completely",
                                  "basis": "agent_proposed", "answer_id": ""}],
        "delegated_decisions": [],
        "acceptance_criteria": [{"id": "C1", "criterion": "The project's checks pass with the change in place",
                                 "verification_method": CHECK, "human_review": False}],
        "open_blocking_questions": [],
    }
    if (DATA.get("bug_diagnosis") or {}).get("root_cause"):
        body["task_kind"] = "bugfix"  # planned from a bug diagnosis
    if final:
        body["initial_task"] = {"kind": "implement", "milestone_id": "M1", "objective": outcome(),
                                "affected_paths": PATHS, "requirements": [requirements()[0]["text"]],
                                "acceptance_criteria": ["C1"], "validation_plan": [CHECK]}
    return body


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def run_check() -> int:
    proc = subprocess.run(CHECK, shell=True, capture_output=True, text=True, timeout=600)
    emit({"type": "item.completed", "item": {
        "id": "check", "type": "command_execution", "command": CHECK,
        "exit_code": proc.returncode, "aggregated_output": (proc.stdout + proc.stderr)[-2000:]}})
    return proc.returncode


def recognize(brief: str) -> dict:
    """The fake's script for the job-recognition stage: keyword rules over the brief.

    This is not a model and says nothing about model quality; it exists so the
    plumbing (the stage runs, its answer reaches the status view) can be proven
    offline. A live profile is what tests recognition itself.
    """
    text = brief.lower()

    def has(*patterns):
        return any(re.search(pattern, text) for pattern in patterns)

    if has(r"\bimplement (it|this|the design)\b", r"has already been .*approved") and not has(r"do(n't| not) implement anything"):
        kind, signal = "build", "implement it / already approved"
    elif has(r"\bdesign\b") and has(r"\breview\b", r"do(n't| not) implement", r"\bdesign how\b", r"^design\b"):
        kind, signal = "design", "design + review/don't implement"
    elif has(r"\breview\b", r"look over", r"safe to merge", r"\bpr[- ]?\d+", r"\.patch\b", r"\bdiff\b"):
        kind, signal = "review", "review/patch"
    elif has(r"\bfix\b", r"figure out why", r"stopped (working|being)", r"\bbug\b", r"duplicat", r"\btwice\b"):
        kind, signal = "bugfix", "fix/why/duplicates"
    elif text.rstrip().endswith("?") or has(r"^should ", r"^why ", r"^what would", r"want to understand", r"the analysis"):
        kind, signal = "discuss", "a question"
    else:
        kind, signal = "build", "no other signal"
    named = re.search(r"(docs/design/[\w./-]+\.md)", brief)
    design = named.group(1) if kind == "build" and named and has(r"approved") else ""
    return {"workflow": kind, "reason": f"Scripted keyword rule: {signal}", "signals": [signal],
            "design_document": design}


def check_design(data: dict) -> dict:
    """The fake's design check: the conflicts in the solution's <design>.blockers.json, if it has one
    (the run must stop), otherwise no conflicts and one binding decision (planning goes ahead)."""
    design = data.get("design_document") or ""
    blockers = Path(CONFIG["reference"]) / Path(design).with_suffix(".blockers.json") if design else None
    conflicts = json.loads(blockers.read_text()).get("conflicts", []) if blockers and blockers.is_file() else []
    return {"design_document": design, "summary": "Scripted design check from the scenario solution",
            "constraints": [] if conflicts else [f"Implement {design} exactly as written"], "conflicts": conflicts}


def stray_edits(allowed: str) -> None:
    """Behave like a model that edits files it was told not to: copy every solution file outside
    ``allowed`` into the workspace. A read-only job's runner must then reject the attempt, which is
    how a broken solution such as review-planted-defects/broken/edits-code is proven to be caught."""
    root = Path(CONFIG["reference"])
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and not relative.startswith(allowed) and "__pycache__" not in relative:
            target = Path.cwd() / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def review() -> dict:
    """The fake's review: the findings from the solution it was told to apply (reference or broken).
    The runner writes review/findings.json from this report. In a scenario with
    follow-up turns the solution is the end state of the whole conversation, whose
    code changes belong to a later turn, so the review does not play them as stray edits."""
    if not CONFIG.get("turns"):
        stray_edits("review/")
    path = Path(CONFIG["reference"]) / "review" / "findings.json"
    saved = json.loads(path.read_text()) if path.is_file() else {}
    # Targeted tests in the solution are delivered into the workspace under review/tests/,
    # the one place a review may write (autocode_review_job.TESTS_PREFIX).
    tests = Path(CONFIG["reference"]) / "review" / "tests"
    delivered = []
    if tests.is_dir():
        shutil.copytree(tests, Path.cwd() / "review" / "tests", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        delivered = sorted(f"review/tests/{p.name}" for p in tests.glob("test_*.py"))
    return {"verdict": saved.get("verdict", "approve"), "summary": "Scripted review from the scenario solution",
            "change_under_review": "the change named in the request",
            "findings": [{key: f.get(key, [] if key == "lines" else "") for key in
                          ("id", "severity", "file", "lines", "summary", "evidence")} for f in saved.get("findings", [])],
            "tests_run": [str(t) for t in saved.get("tests_run", [])], "delivered_tests": delivered}


def investigate() -> dict:
    """The fake's investigation: the diagnosis note in the solution it was told to apply, if any.
    A note with "reproduced": false ends the run; anything else hands over to the build pipeline,
    where the fake Builder applies the solution. The runner writes the note; the fake writes nothing."""
    notes = sorted((Path(CONFIG["reference"]) / "docs" / "bugs").glob("*.json"))
    saved = json.loads(notes[0].read_text()) if notes else {}
    reproduced = saved.get("reproduced", True) is not False
    text = lambda *keys: next((str(saved[key]) for key in keys if saved.get(key)), "")
    return {"outcome": "reproduced" if reproduced else "not_reproduced",
            "note_path": f"docs/bugs/{notes[0].name}" if notes else "docs/bugs/scripted-diagnosis.json",
            "observed": text("observed", "observed_by_reporter") or CONFIG["title"],
            "reproduction": text("reproduction", "reproduction_attempted") or "Scripted reproduction",
            "root_cause": text("root_cause") or ("Scripted root cause" if reproduced else ""),
            "affected_paths": (saved.get("affected_paths") or [p for p in PATHS if p.endswith(".py") and "test" not in p])
                              if reproduced else [],
            "test_paths": ([p for p in PATHS if "test" in p] or ["tests/"]) if reproduced else [],
            "invariant": text("invariant") or ("Scripted invariant" if reproduced else ""),
            "conclusion": text("conclusion", "finding", "fix") or "Scripted conclusion",
            "fix_size": (saved.get("fix_size") or "small") if reproduced else "none",
            "fix_plan": [text("fix")] if reproduced and saved.get("fix") else [],
            "questions": [str(q) for q in saved.get("questions", [])], "tests_run": ["scripted"],
            "plan_approval_requested": bool(saved.get("plan_approval_requested"))}


def design() -> dict:
    """The fake's Architect: the design review in the solution it was told to apply, if any. With none
    (a request for a new design, such as architecture-two-services), it hands over to the build pipeline."""
    path = Path(CONFIG["reference"]) / "review" / "design-review.json"
    if path.is_file():
        stray_edits("review/")
    if not path.is_file():
        return {"mode": "propose", "design_under_review": "", "verdict": "not_applicable",
                "summary": "A new design is requested", "satisfied": [], "concerns": [], "questions": []}
    saved = json.loads(path.read_text())
    concerns = [{key: str(c.get(key, "")) for key in ("id", "area", "severity", "summary", "evidence")}
                for c in saved.get("concerns", [])]
    return {"mode": "review", "design_under_review": "the design named in the request",
            "verdict": "request_changes" if any(c["severity"] == "blocking" for c in concerns) else "approve",
            "summary": "Scripted design review from the scenario solution",
            "satisfied": [str(s) for s in saved.get("satisfied", [])], "concerns": concerns,
            "questions": [{"id": str(q.get("id", "")), "question": str(q.get("question", "")),
                           "options": [str(o) for o in q.get("options", [])]} for q in saved.get("questions", [])]}


def answer() -> dict:
    """The fake's Analyst: the note in the solution it was told to apply (the one file under docs/),
    returned as note_path/note_content for the runner to write; stray solution files are applied
    like a model that edits code it was told not to, so the runner's read-only check is exercised."""
    root = Path(CONFIG["reference"])
    notes = sorted(p for p in (root / "docs").rglob("*") if p.is_file() and p.name != "README.md") \
        if (root / "docs").is_dir() else []
    stray_edits("docs/")
    tracked = [ref for ref in source_refs() if ref != "task"]
    note = notes[0] if notes else None
    return {"answer": "Scripted answer from the scenario solution",
            "evidence": [{"claim": "Scripted evidence", "source": tracked[0] if tracked else "README.md"}],
            "questions": [], "note_path": note.relative_to(root).as_posix() if note else "",
            "note_content": note.read_text() if note else ""}


def report_for(stage: str, data: dict) -> dict:
    if stage == "recognize_workflow":
        return recognize(data.get("task") or CONFIG["brief"])
    if stage == "answer_question":
        return answer()
    if stage == "check_design":
        return check_design(data)
    if stage == "investigate_stuck":
        # A scripted run that got stuck is a scenario defect; pause and say so.
        return {"diagnosis": "Offline fixture: it cannot diagnose; the run pauses as before.", "cause": "other", "guidance": "", "recommendation": "pause", "user_question": "", "evidence_refs": []}
    if stage == "review_change":
        return review()
    if stage == "review_design":
        return design()
    if stage == "investigate_bug":
        return investigate()
    task = data.get("current_task") or {}
    revision = data.get("goal_contract") or {"revision": 0, "hash": ""}
    common = {
        "contract_revision": revision.get("revision", 0), "contract_hash": revision.get("hash", ""),
        "task_id": task.get("id", ""), "deferred_backlog": [],
        "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                         "options": [], "proposed_delta": ""},
    }
    planning = {"code_refs": [ref for ref in source_refs() if ref != "task"], "contract_changes": [], "conflict_resolutions": [], "requirement_trace": trace()}
    if stage == "requirements_gather":
        return {"summary": "Scripted requirements: one per brief sentence",
                "intended_outcome": outcome(), "required_behaviors": [r["text"] for r in requirements()],
                "constraints": [], "acceptance_tests": [CHECK], "source_refs": source_refs(),
                "proposed_assumptions": [], "open_questions": [], "requirements": requirements(),
                "ignored_statements": [], "conflicts": [], "proposed_reframes": []}
    if stage == "astra_discovery":
        report = {"summary": "Scripted plan", "contract": contract(), "alternatives": [], "uncertainties": [], **planning}
        if CONFIG.get("fault") == "planner_citation" and not guided_to_fix_citation():
            report["code_refs"] = [f"{note} (saved diagnosis: observed, reproduction, root cause)" for note in bug_notes()]
        return report
    if stage == "astra_challenge":
        return {"summary": "Scripted plan review: no concerns", "concerns": []}
    if stage == "glm_revise":
        return {"summary": "Scripted revision: nothing to revise", "contract": contract(), "responses": [], **planning}
    if stage == "astra_finalize":
        final = {key: value for key, value in planning.items() if key != "code_refs"}
        return {"summary": "Scripted final plan", "contract": contract(final=True), "decisions": [], **final}
    if stage == "terra":
        shutil.copytree(CONFIG["reference"], Path.cwd(), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        code = run_check()
        return {**common, "summary": "Applied the scenario reference solution", "changed_files": PATHS,
                "commands_run": [CHECK], "results": [f"exit {code}"], "remaining_risks": [],
                "evidence_refs": ["event:check"], "addressed_requirements": ["C1"],
                "untested_behavior": [], "recommended_checks": [CHECK]}
    if stage in ("sol", "astra_checkpoint"):
        code = run_check()
        status = "PASS" if code == 0 else "FAIL"
        return {**common, "verdict": status, "checks_run": [CHECK], "findings": [],
                "finding_dispositions": [], "unverified_criteria": [],
                "checks": [{"command": CHECK, "exit_code": code, "evidence_ref": "event:check"}],
                "criterion_results": [{"id": "C1", "status": status, "evidence_refs": ["event:check"]}],
                "end_to_end_result": {"status": status, "summary": f"{CHECK} exited {code}",
                                      "evidence_refs": ["event:check"]}}
    if stage in ("astra_review", "astra_plan", "astra_resolve"):
        # Echo the approved contract's criteria: the runner rejects a report that restates them
        # differently. A contract the fake did not plan (a bug-fix correction) has its own C1.
        approved = ((data.get("goal_contract") or {}).get("body") or {}).get("acceptance_criteria") \
            or [{"id": "C1", "criterion": "The scenario check command passes"}]
        return {**common, "status": "COMPLETE",
                "acceptance_criteria": [{"id": row["id"], "criterion": row["criterion"],
                                         "status": "verified", "evidence": "event:check"} for row in approved],
                "evidence": ["event:check"], "next_objective": "", "blocker": "", "plan": [],
                "affected_paths": [], "findings": [], "finding_dispositions": [], "agreed_limitations": [],
                "next_task": {"kind": "none", "milestone_id": "", "requirements": [],
                              "acceptance_criteria": [], "validation_plan": [], "findings": []}}
    raise SystemExit(f"fake_codex: no scripted report for stage {stage!r}")


def bug_notes() -> list[str]:
    return [f"docs/bugs/{path.name}" for path in sorted((Path(CONFIG["reference"]) / "docs" / "bugs").glob("*.json"))]


def guided_to_fix_citation() -> bool:
    """Fault "planner_citation" (scenarios/catalog/stuck-planner-citation): the scripted Planner
    repeats a mistake seen live, citing the bug note with prose after its path, until an
    Investigator's guidance in its prompt names the actual problem (code_refs, or the exact path).
    Vague guidance leaves it stuck: the scenario judges the real Investigator, not this script."""
    marker = "INVESTIGATOR GUIDANCE"
    if marker not in PROMPT:
        return False
    guidance = PROMPT.split(marker, 1)[1].split("CURRENT HANDOFF DATA", 1)[0]
    return "code_refs" in guidance or "code_ref" in guidance.lower() or any(note in guidance for note in bug_notes())


def complete(value, schema: dict):
    """Give every required field the script leaves out an empty value of its schema type.

    The runner requires every field in fresh output, and AutoCode keeps adding optional-in-
    spirit fields (lists of things found, flags). A scripted report means "none of those",
    as a real model would say; without this, each new field stops every scripted run.
    """
    kind = schema.get("type")
    kind = kind[0] if isinstance(kind, list) else kind
    if kind == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value and key in properties:
                value[key] = empty(properties[key])
        for key, sub in properties.items():
            if key in value:
                complete(value[key], sub)
    elif kind == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        for item in value:
            complete(item, schema["items"])
    return value


def empty(schema: dict):
    kind = schema.get("type")
    kind = kind[0] if isinstance(kind, list) else kind
    if schema.get("enum"):
        return schema["enum"][0]
    return {"object": lambda: complete({}, schema), "array": list, "string": str, "boolean": bool,
            "integer": int, "number": float, "null": lambda: None}.get(kind, lambda: None)()


def main() -> int:
    if sys.argv[1:] == ["login", "status"]:
        print("Logged in using ChatGPT (scenario fake provider)")
        return 0
    global PROMPT
    prompt = PROMPT = sys.stdin.read()
    session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
    emit({"type": "thread.started", "thread_id": session})
    if "CURRENT HANDOFF DATA\n" not in prompt:
        emit({"error": "no handoff data"})
        return 0
    global DATA
    data = DATA = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
    original = data.get("original") or {}
    stage = data.get("stage") or original.get("stage") or ""
    if data.get("report_repair"):
        # Report repairs are answered as the stage that owns them.
        stage = original.get("stage", stage)
    report = report_for(stage, data)
    if "--output-schema" in sys.argv:
        complete(report, json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text()))
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(report))
    # Plausible usage, so a reader of the events is not misled by an all-zero turn.
    emit({"type": "turn.completed", "usage": {"input_tokens": max(1, len(prompt) // 4),
                                              "output_tokens": max(1, len(json.dumps(report)) // 4)}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
