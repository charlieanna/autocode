"""Independent product acceptance, plus actual failed/fixed public-stage evidence."""
import json
from pathlib import Path
import runpy

from harness.oracle import Check


def check(project, scenario, run=None):
    existing = runpy.run_path(str(Path(__file__).resolve().parents[1] / "completion-rework-direct" / "oracle.py"))
    checks = existing["check"](project, scenario)
    if run is not None:
        view = run.get("view") or {}
        trace = project.parent / "rework-trace.jsonl"
        rows = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        validators = [row for row in rows if row["stage"] == "sol"]
        checks.append(Check("repeat_incident_was_exercised", len(validators) == 3 and
                            [row["exit_code"] for row in validators] == [1, 1, 0], repr(validators)))
        checks.append(Check("known_correction_without_paid_diagnosis", not any(row["stage"] == "astra_resolve" for row in rows), repr(rows)))
        replay = (view.get("evidence") or {}).get("check_replay") or {}
        checks.append(Check("fresh_independent_acceptance", view.get("done") is True and replay.get("verdict") == "PASS" and
                            bool(validators) and replay.get("source_revision") == validators[-1]["source_revision"], repr(view.get("status"))))
    return checks
