#!/usr/bin/env python3
"""Run one AutoCode stage on the `claude` CLI (trial glue, not part of the repository).

argv: workspace sandbox model effort schema report. The stage prompt arrives on stdin.
Claude's stream is translated into the Codex-style events AutoCode's liveness watchdog and usage
accounting read; the schema-checked report is written to `report`.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

APPEND = (
    "You are one stage of an automated engineering pipeline. Return your final report ONLY through the "
    "structured output, exactly matching the schema, and put everything in it: every check, criterion result "
    "and evidence reference. Work only inside the current directory. Never run git commit, "
    "git push, git checkout, git reset, git stash or git branch, and never edit anything under .git/ or "
    ".autocode/ except through the capture command the prompt names."
)
# AutoCode's command-provider contract (tools/providers/command.py) tells a stage to write its report
# to a file. Here the report is the structured output, which this wrapper writes to that file. With
# both instructions a live Validator (2026-09-29) wrote a complete report with Bash, then returned a
# structured output without its checks, and the run spent its token budget on repairs.
FILE_CONTRACT = re.compile(r"Write your final report as exactly one JSON object to this file: \S+")
STRUCTURED = ("Return your final report as exactly one JSON object through the structured output; the runner "
              "saves it to the report file itself, so do not write that file")


def adapt(prompt: str) -> str:
    """The stage prompt with its write-a-file instruction replaced by the structured-output one."""
    return FILE_CONTRACT.sub(STRUCTURED, prompt)


def main() -> None:
    workspace, sandbox, model, effort, schema_path, report = sys.argv[1:7]
    prompt = adapt(sys.stdin.read())
    schema = json.loads(Path(schema_path).read_text())
    run(workspace, sandbox, model, effort, schema, report, prompt)


REMINDER = ("Your work is finished, but no structured report reached the runner. Call the StructuredOutput tool now "
            "with your complete final report, exactly matching the schema. Do not do any more work.")


def emit(row):
    sys.stdout.write(json.dumps(row) + "\n")
    sys.stdout.flush()


def converse(command, prompt, workspace, env):
    """One `claude -p` call, its stream translated to events: (the result row, the report the CLI accepted)."""
    child = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
    child.stdin.write(prompt)
    child.stdin.close()

    # A report the CLI accepted through its StructuredOutput tool. A live ladder-16 Builder (2026-09-30) submitted
    # its report that way, then the API failed mid-response ("Server error mid-response"), the result carried no
    # structured_output, and the finished stage was thrown away. AutoCode validates the report either way.
    text, last_emit, tools, result, offered, accepted = {}, {}, {}, None, {}, None
    for line in child.stdout:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        kind = row.get("type")
        if kind == "stream_event":
            event = row.get("event") or {}
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                key = (row.get("session_id"), event.get("index"), delta.get("type"))
                piece = delta.get("text") or delta.get("thinking") or ""
                if piece:
                    text[key] = text.get(key, "") + piece
                    now = time.monotonic()
                    if now - last_emit.get(key, 0) >= 1.0:
                        last_emit[key] = now
                        item_type = "reasoning" if delta.get("type") == "thinking_delta" else "agent_message"
                        emit({"type": "item.updated", "item": {"id": "t%s-%s" % (key[1], abs(hash(key[0])) % 10**6),
                                                               "type": item_type, "text": text[key]}})
        elif kind == "assistant":
            for block in (row.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    name, args = block.get("name"), block.get("input") or {}
                    if name == "StructuredOutput" and isinstance(args, dict):
                        offered[block["id"]] = args
                    shown = args.get("command") if name == "Bash" and isinstance(args.get("command"), str) \
                        else name + " " + json.dumps(args)[:400]
                    tools[block["id"]] = shown
                    emit({"type": "item.started", "item": {"id": block["id"], "type": "command_execution",
                                                           "command": shown, "status": "in_progress"}})
        elif kind == "user":
            for block in (row.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    body = block.get("content")
                    if block.get("tool_use_id") in offered and not block.get("is_error"):
                        accepted = offered[block["tool_use_id"]]
                    if isinstance(body, list):
                        body = "".join(part.get("text", "") for part in body if isinstance(part, dict))
                    emit({"type": "item.completed", "item": {
                        "id": block.get("tool_use_id"), "type": "command_execution",
                        "command": tools.get(block.get("tool_use_id"), ""), "status": "completed",
                        "aggregated_output": str(body or "")[:20000],
                        "exit_code": 1 if block.get("is_error") else 0}})
        elif kind == "result":
            result = row
    child.wait()
    return result, accepted


def report_of(result, accepted):
    """The stage's report: the result's structured output, else one the CLI accepted before the stream failed."""
    value = (result or {}).get("structured_output")
    if result and not result.get("is_error") and isinstance(value, dict):
        return value
    return accepted


def run(workspace, sandbox, model, effort, schema, report, prompt):
    DENY_GIT = ["Bash(git commit:*)", "Bash(git push:*)", "Bash(git checkout:*)", "Bash(git reset:*)",
                "Bash(git stash:*)", "Bash(git branch:*)", "Bash(git rebase:*)", "Bash(git merge:*)"]
    # The session is kept so a stage that ends without its report can be asked for it once (below).
    command = ["claude", "-p", "--setting-sources", "project", "--model", model,
               "--output-format", "stream-json", "--verbose", "--include-partial-messages",
               "--json-schema", json.dumps(schema), "--append-system-prompt", APPEND]
    if effort and "haiku" not in model:
        command += ["--effort", effort]
    if sandbox == "read-only":
        command += ["--permission-mode", "default", "--allowedTools", "Read", "Grep", "Glob", "Bash",
                    "--disallowedTools", "Edit", "Write", "NotebookEdit", *DENY_GIT]
    else:
        command += ["--permission-mode", "acceptEdits", "--allowedTools", "Read", "Grep", "Glob", "Bash", "Edit",
                    "Write", "--disallowedTools", *DENY_GIT]

    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION")}

    result, accepted = converse(command, prompt, workspace, env)
    value = report_of(result, accepted)
    if value is None and (result or {}).get("session_id"):
        # A live cent-drift Builder (2026-09-30) ran its final tests, then ended without calling StructuredOutput
        # and said it already had, so a finished build was thrown away. Its session still holds the work: ask once.
        # `--resume` goes before the flags that take lists so it is not read as one of their values.
        again, accepted = converse(["claude", "-p", "--resume", result["session_id"], *command[2:]], REMINDER,
                                   workspace, env)
        value = report_of(again, accepted)
        if again:
            result = {**again, "total_cost_usd": (result.get("total_cost_usd") or 0) + (again.get("total_cost_usd") or 0),
                      "usage": {key: (result.get("usage") or {}).get(key, 0) + (again.get("usage") or {}).get(key, 0)
                                for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                                            "output_tokens")}}

    usage = (result or {}).get("usage") or {}
    cached = usage.get("cache_read_input_tokens") or 0
    fresh = (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
    totals = {"input_tokens": fresh + cached, "cached_input_tokens": cached,
              "output_tokens": usage.get("output_tokens") or 0, "reasoning_output_tokens": 0}
    if value is not None:
        Path(report).write_text(json.dumps(value, indent=2) + "\n")
        emit({"type": "turn.completed", "usage": totals, "cost_usd": (result or {}).get("total_cost_usd"), "model": model})
        sys.exit(0)
    emit({"type": "turn.failed", "usage": totals, "cost_usd": (result or {}).get("total_cost_usd"),
          "error": {"message": ((result or {}).get("result") or "claude returned no structured report")[:500]}})
    sys.exit(1)


if __name__ == "__main__":
    main()
