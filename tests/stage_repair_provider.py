#!/usr/bin/env python3
"""Explicitly offline provider faults derived from the revision-31 reports.

The wrapper delegates normal behavior to tools/fake_codex.py. Its repair turns
model the observed mistakes only while the real prompt lacks the corrective
field placement or file/probe mapping. This proves controller behavior and
handoff contents, not that a live model will follow the instructions.
"""
from __future__ import annotations

import copy
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import sys
import traceback


def initial_task(contract):
    """A coherent first task for the existing greeting fixture contract."""
    milestone = contract["milestones"][0]
    return {
        "objective": "Implement greeting CLI", "affected_paths": ["greet.py"],
        "kind": "implement", "milestone_id": milestone["id"],
        "requirements": ["Greet names; reject empty/whitespace input"],
        "acceptance_criteria": milestone["acceptance_criteria"],
        "validation_plan": ["Execute valid, empty and whitespace input"],
    }


def finalizer_report(contract):
    value = {
        "contract": copy.deepcopy(contract), "summary": "Ready for approval",
        "contract_changes": [], "requirement_trace": [], "conflict_resolutions": [],
        "obligation_decisions": [], "decisions": [{"concern_id": "P1",
            "decision": "Reject whitespace", "rationale": "Consistent invalid-input contract",
            "acceptance_test": "Whitespace input exits 2", "resolved": True}],
    }
    value["contract"]["initial_task"] = initial_task(contract)
    return value


def repair_finalizer(prompt, value):
    """Follow explicit nested placement, otherwise reproduce the observed root field."""
    result = copy.deepcopy(value)
    task = result.pop("initial_task", None) or result["contract"].pop("initial_task", None)
    task = task or initial_task(result["contract"])
    head = prompt.split("CURRENT HANDOFF DATA\n", 1)[0]
    if "contract.initial_task" in head:
        result["contract"]["initial_task"] = task
    else:
        result["initial_task"] = task
    return result


def probe_command(path):
    script = ("import json; from pathlib import Path; "
              f"p = Path({path!r}); r = json.loads(p.read_text()); "
              "assert r['decisions'] == []; print('diagnosed missing concern decisions')")
    return shlex.join([sys.executable, "-c", script])


def investigator_report(source):
    source = Path(source)
    return {
        "diagnosis": "The final report omitted the saved concern decisions.",
        "cause": "stage_output", "guidance": "Return every saved concern ID once in decisions.",
        "recommendation": "retry", "user_question": "", "evidence_refs": [str(source)],
        "example": "Given an empty decisions array, the runner rejected missing concern P1.",
        # The live report guessed an archive-prefixed scratch name. The actual
        # scratch contract copies an archived file under its unchanged basename.
        "probe": probe_command("run/" + source.parent.name + "-" + source.name),
        "untestable": "",
    }


def repair_investigator(prompt, data, value):
    """Use the emitted exact scratch map; reproduce generic-event advice when present."""
    result = copy.deepcopy(value)
    head = prompt.split("CURRENT HANDOFF DATA\n", 1)[0]
    result["evidence_refs"] = [ref for ref in result["evidence_refs"] if not ref.startswith("event:")]
    files = (data.get("investigation_context") or {}).get("evidence_files") or []
    mapping = next((row for row in files if row["evidence_ref"] in result["evidence_refs"]), None)
    if mapping:
        result["probe"] = probe_command(mapping["probe_path"])
    else:
        # This was the other observed repair error: a live absolute path rather
        # than the runner's copied scratch path.
        result["probe"] = probe_command(result["evidence_refs"][0])
    permits_events = "Evidence references must be bare event: IDs or exact file paths" in head
    if permits_events and "event: IDs are invalid for investigate_stuck" not in head:
        result["evidence_refs"].append("event:diagnostic-read")
    return result


def append_trace(row):
    with Path(os.environ["STAGE_REPAIR_TRACE"]).open("a") as stream:
        stream.write(json.dumps(row) + "\n")


# The 2026-10-05 self-build planned a ~100 KB contract. Each planning report stayed
# under the 128 KiB report limit, but the Plan Reviewer's repair handoff carries the
# earlier reports too, so it exceeded the 256 KiB handoff limit.
OVERSIZED_REPORT_PADDING = 90 * 1024


def traced(stage, repair):
    path = Path(os.environ["STAGE_REPAIR_TRACE"])
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [row for row in rows if row["stage"] == stage and row["repair"] == repair]


def main():
    delegate = os.environ["STAGE_REPAIR_DELEGATE"]
    if sys.argv[1:] == ["login", "status"]:
        os.execv(sys.executable, [sys.executable, delegate, *sys.argv[1:]])
    prompt = sys.stdin.read()
    data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
    repair = bool(data.get("report_repair"))
    stage = data["original"]["stage"] if repair else data["stage"]
    # The runner already launched this provider process. Reuse it for the fake,
    # rather than adding another interpreter and supervised descendant per turn.
    stdout, stderr = io.StringIO(), io.StringIO()
    original_stdin, sys.stdin = sys.stdin, io.StringIO(prompt)
    returncode = 0
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                runpy.run_path(delegate, run_name="__main__")
            except SystemExit as error:
                if error.code is not None:
                    returncode = error.code if isinstance(error.code, int) else 1
                    if not isinstance(error.code, int):
                        print(error.code, file=sys.stderr)
            except Exception:
                traceback.print_exc()
                returncode = 1
    finally:
        sys.stdin = original_stdin
    if returncode:
        sys.stdout.write(stdout.getvalue())
        sys.stderr.write(stderr.getvalue())
        return returncode
    output = Path(sys.argv[sys.argv.index("-o") + 1])
    value = json.loads(output.read_text())
    event_rows = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    case = os.environ["STAGE_REPAIR_CASE"]
    row = {"stage": stage, "repair": repair, "error": data.get("error", ""), "output": str(output)}
    if stage == "astra_finalize" and case == "finalizer":
        if repair:
            value = repair_finalizer(prompt, data["rejected_report"]["content"])
        else:
            value["contract"].pop("initial_task")
        row["initial_task_path"] = ("contract.initial_task" if "initial_task" in value["contract"]
                                    else "initial_task" if "initial_task" in value else "missing")
    elif case == "oversized" and not repair and stage in ("astra_discovery", "astra_challenge", "glm_revise"):
        value["summary"] += " " + "x" * OVERSIZED_REPORT_PADDING
    elif stage == "astra_finalize" and case == "oversized":
        # Only the first final review is rejected; a repair of it would be a runner fault.
        if not repair and not traced(stage, False):
            value["contract"].pop("initial_task")
    elif stage == "astra_finalize" and case == "investigator":
        # A persistent empty concern response is an explicit fixture fault. The
        # Investigator is the behavior under test, and its guidance ends it.
        if "INVESTIGATOR GUIDANCE" not in prompt.split("CURRENT HANDOFF DATA\n", 1)[0]:
            value["decisions"] = []
    elif stage == "investigate_stuck" and case == "investigator":
        request = (data.get("investigation_context") or {}).get("stuck") if repair else data.get("stuck")
        if request:
            row.update(stuck_status=request["status"],
                       stuck_identity=request["stage"] + ":" + request["status"])
        if repair:
            value = repair_investigator(prompt, data, data["rejected_report"]["content"])
        else:
            source = next(item["output"] for item in reversed(data["recent_stages"])
                          if item["stage"].startswith("astra_finalize") and item.get("rejected"))
            value = investigator_report(source)
            # The event is a real, independently observed read, but the job's
            # evidence field still requires file paths rather than its event ID.
            command = shlex.join([sys.executable, "-c",
                                  f"from pathlib import Path; print(Path({source!r}).read_text())"])
            read = subprocess.run(shlex.split(command), text=True, capture_output=True)
            event_rows.insert(-1, {"type": "item.completed", "item": {
                "id": "diagnostic-read", "type": "command_execution", "command": command,
                "exit_code": read.returncode, "aggregated_output": read.stdout + read.stderr}})
            if read.returncode:
                raise RuntimeError("Offline diagnostic read failed")
        row.update(evidence_refs=value["evidence_refs"], probe=value["probe"],
                   has_scratch_map=bool((data.get("investigation_context") or {}).get("evidence_files")))
    output.write_text(json.dumps(value))
    append_trace(row)
    for event in event_rows:
        print(json.dumps(event))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
