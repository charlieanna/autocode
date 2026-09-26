#!/usr/bin/env python3
"""Offline OpenCode event protocol fixture; never calls an actual provider."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

if sys.argv[1:] == ["--version"]:
    print("1.18.31")
    raise SystemExit(0)
if sys.argv[1:] == ["models"]:
    print("xiaomi-token-plan-sgp/mimo-v2.6-pro\nzai-coding-plan/glm-5.3\n"
          "openai/gpt-6-astra\nopenai/gpt-5.6-terra\nopenai/gpt-5.6-sol")
    raise SystemExit(0)
if sys.argv[1:] == ["auth", "list"]:
    print("● OpenAI " + os.environ.get("AUTOCODE_FIXTURE_OPENAI_AUTH", "oauth"))
    print("● Z.AI Coding Plan api")
    raise SystemExit(0)

assert sys.argv[1] == "run"
assert sys.argv[sys.argv.index("--format") + 1] == "json"
assert "--auto" not in sys.argv and "--continue" not in sys.argv
agent = sys.argv[sys.argv.index("--agent") + 1]
config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
assert config["share"] == "disabled"
permissions = config["agent"][agent]["permission"]
assert permissions["task"] == "deny" and permissions["question"] == "deny"
if agent != "autocode_terra":
    assert permissions["edit"] == "deny"
prompt = sys.stdin.read()
assert "OPENCODE OUTPUT CONTRACT" in prompt
data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
assert data["execution_engine"] == "opencode"
session = sys.argv[sys.argv.index("--session") + 1] if "--session" in sys.argv else "ses_" + uuid.uuid4().hex
if os.environ.get("AUTOCODE_FIXTURE_SESSION_DRIFT"):
    session = "ses_" + uuid.uuid4().hex
message = "msg_" + uuid.uuid4().hex

def emit(kind, part):
    print(json.dumps({"type": kind, "sessionID": session, "part": {
        "sessionID": session, "messageID": message, **part}}), flush=True)

emit("step_start", {"id": "prt_start", "type": "step-start"})
with tempfile.TemporaryDirectory() as temp:
    report = Path(temp) / "report.json"
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("codex")), "-o", str(report)],
                            input=prompt, text=True, capture_output=True)
    if result.returncode:
        for line in result.stdout.splitlines():
            event = json.loads(line)
            if event.get("type") == "error":
                print(json.dumps({"type": "error", "sessionID": session, "error": event["error"]}), flush=True)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    for line in result.stdout.splitlines():
        event = json.loads(line)
        if event.get("type") != "item.completed":
            continue
        item = event["item"]
        emit("tool_use", {"id": "prt_" + item["id"], "type": "tool", "tool": "bash", "callID": "call_fixture",
                          "state": {"status": "completed", "input": {"command": item["command"]},
                                    "metadata": {"exit": item["exit_code"]}, "output": item["aggregated_output"]}})
    final = report.read_text().replace('"event:check"', '"event:prt_check"')
    emit("text", {"id": "prt_text", "type": "text", "text": final, "time": {"end": 1}})
    emit("step_finish", {"id": "prt_finish", "type": "step-finish", "reason": "stop", "cost": 0,
                         "tokens": {"input": 100, "output": 50, "reasoning": 0, "cache": {"read": 0, "write": 0}}})
