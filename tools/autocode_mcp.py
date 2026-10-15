"""An MCP server that puts AutoCode inside the coding agent a person already runs (#693).

It speaks the Model Context Protocol over standard input and output
(newline-delimited JSON-RPC 2.0) with the standard library only, so it adds no
dependency. Its four tools are autocode_agent_step's operations on one workspace,
fixed when the server starts:

    autocode_start     start a run and work until it needs a person or completes
    autocode_status    where a run stands: the status view's next-step fields
    autocode_respond   pass on the person's plan approval or answer, or continue
    autocode_evidence  the completed run's canonical evidence report

Engine, model and budget flags come from the server's own command line, which
the person configures, never from the agent's tool arguments:

    python -m autocode_cli.autocode_mcp --workspace /path/to/project -- --engine claude

A tool call returns when the run stops or completes, which can take as long as the
work does. A refusal, AutoCode's or the step's, is a tool result with ``isError``
so the agent can read it; only a malformed request is a JSON-RPC error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import IO, Any

try:
    from . import autocode_agent_step as agent_step
except ImportError:
    import autocode_agent_step as agent_step

# Newest first; an initialize naming another version gets the newest (the client then decides).
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603

INSTRUCTIONS = (
    "AutoCode plans, builds and independently checks a coding task in this workspace and records evidence. "
    "Every tool returns the run's status. At each stop, tell the person pause_category_label, progress, "
    "explanation and next_command. While needs.kind is approve_plan, show the person the plan. Approve a plan "
    "or answer a question only with the person's own decision, passing the token from the view they saw. Any "
    "other stop is the person's: give them next_command. A call returns when the run next needs a person or "
    "completes."
)
_RUN_DIR = {
    "type": "string",
    "description": "The run to act on. Default: the run this server started, or the workspace's only run.",
}
TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "autocode_start",
        "description": "Start an AutoCode run in the workspace on the person's request and work until it needs "
        "the person (a question, plan approval) or completes. Start only when the person asked for AutoCode: "
        "a live provider spends money.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "brief": {"type": "string", "description": "The person's request, in their words."},
                "workflow": {
                    "type": "string",
                    "enum": list(agent_step.WORKFLOWS),
                    "description": "Name the kind of job instead of having AutoCode recognize it. "
                    "review: judge an existing change, changing nothing.",
                },
            },
            "required": ["brief"],
            "additionalProperties": False,
        },
    },
    {
        "name": "autocode_status",
        "description": "Where a run stands: status, done, pause_category_label, needs, next_command, progress, "
        "and the plan while it waits for approval. Reads only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_dir": _RUN_DIR,
                "full": {"type": "boolean", "description": "Add the whole status view (large)."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "autocode_respond",
        "description": "Pass on the person's decision, then work until the next stop. approve_plan: only after "
        "the person approved the plan they were shown; token is needs.token. answer: the person's answer to "
        "one question; token is needs.resolver_token. continue: only when needs.kind is continue. Every other "
        "stop is refused with the command the person runs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(agent_step.ACTIONS)},
                "token": {"type": "string", "description": "From the view the person decided on."},
                "question_id": {"type": "string", "description": "answer: the question's id."},
                "text": {"type": "string", "description": "answer: the person's answer."},
                "run_dir": _RUN_DIR,
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "autocode_evidence",
        "description": "The completed run's evidence report (Markdown): what was agreed, checked and found. "
        "Refused while the run is unfinished, and for a build once the workspace has changed since; a review "
        "returns the report it recorded.",
        "inputSchema": {"type": "object", "properties": {"run_dir": _RUN_DIR}, "additionalProperties": False},
    },
)


def _start(step: agent_step.AgentStep, arguments: dict) -> dict:
    return step.start(_required(arguments, "brief"), workflow=_text(arguments, "workflow"))


def _status(step: agent_step.AgentStep, arguments: dict) -> dict:
    full = arguments.get("full", False)
    if not isinstance(full, bool):
        raise agent_step.StepError("full must be true or false")
    return step.status(_text(arguments, "run_dir"), full=full)


def _respond(step: agent_step.AgentStep, arguments: dict) -> dict:
    return step.respond(
        _required(arguments, "action"),
        token=_text(arguments, "token"),
        question_id=_text(arguments, "question_id"),
        text=_text(arguments, "text"),
        run_dir=_text(arguments, "run_dir"),
    )


def _evidence(step: agent_step.AgentStep, arguments: dict) -> dict:
    return step.evidence(_text(arguments, "run_dir"))


HANDLERS: dict[str, Callable[[agent_step.AgentStep, dict], dict]] = {
    "autocode_start": _start,
    "autocode_status": _status,
    "autocode_respond": _respond,
    "autocode_evidence": _evidence,
}


def _required(arguments: dict, name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise agent_step.StepError(f"{name} must be a nonempty string")
    return value


def _text(arguments: dict, name: str) -> str | None:
    value = arguments.get(name)
    if value is not None and not isinstance(value, str):
        raise agent_step.StepError(f"{name} must be a string")
    return value


class Server:
    """One MCP session over one workspace. ``handle`` maps a message to its response, or None."""

    def __init__(self, step: agent_step.AgentStep, version: str):
        self.step = step
        self.version = version

    def handle(self, message: Any) -> dict | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            ident = message.get("id") if isinstance(message, dict) else None
            return _error(ident, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        if "method" not in message:
            return None  # a response; this server sends no requests
        if "id" not in message:
            return None  # a notification: initialized, cancelled, ...
        ident, method, params = message["id"], message["method"], message.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return _error(ident, INVALID_REQUEST, "method must be a string and params an object")
        if method == "initialize":
            result: dict = self.initialize(params)
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": list(TOOLS)}
        elif method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments") or {}
            if name not in HANDLERS:
                return _error(ident, INVALID_PARAMS, f"Unknown tool: {name}")
            if not isinstance(arguments, dict):
                return _error(ident, INVALID_PARAMS, "arguments must be an object")
            result = self.call(name, arguments)
        else:
            return _error(ident, METHOD_NOT_FOUND, f"Method not found: {method}")
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    def initialize(self, params: dict) -> dict:
        requested = params.get("protocolVersion")
        return {
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "autocode", "version": self.version},
            "instructions": INSTRUCTIONS,
        }

    def call(self, name: str, arguments: dict) -> dict:
        known = next(tool["inputSchema"]["properties"] for tool in TOOLS if tool["name"] == name)
        try:
            unknown = sorted(set(arguments) - set(known))
            if unknown:  # engine and budget flags in particular: they are the server's, set by the person
                raise agent_step.StepError(f"{name} takes no {', '.join(unknown)}; it takes {', '.join(known)}")
            value = HANDLERS[name](self.step, arguments)
        except agent_step.StepError as error:
            return {"content": [{"type": "text", "text": str(error)}], "isError": True}
        return {
            "content": [{"type": "text", "text": json.dumps(value, indent=2)}],
            "structuredContent": value,
            "isError": False,
        }


def serve(server: Server, lines: Iterable[str], out: IO[str]) -> None:
    """Answer each request line on ``out`` until the input ends."""
    for line in lines:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            response: dict | None = _error(None, PARSE_ERROR, "Parse error")
        else:
            try:
                response = server.handle(message)
            except Exception as error:  # one failed request must not end the agent's session
                ident = message.get("id") if isinstance(message, dict) else None
                response = _error(ident, INTERNAL_ERROR, f"{type(error).__name__}: {error}")
        if response is not None:
            out.write(json.dumps(response) + "\n")
            out.flush()


def _error(ident: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def _version() -> str:
    from importlib import metadata

    try:
        return metadata.version("autocode-supervisor")
    except metadata.PackageNotFoundError:
        return "source"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    options: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, options = argv[:at], argv[at + 1 :]
    parser = argparse.ArgumentParser(
        prog="python -m autocode_cli.autocode_mcp",
        description="Serve AutoCode's task runs to an MCP client over stdio. "
        "Engine, model and budget flags for new runs follow -- (docs/agent-step.md).",
        allow_abbrev=False,
    )
    parser.add_argument("--workspace", default=".", help="the repository runs work in (default: here)")
    arguments = parser.parse_args(argv)
    step = agent_step.AgentStep(Path(arguments.workspace), tuple(options), caller="mcp")
    protocol = sys.stdout
    sys.stdout = sys.stderr  # stdout carries only protocol messages
    serve(Server(step, _version()), sys.stdin, protocol)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
