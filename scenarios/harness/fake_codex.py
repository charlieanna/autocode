#!/usr/bin/env python3
"""Scripted stand-in for the ``codex`` CLI, used by ``run --fake``.

It plans from the scenario brief, "builds" by copying the scenario's reference
solution into the workspace, and validates by really running the scenario's
check command. Only the model is fake: AutoCode's CLI, state machine, approval
gates and evidence checks run for real. This proves the harness and AutoCode's
plumbing for a scenario; it says nothing about model quality.

Configuration comes from the JSON file named by SCENARIO_FAKE_CONFIG:
{"title", "brief", "reference", "check", "paths"}.

In a conversation (``turns``) the solution is the end state of every turn. The fake
reads which turn it serves from the handoff's task, which starts with the newest
message (autocode_follow_up), and with ``turn_paths`` delivers only that turn's files.
A job whose report changes from turn to turn reads it from the solution's
``.fake-turns/<turn>/<stage>.json`` (``turn_report``); that folder is never part of
the project (harness.oracle.IGNORED).

A hybrid run (harness/hybrid.py) runs it through a config-registered tool instead of
as ``codex``; AutoCode's prompt then asks for capture receipts, and the fake runs its
check through the handoff's capture_command and cites the receipt (receipt_mode).

In a program (scenarios/README.md, "Programs") every workstream is a run whose task is
the program's workstream brief. The fake reads that brief as a model would and plans the
workstream alone (``workstream``): a code workstream its own milestone, an integration
workstream every requirement and journey it inherits, each under the inherited ids.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

CONFIG = json.loads(Path(os.environ["SCENARIO_FAKE_CONFIG"]).read_text())
PROMPT = ""
OUTPUT_SCHEMA: dict = {}
DATA: dict = {}
CHECK = CONFIG["check"]
ALL_PATHS = CONFIG["paths"]
PATHS = ALL_PATHS  # this turn's solution files (turn_paths), set per invocation in main()
FOLLOW_UP = "\n\nThis follows up an earlier request in the same conversation"
# Multi-milestone scenarios (parallel-diamond) declare their graph here; the
# scripted plan, builds and per-milestone validation follow it, and AutoCode's
# real orchestrator does the parallel scheduling.
MILESTONES = CONFIG.get("milestones") or []
PROGRESSIVE = CONFIG.get("fault", "").startswith("progressive_")
RENEWED_OUTCOME = "Retain durable lesson answers and expose a browsable lesson catalog"
SCOPED_NOTE = "# Scoped consent: lesson note"
SCOPED_DENIAL = "No. Do not add the optional note anywhere. Deliver the original goal using the offline fallback."
SCOPED_CONDITION = "Yes, append only '# Scoped consent: lesson note' to lessons.py. Do not add this note to progress.py or any other file."
# The program's workstream brief (autocode_program.compose_brief): its first line, and the line naming the
# requirement ids the workstream's plan must keep. tests/test_program.py checks the fake still reads them.
WORKSTREAM_HEADER = re.compile(r"PROGRAM WORKSTREAM (\S+) \((\w+)\) of program ")
INHERITED = re.compile(r"^Inherited requirements: keep each as an acceptance criterion of your plan with exactly "
                       r"this id \(([^)]*)\)", re.M)
WORKSTREAM: dict | None = None  # the program workstream this call serves (workstream()), set in main()
SCOPED_REQUEST = {"kind": "permission",
                  "discovered": "An optional explanatory comment can be added to lessons.py, already inside the current task's approved paths",
                  "impact": "The optional note requires consent; denial leaves an offline implementation of the original goal available",
                  "decision_needed": "May I append only '# Scoped consent: lesson note' to lessons.py?",
                  "options": [SCOPED_DENIAL, SCOPED_CONDITION],
                  "proposed_delta": "No goal, scope, criterion, behavior, filesystem, provider or spending change. Optional comment only in lessons.py within the existing approved paths."}


def turn_number() -> int:
    """Which turn of the conversation this stage serves: 0 for the brief, n for the n-th follow-up.
    A report repair's packet has no task; it serves the turn of the stage it repairs, the last one seen."""
    seen = Path(os.environ["SCENARIO_FAKE_CONFIG"]).with_name("fake-turn.json")
    if not DATA.get("task"):
        return json.loads(seen.read_text()) if seen.is_file() else 0
    task = str(DATA["task"])
    number = max((n for n, say in enumerate(CONFIG.get("turns") or [], start=1) if task.startswith(say)), default=0)
    seen.write_text(json.dumps(number))
    return number


def turn_report(stage: str) -> dict | None:
    """The solution's scripted report for ``stage`` in this turn, or None when it has none."""
    path = Path(CONFIG["reference"]) / ".fake-turns" / str(turn_number()) / f"{stage}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def turn_paths() -> list[str]:
    """The solution files this turn delivers: every one, unless [fake] turn_paths splits them by turn."""
    table = CONFIG.get("turn_paths") or []
    if not table:
        return ALL_PATHS
    prefixes = tuple(table[turn_number()])
    return [path for path in ALL_PATHS if path.startswith(prefixes)]


def turn_permissions() -> list[str]:
    """The plan's permission boundary. A real Planner bounds each job to what it delivers, so in a
    conversation with turn_paths the fake bounds each turn to that turn's paths."""
    table = CONFIG.get("turn_paths") or []
    if not table:
        return ["Read and edit only this scenario workspace"]
    return ["Edit only " + ", ".join(table[turn_number()]) + " in this scenario workspace"]


def permission_changes() -> list[dict]:
    """What the Planner declares when this turn's boundary replaces the approved one, as it is asked to:
    one row naming the previous boundary, the new text as its replacement, backed by the newest
    follow-up's receipt. Live Planners wrote exactly this row for "Build it." (issue #185). A build of
    the design an earlier turn wrote has no approved contract to revise (the design check archives it),
    so it declares nothing."""
    contract = DATA.get("goal_contract") or {}
    old = (contract.get("body") or {}).get("permission_boundaries") or []
    new = turn_permissions()
    feedback = [row for row in DATA.get("brief_feedback") or [] if row.get("id")]
    if contract.get("approval_status") != "approved" or not feedback or old == new or len(old) != 1:
        return []
    return [{"item": old[0], "change": "permission_changed", "basis": "user_feedback",
             "answer_id": feedback[-1]["id"], "replacement": new[0], "example_correction": None}]


def workstream() -> dict | None:
    """The program workstream this call serves, {id, kind, inherited, task}, or None outside a program.

    A report repair's packet has no task: it serves the workstream last seen in this worktree. That is
    kept beside the configuration, never in the worktree (the program would count it as a delivered
    file), one file per worktree because parallel workstreams call the fake at the same time."""
    seen = Path(os.environ["SCENARIO_FAKE_CONFIG"]).with_name(
        "fake-workstream-" + hashlib.sha256(str(Path.cwd()).encode()).hexdigest()[:12] + ".json")
    task = str(DATA.get("task") or "")
    if not task:
        return json.loads(seen.read_text()) if seen.is_file() else None
    head = WORKSTREAM_HEADER.match(task)
    if not head:
        return None
    inherited = INHERITED.search(task)
    row = {"id": head.group(1), "kind": head.group(2), "task": task,
           "inherited": [cid.strip() for cid in inherited.group(1).split(",") if cid.strip()] if inherited else []}
    seen.write_text(json.dumps(row))
    return row


def conforms(path: str) -> bool:
    """Whether the worktree already holds the solution's version of ``path``."""
    here = Path.cwd() / path
    return here.is_file() and here.read_bytes() == (Path(CONFIG["reference"]) / path).read_bytes()


def workstream_scope(row: dict) -> tuple[list[dict], str, list[str]]:
    """(milestones, check, paths) for one workstream: a code workstream plans only its own [fake] milestones
    row, with no dependencies (the program merged them first) and the ids it inherits as its criteria, and
    only validates when its files already match the solution (a re-check, milestone_task); the integration
    workstream delivers the solution files no workstream merged, verified by the scenario's check, so a
    program scenario's solution needs one (catalog.load)."""
    if row["kind"] != "code":
        return [], CONFIG["check"], [path for path in ALL_PATHS if not conforms(path)]
    own = next((item for item in CONFIG.get("milestones") or [] if item["id"] == row["id"]), None)
    if own is None:
        raise SystemExit(f"fake_codex: program workstream {row['id']} has no [fake] milestones row")
    return [{**own, "depends_on": [], "criteria": row["inherited"] or [criterion_id(own["id"])]}], own["verify"], \
        list(own["paths"])


def request() -> str:
    """The request this turn serves: the brief, or the newest follow-up without the earlier turns it quotes.
    A program workstream's request is its own brief."""
    if WORKSTREAM:
        return WORKSTREAM["task"]
    if not CONFIG.get("turns") or not DATA.get("task"):
        return CONFIG["brief"]
    return str(DATA["task"]).split(FOLLOW_UP, 1)[0]


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
                                      "evidence_refs": [ref for _, ref in results],
                                      "technical_result": None, "pending_human_criteria": []}}
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


def criterion_ids(milestone_id: str) -> list[str]:
    """The criteria a milestone's plan names: C_<id>, or the ids a program workstream inherits."""
    return list(milestone_row(milestone_id).get("criteria") or [criterion_id(milestone_id)])


def milestone_task(milestone_id: str) -> dict:
    row = milestone_row(milestone_id)
    # A program workstream re-checked after an agreement revision may find its files already right.
    rechecked = WORKSTREAM is not None and all(conforms(path) for path in row["paths"])
    return {"kind": "validate" if rechecked else "implement", "milestone_id": milestone_id,
            "objective": row["objective"],
            "affected_paths": list(row["paths"]), "requirements": [row["objective"]],
            "validation_plan": [row["verify"]], "acceptance_criteria": criterion_ids(milestone_id),
            "contract_revision": (DATA.get("goal_contract") or {}).get("revision", 0),
            "contract_hash": (DATA.get("goal_contract") or {}).get("hash", "")}


def first_test(paths: list[str]) -> str | None:
    """The first test function in the solution's version of the Python test files among ``paths``."""
    for path in paths:
        source = Path(CONFIG["reference"]) / path
        if Path(path).name.startswith("test_") and path.endswith(".py") and source.is_file():
            found = re.search(r"^\s+def (test_\w+)\(", source.read_text(), re.M)
            if found:
                return found.group(1)
    return None


def verification_method(row: dict) -> str:
    """A milestone's check; a re-checked workstream whose files already conform guards its criteria with a
    test it already has, as a live Planner marked a skeleton's re-check (2026-10-06), so the runner's
    regression proof runs on an unchanged source."""
    test = first_test(row["paths"]) if milestone_task(row["id"])["kind"] == "validate" else None
    return "guard: " + test if test else row["verify"]


def integration_method(cid: str) -> str:
    """The final check's mark for an inherited id, as its brief asks: the merged product already delivers it,
    so it is a guard naming a test a merged workstream has (C_<id>), or the journey test the final check adds
    without changing the product; the runner's regression proof then runs against the integration head."""
    rows = {criterion_id(row["id"]): row for row in CONFIG.get("milestones") or []}
    owned = [path for path in ALL_PATHS if not any(path in row["paths"] for row in rows.values())]
    test = first_test(rows[cid]["paths"] if cid in rows else owned)
    return "guard: " + test if test else CHECK


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
    event_id = "check-" + (uuid.uuid5(uuid.NAMESPACE_URL, command).hex[:24] if PROGRESSIVE or WORKSTREAM else
                          re.sub(r"[^a-z0-9]+", "-", command.lower()).strip("-")[:24])
    proc = subprocess.run(shlex.split(command), cwd=Path.cwd(), capture_output=True, text=True, timeout=600)
    emit({"type": "item.completed", "item": {
        "id": event_id, "type": "command_execution",
        "command": command, "exit_code": 0 if fabricated else proc.returncode,
        "aggregated_output": "Fabricated provider PASS" if fabricated else (proc.stdout + proc.stderr)[-2000:]}})
    return proc.returncode, f"event:{event_id}"


def requirements() -> list[dict]:
    """One requirement per sentence of the brief, then of the user's saved feedback, quoted verbatim, as
    AutoCode's planner rules demand."""
    texts = [request(), *(row.get("text", "") for row in DATA.get("brief_feedback") or []
                          if row.get("actor") == "user_cli")]
    sentences = [part.strip() for text in texts for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]
    return [{"id": f"R{number}", "text": sentence, "source_quote": sentence}
            for number, sentence in enumerate(sentences, start=1)]


def source_refs() -> list[str]:
    """AutoCode requires planning to cite real files once the workspace has any."""
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split()
    return tracked[:8] or ["task"]


def feedback_rows() -> list[dict]:
    """Rows AutoCode asks the Planner to trace that are not brief sentences: the user's feedback on a shown plan."""
    brief = {row["id"] for row in requirements()}
    return [row for row in DATA.get("requirement_trace_rows") or [] if row["requirement_id"] not in brief]


def trace() -> list[dict]:
    body = (DATA.get("goal_contract") or {}).get("body") or {}
    if PROGRESSIVE and body.get("intended_outcome") == RENEWED_OUTCOME:
        # Original full-suite G still proves backwards-compatible behavior;
        # the explicitly edited goal retires only the named standalone check.
        return [{"requirement_id": row["id"], "disposition": "covered",
                 "evidence": body["acceptance_criteria"][0]["id"]} for row in requirements()]
    # Feedback is applied as a required behavior quoting it (contract), which covers its row.
    rows = DATA.get("requirement_trace_rows")
    if rows is not None:
        return [{"requirement_id": row["requirement_id"], "disposition": "covered", "evidence": row["requirement"]}
                for row in rows]
    return [{"requirement_id": row["id"], "disposition": "covered", "evidence": row["text"]}
            for row in requirements()]


def outcome() -> str:
    """What the request asks for, in its own words (its first sentence)."""
    first = re.split(r"(?<=[.!?])\s+", request().strip(), maxsplit=1)[0]
    return first[:240]


def approach() -> list[str]:
    """The fix the saved diagnosis proposes when there is one; otherwise the request itself."""
    notes = sorted((Path(CONFIG["reference"]) / "docs" / "bugs").glob("*.json"))
    fix = json.loads(notes[0].read_text()).get("fix", "") if notes else ""
    return [fix or f"Implement the requested change: {outcome()}"]


def scenario_dir() -> Path:
    """The catalog directory of the scenario being run: the solution the fake applies lies under it."""
    return next(parent for parent in Path(CONFIG["reference"]).parents if (parent / "scenario.toml").is_file())


def scripted_fault(name: str) -> dict:
    """A scripted fault's module from scenarios/harness/. This script runs as a copy in the evidence
    directory, so the module is found through the scenario directory instead of beside it."""
    import runpy
    return runpy.run_path(str(scenario_dir().parents[1] / "harness" / name))


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
        "required_behaviors": list(dict.fromkeys([row["text"] for row in requirements()]
                                                 + [row["requirement"] for row in feedback_rows()])),
        "important_failure_cases": ["The scenario check command fails"],
        "scope_exclusions": ["Anything outside the scenario brief"],
        "constraints": ["Change only the paths the reference solution touches"],
        "permission_boundaries": turn_permissions(),
        "accepted_assumptions": [{"text": "The request describes the intended behavior completely",
                                  "basis": "agent_proposed", "answer_id": ""}],
        "delegated_decisions": [],
        "acceptance_criteria": [{"id": "C1", "criterion": "The project's checks pass with the change in place",
                                 "verification_method": CHECK, "human_review": False}],
        "open_blocking_questions": [],
    }
    if MILESTONES:
        body["milestones"] = [{"id": row["id"], "objective": row["objective"],
                               "acceptance_criteria": criterion_ids(row["id"]),
                               "depends_on": list(row.get("depends_on", [])),
                               "affected_paths": list(row["paths"])} for row in MILESTONES]
        body["deliverables"] = [p for row in MILESTONES for p in row["paths"]]
        body["acceptance_criteria"] = [{"id": cid, "criterion": row["objective"],
                                        "verification_method": verification_method(row), "human_review": False}
                                       for row in MILESTONES for cid in criterion_ids(row["id"])]
        if final:
            first = milestone_task(MILESTONES[0]["id"])
            body["initial_task"] = {key: first[key] for key in
                                    ("kind", "milestone_id", "objective", "affected_paths",
                                     "requirements", "acceptance_criteria", "validation_plan")}
        return body
    if CONFIG.get("fault") == "recovery_novelty_narrow":
        # #423: the failing task owns two criteria, so the Resolver's repair can keep only one.
        body["acceptance_criteria"].append({"id": "C2", "criterion": "Blank and whitespace-only names print usage",
                                            "verification_method": CHECK, "human_review": False})
        body["milestones"][0]["acceptance_criteria"] = ["C1", "C2"]
    if CONFIG.get("fault") == "vacuous_refusal_tests":
        # One "test: test_cN_..." criterion per reference test, as a Planner writes them; the runner's
        # regression proof then checks each named test against the original code.
        rows = scripted_fault("vacuous_refusal_provider.py")["criteria"](scenario_dir() / "reference")
        body["acceptance_criteria"] = rows
        body["milestones"][0]["acceptance_criteria"] = [row["id"] for row in rows]
    if WORKSTREAM and WORKSTREAM["kind"] != "code":
        # The integration workstream keeps every requirement and journey it inherits, by id; the
        # scenario's check is the merged product's whole check.
        ids = WORKSTREAM["inherited"] or ["C1"]
        body["acceptance_criteria"] = [{"id": cid, "criterion": f"{cid} holds on the merged product",
                                        "verification_method": integration_method(cid), "human_review": False}
                                       for cid in ids]
        body["milestones"][0]["acceptance_criteria"] = ids
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
                                 "acceptance_criteria": [row["id"] for row in body["acceptance_criteria"]],
                                 "validation_plan": [CHECK]}
        if os.environ.get("SCENARIO_FAKE_NEGATIVE_PLAN") == "1":
            body["initial_task"]["validation_plan"].append(
                "Run `python3 greet.py Alice`, `python3 greet.py` and `python3 greet.py Alice Bob` "
                "directly and confirm exact stdout/stderr bytes and exit codes 0/2/2.")
        if PROGRESSIVE and "PROGRESSIVE PLANNING" in PROMPT:
            body["initial_task"] = progressive_task()
    return body


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


QUOTA_SPENT_MODEL = "gpt-5.6-sol"  # the driver's default Tester model (driver.FAKE_FLAGS)


def model_argument() -> str | None:
    return sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv[:-1] else None


# A config-registered tool (output = "report_file", as in a hybrid run: harness/hybrid.py) cites command evidence
# through capture_command receipts, never Codex event ids. AutoCode's prompt says so, and then run_check() runs the
# check through the handoff's capture_command; cite_receipts() puts that receipt where the report says event:check.
RECEIPT_CONTRACT = "Do not cite event: IDs"
RECEIPTS: list[tuple[str, str, int]] = []  # this call's (receipt path, command text, exit code)


def receipt_mode() -> bool:
    return RECEIPT_CONTRACT in PROMPT and bool(DATA.get("capture_command"))


def check_argv() -> list[str]:
    return shlex.split(CHECK) if not re.search(r"[|&;<>()$`*?~]", CHECK) else ["sh", "-c", CHECK]


def run_check() -> int:
    if receipt_mode():
        receipt = (Path.cwd() / ".autocode" / "evidence" / f"scripted-check-{uuid.uuid4().hex[:12]}.json").resolve()
        proc = subprocess.run([*shlex.split(DATA["capture_command"]), "--output", str(receipt), "--", *check_argv()],
                              capture_output=True, text=True, timeout=600)
        RECEIPTS.append((str(receipt), shlex.join(check_argv()), proc.returncode))
        return proc.returncode
    proc = subprocess.run(CHECK, shell=True, capture_output=True, text=True, timeout=600)
    emit({"type": "item.completed", "item": {
        "id": "check", "type": "command_execution", "command": CHECK,
        "exit_code": proc.returncode, "aggregated_output": (proc.stdout + proc.stderr)[-2000:]}})
    return proc.returncode


def cite_receipts(value):
    """In receipt mode, the report's Codex evidence (event:check, check:1, a check's event:) cites a receipt
    instead: this call's own, else the newest one in the workspace's evidence directory (the check a reviewer
    read). A check of the scenario's command takes the receipt's command text and exit code."""
    if not receipt_mode():
        return value
    if RECEIPTS:
        receipt, text, code = RECEIPTS[-1]
    else:
        found = sorted((Path.cwd() / ".autocode" / "evidence").glob("*.json"), key=lambda path: path.stat().st_mtime)
        if not found:
            return value
        receipt, text, code = str(found[-1].resolve()), shlex.join(check_argv()), None
    if isinstance(value, dict):
        fixed = {key: cite_receipts(item) for key, item in value.items()}
        if fixed.get("command") == CHECK and str(fixed.get("evidence_ref", "")).startswith(("event:", receipt)):
            fixed.update(command=text, evidence_ref=receipt,
                         exit_code=code if fixed.get("exit_code") is None else fixed["exit_code"])
        elif str(fixed.get("evidence_ref", "")).startswith("event:"):
            fixed["evidence_ref"] = receipt
        return fixed
    if isinstance(value, list):
        return [cite_receipts(item) for item in value]
    if value in ("event:check", "check:1"):
        return receipt
    if value == CHECK:
        return text
    return value


def recognize(brief: str, follow_up: dict | None = None) -> dict:
    """The fake's script for the job-recognition stage: keyword rules over the brief.

    This is not a model and says nothing about model quality; it exists so the
    plumbing (the stage runs, its answer reaches the status view) can be proven
    offline. A live profile is what tests recognition itself.
    """
    text = brief.lower()

    def has(*patterns):
        return any(re.search(pattern, text) for pattern in patterns)

    if WORKSTREAM_HEADER.match(brief):
        # A program workstream builds; its brief's "satisfy human review" is not a request for a review.
        kind, signal = "build", "a program workstream brief"
    elif (follow_up or {}).get("previous_workflow") == "review" and has(r"\bfix\b", r"\bland\b", r"\bapply\b"):
        kind, signal = "build", "follow-up: act on the review's findings"
    elif ((follow_up or {}).get("previous_design") or {}).get("mode") == "review" and not has(r"\bbuild\b",
                                                                                              r"\bimplement\b"):
        kind, signal = "design", "follow-up: a reply to the design review"
    elif has(r"\bimplement (it|this|the design)\b", r"has already been .*approved") and not has(r"do(n't| not) implement anything"):
        kind, signal = "build", "implement it / already approved"
    elif has(r"\bdesign\b") and has(r"\breview\b", r"do(n't| not) implement", r"\bdesign how\b", r"^design\b",
                                    r"\bdesign (it|this)\b"):
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
    # A build after a design turn implements the design that turn delivered, as the recognizer is told.
    produced = ((follow_up or {}).get("previous_design") or {}).get("documents") or []
    if kind == "build" and not design and len(produced) == 1:
        design, signal = produced[0], signal + "; follow-up: build the design the previous turn produced"
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
        if (path.is_file() and not relative.startswith(allowed) and "__pycache__" not in relative
                and not relative.startswith(".fake-turns/")):
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
    (a request for a new design, such as architecture-two-services), it hands over to the build pipeline.
    In a conversation each turn's review can be scripted apart (``turn_report``); a revision (the runner
    sends previous_review, or a repair's schema asks for a resolution) passes each concern's status and
    resolution through."""
    path = Path(CONFIG["reference"]) / "review" / "design-review.json"
    scripted = turn_report("review_design")
    if path.is_file() or scripted:
        stray_edits("review/")
    if not path.is_file() and not scripted:
        return {"mode": "propose", "design_under_review": "", "verdict": "not_applicable",
                "summary": "A new design is requested", "satisfied": [], "concerns": [], "questions": []}
    saved = scripted or json.loads(path.read_text())
    schema = (Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text()
              if "--output-schema" in sys.argv else "")
    revising = "previous_review" in DATA or '"resolution"' in schema
    # A blocking concern needs its example; a scripted review probes nothing (probe "").
    concerns = [{**{key: str(c.get(key, "")) for key in ("id", "area", "severity", "summary", "evidence")},
                 "example": str(c.get("example") or "Scripted example: " + str(c.get("summary", ""))),
                 "probe": str(c.get("probe", "")),
                 **({"status": str(c.get("status") or "open"), "resolution": str(c.get("resolution", ""))}
                    if revising else {})}
                for c in saved.get("concerns", [])]
    open_blocking = any(c["severity"] == "blocking" and c.get("status", "open") == "open" for c in concerns)
    return {"mode": "review", "design_under_review": str(saved.get("design_under_review")
                                                         or "the design named in the request"),
            "verdict": "request_changes" if open_blocking else "approve",
            "summary": "Scripted design review from the scenario solution",
            "satisfied": [str(s) for s in saved.get("satisfied", [])], "concerns": concerns,
            "questions": [{"id": str(q.get("id", "")), "question": str(q.get("question", "")),
                           "options": [str(o) for o in q.get("options", [])]} for q in saved.get("questions", [])]}


def answer() -> dict:
    """The fake's Analyst: the note in the solution it was told to apply (the one file under docs/),
    returned as note_path/note_content for the runner to write; stray solution files are applied
    like a model that edits code it was told not to, so the runner's read-only check is exercised."""
    root = Path(CONFIG["reference"])
    notes = sorted(p for p in (root / "docs").rglob("*") if p.is_file() and p.name != "README.md"
                   and p.relative_to(root).as_posix() in PATHS) if (root / "docs").is_dir() else []
    # In a conversation the solution's code belongs to later turns, so it is not played as stray edits.
    if not CONFIG.get("turns"):
        stray_edits("docs/")
    tracked = [ref for ref in source_refs() if ref != "task"]
    note = notes[0] if notes else None
    return {"answer": "Scripted answer from the scenario solution",
            "evidence": [{"claim": "Scripted evidence", "source": tracked[0] if tracked else "README.md"}],
            "questions": [], "note_path": note.relative_to(root).as_posix() if note else "",
            "note_content": note.read_text() if note else ""}


def brief_observation_changes(data: dict) -> list[dict]:
    """Script only exact source-backed amendments; the policy authenticates them."""
    body = (data.get('goal_contract') or {}).get('body') or {}
    wrapper = body.get('brief_acceptance') or {}
    inventory = data.get('brief_declaration_inventory') or []
    inactive = set(wrapper.get('manifest', {}).get('inactive_declaration_ids', []))
    prior = {row['declaration']['id']: row['hash']
             for row in wrapper.get('manifest', {}).get('observations', [])}
    sources = [('feedback:' + row['id'], row['text']) for row in data.get('brief_feedback') or []]
    sources += [('answer:' + qid, row['text']) for qid, row in (data.get('saved_answers') or {}).items()
                if row.get('kind') == 'answer']
    changes = []
    for source_id, text in sources:
        if not re.search(r'\b(?:replace|instead|change|revise)\b', text, re.I):
            continue
        for new in (row for row in inventory if row['source_id'] == source_id):
            old = next((row for row in inventory if row['id'] != new['id']
                        and row['source_id'] != source_id and row['id'] not in inactive
                        and row['program'] == new['program'] and row['observe_argv'] == new['observe_argv']
                        and (row['literal'] in text or row['program'] + ' ' + ' '.join(row['observe_argv']) in text)), None)
            if old is not None:
                changes.append({'previous_hash': prior.get(old['id'], old['declaration_hash']),
                                'declaration_id': new['id'], 'source_event_id': source_id})
                inactive.add(old['id'])
    return changes


def brief_observations(data: dict) -> list[dict]:
    """Choose probe input from the human declaration; never inspect delivered tests."""
    body = (data.get('goal_contract') or {}).get('body') or {}
    criteria = [row['id'] for row in body.get('acceptance_criteria', [])] or ['C1']
    existing = {row['declaration']['id']: row['proposal']
                for row in (body.get('brief_acceptance') or {}).get('manifest', {}).get('observations', [])}
    changes = brief_observation_changes(data)
    inactive = set((body.get('brief_acceptance') or {}).get('manifest', {}).get('inactive_declaration_ids', []))
    retired_hashes = {row['previous_hash'] for row in changes}
    inactive.update(row['id'] for row in data.get('brief_declaration_inventory') or []
                    if row['declaration_hash'] in retired_hashes
                    or any(saved['declaration']['id'] == row['id'] and saved['hash'] in retired_hashes
                           for saved in (body.get('brief_acceptance') or {}).get('manifest', {}).get('observations', [])))
    result = []
    for declaration in data.get('brief_declaration_inventory') or []:
        if declaration['id'] in inactive:
            continue
        if declaration['id'] in existing:
            result.append(existing[declaration['id']])
            continue
        steps, bindings = [], []
        variables = {word for words in declaration['commands'] for word in words
                     if re.fullmatch(r'[A-Z][A-Z_]*', word) and word != 'ID'
                     and re.search(r'\b' + re.escape(word) + r'\b', declaration['literal'])}
        for variable in sorted(variables):
            pattern = next(words for words in declaration['commands'] if variable in words)
            arguments = ['brief-probe' if re.fullmatch(r'[A-Z][A-Z_]*', word) else word for word in pattern]
            bindings.append({'placeholder': variable, 'step': len(steps), 'argument': pattern.index(variable)})
            steps.append({'argv': arguments})
        # Like the live Plan Reviewers (#452 runs on d6aded9), set up a second item, so a
        # listing prints more than one line.
        steps += [{'argv': ['brief-probe-2' if word == 'brief-probe' else word for word in step['argv']]}
                  for step in steps]
        steps.append({'argv': list(declaration['observe_argv'])})
        result.append({'declaration_id': declaration['id'], 'criterion_ids': criteria,
                       'steps': steps, 'observe_step': len(steps) - 1, 'bindings': bindings})
    return result


def builder_evidence_repair(data: dict) -> dict:
    """A Builder report repair: the rejected report, now citing the check it ran.

    Repair packets omit the live contract and task, so the identity comes from
    report_identity, and the citation from the original execution's checks.
    """
    source = data.get('rejected_report') or data.get('original_report') or {}
    content = source.get('content') or '{}'
    report = json.loads(content if isinstance(content, str) else json.dumps(content))
    report.update(data.get('report_identity') or {})
    report['evidence_refs'] = [row['evidence_ref'] for row in data.get('original_executed_checks') or []][:1]
    return report


def risk_observations(data: dict) -> list[dict]:
    body = (data.get('goal_contract') or {}).get('body') or {}
    criteria = [row['id'] for row in body.get('acceptance_criteria', [])] or ['C1']
    existing = {row['declaration']['id']: row['proposal']
                for row in (body.get('risk_acceptance') or {}).get('manifest', {}).get('observations', [])}
    result = []
    for declaration in data.get('risk_declaration_inventory') or []:
        if declaration['id'] in existing:
            result.append(existing[declaration['id']])
        elif declaration.get('supported') and declaration.get('allowed_modules'):
            result.append({'declaration_id': declaration['id'], 'criterion_ids': criteria,
                           'module': declaration['allowed_modules'][0]})
    return result


def report_identity(data: dict) -> dict:
    """Use this invocation's schema and handoff, including context-free repairs."""
    contract = data.get("goal_contract") or {}
    identity = {"contract_revision": contract.get("revision", 0),
                "contract_hash": contract.get("hash", ""),
                "task_id": (data.get("current_task") or {}).get("id", "")}
    identity.update({key: value for key, value in (data.get("report_identity") or {}).items()
                     if key in identity and value is not None})
    for key in identity:
        allowed = OUTPUT_SCHEMA.get("properties", {}).get(key, {}).get("enum", [])
        if len(allowed) == 1:
            identity[key] = allowed[0]
    return identity


def builder_failure_investigation(data: dict) -> dict:
    """Classify the stock fixture's failed proof, not a generic exhausted-stage pause."""
    failure = data['builder_failure']
    record = failure['record']
    refs = [record['output'], record['after_ref']]
    if not failure['failure_id'] or not set(refs) <= set(failure['evidence_refs']):
        raise SystemExit('fake_codex: stock classification lacks pinned report/source evidence')
    rejected = json.loads(Path(refs[0]).read_text())
    findings = ' '.join(row['finding'] for row in rejected.get('findings') or [])
    names = sorted(set(re.findall(r'has no test named (test_\w+)', findings)))
    if rejected.get('status') != 'REWORK' or 'regression_proof is not PASS:' not in findings or not names:
        raise SystemExit('fake_codex: stock classification needs an actual failed refusal-test proof')
    diagnosis = (f"The failed regression proof names {', '.join(names)}. These delivered refusal tests "
                 "only assert exit 2, nonempty stderr and unchanged stock.json, so argparse's unknown-command "
                 "refusal passes them too. The current move command rejects zero quantity itself. "
                 "This is an implementation defect in tests/test_stock.py, not a new approach or permission.")
    # The runner copies only these allowed run files; the probe reads current code
    # and runs a refusal that cannot write stock.json, under native read-only containment.
    code = f'''import ast, hashlib, json, re, subprocess, sys
from pathlib import Path
r = json.loads(Path({'run/' + Path(refs[0]).name!r}).read_text())
s = json.loads(Path({'run/' + Path(refs[1]).name!r}).read_text())
assert r['status'] == 'REWORK'
assert {{k: r[k] for k in ('task_id', 'contract_hash', 'contract_revision')}} == { {k: failure['binding'][k] for k in ('task_id', 'contract_hash', 'contract_revision')}!r}
assert s['revision'] == {record['source_revision']!r}
for path in ('stock.py', 'tests/test_stock.py'):
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == s['files'][path]
findings = ' '.join(row['finding'] for row in r['findings'])
assert 'regression_proof is not PASS:' in findings
assert sorted(set(re.findall(r'has no test named (test_\\w+)', findings))) == {names!r}
tests = {{node.name: ast.unparse(node) for node in ast.walk(ast.parse(Path('tests/test_stock.py').read_text())) if isinstance(node, ast.FunctionDef)}}
for name in {names!r}:
    assert 'self.assertEqual(result.returncode, 2)' in tests[name]
    assert 'self.assertTrue(result.stderr.strip())' in tests[name]
    assert 'self.assertEqual(self.store_bytes(), before)' in tests[name]
    assert 'assert_refused' not in tests[name] and 'invalid choice' not in tests[name]
p = subprocess.run([sys.executable, 'stock.py', 'move', 'bolt', '0', 'A1', 'B2'], capture_output=True, text=True, timeout=10)
assert p.returncode == 2 and p.stderr.startswith('stock.py: ') and 'invalid choice' not in p.stderr
'''
    return {'diagnosis': diagnosis, 'cause': 'stage_output', 'guidance':
            "Repair only tests/test_stock.py: require command-specific stderr starting with 'stock.py: ' "
            "and reject 'invalid choice'; keep every existing refusal assertion and let the runner re-prove it.",
            'recommendation': 'retry', 'user_question': '', 'evidence_refs': refs,
            'example': 'Given the current vacuous refusal tests, the project check passes but the runner '
                       'rejects their regression proof because the named tests also pass without move/remove.',
            'probe': shlex.join([sys.executable, '-B', '-c', code]), 'untestable': '',
            'failure_class': 'execution', 'failure_id': failure['failure_id']}


def report_for(stage: str, data: dict) -> dict:
    if stage == 'requirements':
        requirements_body = contract()
        for field in ('end_to_end_flow', 'technical_approach', 'milestones', 'initial_task'):
            requirements_body.pop(field, None)
        return {'summary': 'Scripted requirements before v2 planning', 'requirements': requirements_body}
    if stage in ('plan', 'plan_review', 'plan_revise', 'plan_finalize'):
        equivalent = {'plan': 'astra_discovery', 'plan_review': 'astra_challenge',
                      'plan_revise': 'glm_revise', 'plan_finalize': 'astra_finalize'}[stage]
        value = report_for(equivalent, data)
        if stage in ('plan', 'plan_revise'):
            value['contract'] = contract(final=True)
        allowed = {'summary', 'contract', 'responses', 'decisions', 'concerns', 'progressive_proposal'}
        if stage == 'plan_finalize':
            allowed |= {'brief_observations', 'brief_observation_changes', 'risk_observations', 'risk_observation_changes'}
        return {key: value for key, value in value.items() if key in allowed}
    execution_repair = data.get("report_repair") and stage in (
        "terra", "sol", "astra_checkpoint", "astra_review", "astra_plan", "astra_resolve")
    if data.get("report_repair") and (PROGRESSIVE or execution_repair):
        # A report-only repair may preserve its original proposal/checklist, not
        # invent execution authority from a packet without execution context.
        original = data.get("rejected_report") or data.get("original_report") or {}
        content = original.get("content") if isinstance(original, dict) else None
        if content is not None:
            try:
                report = json.loads(content) if isinstance(content, str) else json.loads(json.dumps(content))
            except (TypeError, ValueError):
                raise SystemExit("fake_codex: report-only repair needs an object report") from None
            if not isinstance(report, dict):
                raise SystemExit("fake_codex: report-only repair needs an object report")
            if execution_repair:
                report.update(report_identity(data))
            return report
        if execution_repair:
            raise SystemExit("fake_codex: report-only repair is missing its rejected report")
    if stage == "recognize_workflow":
        return recognize(data.get("task") or CONFIG["brief"], data.get("follow_up"))
    if stage == "answer_question":
        return answer()
    if stage == "check_design":
        return check_design(data)
    if stage == "investigate_stuck":
        if CONFIG.get('fault') == 'vacuous_refusal_tests' and data.get('builder_failure'):
            return builder_failure_investigation(data)
        if CONFIG.get("fault") == "vacuous_refusal_tests" and os.environ.get("SCENARIO_FAKE_SCOPE_SLIP") == "retry":
            return scripted_fault("vacuous_refusal_provider.py")["investigate"](data)
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
    if (stage in ("sol", "astra_checkpoint", "astra_review")
            and os.environ.get("SCENARIO_FAKE_REQUIRE_DIAGNOSIS_PROVENANCE")):
        artifacts = data.get("runner_artifacts") or []
        note_path = "docs/bugs/scripted-diagnosis.json"
        note = Path(data["workspace"]) / note_path
        if (len(artifacts) != 1 or artifacts[0].get("path") != note_path
                or artifacts[0].get("kind") != "runner_written_diagnosis"
                or artifacts[0].get("sha256") != hashlib.sha256(note.read_bytes()).hexdigest()
                or note_path in task.get("affected_paths", [])
                or note_path not in (data.get("regression_proof") or {}).get("source_files", [])):
            raise SystemExit("Scripted reviewer: diagnosis ownership is not authenticated or raw proof was hidden")
    common = {
        **report_identity(data), "deferred_backlog": [],
        "user_request": {"kind": "none", "discovered": "", "impact": "", "decision_needed": "",
                         "options": [], "proposed_delta": ""},
    }
    planning = {"code_refs": [ref for ref in source_refs() if ref != "task"], "contract_changes": permission_changes(),
                "conflict_resolutions": [], "requirement_trace": trace()}
    if CONFIG.get("fault") == "verification_reuse" and stage == "sol":
        import runpy
        scenario = next(parent for parent in Path(CONFIG["reference"]).parents
                        if (parent / "scenario.toml").is_file())
        provider = runpy.run_path(str(scenario.parents[1] / "harness" / "verification_reuse_provider.py"))
        return provider["report_for"](data, common, emit)
    if CONFIG.get("fault", "").startswith("recovery_novelty_") and stage in (
            "terra", "sol", "astra_review", "astra_resolve"):
        import runpy
        scenario = next(parent for parent in Path(CONFIG["reference"]).parents
                        if (parent / "scenario.toml").is_file())
        provider = runpy.run_path(str(scenario.parents[1] / "harness" / "recovery_novelty_provider.py"))
        return provider["report_for"](stage, data, common, CONFIG, run_check, requirements)
    if CONFIG.get("fault", "").startswith("completion_rework_") and stage in (
            "terra", "sol", "astra_review", "astra_resolve"):
        provider = scripted_fault("completion_rework_provider.py")
        return provider["report_for"](stage, data, common, CONFIG, run_check, requirements)
    if CONFIG.get("fault") == "vacuous_refusal_tests" and stage in ("terra", "sol", "astra_review", "astra_resolve"):
        provider = scripted_fault("vacuous_refusal_provider.py")
        return provider["report_for"](stage, data, common, CONFIG, run_check, requirements)
    if PROGRESSIVE:
        report = progressive_report(stage, data, common)
        if report is not None:
            return report
        if stage in ("terra", "sol", "astra_review", "astra_checkpoint", "astra_resolve"):
            raise SystemExit("fake_codex: progressive execution missing its authoritative verification packet")
        if "PROGRESSIVE PLANNING" in PROMPT and stage in ("astra_discovery", "glm_revise", "astra_finalize"):
            planning["progressive_proposal"] = progressive_proposal()
    if stage == "requirements_gather":
        # A follow-up's requirements are its own sentences; the earlier request's are another job's.
        quoted = [r["text"] for r in requirements()]
        ignored = [sentence for sentence in data.get("requirement_coverage_checklist") or []
                   if CONFIG.get("turns") and not any(sentence in quote or quote in sentence for quote in quoted)]
        return {"summary": "Scripted requirements: one per brief sentence",
                "intended_outcome": outcome(), "required_behaviors": quoted,
                "constraints": [], "acceptance_tests": [CHECK], "source_refs": source_refs(),
                "proposed_assumptions": [], "open_questions": [], "requirements": requirements(),
                "ignored_statements": ignored, "conflicts": [], "proposed_reframes": []}
    # An adaptive-planning Planner drafts the complete plan, initial_task included.
    adaptive = ("ADAPTIVE PLANNING" in PROMPT or
                "initial_task" in OUTPUT_SCHEMA.get("properties", {}).get("contract", {}).get("required", []))
    if stage == "astra_discovery":
        report = {"summary": "Scripted plan", "contract": contract(final=adaptive), "alternatives": [],
                  "uncertainties": [], **planning}
        if os.environ.get("SCENARIO_FAKE_REQUIREMENTS_RERUN") == "1" and "set requirements_rerun" in PROMPT:
            # The Planner sends feedback that changes what is built back to Requirements.
            report["requirements_rerun"] = "Scripted: the feedback changes what is being built"
        if CONFIG.get("fault") == "planner_citation" and not guided_to_fix_citation():
            # Prose after an existing path is valid. A nonexistent sibling is a
            # deterministic rejected citation, including on report-only repairs.
            report["code_refs"] = [f"{note}.missing" for note in bug_notes()]
        return report
    reviewed = ((((DATA.get("planning") or {}).get("reports") or {}).get("astra_challenge") or ((DATA.get("planning") or {}).get("reports") or {}).get("plan_review") or {})
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
        return {"summary": "Scripted plan review: no concerns", "concerns": [],
                "brief_observations": brief_observations(data), "brief_observation_changes": brief_observation_changes(data), "risk_observations": risk_observations(data), "risk_observation_changes": []}
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
        return {"summary": "Scripted final plan", "contract": contract(final=True), "decisions": decisions, "brief_observations": brief_observations(data), "brief_observation_changes": brief_observation_changes(data), "risk_observations": risk_observations(data), "risk_observation_changes": [], **final}
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
                "evidence_refs": [evidence], "addressed_requirements": criterion_ids(row["id"]),
                "untested_behavior": [], "recommended_checks": [row["verify"]]}
    if stage == "terra" and (CONFIG.get("turn_paths") or WORKSTREAM):
        # Deliver this turn's (or this workstream's) files only, and only those the runner assigned.
        assigned = tuple((data.get("current_task") or {}).get("affected_paths") or PATHS)
        for rel in (path for path in PATHS if path.startswith(assigned)):
            destination = Path.cwd() / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(CONFIG["reference"]) / rel, destination)
    elif stage == "terra":
        shutil.copytree(CONFIG["reference"], Path.cwd(), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if stage == "terra":
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
            every = [cid for row in MILESTONES for cid in criterion_ids(row["id"])]
            checks = [{"command": CHECK, "exit_code": code, "evidence_ref": "event:check"}]
            criteria = [{"id": cid, "status": status, "evidence_refs": ["event:check"]} for cid in every]
            return {**common, "verdict": status, "checks_run": [CHECK], "findings": [],
                    "finding_dispositions": [], "unverified_criteria": [], "checks": checks,
                    "criterion_results": criteria,
                    "end_to_end_result": {"status": status, "summary": f"{CHECK} exited {code}",
                                          "evidence_refs": ["event:check"],
                                          "technical_result": None, "pending_human_criteria": []}}
        checks, criteria, member_results = [], [], []
        for milestone_id in members:
            row = milestone_row(milestone_id)
            code, evidence = run_verify(row["verify"])
            status = "PASS" if code == 0 else "FAIL"
            checks.append({"command": row["verify"], "exit_code": code, "evidence_ref": evidence})
            criteria += [{"id": cid, "status": status, "evidence_refs": [evidence]}
                         for cid in criterion_ids(milestone_id)]
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
                                        "evidence_refs": [row["evidence_refs"][0] for row in member_results],
                                        "technical_result": None, "pending_human_criteria": []}}
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
                                      "evidence_refs": [ref], "technical_result": None, "pending_human_criteria": []}}
    if stage in ("astra_review", "astra_plan") and MILESTONES and not (data.get("goal_contract") or {}).get("revision"):
        # Report repair: the handoff has no live contract or current task; fix
        # the rejected draft's identity fields and return it as instructed.
        import json as _json
        source = data.get("rejected_report") or data.get("original_report") or {}
        draft = source.get("content") if isinstance(source, dict) else None
        fixed = _json.loads(draft) if draft else {}
        fixed.update(report_identity(data))
        return fixed
    if stage in ("astra_review", "astra_plan") and MILESTONES:
        task = data.get("current_task") or {}
        done = done_milestones(data)
        approved = ((data.get("goal_contract") or {}).get("body") or {}).get("acceptance_criteria") \
            or [{"id": cid, "criterion": row["objective"]} for row in MILESTONES for cid in criterion_ids(row["id"])]
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
                                   "acceptance_criteria": criterion_ids(upcoming["id"]),
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
    kind = ("null" if "null" in kind else kind[0]) if isinstance(kind, list) else kind
    if schema.get("enum"):
        return schema["enum"][0]
    return {"object": lambda: complete({}, schema), "array": list, "string": str, "boolean": bool,
            "integer": int, "number": float, "null": lambda: None}.get(kind, lambda: None)()


# Were it ever run, it would leave cmd-only-final-ran in the project (tests/test_cmd_only_report_cli.py).
CMD_ONLY_FINAL = {"cmd": "touch cmd-only-final-ran && ls docs/ && wc -l docs/*.md"}


def cmd_only_final(stage: str) -> bool:
    """Fault SCENARIO_FAKE_CMD_ONLY=<stage>[:<count>] (issue #512): the stage's final message is a shell
    command, ``{"cmd": ...}``, instead of its report, as a Codex-transport provider did for the Plan
    Reviewer. Its first <count> calls answer so, report repairs and corrections included; with no count,
    every call does. The count is kept beside the configuration, never in the project."""
    wanted, _, count = os.environ.get("SCENARIO_FAKE_CMD_ONLY", "").partition(":")
    if not wanted or wanted != stage:
        return False
    seen = Path(os.environ["SCENARIO_FAKE_CONFIG"]).with_name("fake-cmd-only.json")
    calls = json.loads(seen.read_text()) if seen.is_file() else 0
    seen.write_text(json.dumps(calls + 1))
    return not count or calls < int(count)


def main() -> int:
    if sys.argv[1:] == ["login", "status"]:
        print("Logged in using ChatGPT (scenario fake provider)")
        return 0
    global PROMPT, OUTPUT_SCHEMA
    prompt = PROMPT = sys.stdin.read()
    session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
    emit({"type": "thread.started", "thread_id": session})
    # Each session's handoff, beside the configuration: a same-session correction
    # (autocode_format_correction) carries none, and the model answers it from its session.
    remembered = Path(os.environ["SCENARIO_FAKE_CONFIG"]).with_name("fake-sessions") / f"{session}.txt"
    if "CURRENT HANDOFF DATA\n" not in prompt and "resume" in sys.argv and remembered.is_file():
        prompt = PROMPT = remembered.read_text()
    if "CURRENT HANDOFF DATA\n" not in prompt:
        emit({"error": "no handoff data"})
        return 0
    remembered.parent.mkdir(exist_ok=True)
    remembered.write_text(prompt)
    global DATA, PATHS, WORKSTREAM, MILESTONES, CHECK
    data = DATA = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
    PATHS = turn_paths()
    WORKSTREAM = workstream()
    if WORKSTREAM:
        MILESTONES, CHECK, PATHS = workstream_scope(WORKSTREAM)
    original = data.get("original") or {}
    stage = data.get("stage") or original.get("stage") or ""
    if data.get("report_repair"):
        # Report repairs are answered as the stage that owns them.
        stage = original.get("stage", stage)
    if CONFIG.get("fault") == "quota_once" and stage == "sol" and model_argument() == QUOTA_SPENT_MODEL:
        # Fault "quota_once" (scenarios/catalog/quota-route-handoff): the Tester's model has no quota left.
        # It fails the first time; once a person names another model the Tester runs normally.
        emit({"type": "error", "error": {"message": "subscription usage limit reached; add credits"}})
        return 3
    OUTPUT_SCHEMA = (json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
                     if "--output-schema" in sys.argv else {})
    # Live #452 run jb1acns9: the Builder's first report after approval cites no evidence.
    no_evidence = stage == 'terra' and os.environ.get('SCENARIO_FAKE_BUILDER_NO_EVIDENCE') == '1'
    if no_evidence and data.get('report_repair'):
        report = builder_evidence_repair(data)
    else:
        report = cite_receipts(report_for(stage, data))
    if stage in ('astra_challenge', 'astra_finalize', 'plan_finalize') and os.environ.get('SCENARIO_FAKE_RISK_OMIT') == '1':
        report['risk_observations'] = []
    if stage in ('astra_challenge', 'astra_finalize', 'plan_finalize') and os.environ.get('SCENARIO_FAKE_BRIEF_OMIT') == '1':
        report['brief_observations'] = []
    if no_evidence and not data.get('report_repair'):
        marker = Path(os.environ["SCENARIO_FAKE_CONFIG"]).with_name("fake-builder-no-evidence")
        if not marker.exists():  # once: later Builder runs cite their check as usual
            marker.touch()
            report['evidence_refs'] = []
    if os.environ.get("SCENARIO_FAKE_SIDE"):
        # A hybrid run's witness (harness/hybrid.py): which side of the route this scripted call stood for.
        with Path(os.environ["SCENARIO_FAKE_CONFIG"]).with_name("fake-calls.jsonl").open("a") as handle:
            handle.write(json.dumps({"stage": stage, "repair": bool(data.get("report_repair")),
                                     "side": os.environ["SCENARIO_FAKE_SIDE"]}) + "\n")
    if "--output-schema" in sys.argv:
        schema = OUTPUT_SCHEMA
        # Runner-owned brief fields exist only for protected human declarations.
        # Match this call's schema, including report repairs with a smaller packet.
        for field in ('brief_observations', 'brief_observation_changes', 'risk_observations', 'risk_observation_changes'):
            if field not in schema.get('properties', {}):
                report.pop(field, None)
        complete(report, schema)
    if cmd_only_final(stage):
        report = dict(CMD_ONLY_FINAL)
    Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(report))
    # Plausible usage, so a reader of the events is not misled by an all-zero turn.
    emit({"type": "turn.completed", "usage": {"input_tokens": max(1, len(prompt) // 4),
                                              "output_tokens": max(1, len(json.dumps(report)) // 4)}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
