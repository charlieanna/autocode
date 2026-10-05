"""Repeat an actual failing build, then either hold or propose a discriminating fix."""
import json
import hashlib
import os
from pathlib import Path
import runpy


def report_for(stage, data, common, config, run_check, requirements):
    helper = runpy.run_path(str(Path(__file__).with_name("completion_rework_provider.py")))
    nonpython = config["fault"] == "recovery_novelty_nonpython"
    trace = Path(os.environ["SCENARIO_FAKE_CONFIG"]).parent / "rework-trace.jsonl"
    if nonpython and stage == "terra":
        previous = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        count = sum(row["stage"] == "terra" for row in previous) + 1
        root = Path(config["reference"]).parent
        source = root / ("seed" if count <= 2 else "reference") / "greet.js"
        Path("greet.js").write_text(source.read_text() + f"\n// controlled attempt {count}\n")
        code = run_check()
        with trace.open("a") as handle:
            handle.write(json.dumps({"stage": stage, "task_id": common["task_id"], "source_revision": data["source_revision"],
                "source_sha256": hashlib.sha256(Path("greet.js").read_bytes()).hexdigest(), "exit_code": code}) + "\n")
        return {**common, "summary": "Executed the real JavaScript product through its unchanged test adapter",
                "changed_files": ["greet.js"], "commands_run": [config["check"]], "results": [f"exit {code}"],
                "remaining_risks": [], "evidence_refs": ["event:check"],
                "addressed_requirements": data["current_task"]["acceptance_criteria"], "untested_behavior": [],
                "recommended_checks": [config["check"]]}
    result = helper["report_for"](stage, data, common,
        {**config, "fault": "completion_rework_recurring"}, run_check, requirements)
    if nonpython and result.get("next_task"):
        result["plan"] = [line.replace("greet.py", "greet.js") for line in result.get("plan", [])]
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    builds = sum(row["stage"] == "terra" for row in rows)
    if (config["fault"] != "recovery_novelty_hold" and builds >= 2 and result.get("status") == "REWORK"
            and stage in ("astra_review", "astra_resolve")):
        output = (data.get("validation") or {}).get("output")
        result["recovery_change"] = {
            "hypothesis": "The input guard checks argument count but never rejects a blank name",
            "target": "greet.py", "before": "if len(sys.argv) != 2:",
            "after": "if len(sys.argv) != 2 or not sys.argv[1].strip():",
            "expected_check": config["check"], "expected_result": "exit 0; empty and whitespace names exit 2",
            "evidence_refs": [output] if output else [], "question": ""}
        if config["fault"] == "recovery_novelty_bad_refs":
            result["recovery_change"]["evidence_refs"] = ["/not-owned/claimed-proof.json"]
        if config["fault"] in ("recovery_novelty_question", "recovery_novelty_narrow"):
            result["recovery_change"]["question"] = "Does the failure come from the argument guard rather than the greeting output branch?"
        if config["fault"] == "recovery_novelty_narrow" and stage == "astra_resolve":
            # #423: repair only the failed blank-name criterion, not the whole failed task.
            result["next_task"]["acceptance_criteria"] = ["C2"]
        if nonpython:
            result["recovery_change"].update(target="greet.js", before="if (args.length !== 1)",
                                            after="if (args.length !== 1 || !args[0].trim())")
    return result
