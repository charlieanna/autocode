#!/usr/bin/env python3
"""Offline Codex for the bundled codex provider config (``tools/providers/configs/codex.toml``); never contacts a model.

A test copies this file to ``fixture-bin/codex`` and ``fake_codex.py`` to ``fixture-bin/codex-scripted``. A stage
call through the config (``codex exec --ephemeral ...``) runs the scripted fixture for the stage, as Codex would
answer it, then keeps each check's evidence the way the config's output contract asks a model to: it runs the
check again through the handoff's ``capture_command`` and cites the receipt in place of the Codex event
reference, because a report-file provider's commands are evidence only through capture receipts. Every other
call (``login status`` and the scripted fixture runs ``fake_claude.py`` makes) goes to the
scripted fixture unchanged; ``--version`` answers as Codex 0.161 does.
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ARGS = sys.argv[1:]
SCRIPTED = str(Path(__file__).with_name("codex-scripted"))

if ARGS == ["--version"]:
    # The config's version check: the codex_artifacts sandbox needs Codex 0.160.0 or newer.
    print("codex-cli 0.161.0")
    raise SystemExit(0)
if "--ephemeral" not in ARGS:
    os.execv(sys.executable, [sys.executable, SCRIPTED, *ARGS])

prompt = sys.stdin.read()
data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
stage = data.get("stage") or "stage"
report = Path(ARGS[ARGS.index("-o") + 1])
codex = subprocess.run([sys.executable, SCRIPTED, *ARGS], input=prompt, text=True, capture_output=True)
sys.stdout.write(codex.stdout)
sys.stderr.write(codex.stderr)
if codex.returncode or not report.is_file():
    raise SystemExit(codex.returncode or 1)

rows = [json.loads(line) for line in codex.stdout.splitlines() if line.strip().startswith("{")]
commands = [
    row["item"]
    for row in rows
    if row.get("type") == "item.completed" and row["item"].get("type") == "command_execution"
]
receipts = {}
capture = data.get("capture_command")
for index, item in enumerate(commands):
    if capture and type(item.get("exit_code")) is int:
        receipt = f".autocode/evidence/codex-{stage}-{index}.json"
        line = f"{capture} --output {shlex.quote(receipt)} -- {item['command']}"
        captured = subprocess.run(line, shell=True, text=True, capture_output=True)
        if captured.returncode == item["exit_code"] and Path(receipt).is_file():
            receipts[item["command"]] = receipt
last = list(receipts.values())[-1] if receipts else None


def cite(value):
    """The report with each command event reference replaced by that command's capture receipt."""
    if isinstance(value, list):
        return [cite(entry) for entry in value]
    if isinstance(value, dict):
        cited = {key: cite(entry) for key, entry in value.items()}
        if isinstance(value.get("command"), str) and value["command"] in receipts and "evidence_ref" in value:
            cited["evidence_ref"] = receipts[value["command"]]
        return cited
    if isinstance(value, str) and value.startswith("event:") and last:
        return last
    return value


report.write_text(json.dumps(cite(json.loads(report.read_text()))))
