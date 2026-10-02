"""Inject startup failures before the scripted provider starts its model turn.

The provider boundary alone is changed. Never edits runner state; any retained
source change belongs to the negative-control provider, not the harness driver.
"""
import io
import json
import os
from pathlib import Path
import sys


def before_launch():
    # Login/model/version probes are not provider requests.
    if not any(a in sys.argv[1:] for a in ("exec", "run", "-o")):
        return
    config = json.loads(Path(os.environ["SCENARIO_FAKE_CONFIG"]).read_text())
    fault = config["startup_fault"]
    root = Path(fault["root"])
    prompt = sys.stdin.read()
    sys.stdin = io.StringIO(prompt)
    data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
    stage = data.get("stage") or (data.get("original") or {}).get("stage")
    if stage not in fault.get("stages", ["recognize_workflow"]):
        return
    count_path = root / "startup-count.txt"
    count = int(count_path.read_text()) + 1 if count_path.exists() else 1
    count_path.write_text(str(count))
    per_stage_path = root / "startup-stages.json"
    counts = json.loads(per_stage_path.read_text()) if per_stage_path.exists() else {}
    counts[stage] = counts.get(stage, 0) + 1
    per_stage_path.write_text(json.dumps(counts))
    fail = (counts[stage] == 1 if fault["case"] == "per_stage" else fault["case"] != "once" or count == 1)
    with (root / "startup-trace.jsonl").open("a") as out:
        out.write(json.dumps({"stage": stage, "attempt": count, "injected": fail}) + "\n")
    if not fail:
        return
    case = fault["case"]
    if case == "session":
        print(json.dumps({"type": "thread.started", "thread_id": "started-session"}), flush=True)
    if case == "source":
        Path("README.md").write_text("Partial provider work must remain retained.\n")
    if case == "report":
        Path(sys.argv[sys.argv.index("-o") + 1]).write_text('{"partial":')
    if case == "quota":
        print(json.dumps({"type": "turn.failed", "error": {"message": "usage limit quota"}}), flush=True)
    else:
        print("\x1b[91mError: \x1b[0mUnexpected error\n\ndatabase is locked", flush=True)
    raise SystemExit(1)
