#!/usr/bin/env python3
"""A real Resolver blocker over the ordinary offline greeting provider."""

import io
import json
import os
import runpy
import sys
from pathlib import Path

base = Path(__file__).with_name("greeting-provider.py")
if sys.argv[1:] == ["login", "status"]:
    runpy.run_path(str(base), run_name="__main__")

prompt = sys.stdin.read()
data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
sys.stdin = io.StringIO(prompt)
fixture = None
try:
    fixture = runpy.run_path(str(base), run_name="__main__")
except SystemExit as error:
    if error.code not in (None, 0):
        raise

stage = (data.get("original") or {}).get("stage") if data.get("report_repair") else data["stage"]
output = Path(sys.argv[sys.argv.index("-o") + 1])
report = json.loads(output.read_text())
corrected = "The CLI greets a nonempty name and refuses empty names."
if stage == "astra_resolve":
    report.update(
        status="BLOCKED",
        diagnosis="The criterion's generic wording does not state the required invalid-input behavior.",
        blocker="The person must decide whether to clarify the approved criterion.",
        next_objective="",
        next_task={
            "kind": "none",
            "milestone_id": "",
            "requirements": [],
            "acceptance_criteria": [],
            "validation_plan": [],
            "findings": [],
        },
        user_request={
            "kind": "blocker",
            "discovered": "The approved criterion says only Contract holds.",
            "impact": "The invalid-input obligation should be explicit before more building.",
            "decision_needed": "Accept recommended revision to clarify the greeting criterion?",
            "options": ["Accept recommended revision", "Keep the approved criterion"],
            "proposed_delta": "Clarify C1 wording; its verification and review requirements are unchanged.",
        },
    )
    mode = os.environ.get("RECOMMENDATION_FIXTURE", "valid")
    if mode != "missing":
        report["user_request"]["recommended_revision"] = {
            "acceptance_criteria": [{"id": "unknown" if mode == "unknown" else "C1", "criterion": corrected}]
        }

# Subsequent independent planning preserves the person's accepted criterion.
prior = (data.get("goal_contract") or {}).get("body") or {}
if any(row.get("criterion") == corrected for row in prior.get("acceptance_criteria", [])):
    for row in (report.get("contract") or {}).get("acceptance_criteria", []):
        if row["id"] == "C1":
            row["criterion"] = corrected
            # Cite the wording the person actually accepted, as a real
            # Plan Reviewer must when the correction adds a human source.
            for source in data.get("source_inventory") or []:
                if corrected in source.get("text", ""):
                    citation = {"source_id": source["id"], "quote": corrected}
                    if citation not in row.setdefault("sources", []):
                        row["sources"].append(citation)
    # The runner carries its sealed trace. Models return only a fresh review's
    # top-level source_trace judgment, never the runner-owned body wrapper.
    if "contract" in report:
        report["contract"].pop("source_trace", None)
        if fixture is not None and "source_trace" in report:
            # The base review predates our added accepted-wording citation.
            # Recompute its judgment against the actual emitted criterion.
            # Compact finalization retains the authenticated body by token;
            # the wire object itself has no criteria to classify. Judge the
            # retained body rather than declaring every source out of scope.
            traced = prior if report["contract"].get("retain_contract") else report["contract"]
            report["source_trace"] = fixture["source_trace_for"](traced)
output.write_text(json.dumps(report))
with Path(os.environ["RECOMMENDATION_TRACE"]).open("a") as trace:
    trace.write(
        json.dumps({"stage": stage + ("_report_repair" if data.get("report_repair") else ""), "output": str(output)})
        + "\n"
    )
