#!/usr/bin/env python3
"""Scripted `codex` stand-in for testing autocode_multicomponent.py.

Unlike the single-scenario fakes elsewhere, one process of this script must
answer for whichever component's worktree it is invoked in: it reads which
component from the brief text (autocode_multicomponent.component_brief always
starts "Implement the <id> component of..."), then applies that component's
own files from FAKE_MANIFEST (env var, a JSON file mapping id -> {"file":
relative path, "content": text, "check": shell command}, plus an optional
"files": {relative path: text} for a component that delivers several files;
"file" and "content" may then be left out).

Only the model is fake; AutoCode's CLI, approval gate, and evidence checks run
for real, once per component's own worktree.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

MANIFEST = json.loads(Path(os.environ["FAKE_MANIFEST"]).read_text())


def source_refs() -> list[str]:
    """AutoCode requires the requirements handoff to cite a real tracked file once
    the workspace has any (see autopilot._check_code_refs)."""
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
    return tracked[:8] or ["task"]


def find_component(text: str) -> str | None:
    match = re.search(r"Implement the (\S+) component", text)
    return match.group(1) if match and match.group(1) in MANIFEST else None


def requirements(component_id: str, spec: dict) -> list[dict]:
    return [{"id": "R1", "text": spec["description"], "source_quote": spec["description"]}]


def brief_of(data: dict) -> str:
    """The component brief this stage was given: the task, or, in a requirements
    report repair, the first source text (the task again)."""
    return data.get("task") or next(iter(data.get("source_texts") or []), "") or ""


def ignored_statements(component_id: str, data: dict) -> list[str]:
    """The brief's sentences read as cue sentences (only/do not/must/required) by the
    real requirement-coverage check: its process instructions, any embedded contract
    schema, a runtime block's instructions. A real model would quote or classify
    each; this fake has one scripted requirement, so it lists the whole component
    brief, which contains every cue sentence verbatim. Without the brief in the
    handoff, the two process sentences every brief has."""
    if brief_of(data):
        return [brief_of(data)]
    return [f"Own only the directory components/{component_id}/; do not create or edit any file outside it.",
            "Do not implement or stub another component's directory; integration happens separately."]


def deliverables(spec: dict) -> dict[str, str]:
    """Every file the component delivers, path -> content."""
    files = {spec["file"]: spec["content"]} if "file" in spec else {}
    files.update(spec.get("files") or {})
    return files


def contract(spec: dict, final: bool = False, task: str = "") -> dict:
    paths = list(deliverables(spec))
    # The whole brief, verbatim, keeps every backticked literal it states (contract names
    # among them), which the runner checks each planner draft for (autocode_brief_literals).
    approach = ["Scripted fake writes the component's files"] + ([f"Brief, kept verbatim: {task}"] if task else [])
    body = {
        "intended_outcome": spec["description"], "intended_user": "Another component", "end_to_end_flow": [spec["check"]],
        "technical_approach": approach,
        "milestones": [{"id": "M1", "objective": spec["description"], "acceptance_criteria": ["C1"], "depends_on": [],
                        "affected_paths": paths}],
        "deliverables": paths, "required_behaviors": [spec["description"]],
        "important_failure_cases": ["The check command fails"], "scope_exclusions": ["Anything outside this component", "Visual acceptance"],
        "constraints": ["Only write files under this component's own directory"],
        "permission_boundaries": ["Read and edit only this component's worktree"],
        "accepted_assumptions": [{"text": "The scripted deliverable is correct", "basis": "agent_proposed", "answer_id": ""}],
        "delegated_decisions": [],
        "acceptance_criteria": [{"id": "C1", "criterion": "The check command passes",
                                 "verification_method": f"test: {spec['test']}" if spec.get("test") else spec["check"],
                                 "human_review": False}],
        "open_blocking_questions": [],
    }
    if final:
        body["initial_task"] = {"kind": "implement", "milestone_id": "M1", "objective": spec["description"],
                                "affected_paths": paths, "requirements": [spec["description"]],
                                "acceptance_criteria": ["C1"], "validation_plan": [spec["check"]]}
    for field in ("end_to_end_flow", "permission_boundaries"):
        if field in spec:
            body[field] = spec[field]
    if "plan_paths" in spec:
        body["milestones"][0]["affected_paths"] = spec["plan_paths"]
        if final:
            body["initial_task"]["affected_paths"] = spec["plan_paths"]
    return body


def run_check(spec: dict) -> int:
    proc = subprocess.run(spec["check"], shell=True, capture_output=True, text=True, timeout=120)
    print(json.dumps({"type": "item.completed", "item": {
        "id": "check", "type": "command_execution", "command": spec["check"],
        "exit_code": proc.returncode, "aggregated_output": (proc.stdout + proc.stderr)[-2000:]}}), flush=True)
    return proc.returncode


PROMPT = ""


def report_for(stage: str, component_id: str, spec: dict, data: dict) -> dict:
    task = data.get("current_task") or {}
    revision = data.get("goal_contract") or {"revision": 0, "hash": ""}
    common = {"contract_revision": revision.get("revision", 0), "contract_hash": revision.get("hash", ""),
             "task_id": task.get("id", ""), "deferred_backlog": [],
             "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                              "options": [], "proposed_delta": ""}}
    planning = {"code_refs": source_refs(), "contract_changes": [], "conflict_resolutions": [],
               "requirement_trace": [{"requirement_id": "R1", "disposition": "covered", "evidence": spec["description"]}]}
    if stage == "recognize_workflow":
        # Vague keeps the Requirements stage this fake's requirement trace relies on (adaptive planning is the default).
        return {"workflow": "build", "reason": "Scripted: component builds are builds", "signals": [], "design_document": "",
                **({"clarity": "vague"} if 'Add "clarity"' in PROMPT else {})}
    if stage == "investigate_stuck":
        return {"diagnosis": "Offline fixture: it cannot diagnose; the run pauses as before.", "cause": "other", "guidance": "", "recommendation": "pause", "user_question": "", "evidence_refs": [], "example": "", "probe": "", "untestable": ""}
    if stage == "requirements_gather":
        return {"summary": "Scripted component requirements", "intended_outcome": spec["description"],
                "required_behaviors": [spec["description"]], "constraints": [], "acceptance_tests": [spec["check"]],
                "source_refs": source_refs(), "proposed_assumptions": [], "open_questions": [],
                "requirements": requirements(component_id, spec),
                "ignored_statements": ignored_statements(component_id, data), "conflicts": [], "proposed_reframes": []}
    adaptive = "ADAPTIVE PLANNING" in PROMPT  # an adaptive Planner's draft carries its initial_task
    task = brief_of(data)
    if stage == "astra_discovery":
        return {"summary": "Scripted component plan", "contract": contract(spec, final=adaptive, task=task),
                "alternatives": [], "uncertainties": [], **planning}
    if stage == "astra_challenge":
        return {"summary": "Scripted plan review: no concerns", "concerns": []}
    if stage == "glm_revise":
        return {"summary": "Scripted revision: nothing to revise",
                "contract": contract(spec, final=adaptive, task=task), "responses": [], **planning}
    if stage == "astra_finalize":
        final = {key: value for key, value in planning.items() if key != "code_refs"}
        return {"summary": "Scripted final component plan", "contract": contract(spec, final=True, task=task),
                "decisions": [], **final}
    if stage == "terra":
        for name, content in deliverables(spec).items():
            path = Path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        if spec.get("reaccept_ui_run"):
            # Simulate an external UI workflow accepting a replacement design
            # while this component is building against the previous acceptance.
            directory = Path(spec["reaccept_ui_run"])
            handoff = json.loads((directory / "handoff.json").read_text())
            ref = handoff["artifacts"]["brief"]
            brief = directory / ref["path"]
            brief.write_text("Accepted replacement alpha layout during implementation.")
            ref["sha256"] = hashlib.sha256(brief.read_bytes()).hexdigest()
            (directory / "handoff.json").write_text(json.dumps(handoff))
        code = run_check(spec)
        return {**common, "summary": "Wrote the component's files", "changed_files": list(deliverables(spec)),
                "commands_run": [spec["check"]], "results": [f"exit {code}"], "remaining_risks": [],
                "evidence_refs": ["event:check"], "addressed_requirements": ["C1"], "untested_behavior": [],
                "recommended_checks": [spec["check"]]}
    if stage in ("sol", "astra_checkpoint"):
        code = run_check(spec)
        status = "PASS" if code == 0 else "FAIL"
        return {**common, "verdict": status, "checks_run": [spec["check"]], "findings": [], "finding_dispositions": [],
                "unverified_criteria": [], "checks": [{"command": spec["check"], "exit_code": code, "evidence_ref": "event:check"}],
                "criterion_results": [{"id": "C1", "status": status, "evidence_refs": ["event:check"]}],
                "end_to_end_result": {"status": status, "summary": f"{spec['check']} exited {code}",
                                      "evidence_refs": ["event:check"], "technical_result": None,
                                      "pending_human_criteria": []}}
    if stage in ("astra_review", "astra_plan", "astra_resolve"):
        return {**common, "status": "COMPLETE",
                "acceptance_criteria": [{"id": "C1", "criterion": "The check command passes",
                                         "status": "verified", "evidence": "event:check"}],
                "evidence": ["event:check"], "next_objective": "", "blocker": "", "plan": [], "affected_paths": [],
                "findings": [], "finding_dispositions": [], "agreed_limitations": [],
                "next_task": {"kind": "none", "milestone_id": "", "requirements": [], "acceptance_criteria": [],
                              "validation_plan": [], "findings": []}}
    raise SystemExit(f"multicomponent_fake: no scripted report for stage {stage!r}")


def component_id_for(prompt: str, data: dict) -> str:
    """A report-repair prompt asks only to reformat a prior answer; it does not
    repeat the original brief, so the component id is read from the archived
    original prompt file it points to instead of the current prompt text."""
    found = find_component(prompt)
    if found:
        return found
    original_prompt_path = (data.get("original") or {}).get("prompt")
    found = original_prompt_path and find_component(Path(original_prompt_path).read_text())
    if not found:
        raise SystemExit(f"multicomponent_fake: no manifest entry found for this stage's brief")
    return found


def complete(value, schema):
    """Give every required field the script leaves out an empty value of its type (same as
    scenarios/harness/fake_codex.py): new report fields then mean "none", not a crash."""
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), "null")
    if kind == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value and key in properties:
                sub = properties[key]
                sub_kind = sub.get("type")
                if isinstance(sub_kind, list):
                    sub_kind = "null" if "null" in sub_kind else sub_kind[0]
                value[key] = (sub["enum"][0] if sub.get("enum") else
                              {"object": lambda: complete({}, sub), "array": list, "string": str, "boolean": bool,
                               "integer": int, "number": float}.get(sub_kind, lambda: None)())
        for key, sub in properties.items():
            if key in value:
                complete(value[key], sub)
    elif kind == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        for item in value:
            complete(item, schema["items"])
    return value


def main() -> int:
    if sys.argv[1:] == ["login", "status"]:
        print("Logged in using ChatGPT (multicomponent fake)")
        return 0
    session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
    global PROMPT
    prompt = PROMPT = sys.stdin.read()
    print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)
    if "CURRENT HANDOFF DATA\n" not in prompt:
        print(json.dumps({"error": "no handoff data"}))
        return 0
    data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
    original = data.get("original") or {}
    stage = data.get("stage") or original.get("stage") or ""
    if data.get("report_repair"):
        stage = original.get("stage", stage)
    if data.get("report_repair"):
        # A repair envelope is not a new task handoff. Preserve the original
        # report identity, failures and event references without rerunning work.
        # This fixture cannot manufacture evidence to fix a semantic rejection.
        supplied = data.get("original_report") or data.get("rejected_report") or {}
        if not isinstance(supplied.get("content"), dict):
            raise SystemExit("multicomponent_fake: report repair needs the supplied structured report")
        report = json.loads(json.dumps(supplied["content"]))
    else:
        component_id = component_id_for(prompt, data)
        spec = MANIFEST[component_id]
        if spec.get("observations"):
            # Optional external fixture output: assert what the real CLI sent to the
            # provider without peeking into the task run's private state.json.
            directory = Path(spec["observations"])
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{component_id}-{stage}-{uuid.uuid4().hex}.json").write_text(json.dumps(
                {"component_id": component_id, "stage": stage, "prompt": prompt}))
        report = report_for(stage, component_id, spec, data)
    if "--output-schema" in sys.argv:
        complete(report, json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text()))
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(report))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
