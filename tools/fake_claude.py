#!/usr/bin/env python3
"""Offline Claude Code CLI for end-to-end tests of the built-in claude provider (#717); never contacts a model.

A test copies this file to ``fixture-bin/claude`` beside ``codex`` (``fake_codex.py``) and
``goal_fixtures.py``. ``claude -p`` runs the scripted Codex fixture for the stage and answers in
Claude Code's stream-json shapes: an init row, one Bash ``tool_use``/``tool_result`` pair per command
the fixture ran, the report through StructuredOutput, and a ``result`` row whose ``total_cost_usd`` is
cumulative per session, as Claude Code's is.

Every invocation appends ``{"argv", "cwd", "env_names"}`` (names, never values; a ``-p`` call adds the
first characters of its prompt as ``prompt_head``) to ``$AUTOCODE_FIXTURE_CLAUDE_LOG`` when it is set. Sessions live in ``$AUTOCODE_FIXTURE_CLAUDE_HOME``.

Knobs (a stage is the handoff's ``stage``):
  AUTOCODE_FIXTURE_CLAUDE_VERSION      the ``--version`` text
  AUTOCODE_FIXTURE_CLAUDE_AUTH         claude.ai (default), logged_out, api_key or bedrock
  AUTOCODE_FIXTURE_CLAUDE_START_ERROR  the stage whose call fails before starting a session
  AUTOCODE_FIXTURE_TRUNCATE_STAGE      the stage cut at Claude's output token limit (synthesized
                                       from the live 2.1.299 length-stop shape)
  AUTOCODE_FIXTURE_CLAUDE_NO_REPORT_STAGE  the stage whose first call ends without a report
  AUTOCODE_FIXTURE_CLAUDE_CUT_STAGE    the stage whose stream is cut mid-row by a kill
"""

import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ARGS = sys.argv[1:]
LIMIT_TEXT = (
    "API Error: Claude's response exceeded the 32000 output token maximum. To configure this behavior, "
    "set the CLAUDE_CODE_MAX_OUTPUT_TOKENS environment variable."
)
CORRECTION = "Your previous final message could not be parsed"
USAGE = {
    "input_tokens": 70,
    "cache_creation_input_tokens": 10,
    "cache_read_input_tokens": 20,
    "output_tokens": 30,
    "output_tokens_details": {"thinking_tokens": 5},
}


def record(**extra):
    if os.environ.get("AUTOCODE_FIXTURE_CLAUDE_LOG"):
        entry = {"argv": ARGS, "cwd": os.getcwd(), "env_names": sorted(os.environ), **extra}
        with open(os.environ["AUTOCODE_FIXTURE_CLAUDE_LOG"], "a") as log:
            log.write(json.dumps(entry) + "\n")


if ARGS[:1] != ["-p"]:
    record()
if ARGS == ["--version"]:
    print(os.environ.get("AUTOCODE_FIXTURE_CLAUDE_VERSION", "2.1.299 (Claude Code)"))
    raise SystemExit(0)
if ARGS == ["auth", "status", "--json"]:
    mode = os.environ.get("AUTOCODE_FIXTURE_CLAUDE_AUTH", "claude.ai")
    answers = {
        "claude.ai": {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "email": "fixture@example.test",
        },
        "logged_out": {"loggedIn": False},
        "api_key": {
            "loggedIn": True,
            "authMethod": "api_key",
            "apiKeySource": "ANTHROPIC_API_KEY",
            "apiProvider": "firstParty",
        },
        "bedrock": {"loggedIn": True, "authMethod": "third_party", "apiProvider": "bedrock"},
    }
    print(json.dumps(answers[mode], indent=2))
    raise SystemExit(1 if mode == "logged_out" else 0)

assert ARGS[:2] == ["-p", "--json-schema"], ARGS[:3]
home = Path(os.environ.get("AUTOCODE_FIXTURE_CLAUDE_HOME") or Path(__file__).with_name(".fake-claude"))
home.mkdir(parents=True, exist_ok=True)
model = ARGS[ARGS.index("--model") + 1]
resumed = ARGS[ARGS.index("--resume") + 1] if "--resume" in ARGS else None
prompt = sys.stdin.read()
record(prompt_head=prompt[:120])


def emit(row):
    print(json.dumps(row), flush=True)


def session_state(session):
    path = home / f"{session}.json"
    return json.loads(path.read_text()) if path.is_file() else {"calls": 0, "report": None}


def save(session, value):
    (home / f"{session}.json").write_text(json.dumps(value))


def assistant(session, content, **extra):
    emit({"type": "assistant", "session_id": session, "message": {"id": "msg_fixture", "content": content}, **extra})


def tool_result(session, tool_id, content, meta, is_error):
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": is_error}
    emit(
        {
            "type": "user",
            "session_id": session,
            "message": {"role": "user", "content": [block]},
            "tool_use_result": meta,
        }
    )


def deliver(session, report_text, calls):
    """The report through StructuredOutput, then the call's result: Claude Code's success shape."""
    report = json.loads(report_text)
    assistant(session, [{"type": "tool_use", "id": "toolu_report", "name": "StructuredOutput", "input": report}])
    tool_result(
        session,
        "toolu_report",
        "Structured output provided successfully",
        "Structured output provided successfully",
        None,
    )
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": session,
            "structured_output": report,
            "result": report_text,
            "usage": USAGE,
            "total_cost_usd": round(0.01 * calls, 6),
        }
    )


if resumed is not None and not (home / f"{resumed}.json").is_file():
    sys.stderr.write(f"No conversation found with session ID: {resumed}\n")
    raise SystemExit(1)

if prompt.startswith(CORRECTION):
    # The same-session format correction: Claude re-emits the report it already wrote.
    assert resumed, "a format correction resumes its stage's session"
    saved = session_state(resumed)
    saved["calls"] += 1
    save(resumed, saved)
    emit({"type": "system", "subtype": "init", "session_id": resumed, "model": model})
    deliver(resumed, saved["report"], saved["calls"])
    raise SystemExit(0)

assert "CLAUDE CODE OUTPUT CONTRACT" in prompt, "the stage prompt carries the provider's output contract"
data = json.loads(prompt.split("\nCURRENT HANDOFF DATA\n", 1)[1])
stage = data.get("stage") or ""
if os.environ.get("AUTOCODE_FIXTURE_CLAUDE_START_ERROR") == stage:
    sys.stderr.write("Error: --json-schema must be a JSON object\n")
    raise SystemExit(1)

with tempfile.TemporaryDirectory() as temp:
    schema, report = Path(temp) / "schema.json", Path(temp) / "report.json"
    schema.write_text(ARGS[2])
    command = [sys.executable, str(Path(__file__).with_name("codex")), "exec", "--output-schema", str(schema)]
    command += ["-o", str(report), "--model", model, *(["resume", resumed] if resumed else []), "-"]
    codex = subprocess.run(command, input=prompt, text=True, capture_output=True)
    rows = [json.loads(line) for line in codex.stdout.splitlines() if line.strip().startswith("{")]
    session = next((row["thread_id"] for row in rows if row.get("type") == "thread.started"), resumed or "fixture")
    if codex.returncode:
        said = next(
            (
                str((row.get("error") or {}).get("message") or row.get("message"))
                for row in rows
                if row.get("type") == "error"
            ),
            codex.stderr.strip() or "fixture failure",
        )
        emit({"type": "system", "subtype": "init", "session_id": session, "model": model})
        assistant(session, [{"type": "text", "text": said}], error="unknown", is_api_error_message=True)
        emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "session_id": session,
                "api_error_status": 429 if "rate" in said.lower() else None,
                "result": said,
            }
        )
        raise SystemExit(1)
    report_text = report.read_text()

saved = session_state(session)
saved["calls"] += 1
emit({"type": "system", "subtype": "init", "session_id": session, "model": model})
commands = [
    row["item"]
    for row in rows
    if row.get("type") == "item.completed" and row["item"].get("type") == "command_execution"
]
for index, item in enumerate(commands):
    tool_id = f"toolu_{item['id']}"
    assistant(session, [{"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": item["command"]}}])
    output, code = item.get("aggregated_output", ""), item["exit_code"]
    if code == 0:
        tool_result(session, tool_id, output, {"stdout": output, "stderr": "", "interrupted": False}, False)
    else:
        text = f"Exit code {code}\n{output}"
        tool_result(session, tool_id, text, "Error: " + text, True)
    # The translator numbers these Bash calls item_0, item_1, ... (nothing is emitted before them).
    report_text = report_text.replace(f'"event:{item["id"]}"', f'"event:item_{index}"')
with contextlib.suppress(ValueError):  # compact, so a truncated copy is the report without its closing brace
    report_text = json.dumps(json.loads(report_text), separators=(",", ":"))
saved["report"] = report_text
save(session, saved)

if os.environ.get("AUTOCODE_FIXTURE_CLAUDE_CUT_STAGE") == stage:
    sys.stdout.write('{"type": "assistant", "message": {"content": [')
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGKILL)
if os.environ.get("AUTOCODE_FIXTURE_TRUNCATE_STAGE") == stage:
    # Synthesized from the live 2.1.299 length stop (tools/fixtures/claude-2.1.299-length-stop.jsonl):
    # the report was being written when Claude Code ran out of output tokens.
    emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "msg_cap", "usage": USAGE}}})
    block = {"type": "tool_use", "id": "toolu_report", "name": "StructuredOutput", "input": {}}
    emit({"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": block}})
    delta = {"type": "input_json_delta", "partial_json": report_text[:-1]}
    emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": delta}})
    for _ in range(3):
        notice = {"type": "text", "text": "Output token limit hit. Resume directly."}
        emit(
            {
                "type": "user",
                "session_id": session,
                "message": {"role": "user", "content": [notice]},
                "isSynthetic": True,
            }
        )
    assistant(session, [{"type": "text", "text": LIMIT_TEXT}], error="max_output_tokens", is_api_error_message=True)
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "session_id": session,
            "api_error": "max_output_tokens",
            "result": LIMIT_TEXT,
            "usage": USAGE,
            "total_cost_usd": round(0.01 * saved["calls"], 6),
        }
    )
    raise SystemExit(1)
marker = home / f"no-report-{stage}"
if os.environ.get("AUTOCODE_FIXTURE_CLAUDE_NO_REPORT_STAGE") == stage and not marker.exists():
    marker.write_text(session)
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": session,
            "result": "I am done.",
            "usage": USAGE,
            "total_cost_usd": round(0.01 * saved["calls"], 6),
        }
    )
    raise SystemExit(0)
deliver(session, report_text, saved["calls"])
