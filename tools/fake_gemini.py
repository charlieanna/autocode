#!/usr/bin/env python3
"""Offline Gemini CLI for end-to-end tests of the built-in gemini provider; never contacts a model.

A test copies this file to ``fixture-bin/gemini`` beside ``codex`` (``fake_codex.py``) and
``goal_fixtures.py``. A headless stage call (``--output-format stream-json``) runs the scripted Codex
fixture for the stage and answers in Gemini CLI 0.71's stream-json shapes: an ``init`` row, the user's
prompt, one ``run_shell_command`` ``tool_use``/``tool_result`` pair per command the fixture ran (with no
exit code, as Gemini CLI reports none), the report as the assistant's last message, and a ``result``
row with the call's token counts. Because the stream carries no exit codes, it keeps each check's
evidence the way the output contract asks a model to: it runs the check again through the handoff's
``capture_command`` and cites the receipt.

``gemini --version`` and ``gemini models list --output-format json`` answer as Gemini CLI does; the
sign-in is read from ``$GEMINI_CLI_HOME/.gemini`` by AutoCode itself, so a test writes its settings
there. Every invocation appends ``{"argv", "cwd", "env_names"}`` (names, never values; a stage call
adds the first characters of its prompt as ``prompt_head``) to ``$AUTOCODE_FIXTURE_GEMINI_LOG``.

Knobs:
  AUTOCODE_FIXTURE_GEMINI_VERSION   the ``--version`` text
  AUTOCODE_FIXTURE_GEMINI_REFRESH   ``workspace`` or ``expired``: what Gemini CLI's debug log says while
                                    listing models (a Workspace account without GOOGLE_CLOUD_PROJECT, an
                                    expired sign-in)
  AUTOCODE_FIXTURE_GEMINI_FENCE     the stage whose report comes in a ```json fence
  AUTOCODE_FIXTURE_GEMINI_MODELS    further model ids the account lists, comma-separated (a subscription's
                                    newer Pro)
"""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ARGS = sys.argv[1:]
# AUTOCODE_FIXTURE_GEMINI_MODELS adds models the account lists, comma-separated (a newer Pro, say).
LISTED = tuple(name for name in os.environ.get("AUTOCODE_FIXTURE_GEMINI_MODELS", "").split(",") if name)
MODELS: tuple[str, ...] = ("auto", "gemini-2.5-pro", "gemini-3.5-flash", "gemini-3.1-flash-lite", *LISTED)
REFRESH = {
    "workspace": (
        "Failed to refresh auth for models list, falling back to default catalog: ProjectIdRequiredError: "
        "This account requires setting the GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_PROJECT_ID env var."
    ),
    "expired": "Cached credentials are not valid: invalid_grant",
}


def record(**extra):
    if os.environ.get("AUTOCODE_FIXTURE_GEMINI_LOG"):
        entry = {"argv": ARGS, "cwd": os.getcwd(), "env_names": sorted(os.environ), **extra}
        with open(os.environ["AUTOCODE_FIXTURE_GEMINI_LOG"], "a") as log:
            log.write(json.dumps(entry) + "\n")


def emit(row):
    print(json.dumps(row), flush=True)


if "stream-json" not in ARGS:
    record()
    if ARGS == ["--version"]:
        print(os.environ.get("AUTOCODE_FIXTURE_GEMINI_VERSION", "0.71.1"))
        raise SystemExit(0)
    if ARGS[:2] == ["models", "list"]:
        said = REFRESH.get(os.environ.get("AUTOCODE_FIXTURE_GEMINI_REFRESH", ""))
        if said and os.environ.get("GEMINI_DEBUG_LOG_FILE"):
            with open(os.environ["GEMINI_DEBUG_LOG_FILE"], "a") as log:
                log.write(f"[2026-11-25T00:00:00.000Z] [DEBUG] {said}\n")
        print(json.dumps([{"modelId": model, "id": model, "name": model} for model in MODELS], indent=2))
        raise SystemExit(0)
    sys.stderr.write(f"fake gemini: unexpected arguments {ARGS}\n")
    raise SystemExit(2)

model = ARGS[ARGS.index("--model") + 1]
prompt = sys.stdin.read()
record(prompt_head=prompt[:120])
assert "GEMINI CLI OUTPUT CONTRACT" in prompt, "the stage prompt carries the provider's output contract"
instructions, _, packet = prompt.partition("\nCURRENT HANDOFF DATA\n")
data = json.loads(packet)
stage = data.get("stage") or ""
session = str(uuid.uuid4())
emit({"type": "init", "session_id": session, "model": model})
emit({"type": "message", "role": "user", "content": prompt[:200]})

with tempfile.TemporaryDirectory() as temp:
    schema_text = instructions.rsplit("REPORT SCHEMA (your last message must be one JSON object matching it):\n", 1)[1]
    schema, report = Path(temp) / "schema.json", Path(temp) / "report.json"
    schema.write_text(schema_text.strip())
    command = [sys.executable, str(Path(__file__).with_name("codex")), "exec", "--output-schema", str(schema)]
    command += ["-o", str(report), "--model", model, "-"]
    codex = subprocess.run(command, input=prompt, text=True, capture_output=True)
    rows = [json.loads(line) for line in codex.stdout.splitlines() if line.strip().startswith("{")]
    if codex.returncode:
        said = next(
            (
                str((row.get("error") or {}).get("message") or row.get("message"))
                for row in rows
                if row.get("type") == "error"
            ),
            codex.stderr.strip() or "fixture failure",
        )
        emit({"type": "result", "status": "error", "error": {"type": "Error", "message": said}, "stats": {}})
        raise SystemExit(1)
    report_text = report.read_text()

commands = [
    row["item"]
    for row in rows
    if row.get("type") == "item.completed" and row["item"].get("type") == "command_execution"
]
receipts = {}
capture = data.get("capture_command")
for index, item in enumerate(commands):
    tool_id = f"run_shell_command__{index}"
    emit(
        {
            "type": "tool_use",
            "tool_name": "run_shell_command",
            "tool_id": tool_id,
            "parameters": {"command": item["command"]},
        }
    )
    output = item.get("aggregated_output", "")
    status = "success"
    emit({"type": "tool_result", "tool_id": tool_id, "status": status, "output": output})
    if capture and type(item.get("exit_code")) is int:
        # The evidence Gemini CLI's stream cannot carry: the check again, through the capture command.
        receipt = f".autocode/evidence/gemini-{stage or 'stage'}-{index}.json"
        line = f"{capture} --output {shlex.quote(receipt)} -- {item['command']}"
        captured = subprocess.run(line, shell=True, text=True, capture_output=True)
        emit(
            {
                "type": "tool_use",
                "tool_name": "run_shell_command",
                "tool_id": f"{tool_id}c",
                "parameters": {"command": line},
            }
        )
        emit({"type": "tool_result", "tool_id": f"{tool_id}c", "status": "success", "output": captured.stdout[-2000:]})
        if captured.returncode == item["exit_code"] and Path(receipt).is_file():
            receipts[item["command"]] = receipt


def cite(value, last):
    """The report with each command event reference replaced by that command's capture receipt."""
    if isinstance(value, list):
        return [cite(entry, last) for entry in value]
    if isinstance(value, dict):
        cited = {key: cite(entry, last) for key, entry in value.items()}
        if isinstance(value.get("command"), str) and value["command"] in receipts and "evidence_ref" in value:
            cited["evidence_ref"] = receipts[value["command"]]
        return cited
    if isinstance(value, str) and value.startswith("event:") and last:
        return last
    return value


last = list(receipts.values())[-1] if receipts else None
report_value = cite(json.loads(report_text), last)
text = json.dumps(report_value)
if os.environ.get("AUTOCODE_FIXTURE_GEMINI_FENCE") == stage:
    text = "```json\n" + json.dumps(report_value, indent=2) + "\n```"
emit({"type": "message", "role": "assistant", "content": text[: len(text) // 2], "delta": True})
emit({"type": "message", "role": "assistant", "content": text[len(text) // 2 :], "delta": True})
stats = {
    "total_tokens": 130,
    "input_tokens": 100,
    "output_tokens": 30,
    "cached": 40,
    "input": 60,
    "tool_calls": len(commands),
}
emit({"type": "result", "status": "success", "stats": {**stats, "duration_ms": 5, "models": {model: stats}}})
