#!/usr/bin/env python3
"""Offline OpenCode event protocol fixture; never calls an actual provider.

AUTOCODE_FIXTURE_QUALIFIED_OPENCODE=1 (tests only, #709) lets the runner contain its launches
in the real kernel tool boundary: --version reports the conformance-tested 1.18.33, and the
model-free `debug agent` checks the runner makes before a contained launch are answered, each
`--tool bash` command running through the boundary shell the runner configured. Only those
commands and a Builder write planted with AUTOCODE_FIXTURE_SHELL_ESCAPE go through that shell:
the scripted Codex delegate's edits and checks below still run as ordinary processes, so this
is no proof of OpenCode's own tool dispatch. Without these variables nothing here changes.
"""

import fnmatch
import json
import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

QUALIFIED = os.environ.get("AUTOCODE_FIXTURE_QUALIFIED_OPENCODE") == "1"
if sys.argv[1:] == ["--version"]:
    print("1.18.33" if QUALIFIED else "1.18.31")
    raise SystemExit(0)
if sys.argv[1:] == ["models"]:
    print(
        "mimo-token-plan/mimo-v2.6-pro\nopencode/mimo-v2.6-flash-free\n"
        "xiaomi-token-plan-sgp/mimo-v2.6-pro\nzai-coding-plan/glm-5.3\n"
        "openai/gpt-6-astra\nopenai/gpt-6-sol\nopenai/gpt-6-luna\nopenai/gpt-5.6-terra\nopenai/gpt-5.6-sol"
    )
    raise SystemExit(0)
if sys.argv[1:] == ["auth", "list"]:
    print("● OpenAI " + os.environ.get("AUTOCODE_FIXTURE_OPENAI_AUTH", "oauth"))
    print("● Z.AI Coding Plan api")
    raise SystemExit(0)
if QUALIFIED and sys.argv[1:3] == ["debug", "agent"]:
    # The agent OpenCode would run: its default allow first, then this agent's own permission
    # entries, the last matching rule winning (tool_containment.shell_permissions reads them).
    agent = sys.argv[3]
    config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
    rules = [{"permission": "*", "pattern": "*", "action": "allow"}]
    for name, value in config["agent"][agent].get("permission", {}).items():
        patterns = value.items() if isinstance(value, dict) else [("*", value)]
        rules.extend({"permission": name, "pattern": pattern, "action": action} for pattern, action in patterns)
    if "--tool" not in sys.argv:

        def enabled(tool):
            matching = [
                rule for rule in rules if rule["pattern"] == "*" and fnmatch.fnmatchcase(tool, rule["permission"])
            ]
            return matching[-1]["action"] != "deny"

        tools = ("bash", "read", "edit", "write", "glob", "grep", "list", "webfetch", "task", "todowrite")
        print(json.dumps({"name": agent, "permission": rules, "tools": {tool: enabled(tool) for tool in tools}}))
        raise SystemExit(0)
    assert sys.argv[sys.argv.index("--tool") + 1] == "bash"
    params = json.loads(sys.argv[sys.argv.index("--params") + 1])
    ran = subprocess.run(
        [config.get("shell") or os.environ["SHELL"], "-c", params["command"]],
        cwd=params["workdir"],
        capture_output=True,
        text=True,
        timeout=params["timeout"] / 1000,
    )
    reply = {"output": ran.stdout + ran.stderr, "metadata": {"exit": ran.returncode}}
    print(json.dumps({"input": {"command": params["command"]}, "result": reply}))
    raise SystemExit(0)

assert sys.argv[1] == "run"
assert sys.argv[sys.argv.index("--format") + 1] == "json"
assert "--auto" not in sys.argv and "--continue" not in sys.argv
agent = sys.argv[sys.argv.index("--agent") + 1]
config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
assert config["share"] == "disabled"
permissions = config["agent"][agent]["permission"]
if QUALIFIED:
    # A contained launch's shape (tool_containment.shell_permissions): "*" denies every tool but bash.
    permissions = {**dict.fromkeys(("task", "question", "edit"), permissions.get("*")), **permissions}
assert permissions["task"] == "deny" and permissions["question"] == "deny"
if agent != "autocode_terra":
    assert permissions["edit"] == "deny"
prompt = sys.stdin.read()
output_schema = None
if os.environ.get("AUTOCODE_FIXTURE_REPORT_LOSS") and prompt.startswith(
    "Your previous final message could not be parsed as the report:"
):
    # Same-session serialization correction deliberately has no full handoff.
    data: dict[str, Any] = {
        "execution_engine": "opencode",
        "report_repair": True,
        "stage": "sol_report_repair",
        "original": {"stage": "sol"},
    }
    prompt += "\nCURRENT HANDOFF DATA\n" + json.dumps(data)
else:
    assert "OPENCODE OUTPUT CONTRACT" in prompt
    prefix, handoff = prompt.split("\nCURRENT HANDOFF DATA\n", 1)
    data = json.loads(handoff)
    # OpenCode receives its schema in the prompt, while the scripted Codex
    # delegate reads --output-schema. Relay the actual current contract, including
    # context-free report repairs, rather than deriving fields from the handoff.
    try:
        contract = prefix.rsplit("\nOPENCODE OUTPUT CONTRACT\n", 1)[1]
        if QUALIFIED:
            # A contained launch follows the contract with its boundary (provider_launch.containment_prompt).
            contract = contract.split("\nNATIVE TOOL BOUNDARY:", 1)[0]
        start = contract.index("\n{") + 1
        output_schema, end = json.JSONDecoder().raw_decode(contract[start:])
        if not isinstance(output_schema, dict) or contract[start + end :].strip():
            raise ValueError("Invalid schema contract")
    except (IndexError, ValueError):
        raise SystemExit("fake_opencode: invalid current output schema") from None
assert data["execution_engine"] == "opencode"
session = sys.argv[sys.argv.index("--session") + 1] if "--session" in sys.argv else "ses_" + uuid.uuid4().hex
if os.environ.get("AUTOCODE_FIXTURE_SESSION_DRIFT"):
    session = "ses_" + uuid.uuid4().hex
message = "msg_" + uuid.uuid4().hex


def emit(kind, part):
    print(
        json.dumps({"type": kind, "sessionID": session, "part": {"sessionID": session, "messageID": message, **part}}),
        flush=True,
    )


emit("step_start", {"id": "prt_start", "type": "step-start"})
planted = os.environ.get("AUTOCODE_FIXTURE_SHELL_ESCAPE")
if planted and agent == "autocode_terra":
    # A Builder shell write outside the workspace, through the shell this launch was given.
    planted_command = ": > " + shlex.quote(planted)
    shell = config.get("shell") or os.environ.get("SHELL") or "/bin/sh"
    ran = subprocess.run([shell, "-c", planted_command], capture_output=True, text=True, timeout=30)
    emit(
        "tool_use",
        {
            "id": "prt_planted_escape",
            "type": "tool",
            "tool": "bash",
            "callID": "call_planted_escape",
            "state": {
                "status": "completed",
                "input": {"command": planted_command},
                "metadata": {"exit": ran.returncode},
                "output": ran.stdout + ran.stderr,
            },
        },
    )
# The provider's content filter refuses this stage on the named model: the model's own
# words, a final content-filter finish and a clean exit, with no error event (#464).
refused_stage = os.environ.get("AUTOCODE_FIXTURE_CONTENT_FILTER_STAGE")
refused = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None
if (
    refused_stage
    and refused_stage == data.get("stage")
    and os.environ.get("AUTOCODE_FIXTURE_CONTENT_FILTER_MODEL") in (None, refused)
):
    emit(
        "text",
        {
            "id": "prt_text",
            "type": "text",
            "time": {"end": 1},
            "text": "The request was rejected because it was considered high risk",
        },
    )
    emit(
        "step_finish",
        {
            "id": "prt_finish",
            "type": "step-finish",
            "reason": "content-filter",
            "cost": 0,
            "tokens": {"input": 100, "output": 1, "reasoning": 40, "cache": {"read": 0, "write": 0}},
        },
    )
    raise SystemExit(0)
fixture_root = None
if os.environ.get("AUTOCODE_REPLAY_EMITTER_COMMAND"):
    try:
        literal = os.environ["AUTOCODE_REPLAY_FIXTURE_ROOT"]
        fixture_root = Path(literal)
        if (
            not fixture_root.is_absolute()
            or str(fixture_root) != literal
            or fixture_root == Path(fixture_root.anchor)
            or fixture_root.resolve(strict=True) != fixture_root
            or not fixture_root.is_dir()
            or any(parent.is_symlink() for parent in (fixture_root, *fixture_root.parents))
        ):
            raise ValueError("Fixture relay root is not canonical")
    except (KeyError, OSError, RuntimeError, ValueError):
        raise SystemExit("fake_opencode: unavailable owned fixture relay root") from None
with tempfile.TemporaryDirectory(dir=fixture_root) as temp:
    report = Path(temp) / "report.json"
    if fixture_root is not None:
        report = Path(temp) / ".autocode/runs/relay/iterations/001/report-01.json"
        report.parent.mkdir(parents=True)
    command = [sys.executable, str(Path(__file__).with_name("codex")), "-o", str(report)]
    if output_schema is not None:
        schema = Path(temp) / "schema.json"
        schema.write_text(json.dumps(output_schema))
        command.extend(["--output-schema", str(schema)])
    result = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        for line in result.stdout.splitlines():
            event = json.loads(line)
            if event.get("type") == "error":
                print(json.dumps({"type": "error", "sessionID": session, "error": event["error"]}), flush=True)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    relayed = []
    for line in result.stdout.splitlines():
        event = json.loads(line)
        if event.get("type") != "item.completed":
            continue
        item = event["item"]
        relayed.append(item["id"])
        emit(
            "tool_use",
            {
                "id": "prt_" + item["id"],
                "type": "tool",
                "tool": "bash",
                "callID": "call_fixture",
                "state": {
                    "status": "completed",
                    "input": {"command": item["command"]},
                    "metadata": {"exit": item["exit_code"]},
                    "output": item["aggregated_output"],
                },
            },
        )
    final = report.read_text().replace('"event:check"', '"event:prt_check"')
    # The delegate cites its commands by Codex item id; this client relayed each as part prt_<id>.
    for item_id in relayed:
        final = final.replace(json.dumps("event:" + item_id), json.dumps("event:prt_" + item_id))
    if os.environ.get("AUTOCODE_FIXTURE_INVALID_INVESTIGATOR") and data.get("stage") == "investigate_stuck":
        value = json.loads(final)
        value.pop("diagnosis")
        final = json.dumps(value)
    loss_cache = os.environ.get("AUTOCODE_FIXTURE_REPORT_LOSS")
    owner = (data.get("original") or {}).get("stage", data.get("stage"))
    if loss_cache and owner == "sol":
        # Preserve the synthetic complete report for report-only replies while
        # dropping delivery from both the original and correction/repair calls.
        Path(loss_cache).write_text(final)
        final = final[:1]
    if os.environ.get("AUTOCODE_FIXTURE_TRUNCATE_STAGE") and os.environ["AUTOCODE_FIXTURE_TRUNCATE_STAGE"] == data.get(
        "stage"
    ):
        # Like OpenCode 1.x: use the output cap this process actually received.
        cap_text = os.environ.get("OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX", "")
        cap = int(cap_text) if cap_text.isdigit() and int(cap_text) > 0 else 32000
        emit("text", {"id": "prt_text", "type": "text", "text": final[:-1], "time": {"end": 1}})
        emit(
            "step_finish",
            {
                "id": "prt_finish",
                "type": "step-finish",
                "reason": "length",
                "cost": 0,
                "tokens": {
                    "input": 100,
                    "output": cap - cap // 2,
                    "reasoning": cap // 2,
                    "cache": {"read": 0, "write": 0},
                },
            },
        )
        raise SystemExit(0)
    emit("text", {"id": "prt_text", "type": "text", "text": final, "time": {"end": 1}})
    emit(
        "step_finish",
        {
            "id": "prt_finish",
            "type": "step-finish",
            "reason": "stop",
            "cost": 0,
            "tokens": {"input": 100, "output": 50, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        },
    )
