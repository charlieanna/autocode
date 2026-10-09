#!/usr/bin/env python3
"""One stage of a hybrid scenario run: scripted by the fake provider, or handed to the live tool unchanged.

AutoCode runs a hybrid run (harness/hybrid.py) on a config-registered tool named ``hybrid`` whose command is
this script: the route file, then every value AutoCode fills in (workspace, sandbox, model, effort, schema,
report, role, run_dir, prompt_file). Each call reads its stage from the handoff and goes to one side:

- scripted: the scenario's fake provider (fake_codex.py with the scenario's fault) answers it, when the route
  scripts every attempt of that stage (``scripted``) or this is the stage's first attempt (``first_attempt``);
- live: otherwise. The live tool's own command is filled with the same values and run with the environment
  the harness started from, so a live stage is launched exactly as a live run launches it.

A report-only repair goes to the side that served the stage it repairs. Every call appends one row to the
route's trace (stage, side, report path): the harness tells scripted calls from live ones by it afterwards.
Standard library only: this file runs as a copy in the run's evidence directory.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

VALUES = ("workspace", "sandbox", "model", "effort", "schema", "report", "role", "run_dir", "prompt_file")
MARKER = "CURRENT HANDOFF DATA\n"
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def handoff(prompt: str) -> dict:
    try:
        data = json.loads(prompt.split(MARKER, 1)[1]) if MARKER in prompt else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def stage_of(data: dict) -> tuple[str, bool]:
    """(the stage the call serves, whether it is a report-only repair of that stage)."""
    original = data.get("original") if isinstance(data.get("original"), dict) else {}
    if data.get("report_repair"):
        return str(original.get("stage") or data.get("stage") or ""), True
    return str(data.get("stage") or original.get("stage") or ""), False


def side_for(route: dict, stage: str, repair: bool, trace: list[dict]) -> str:
    """Which side serves this call. Pure: the route and the calls already served."""
    if repair:
        served = [row for row in trace if row.get("stage") == stage]
        return served[-1]["side"] if served else "live"
    if stage in route.get("scripted", ()):
        return "scripted"
    attempts = sum(1 for row in trace if row.get("stage") == stage and not row.get("repair"))
    return "scripted" if stage in route.get("first_attempt", ()) and attempts == 0 else "live"


def fill(part: str, values: dict) -> str:
    """A live command argument with AutoCode's placeholders filled; {{ and }} are literal braces."""
    opened, closed = "\x00OPEN\x00", "\x00CLOSE\x00"
    masked = part.replace("{{", opened).replace("}}", closed)
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], masked).replace(opened, "{").replace(closed, "}")


def read_trace(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run(command: list[str], prompt: str | None, env: dict) -> int:
    """Run one side's command in AutoCode's working directory (the workspace) with the prompt on stdin (when
    AutoCode sends it there), its output passed through, and stop it when this process is told to stop."""
    child = subprocess.Popen(command, env=env, stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL)

    def forward(signum, _frame):
        child.send_signal(signum)
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, forward)
    if prompt is not None:
        try:
            child.stdin.write(prompt.encode())
            child.stdin.close()
        except BrokenPipeError:
            pass
    return child.wait()


def main(argv: list[str]) -> int:
    route = json.loads(Path(argv[0]).read_text())
    values = dict(zip(VALUES, argv[1:], strict=False))
    if len(values) != len(VALUES):
        print(f"hybrid_stage: expected {len(VALUES)} values after the route, got {len(argv) - 1}", file=sys.stderr)
        return 2
    from_file = route.get("prompt") == "file"
    prompt = Path(values["prompt_file"]).read_text() if from_file else sys.stdin.read()
    stage, repair = stage_of(handoff(prompt))
    trace_path = Path(route["trace"])
    with trace_path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)  # parallel Builders: one decision at a time
        side = side_for(route, stage, repair, read_trace(trace_path))
        handle.write(json.dumps({"stage": stage, "repair": repair, "side": side, "role": values["role"],
                                 "model": values["model"], "report": values["report"],
                                 "at": datetime.now(UTC).isoformat()}) + "\n")
    serving = route["sides"][side]
    env = dict(os.environ)
    for name, value in (serving.get("env") or {}).items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    if side == "scripted":
        command = [*serving["command"], "exec", "--model", values["model"],
                   *(["--output-schema", values["schema"]] if values["schema"] else []), "-o", values["report"]]
        return run(command, prompt, env)
    command = [fill(part, values) for part in serving["command"]]
    return run(command, None if from_file else prompt, env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
