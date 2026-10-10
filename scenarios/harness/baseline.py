"""Run a plain coding agent on a scenario: the baseline AutoCode is compared against.

The baseline gets the same seed project and the same brief, in one call, with no
planning, review or completion gate. The same oracle then judges what it
delivered. An agent has no way to report "done" other than exiting, so exit 0
counts as claiming completion.

Presets reuse the command lines AutoCode itself launches (tools/autocode.py for
Codex, tools/providers/opencode.py for OpenCode). The brief goes on stdin.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .project import overlay_paths

FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"
CLAIMED = "COMPLETE"  # what an agent that exits 0 claims, in verdict.judge's terms
STOPPED = "PAUSED_BASELINE_EXIT"  # a nonzero exit or a timeout: the agent did not claim completion

PRESETS = {
    "codex": lambda project, model: [
        "codex",
        "exec",
        "-C",
        str(project),
        "--sandbox",
        "workspace-write",
        "-",
        *(["--model", model] if model else []),
    ],
    "opencode": lambda project, model: [
        "opencode",
        "run",
        "--dir",
        str(project),
        *(["--model", model] if model else []),
    ],
}


def command(preset: str | None, template: str | None, project: Path, model: str | None) -> list[str]:
    """The agent's argv. A template is split on spaces; {project} and {model} are substituted."""
    if template:
        return [part.replace("{project}", str(project)).replace("{model}", model or "") for part in template.split()]
    if preset not in PRESETS:
        raise ValueError(f"unknown baseline {preset!r}; choose one of {', '.join(PRESETS)} or pass --baseline-command")
    return PRESETS[preset](project, model)


def fake_setup(root: Path, solution: Path) -> tuple[list[str], dict]:
    """A scripted agent that lays ``solution`` over the project, like the fake AutoCode provider does."""
    config = root / "fake-agent.json"
    config.write_text(json.dumps({"solution": str(solution), "paths": overlay_paths(solution)}))
    return [sys.executable, str(FAKE_AGENT)], {"SCENARIO_FAKE_AGENT_CONFIG": str(config)}


def run(argv: list[str], project: Path, brief: str, log: Path, *, env: dict, timeout_seconds: int) -> dict:
    """Run the agent once in the project. Returns what the comparison records about the call."""
    started = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            argv,
            cwd=project,
            input=brief,
            capture_output=True,
            text=True,
            env={**os.environ, **env},
            timeout=timeout_seconds,
        )
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as error:
        timed_out, exit_code = True, None
        stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else error.stdout or ""
        stderr = f"TIMEOUT after {timeout_seconds}s"
    except FileNotFoundError as error:
        exit_code, stdout, stderr = 127, "", str(error)
    log.write_text(f"$ {' '.join(argv)}\n\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n")
    return {
        "argv": argv,
        "exit": exit_code,
        "timed_out": timed_out,
        "seconds": round(time.monotonic() - started, 1),
        "status": CLAIMED if exit_code == 0 else STOPPED,
        "log": str(log),
    }


def available(argv: list[str]) -> bool:
    return bool(shutil.which(argv[0])) or Path(argv[0]).is_file()
