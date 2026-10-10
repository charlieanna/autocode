"""Inject startup failures before the scripted provider starts its model turn.

The provider boundary alone is changed. Never edits runner state; any retained
source change belongs to the negative-control provider, not the harness driver.
"""

import io
import json
import os
import sys
import threading
import time
from pathlib import Path


def await_registration(root):
    """Keep this fake launch alive until the supervisor records its birth identity.

    Real startup failures have time to be observed. An instant scripted exit can
    race process sampling, correctly forcing uncertain-execution handling instead
    of exercising startup recovery. Synchronize on the launch receipt, not a delay.
    """
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        for path in (root / "project/.autocode/runs").glob("*/active-processes.json"):
            try:
                receipt = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if any(
                row.get("pid") == os.getpid() and row.get("birth_identity") is not None
                for row in receipt.get("processes", [])
            ):
                return
        threading.Event().wait(0.01)
    raise RuntimeError("Startup fixture launch was not registered by the supervisor")


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
    fail = counts[stage] == 1 if fault["case"] == "per_stage" else fault["case"] != "once" or count == 1
    with (root / "startup-trace.jsonl").open("a") as out:
        out.write(json.dumps({"stage": stage, "attempt": count, "injected": fail}) + "\n")
    if not fail:
        return
    await_registration(root)
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
