"""Deterministic CLI fixture for the conformance command; never calls a model."""

from __future__ import annotations

import json
import os
import secrets
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

FAULTS = (
    "malformed_report",
    "forged_evidence",
    "wrong_exit",
    "unexpected_write",
    "incomplete_turn",
    "wrong_session",
    "missing_usage",
    "wrong_nonce",
    "stale_report",
    "process_failure",
)


def environment(directory, env, fault=None):
    binary = directory / "fake-bin"
    binary.mkdir()
    for executable, provider in (
        ("codex", "codex"),
        ("opencode", "opencode"),
        ("kilo", "kilocode"),
        ("claude", "claude"),
    ):
        path = binary / executable
        path.write_text(
            f"#!{sys.executable}\nimport sys\n"
            f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
            f"from provider_conformance_fake import main\nmain({provider!r})\n"
        )
        path.chmod(0o755)
    return {
        **env,
        "PATH": str(binary) + os.pathsep + env.get("PATH", os.defpath),
        "AUTOCODE_CONFORMANCE_FAULT": fault or "",
    }


def main(provider):
    args = sys.argv[1:]
    if provider == "claude":
        return _claude(args)
    opencode_2 = provider == "opencode" and os.environ.get("AUTOCODE_CONFORMANCE_OPENCODE_GENERATION") == "2"
    if args == ["--version"]:
        if opencode_2:
            print("opencode v2.0.20")
        else:
            print("1.18.31" if provider == "opencode" else "conformance-fixture-1")
        return
    if args == ["models"]:
        print("test/probe")
        return
    if args == ["login", "status"]:
        print("Logged in using ChatGPT")
        return
    if args == ["auth", "list"]:
        print("● OpenAI oauth")
        return
    prompt = sys.stdin.read()
    data = json.loads(prompt.rsplit("\nCURRENT HANDOFF DATA\n", 1)[1])
    workspace = Path(data["workspace"])
    if opencode_2:
        assert "--dir" not in args and "--variant" not in args and "--standalone" in args
        assert "#" in args[args.index("--model") + 1]
        assert Path.cwd() == workspace
    else:
        directory_flag = "-C" if provider == "codex" else "--dir"
        assert Path(args[args.index(directory_flag) + 1]) == workspace == Path.cwd()
    resume_flag = "resume" if provider == "codex" else "--session"
    session = args[args.index(resume_flag) + 1] if resume_flag in args else "session_" + secrets.token_hex(8)
    fault = os.environ.get("AUTOCODE_CONFORMANCE_FAULT", "")
    if fault == "wrong_session" and data["phase"] == "resume":
        session = "wrong_resumed_session"
    native = provider == "codex"

    def emit(value):
        print(json.dumps(value), flush=True)

    def part(kind, identity, **values):
        emit(
            {
                "type": kind,
                "sessionID": session,
                "part": {"id": identity, "sessionID": session, "messageID": "msg_probe", **values},
            }
        )

    if native:
        emit({"type": "thread.started", "thread_id": session})
    else:
        part("step_start", "start")
    if data["phase"] == "build":
        (workspace / "output.txt").write_bytes((workspace / "input.txt").read_bytes())
    if fault == "unexpected_write":
        (workspace / "unexpected.txt").write_text("unapproved edit")
    checks = []
    for index, command in enumerate(data["commands"]):
        actual = subprocess.run(shlex.split(command), cwd=workspace, capture_output=True, text=True)
        checks.append({"command": command, "exit_code": actual.returncode})
        if fault == "forged_evidence":
            continue
        code = 0 if fault == "wrong_exit" else actual.returncode
        if native:
            emit(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "id": f"cmd_{index}",
                        "command": command,
                        "exit_code": code,
                        "aggregated_output": actual.stdout + actual.stderr,
                    },
                }
            )
        elif opencode_2:
            # OpenCode 2 nests the shell exit under the tool metadata object.
            part(
                "tool_use",
                f"cmd_{index}",
                tool="shell",
                state={
                    "status": "completed",
                    "input": {"command": command},
                    "metadata": {"metadata": {"exit": code, "truncated": False}},
                    "output": actual.stdout + actual.stderr,
                },
            )
        else:
            part(
                "tool_use",
                f"cmd_{index}",
                tool="bash",
                state={
                    "status": "completed",
                    "input": {"command": command},
                    "metadata": {"exit": code},
                    "output": actual.stdout + actual.stderr,
                },
            )
    report = {
        "probe_id": data["probe_id"],
        "workspace": str(workspace),
        "nonce": (workspace / "input.txt").read_text().strip(),
        "checks": checks,
    }
    if fault == "wrong_nonce":
        report["nonce"] = "not-read"
    if fault == "stale_report":
        report["probe_id"] = "old-probe"
    final = "invalid JSON" if fault == "malformed_report" else json.dumps(report)
    if native:
        Path(args[args.index("-o") + 1]).write_text(final)
        terminal = {"type": "turn.failed" if fault == "incomplete_turn" else "turn.completed"}
        if fault != "missing_usage":
            terminal["usage"] = {
                "input_tokens": 100,
                "cached_input_tokens": 20,
                "output_tokens": 30,
                "reasoning_output_tokens": 5,
            }
        emit(terminal)
    else:
        part("text", "report", text=final)
        tokens = (
            {}
            if fault == "missing_usage"
            else {"input": 70, "output": 25, "reasoning": 5, "cache": {"read": 20, "write": 10}}
        )
        part("step_finish", "finish", reason="tool-calls" if fault == "incomplete_turn" else "stop", tokens=tokens)
    if fault == "process_failure":
        raise SystemExit(9)


def _claude(args):
    """Claude Code's print mode, as the built-in claude provider's translator reads it (#717)."""
    if args == ["--version"]:
        print("0.0.1 (Claude Code conformance fixture)")  # no fake can match a real matrix row
        return
    if args == ["auth", "status", "--json"]:
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}))
        return
    assert args[:2] == ["-p", "--json-schema"], args[:2]
    assert args[args.index("--output-format") + 1] == "stream-json" and "--dir" not in args
    prompt = sys.stdin.read()
    data = json.loads(prompt.rsplit("\nCURRENT HANDOFF DATA\n", 1)[1])
    workspace = Path(data["workspace"])
    assert Path.cwd() == workspace
    session = args[args.index("--resume") + 1] if "--resume" in args else str(uuid.uuid4())
    fault = os.environ.get("AUTOCODE_CONFORMANCE_FAULT", "")
    if fault == "wrong_session" and data["phase"] == "resume":
        session = "wrong_resumed_session"

    def emit(value):
        print(json.dumps(value), flush=True)

    def tool(tool_id, name, arguments):
        content = [{"type": "tool_use", "id": tool_id, "name": name, "input": arguments}]
        emit({"type": "assistant", "session_id": session, "message": {"id": f"msg_{tool_id}", "content": content}})

    def result(tool_id, content, meta, is_error):
        block = {"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": is_error}
        emit(
            {
                "type": "user",
                "session_id": session,
                "message": {"role": "user", "content": [block]},
                "tool_use_result": meta,
            }
        )

    emit({"type": "system", "subtype": "init", "session_id": session})
    if data["phase"] == "build":
        (workspace / "output.txt").write_bytes((workspace / "input.txt").read_bytes())
    if fault == "unexpected_write":
        (workspace / "unexpected.txt").write_text("unapproved edit")
    checks = []
    for index, command in enumerate(data["commands"]):
        actual = subprocess.run(shlex.split(command), cwd=workspace, capture_output=True, text=True)
        checks.append({"command": command, "exit_code": actual.returncode})
        if fault == "forged_evidence":
            continue
        tool(f"toolu_cmd_{index}", "Bash", {"command": command})
        if actual.returncode == 0 or fault == "wrong_exit":  # wrong_exit: a failure shaped as a success
            meta = {"stdout": actual.stdout, "stderr": actual.stderr, "interrupted": False}
            result(f"toolu_cmd_{index}", actual.stdout, meta, False)
        else:
            text = f"Exit code {actual.returncode}\n{actual.stderr}{actual.stdout}"
            result(f"toolu_cmd_{index}", text, "Error: " + text, True)
    report = {
        "probe_id": data["probe_id"],
        "workspace": str(workspace),
        "nonce": (workspace / "input.txt").read_text().strip(),
        "checks": checks,
    }
    if fault == "wrong_nonce":
        report["nonce"] = "not-read"
    if fault == "stale_report":
        report["probe_id"] = "old-probe"
    if fault in ("malformed_report", "incomplete_turn"):
        subtype, said = (
            ("error_max_structured_output_retries", "fixture")
            if fault == "malformed_report"
            else ("error_during_execution", "fixture incomplete turn")
        )
        emit({"type": "result", "subtype": subtype, "is_error": True, "session_id": session, "errors": [said]})
        raise SystemExit(1)
    tool("toolu_report", "StructuredOutput", report)
    result("toolu_report", "Structured output provided successfully", "Structured output provided successfully", None)
    final = {"type": "result", "subtype": "success", "is_error": False, "session_id": session}
    final.update(structured_output=report, total_cost_usd=0.01)
    if fault != "missing_usage":
        final["usage"] = {
            "input_tokens": 70,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
            "output_tokens": 30,
            "output_tokens_details": {"thinking_tokens": 5},
        }
    emit(final)
    if fault == "process_failure":
        raise SystemExit(9)
