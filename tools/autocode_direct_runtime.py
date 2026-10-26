"""AutoCode's bounded text/tool agent; launched by the existing provider supervisor.

The authenticated process calls inference only. Model commands run in the existing
kernel-contained, credential-free shell. No other in-process tool is exposed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import subprocess
import sys
import time
import uuid
from pathlib import Path

try:
    from . import autocode_direct_config as config
    from . import autocode_direct_transport as transport
    from . import autocode_tool_containment as containment
    from . import autocode_verification_copy as copies
except ImportError:
    import autocode_direct_config as config
    import autocode_direct_transport as transport
    import autocode_tool_containment as containment
    import autocode_verification_copy as copies

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Execute a command in the task workspace inside a kernel sandbox. The stage's source-write permissions are enforced; no network or credentials are available.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    }
]


def emit(value):
    print(json.dumps(value), flush=True)


class Shell:
    def __init__(self, workspace, run_dir, allow_write, planning):
        self.workspace = Path(workspace).resolve()
        self.boundary = containment.prepare(
            self.workspace,
            allow_write=allow_write,
            read_roots=(Path(__file__).parent.resolve(), Path(sys.prefix).resolve()),
            protected_paths=(Path(run_dir).resolve(), Path(__file__).parent.resolve()),
            environment=os.environ,
            private_run=run_dir if planning else None,
            verification_copy=not allow_write and not planning,
        )
        self.cwd = self.workspace
        if self.boundary.get("verification_copy"):
            self.cwd, _ = copies.execution(
                self.boundary["verification_copy"], self.boundary["verification_copy_sha256"], self.workspace
            )
        self.identities = {
            key: hashlib.sha256(Path(self.boundary[key]).read_bytes()).hexdigest() for key in ("shell", "profile")
        }

    def __call__(self, command):
        for key, expected in self.identities.items():
            if hashlib.sha256(Path(self.boundary[key]).read_bytes()).hexdigest() != expected:
                raise ValueError("Direct tool sandbox changed; no command was executed")
        # The wrapper clears the environment before the sandbox. Its parent also
        # receives no API/AWS secrets or controller tokens in environment/argv.
        with subprocess.Popen(
            [self.boundary["shell"], "-c", command],
            cwd=self.cwd,
            env={"PATH": "/usr/bin:/bin"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        ) as process:
            deadline = time.monotonic() + 90
            output, truncated = bytearray(), False
            try:
                with selectors.DefaultSelector() as ready:
                    ready.register(process.stdout, selectors.EVENT_READ)
                    while ready.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not ready.select(remaining):
                            raise subprocess.TimeoutExpired("contained shell", 90)
                        chunk = os.read(process.stdout.fileno(), 8192)
                        if not chunk:
                            ready.unregister(process.stdout)
                            break
                        room = max(0, 65536 - len(output))
                        output.extend(chunk[:room])
                        truncated |= len(chunk) > room
                code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except BaseException:
                process.kill()
                process.wait()
                raise
            return code, output.decode(errors="replace") + ("\n[output truncated at 65536 bytes]" if truncated else "")


def run(settings, key, prompt, model, effort, report, execute, *, complete=None):
    """Bounded tool loop; injected transport/tool seams allow deterministic fixtures."""
    emit({"type": "thread.started", "thread_id": "autocode-" + uuid.uuid4().hex})
    emit({"type": "turn.started"})
    messages = [
        {
            "role": "system",
            "content": "Follow the task handoff and stage permissions. Use shell for actual work and checks. Finish with only the requested JSON report; never invent command evidence.",
        },
        {"role": "user", "content": prompt},
    ]
    tool_ids = set()
    for _ in range(32):
        if complete:
            message, stop, usage = complete(messages, TOOLS, model, effort)
        elif settings["transport"] == "compatible":
            message, stop, usage = transport.compatible(settings, key, messages, TOOLS, model, effort)
        else:
            message, stop, usage = transport.anthropic(settings, key, messages, TOOLS, model, effort)
        counts = {}
        for source, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
            count = usage.get(source)
            if type(count) is int and count >= 0:
                counts[target] = count
        cached = (
            usage.get("prompt_tokens_details", {}).get("cached_tokens")
            if isinstance(usage.get("prompt_tokens_details"), dict)
            else None
        )
        if type(cached) is int and cached >= 0:
            counts["cached_input_tokens"] = cached
        # One partial row per response keeps every request observable if a later
        # request fails. Unknown usage is omitted, never manufactured as zero.
        emit({"type": "usage.partial", "usage": counts})
        if stop not in ("stop", "tool_calls"):
            raise transport.TransportError(f"Direct model stopped with {stop}; report is incomplete")
        calls = message.get("tool_calls") or []
        if stop == "stop" and calls or stop == "tool_calls" and not calls:
            raise transport.TransportError("Direct model returned an inconsistent tool-call stop")
        if not calls:
            text = message.get("content")
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError("Direct model final report must be a JSON object")
            Path(report).write_text(text)
            emit({"type": "item.completed", "item": {"type": "agent_message", "text": text}})
            emit({"type": "turn.completed", "usage": {}})
            return 0
        if len(calls) > 8:
            raise ValueError("Direct model exceeded the per-response tool-call bound")
        ids = [call.get("id") for call in calls]
        if any(not isinstance(identity, str) or not identity or identity in tool_ids for identity in ids) or len(
            set(ids)
        ) != len(ids):
            raise ValueError("Direct model returned duplicate or invalid tool-call identities")
        tool_ids.update(ids)
        messages.append(message)
        for call in calls:
            function = call["function"]
            args = json.loads(function["arguments"])
            if (
                function["name"] != "shell"
                or not isinstance(args, dict)
                or set(args) != {"command"}
                or not isinstance(args["command"], str)
                or not args["command"].strip()
            ):
                raise ValueError("Direct model requested an unsupported tool")
            emit(
                {
                    "type": "item.started",
                    "item": {"type": "command_execution", "id": call["id"], "command": args["command"]},
                }
            )
            code, output = execute(args["command"])
            emit(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "id": call["id"],
                        "command": args["command"],
                        "exit_code": code,
                        "aggregated_output": output,
                    },
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps({"exit_code": code, "output": output}),
                }
            )
    raise ValueError("Direct agent reached its 32-response bound; no completion report was accepted")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", default="")
    parser.add_argument("--effort", default="")
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--planning", action="store_true")
    parser.add_argument("--connection-hash", required=True)
    parser.add_argument("-o", required=True)
    args = parser.parse_args()
    try:
        settings = config.load()
        if hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest() != args.connection_hash:
            raise ValueError("Direct connection changed after launch preparation")
        record = config.provider_settings(settings, args.provider)
        if not config.model_allowed(settings, record["api_provider"], args.model):
            raise ValueError("Model is not configured for this direct connection")
        key = config.api_key(settings, record["api_provider"])
        # Fail closed on unsupported host, missing sandbox or setup before an
        # inference request. The provider's outer supervisor still owns cleanup.
        shell = Shell(args.workspace, args.run_dir, args.allow_write, args.planning)
        return run(record, key, sys.stdin.read(), args.model, args.effort, args.o, shell)
    except transport.TransportError as error:
        emit(
            {"type": "error", "error": {"name": "APIError", "data": {"statusCode": error.code}, "message": str(error)}}
        )
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        emit(
            {
                "type": "error",
                "error": {
                    "name": "DirectRuntimeError",
                    "message": "AutoCode direct runtime could not finish; check setup, tool containment and the retained attempt",
                },
            }
        )
    emit({"type": "turn.failed"})
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
