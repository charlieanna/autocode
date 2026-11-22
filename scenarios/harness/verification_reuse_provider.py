"""A supplemental real check, distinct from the mandatory canonical command."""

import copy
import json
import os
import subprocess
from pathlib import Path


def _fixed_file():
    """A workspace file a scripted Tester's probe_reason names: a reason stands only when it names a path
    that exists in the workspace (tools/autocode_tester_probes.py)."""
    root = Path.cwd()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_file() and "." in path.name[1:] and not any(part.startswith(".") for part in relative.parts):
            return relative.as_posix()
    return ""


def report_for(data, common, emit):
    if data.get("report_repair"):
        # Repair packets deliberately omit the live contract/task. Preserve the
        # original executed checks and outcomes; correct only their bound identity.
        source = data.get("rejected_report") or data.get("original_report") or {}
        content = source["content"]
        report = json.loads(content) if isinstance(content, str) else copy.deepcopy(content)
        report.update(data["report_identity"])
        return report
    command = "python3 -m unittest -v test_greet"
    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)
    emit(
        {
            "type": "item.completed",
            "item": {
                "id": "check",
                "type": "command_execution",
                "command": command,
                "exit_code": result.returncode,
                "aggregated_output": result.stdout + result.stderr,
            },
        }
    )
    status = "PASS" if result.returncode == 0 else "FAIL"
    if os.environ.get("SCENARIO_VERIFICATION_FORCE_REPAIR") and not data.get("report_repair"):
        common = {**common, "task_id": "scripted-invalid-task"}
    return {
        **common,
        "verdict": status,
        "checks_run": [command],
        "findings": [],
        "finding_dispositions": [],
        "unverified_criteria": [],
        "checks": [{"command": command, "exit_code": result.returncode, "evidence_ref": "event:check"}],
        "criterion_results": [
            {
                "id": row["id"],
                "status": status,
                "evidence_refs": ["check:1"],
                # The supplemental run varies no input of its own, so the Tester records why (autocode_tester_probes).
                **(
                    {
                        "probe_reason": f"Scripted Tester: {row['id']} has only its fixed test run; {_fixed_file()} holds the input"
                    }
                    if status == "PASS"
                    else {}
                ),
            }
            for row in data["goal_contract"]["body"]["acceptance_criteria"]
        ],
        "end_to_end_result": {
            "status": status,
            "summary": f"Supplemental tests exited {result.returncode}",
            "evidence_refs": ["check:1"],
        },
    }
