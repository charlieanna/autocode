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
# Multi-milestone scenarios (parallel-diamond) declare their graph here; the
# scripted plan, builds and per-milestone validation follow it, and AutoCode's
# real orchestrator does the parallel scheduling.
MILESTONES = CONFIG.get("milestones") or []
PROGRESSIVE = CONFIG.get("fault", "").startswith("progressive_")
RENEWED_OUTCOME = "Retain durable lesson answers and expose a browsable lesson catalog"
SCOPED_NOTE = "# Scoped consent: lesson note"
SCOPED_DENIAL = "No. Do not add the optional note anywhere. Deliver the original goal using the offline fallback."
SCOPED_CONDITION = "Yes, append only '# Scoped consent: lesson note' to lessons.py. Do not add this note to progress.py or any other file."
SCOPED_REQUEST = {"kind": "permission",
                  "discovered": "An optional explanatory comment can be added to lessons.py, already inside the current task's approved paths",
                  "impact": "The optional note requires consent; denial leaves an offline implementation of the original goal available",
                  "decision_needed": "May I append only '# Scoped consent: lesson note' to lessons.py?",
                  "options": [SCOPED_DENIAL, SCOPED_CONDITION],
                  "proposed_delta": "No goal, scope, criterion, behavior, filesystem, provider or spending change. Optional comment only in lessons.py within the existing approved paths."}


def renewal_proposal(later=False, done=None):
    body = (DATA.get("goal_contract") or {}).get("body") or {}
    cid = body["acceptance_criteria"][0]["id"]
    head = {"id": "S3", "intended_result": "Browse lessons while retaining saved answers",
            "criterion_ids": [cid], "paths": PATHS, "depends_on": [], "tentative": False,
            "checks": [{"id": "N", "relation": "contributes_to", "criterion_ids": [cid],
                        "method": "python3 -m unittest test_change.NewJourney"},
                       {"id": "PRODUCT", "relation": "fully_verify", "criterion_ids": [cid],
                        "method": CHECK}]}
    final = {"id": "S4", "intended_result": "Prove the retained answers and complete browsable catalog",
             "criterion_ids": [cid], "paths": PATHS, "depends_on": ["S3"], "tentative": not later,
             "checks": [{"id": "F", "relation": "fully_verify", "criterion_ids": [cid],
                         "method": "python3 -m unittest test_change"}]}
    return {"version": 1, "needed_because": "Deliver catalog browsing before fresh whole-product proof",
            "shared_decisions": ["Retain source modules, cumulative saved-answer proof and prior history"],
            "outstanding_criteria": [], "done_slices": done or [],
            "slices": [final] if later else [head, final]}


def progressive_proposal(later=False, done=None):
    """Scripted model proposal, never approval or a runner-owned receipt."""
    if ((DATA.get("goal_contract") or {}).get("body") or {}).get("intended_outcome") == RENEWED_OUTCOME:
        return renewal_proposal(later, done)
    first = {"id": "S1", "intended_result": "Open a lesson, answer and reopen durable progress",
             "criterion_ids": ["C1"], "paths": PATHS, "depends_on": [], "tentative": False,
             "checks": [{"id": "A", "relation": "contributes_to", "criterion_ids": ["C1"],
                         "method": "python3 -m unittest test_journey.Skeleton"}]}
    second = {"id": "S2", "intended_result": "Recommend the next lesson from retained durable progress",
              "criterion_ids": ["C1"], "paths": PATHS, "depends_on": ["S1"], "tentative": not later,
              "checks": [{"id": "B", "relation": "contributes_to", "criterion_ids": ["C1"],
                          "method": "python3 -m unittest test_journey.Recommendation"},
                         {"id": "PRODUCT", "relation": "fully_verify", "criterion_ids": ["C1"],
                          "method": CHECK}]}
    slices = [second] if later else [first, second]
    done = (["S1"] if later else []) if done is None else done
    if later and CONFIG.get("fault") in ("progressive_split", "progressive_split_retry"):
        child = {**second, "id": "S2a", "intended_result": "NEW: recommend using the earlier saved progress",
                 "checks": [second["checks"][0]], "tentative": False}
        final = {**second, "id": "S2b", "intended_result": "NEW: verify the complete retained learning journey",
                 "depends_on": ["S2a"], "checks": [{**second["checks"][1],
                 "method": "python3 -m unittest test_journey.Product"}], "tentative": True}
        if "S2a" in done:
            final["tentative"] = False
            slices = [final]
        else:
            slices = [child, final]
    if CONFIG.get("fault") == "progressive_reorder":
        third = {**second, "id": "S3", "intended_result": "Demonstrate recommendation before complete course proof",
                 "checks": [second["checks"][0]], "tentative": not later}
        if not later:
            slices = [first, second, third]
        elif "S3" in done:
            slices = [second]
        else:
            second["tentative"] = True
            slices = [third, second]
    if CONFIG.get("fault") == "progressive_undetailed_future":
        slices[-1]["tentative"] = False
    if CONFIG.get("fault") == "progressive_malformed_future":
        slices[-1]["checks"] = []
    return {"version": 1, "needed_because": "Durable answer-and-persist delivery is useful before recommendations",
            "shared_decisions": ["Keep lessons.py and progress.py and the fixed whole-product criterion"],
            "outstanding_criteria": [], "done_slices": done, "slices": slices}


def progressive_task(later=False, proposal=None):
    head = (proposal or progressive_proposal(later))["slices"][0]
    if not later and CONFIG.get("fault") == "progressive_initial_future_task":
        head = progressive_proposal()["slices"][-1]
    validate_only = head["id"] in ("S2b", "S4") or (later and CONFIG.get("fault") == "progressive_reorder" and head["id"] == "S2")
    task = {"kind": "validate" if validate_only else "implement", "milestone_id": "M1", "objective": head["intended_result"],
            "affected_paths": PATHS, "requirements": [requirements()[0]["text"]],
            "acceptance_criteria": head["criterion_ids"], "validation_plan": [check["method"] for check in head["checks"]]}
    if not later and CONFIG.get("fault") == "progressive_initial_broad_task":
        task["affected_paths"] = [*PATHS, "outside.py"]
    return task


def progressive_report(stage, data, common):
    """Respond only to actual progressive revision/verification handoffs."""
    packet = data.get("progressive_revision")
    if packet:
        if stage == "glm_revise" and packet.get("phase") == "detail":
            previous = data["previous_plan"]["proposal"]
            done = [*previous["done_slices"], previous["slices"][0]["id"]]
            if data["goal_contract"]["body"].get("intended_outcome") == RENEWED_OUTCOME:
                # Historic done IDs are runtime-provided, not invented receipts.
                done = data["progressive"]["done_slices"]
            proposal = progressive_proposal(True, done=done)
            return {"summary": "Detail S2 against retained S1 source", "progressive_proposal":
                    proposal, "initial_task": progressive_task(True, proposal=proposal)}
        if stage == "astra_finalize" and packet.get("phase") == "review":
            if CONFIG.get("fault") == "progressive_stale_source":
                path = Path.cwd() / "lessons.py"
                path.write_text(path.read_text() + "\n# Unauthorized edit during read-only independent review.\n")
            permission_change = (CONFIG.get("fault") == "progressive_permission_change"
                                 and data["goal_contract"]["body"].get("intended_outcome") != RENEWED_OUTCOME)
            return {"summary": ("The proposed catalog export requires learner-chosen output paths outside the workspace; "
                                "obtain scoped permission and a revised reviewed plan before proceeding" if permission_change else
                                "Independent review preserves fixed product coverage and cumulative A+B"),
                    "accepted": not (CONFIG.get("fault") == "progressive_split_retry" and not packet.get("feedback")),
                    "product_changes": False, "permission_changes": permission_change,
                    "unresolved_product_decisions": False}
        raise SystemExit("fake_codex: unsupported progressive revision packet")
    verification = data.get("progressive_verification")
    if not verification:
        return None
    head = verification["active_slice"]
    full_product = (any(check["relation"] == "fully_verify" for check in verification["required_checks"])
                    and not verification["outstanding_criteria"])
    final_slice = full_product and not (head["id"] == "S3" and
                                       data["goal_contract"]["body"].get("intended_outcome") == RENEWED_OUTCOME)
    commands = verification["required_commands"]
    if not commands:
        raise SystemExit("fake_codex: progressive packet has no authoritative required commands")
    scoped = CONFIG.get("fault") == "progressive_scoped_permission" and head["id"] == "S2"
    permission_answer = next((answer["text"] for answer in data.get("saved_answers", {}).values()
                              if answer.get("kind") == "permission_answer" and answer.get("actor") == "user_cli"
                              and answer.get("request") == SCOPED_REQUEST
                              and answer.get("contract_token") == f"r{common['contract_revision']}:{common['contract_hash']}"), None)
    if scoped and permission_answer is not None and permission_answer not in (SCOPED_DENIAL, SCOPED_CONDITION):
        raise SystemExit("fake_codex: scoped permission answer was not preserved verbatim")
    if stage == "terra":
        root = Path(CONFIG["reference"])
        for rel in PATHS:
            destination = Path.cwd() / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, destination)
        if head["id"] == "S1":
            scenario = next(parent for parent in root.parents if (parent / "scenario.toml").is_file())
            shutil.copy2(scenario / "slices" / "S1" / "lessons.py", Path.cwd() / "lessons.py")
        elif head["id"] == "S3" and data["goal_contract"]["body"].get("intended_outcome") == RENEWED_OUTCOME:
            path = Path.cwd() / "lessons.py"
            path.write_text(path.read_text() + "\n\ndef catalog():\n    return list(LESSONS)\n")
        elif (CONFIG.get("fault") in ("progressive_regression", "progressive_fabricated_pass")
              and (data.get("current_task") or {}).get("objective") == head["intended_result"]):
            # The real cumulative A command catches this introduced S1 regression.
            path = Path.cwd() / "lessons.py"
            path.write_text(path.read_text().replace("if response != LESSONS[lesson][1]:",
                                                    "if lesson == 'addition' or response != LESSONS[lesson][1]:"))
        if scoped and permission_answer == SCOPED_CONDITION:
            path = Path.cwd() / "lessons.py"
            path.write_text(path.read_text() + "\n" + SCOPED_NOTE + "\n")
        results = [run_verify(command) for command in commands]
        result = {**common, "summary": f"Deliver {head['id']} through both product modules",
                "changed_files": PATHS, "commands_run": commands,
                "results": [f"exit {code}" for code, _ in results], "remaining_risks": [],
                "evidence_refs": [ref for _, ref in results], "addressed_requirements": ["C1"],
                "untested_behavior": [], "recommended_checks": commands}
        if scoped and permission_answer is None:
            result["user_request"] = SCOPED_REQUEST
        return result
    if stage in ("sol", "astra_checkpoint"):
        fabricated_mode = CONFIG.get("fault") == "progressive_fabricated_pass" and head["id"] == "S2"
        selected = ([cmd for cmd in commands if cmd != "python3 -m unittest test_journey.Skeleton"]
                    if CONFIG.get("fault") == "progressive_missing_check" and head["id"] == "S2" else commands)
        results = [run_verify(command, fabricated=fabricated_mode) for command in selected]
        passed = all(code == 0 for code, _ in results)
        status = "PASS" if passed else "FAIL"
        fabricated = CONFIG.get("fault") == "progressive_fabricated_pass" and not passed
        product = "PASS" if passed and full_product else "NOT_VERIFIED" if passed else "FAIL"
        return {**common, "verdict": "PASS" if fabricated else status, "checks_run": selected,
                "findings": [], "finding_dispositions": [],
                "unverified_criteria": ["C1"] if product == "NOT_VERIFIED" else [],
                "checks": [{"command": cmd, "exit_code": 0 if fabricated else code, "evidence_ref": ref}
                           for cmd, (code, ref) in zip(selected, results)],
                "criterion_results": [{"id": "C1", "status": "PASS" if fabricated else product,
                                       "evidence_refs": [ref for _, ref in results]}],
                "end_to_end_result": {"status": "PASS" if fabricated else product,
                                      "summary": "Cumulative required checks executed; S1 is not full product proof",
                                      "evidence_refs": [ref for _, ref in results]}}
    if stage in ("astra_review", "astra_plan", "astra_resolve"):
        failed = (data.get("validation") or {}).get("verdict") == "FAIL"
        permission_repair = (scoped and permission_answer is not None and
                             (data.get("validation") or {}).get("task_id") != common["task_id"])
        repair = failed or permission_repair
        approved = data["goal_contract"]["body"]["acceptance_criteria"]
        report = {**common, "status": "REWORK" if repair else "COMPLETE" if final_slice else "CONTINUE",
                  "acceptance_criteria": [{"id": row["id"], "criterion": row["criterion"],
                                           "status": "verified" if full_product and not repair else "unverified",
                                           "evidence": "Cumulative Validator execution"} for row in approved],
                  "evidence": (data.get("validation") or {}).get("evidence_refs") or ["event:check"],
                  "next_objective": head["intended_result"] if permission_repair else
                                    "Restore answer persistence without dropping recommendations" if failed else "",
                  "blocker": "", "plan": [], "affected_paths": PATHS if repair else [],
                  "findings": [], "finding_dispositions": [], "agreed_limitations": [],
                  "next_task": {"kind": "validate" if permission_repair and permission_answer == SCOPED_DENIAL else
                                        "implement" if repair else "none", "milestone_id": "M1" if repair else "",
                                "requirements": [requirements()[0]["text"]] if repair else [],
                                "acceptance_criteria": ["C1"] if repair else [],
                                "validation_plan": commands if repair else [], "findings": []}}
        if stage == "astra_review" and permission_repair and permission_answer == SCOPED_DENIAL:
            report["status"] = "CONTINUE"  # Validate the delivered fallback; no implementation repair is needed.
        if scoped and permission_answer is None:
            request = (data.get("agent_request") or {}).get("request")
            if request != SCOPED_REQUEST:
                raise SystemExit("fake_codex: scoped permission lacks its Builder request")
            report.update(status="BLOCKED", user_request=request, blocker=request["decision_needed"])
            for row in report["acceptance_criteria"]:
                row["status"] = "unverified"
        if not repair and not final_slice:
            report["progressive_checkpoint"] = True
        if stage == "astra_resolve":
            report["diagnosis"] = ("Honor the exact saved permission answer, then independently validate the unchanged task; denial selects the offline fallback"
                                   if permission_repair else "S2 rejects the earlier correct addition answer before persisting it; restore A while retaining recommendations B")
        return report
    return None


def criterion_id(milestone_id: str) -> str:
    return "C_" + milestone_id


def milestone_row(milestone_id: str) -> dict:
    return next(row for row in MILESTONES if row["id"] == milestone_id)


def milestone_task(milestone_id: str) -> dict:
    row = milestone_row(milestone_id)
    return {"kind": "implement", "milestone_id": milestone_id, "objective": row["objective"],
            "affected_paths": list(row["paths"]), "requirements": [row["objective"]],
            "validation_plan": [row["verify"]], "acceptance_criteria": [criterion_id(milestone_id)],
            "contract_revision": (DATA.get("goal_contract") or {}).get("revision", 0),
            "contract_hash": (DATA.get("goal_contract") or {}).get("hash", "")}


def done_milestones(data: dict) -> set:
    task = data.get("current_task") or {}
    done = set(((data.get("milestone_checkpoint") or {}).get("accepted_milestones")) or [])
    done |= set(task.get("milestone_ids") or [])
    if task.get("milestone_id"):
        done.add(task["milestone_id"])
    return done


def next_milestone(done: set) -> dict | None:
    for row in MILESTONES:
        if row["id"] not in done and set(row.get("depends_on", [])) <= done:
            return row
    return None


def run_verify(command: str, *, fabricated=False) -> tuple[int, str]:
    """Run one milestone's verification and record it as a real command event
    whose id the reports then cite, the way the runner requires."""
    import shlex
    event_id = "check-" + (uuid.uuid5(uuid.NAMESPACE_URL, command).hex[:24] if PROGRESSIVE else
                          re.sub(r"[^a-z0-9]+", "-", command.lower()).strip("-")[:24])
    proc = subprocess.run(shlex.split(command), cwd=Path.cwd(), capture_output=True, text=True, timeout=600)
    emit({"type": "item.completed", "item": {
        "id": event_id, "type": "command_execution",
        "command": command, "exit_code": 0 if fabricated else proc.returncode,
        "aggregated_output": "Fabricated provider PASS" if fabricated else (proc.stdout + proc.stderr)[-2000:]}})
    return proc.returncode, f"event:{event_id}"


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
    body = (DATA.get("goal_contract") or {}).get("body") or {}
    if PROGRESSIVE and body.get("intended_outcome") == RENEWED_OUTCOME:
        # Original full-suite G still proves backwards-compatible behavior;
        # the explicitly edited goal retires only the named standalone check.
        return [{"requirement_id": row["id"], "disposition": "covered",
                 "evidence": body["acceptance_criteria"][0]["id"]} for row in requirements()]
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
    existing = (DATA.get("goal_contract") or {}).get("body") or {}
    if PROGRESSIVE and existing.get("intended_outcome") == RENEWED_OUTCOME:
        body = json.loads(json.dumps(existing))
        body.pop("initial_task", None)
        if final:
            body["initial_task"] = progressive_task()
        return body
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
    if MILESTONES:
        body["milestones"] = [{"id": row["id"], "objective": row["objective"],
                               "acceptance_criteria": [criterion_id(row["id"])],
                               "depends_on": list(row.get("depends_on", [])),
                               "affected_paths": list(row["paths"])} for row in MILESTONES]
        body["deliverables"] = [p for row in MILESTONES for p in row["paths"]]
        body["acceptance_criteria"] = [{"id": criterion_id(row["id"]), "criterion": row["objective"],
                                        "verification_method": row["verify"], "human_review": False}
                                       for row in MILESTONES]
        if final:
            first = milestone_task(MILESTONES[0]["id"])
            body["initial_task"] = {key: first[key] for key in
                                    ("kind", "milestone_id", "objective", "affected_paths",
                                     "requirements", "acceptance_criteria", "validation_plan")}
        return body
    if (DATA.get("bug_diagnosis") or {}).get("root_cause"):
        body["task_kind"] = "bugfix"  # planned from a bug diagnosis
    if PROGRESSIVE and "PROGRESSIVE PLANNING" in PROMPT:
        body["intended_outcome"] = "Answer lessons with durable progress and recommend the next unfinished lesson"
        body["end_to_end_flow"] = ["Open a lesson", "Answer its exercise", "Reopen saved progress",
                                   "Request the next uncompleted lesson", "Finish both lessons"]
        body["acceptance_criteria"][0]["criterion"] = (
            "Correct answers persist across reopen and recommendations advance through both lessons to course completion")
        body["milestones"][0]["objective"] = body["intended_outcome"]
        existing = (DATA.get("goal_contract") or {}).get("body") or {}
        for field in ("constraints", "technical_approach"):
            body[field] += [line for line in existing.get(field, [])
                            if line.startswith(("Progressive delegation:", "Progressive slice:",
                                                "Product criteria explicitly outstanding:"))]
    if final:
        body["initial_task"] = {"kind": "implement", "milestone_id": "M1", "objective": outcome(),
                                "affected_paths": PATHS, "requirements": [requirements()[0]["text"]],
                                 "acceptance_criteria": ["C1"], "validation_plan": [CHECK]}
        if os.environ.get("SCENARIO_FAKE_NEGATIVE_PLAN") == "1":
            body["initial_task"]["validation_plan"].append(
                "Run `python3 greet.py Alice`, `python3 greet.py` and `python3 greet.py Alice Bob` "
                "directly and confirm exact stdout/stderr bytes and exit codes 0/2/2.")
        if PROGRESSIVE and "PROGRESSIVE PLANNING" in PROMPT:
            body["initial_task"] = progressive_task()
    return body


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def run_check() -> int:
    proc = subprocess.run(CHECK, shell=True, capture_output=True, text=True, timeout=600)
    emit({"type": "item.completed", "item": {
        "id": "check", "type": "command_execution", "command": CHECK,
        "exit_code": proc.returncode, "aggregated_output": (proc.stdout + proc.stderr)[-2000:]}})
    return proc.returncode


def recognize(brief: str, follow_up: dict | None = None) -> dict:
    """The fake's script for the job-recognition stage: keyword rules over the brief.

    This is not a model and says nothing about model quality; it exists so the
    plumbing (the stage runs, its answer reaches the status view) can be proven
    offline. A live profile is what tests recognition itself.
    """
    text = brief.lower()

    def has(*patterns):
        return any(re.search(pattern, text) for pattern in patterns)

    if (follow_up or {}).get("previous_workflow") == "review" and has(r"\bfix\b", r"\bland\b", r"\bapply\b"):
        kind, signal = "build", "follow-up: act on the review's findings"
    elif has(r"\bimplement (it|this|the design)\b", r"has already been .*approved") and not has(r"do(n't| not) implement anything"):
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
    # Adaptive-planning runs also ask how clear the request is; the planning stress corpus
    # scripts the answer per case (scenarios/planning.toml), since keywords cannot judge it.
    return {"workflow": kind, "reason": f"Scripted keyword rule: {signal}", "signals": [signal],
            "design_document": design,
            **({"clarity": os.environ.get("SCENARIO_FAKE_CLARITY") or "clear"} if 'Add "clarity"' in PROMPT else {})}


def check_design(data: dict) -> dict:
    """The fake's design check: the conflicts in the solution's <design>.blockers.json, if it has one
    (the run must stop), otherwise no conflicts and one binding decision (planning goes ahead)."""
    design = data.get("design_document") or ""
    blockers = Path(CONFIG["reference"]) / Path(design).with_suffix(".blockers.json") if design else None
    conflicts = json.loads(blockers.read_text()).get("conflicts", []) if blockers and blockers.is_file() else []
    conflicts = [{**{k: c.get(k, [] if k in ("files", "options") else "") for k in
                     ("design_says", "conflicts_with", "files", "options")},
                  "example": str(c.get("example") or "Scripted example: " + str(c.get("conflicts_with", ""))),
                  "probe": str(c.get("probe", ""))} for c in conflicts]
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
    # A blocking finding is proven by the runner when the solution delivers a test named after it
    # (F1 -> test_f1_...); otherwise the scripted review says why it is not tested.
    names = " ".join(re.findall(r"def (test_\w+)", " ".join(
        (Path(CONFIG["reference"]) / path).read_text() for path in delivered))).lower()
    patches = sorted(p.name for p in Path.cwd().glob("*.patch"))

    def finding(f):
        row = {key: f.get(key, [] if key == "lines" else "") for key in
               ("id", "severity", "file", "lines", "summary", "evidence")}
        tested = f"test_{str(f.get('id', '')).lower()}_" in names
        row["example"] = f.get("example") or "Scripted example: " + str(f.get("summary", ""))
        row["untestable"] = "" if tested else (f.get("untestable") or "Scripted review: the scenario solution "
                                               "delivers no test named after this finding")
        return row
    return {"verdict": saved.get("verdict", "approve"), "summary": "Scripted review from the scenario solution",
            "change_under_review": "the change named in the request", "change_patch": patches[0] if patches else "",
            "findings": [finding(f) for f in saved.get("findings", [])],
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
            "test_cases": (saved.get("test_cases") or scripted_cases()) if reproduced else [],
            "conclusion": text("conclusion", "finding", "fix") or "Scripted conclusion",
            "fix_size": (saved.get("fix_size") or "small") if reproduced else "none",
            "fix_plan": [text("fix")] if reproduced and saved.get("fix") else [],
            "questions": [str(q) for q in saved.get("questions", [])], "tests_run": ["scripted"],
            "plan_approval_requested": bool(saved.get("plan_approval_requested")),
            # The runner checks the reproduction: [fake] probe in scenario.toml exits 0 while the seed's
            # bug is present; a scenario without one takes the untestable path.
            "probe": (CONFIG.get("probe") or "") if reproduced else "",
            "untestable": ("" if CONFIG.get("probe") else "Scripted investigation: this scenario configures "
                           "no reproduction probe") if reproduced else ""}


def scripted_cases() -> list[dict]:
    """English test cases for a scripted diagnosis: one per test function the solution adds, with the
    test's own name as the case id, so the runner can match each case to its test. A solution with no
    new test (a fix without a test) still gets one case, which then has nothing to prove it."""
    root, names = Path(CONFIG["reference"]), []
    for relative in PATHS:
        if "test" not in Path(relative).name or not relative.endswith(".py"):
            continue
        before = Path.cwd() / relative
        existing = set(re.findall(r"def (test_\w+)", before.read_text())) if before.is_file() else set()
        names += [name for name in re.findall(r"def (test_\w+)", (root / relative).read_text()) if name not in existing]
    return [{"id": name.removeprefix("test_"), "given": "the scripted seed", "when": f"{name} runs",
             "then": "it passes only with the fix"} for name in names] or \
        [{"id": "T1", "given": "the scripted seed", "when": "the reported case runs", "then": "it is fixed"}]


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
    # A blocking concern needs its example; a scripted review probes nothing (probe "").
    concerns = [{**{key: str(c.get(key, "")) for key in ("id", "area", "severity", "summary", "evidence")},
                 "example": str(c.get("example") or "Scripted example: " + str(c.get("summary", ""))),
                 "probe": str(c.get("probe", ""))}
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
    if PROGRESSIVE and data.get("report_repair"):
        # A report-only repair may preserve its original proposal/checklist, not
        # invent progressive authority from a packet without execution context.
        original = data.get("rejected_report") or data.get("original_report") or {}
        content = original.get("content") if isinstance(original, dict) else None
        if content:
            return json.loads(content) if isinstance(content, str) else content
    if stage == "recognize_workflow":
        return recognize(data.get("task") or CONFIG["brief"], data.get("follow_up"))
    if stage == "answer_question":
        return answer()
    if stage == "check_design":
        return check_design(data)
    if stage == "investigate_stuck":
        # A scripted run that got stuck is a scenario defect; pause and say so.
        return {"diagnosis": "Offline fixture: it cannot diagnose; the run pauses as before.", "cause": "other", "guidance": "", "recommendation": "pause", "user_question": "", "evidence_refs": [],
                "example": "", "probe": "", "untestable": ""}
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
    if PROGRESSIVE:
        report = progressive_report(stage, data, common)
        if report is not None:
            return report
        if stage in ("terra", "sol", "astra_review", "astra_checkpoint", "astra_resolve"):
            raise SystemExit("fake_codex: progressive execution missing its authoritative verification packet")
        if "PROGRESSIVE PLANNING" in PROMPT and stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            planning["progressive_proposal"] = progressive_proposal()
    if stage == "requirements_gather":
        return {"summary": "Scripted requirements: one per brief sentence",
                "intended_outcome": outcome(), "required_behaviors": [r["text"] for r in requirements()],
                "constraints": [], "acceptance_tests": [CHECK], "source_refs": source_refs(),
                "proposed_assumptions": [], "open_questions": [], "requirements": requirements(),
                "ignored_statements": [], "conflicts": [], "proposed_reframes": []}
    # An adaptive-planning Planner drafts the complete plan, initial_task included.
    adaptive = "ADAPTIVE PLANNING" in PROMPT
    if stage == "astra_discovery":
        report = {"summary": "Scripted plan", "contract": contract(final=adaptive), "alternatives": [],
                  "uncertainties": [], **planning}
        if CONFIG.get("fault") == "planner_citation" and not guided_to_fix_citation():
            # Prose after an existing path is valid. A nonexistent sibling is a
            # deterministic rejected citation, including on report-only repairs.
            report["code_refs"] = [f"{note}.missing" for note in bug_notes()]
        return report
    reviewed = ((((DATA.get("planning") or {}).get("reports") or {}).get("astra_challenge") or {})
                .get("report") or {}).get("concerns") or []
    if stage == "astra_challenge":
        # SCENARIO_FAKE_BLOCKING_REVIEW=1 makes the first review of a plan blocking, so the
        # revise path runs; a review of a revised plan never blocks.
        revised = "glm_revise" in ((DATA.get("planning") or {}).get("reports") or {})
        if os.environ.get("SCENARIO_FAKE_BLOCKING_REVIEW") == "1" and not revised:
            return {"summary": "Scripted plan review: one blocking concern", "concerns": [{
                "id": "B1", "concern": "The plan does not say how the change is verified end to end",
                "evidence_refs": ["task"], "requested_change": f"Run {CHECK} as the acceptance check",
                "acceptance_test": CHECK, "blocking": True}]}
        return {"summary": "Scripted plan review: no concerns", "concerns": []}
    if stage == "glm_revise":
        responses = [{"concern_id": row["id"], "response": "Adopted", "evidence_refs": ["task"],
                      "change": row["requested_change"], "acceptance_test": row["acceptance_test"]}
                     for row in reviewed]
        return {"summary": "Scripted revision", "contract": contract(final=adaptive), "responses": responses,
                **planning}
    if stage == "astra_finalize":
        final = {key: value for key, value in planning.items() if key != "code_refs"}
        decisions = [{"concern_id": row["id"], "decision": "Accepted as revised", "rationale": "Adopted",
                      "acceptance_test": row["acceptance_test"], "resolved": True} for row in reviewed]
        return {"summary": "Scripted final plan", "contract": contract(final=True), "decisions": decisions, **final}
    if stage == "terra" and MILESTONES:
        row = milestone_row((data.get("current_task") or {}).get("milestone_id"))
        applied = []
        for rel in row["paths"]:
            destination = Path.cwd() / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(CONFIG["reference"]) / rel, destination)
            applied.append(rel)
        code, evidence = run_verify(row["verify"])
        return {**common, "summary": f"Applied milestone {row['id']} from the reference solution",
                "changed_files": applied, "commands_run": [row["verify"]],
                "results": [f"exit {code}"], "remaining_risks": [],
                "evidence_refs": [evidence], "addressed_requirements": [criterion_id(row["id"])],
                "untested_behavior": [], "recommended_checks": [row["verify"]]}
    if stage == "terra":
        shutil.copytree(CONFIG["reference"], Path.cwd(), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        code = run_check()
        return {**common, "summary": "Applied the scenario reference solution", "changed_files": PATHS,
                "commands_run": [CHECK], "results": [f"exit {code}"], "remaining_risks": [],
                "evidence_refs": ["event:check"], "addressed_requirements": ["C1"],
                "untested_behavior": [], "recommended_checks": [CHECK]}
    if stage in ("sol", "astra_checkpoint") and MILESTONES:
        task = data.get("current_task") or {}
        members = list(task.get("milestone_ids") or ([task["milestone_id"]] if task.get("milestone_id") else []))
        if not next_milestone(done_milestones(data)):
            # The last milestone validates the whole contract, end to end.
            code = run_check()
            status = "PASS" if code == 0 else "FAIL"
            every = [criterion_id(row["id"]) for row in MILESTONES]
            checks = [{"command": CHECK, "exit_code": code, "evidence_ref": "event:check"}]
            criteria = [{"id": cid, "status": status, "evidence_refs": ["event:check"]} for cid in every]
            return {**common, "verdict": status, "checks_run": [CHECK], "findings": [],
                    "finding_dispositions": [], "unverified_criteria": [], "checks": checks,
                    "criterion_results": criteria,
                    "end_to_end_result": {"status": status, "summary": f"{CHECK} exited {code}",
                                          "evidence_refs": ["event:check"]}}
        checks, criteria, member_results = [], [], []
        for milestone_id in members:
            row = milestone_row(milestone_id)
            code, evidence = run_verify(row["verify"])
            status = "PASS" if code == 0 else "FAIL"
            checks.append({"command": row["verify"], "exit_code": code, "evidence_ref": evidence})
            criteria.append({"id": criterion_id(milestone_id), "status": status,
                             "evidence_refs": [evidence]})
            member_results.append({"milestone_id": milestone_id, "status": status,
                                   "summary": f"{row['verify']} exited {code}",
                                   "evidence_refs": [evidence]})
        verdict = "PASS" if all(row["status"] == "PASS" for row in criteria) else "FAIL"
        report = {**common, "verdict": verdict,
                  "checks_run": [row["command"] for row in checks], "findings": [],
                  "finding_dispositions": [], "unverified_criteria": [], "checks": checks,
                  "criterion_results": criteria,
                  "end_to_end_result": {"status": verdict,
                                        "summary": "; ".join(f"{row['milestone_id']}: {row['status']}"
                                                             for row in member_results),
                                        "evidence_refs": [row["evidence_refs"][0] for row in member_results]}}
        if task.get("milestone_ids"):
            # An integrated batch: report every member's milestone result.
            report["milestone_results"] = member_results
        return report
    if stage in ("sol", "astra_checkpoint"):
        code = run_check()
        status = "PASS" if code == 0 else "FAIL"
        # The Validator sees neither event IDs nor exit codes: it names its check by command and cites it as
        # check:1, and the runner attaches the event and exit code (autocode_check_refs).
        ref, check = ("check:1", {"command": CHECK, "exit_code": None, "evidence_ref": "event:"}) if stage == "sol" \
            else ("event:check", {"command": CHECK, "exit_code": code, "evidence_ref": "event:check"})
        return {**common, "verdict": status, "checks_run": [CHECK], "findings": [],
                "finding_dispositions": [], "unverified_criteria": [], "checks": [check],
                # Every criterion of the approved contract (a bug fix's English test cases add some).
                "criterion_results": [{"id": row["id"], "status": status, "evidence_refs": [ref]}
                                      for row in ((data.get("goal_contract") or {}).get("body") or {})
                                      .get("acceptance_criteria") or [{"id": "C1"}]],
                "end_to_end_result": {"status": status, "summary": f"{CHECK} exited {code}",
                                      "evidence_refs": [ref]}}
    if stage in ("astra_review", "astra_plan") and MILESTONES and not (data.get("goal_contract") or {}).get("revision"):
        # Report repair: the handoff has no live contract or current task; fix
        # the rejected draft's identity fields and return it as instructed.
        import json as _json
        source = data.get("rejected_report") or data.get("original_report") or {}
        draft = source.get("content") if isinstance(source, dict) else None
        fixed = _json.loads(draft) if draft else {}
        identity = data.get("report_identity") or {}
        original = data.get("original") or {}
        fixed.update(contract_revision=identity.get("contract_revision", original.get("contract_revision", 0)),
                     contract_hash=identity.get("contract_hash", original.get("contract_hash", "")),
                     task_id=identity.get("task_id", original.get("task_id", "")))
        return fixed
    if stage in ("astra_review", "astra_plan") and MILESTONES:
        task = data.get("current_task") or {}
        done = done_milestones(data)
        approved = ((data.get("goal_contract") or {}).get("body") or {}).get("acceptance_criteria") \
            or [{"id": criterion_id(row["id"]), "criterion": row["objective"]}
                for row in MILESTONES]
        verified = list(task.get("acceptance_criteria") or [])
        upcoming = next_milestone(done)
        if not upcoming:
            # The final completion owns every criterion, not just the last task's.
            verified = [row["id"] for row in approved]
        report = {**common,
                  "acceptance_criteria": [{"id": row["id"], "criterion": row["criterion"],
                                           "status": "verified" if row["id"] in verified else "unverified",
                                           "evidence": "event:check"} for row in approved],
                  "evidence": ["event:check"], "next_objective": "", "blocker": "", "plan": [],
                  "affected_paths": [], "findings": [], "finding_dispositions": [],
                  "agreed_limitations": []}
        if upcoming:
            report["status"] = "CONTINUE"
            report["next_objective"] = upcoming["objective"]
            report["affected_paths"] = list(upcoming["paths"])
            report["next_task"] = {"kind": "implement", "milestone_id": upcoming["id"],
                                   "requirements": [upcoming["objective"]],
                                   "acceptance_criteria": [criterion_id(upcoming["id"])],
                                   "validation_plan": [upcoming["verify"]], "findings": []}
        else:
            report["status"] = "COMPLETE"
            report["next_task"] = {"kind": "none", "milestone_id": "", "requirements": [],
                                   "acceptance_criteria": [], "validation_plan": [], "findings": []}
        return report
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
    repeatedly cites a nonexistent .missing sibling of the saved bug note until an
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
