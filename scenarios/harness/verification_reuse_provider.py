"""A supplemental real check, distinct from the mandatory canonical command."""
import copy
import json
import os
import subprocess


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
    emit({"type": "item.completed", "item": {"id": "check", "type": "command_execution",
          "command": command, "exit_code": result.returncode,
          "aggregated_output": result.stdout + result.stderr}})
    status = "PASS" if result.returncode == 0 else "FAIL"
    if os.environ.get("SCENARIO_VERIFICATION_FORCE_REPAIR") and not data.get("report_repair"):
        common = {**common, "task_id": "scripted-invalid-task"}
    return {**common, "verdict": status, "checks_run": [command], "findings": [],
            "finding_dispositions": [], "unverified_criteria": [],
            "checks": [{"command": command, "exit_code": result.returncode, "evidence_ref": "event:check"}],
            "criterion_results": [{"id": row["id"], "status": status, "evidence_refs": ["check:1"]}
                                  for row in data["goal_contract"]["body"]["acceptance_criteria"]],
            "end_to_end_result": {"status": status, "summary": f"Supplemental tests exited {result.returncode}",
                                  "evidence_refs": ["check:1"]}}
