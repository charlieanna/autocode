"""Validate the runner-owned prepared capture batch plan."""

import base64
import binascii
import json
import os
import sys
from pathlib import Path

MAX_PLAN_BYTES = 64 * 1024
MAX_ENCODED_BYTES = 4 * ((MAX_PLAN_BYTES + 2) // 3)


def _capture():
    """The running helper owns the launcher; copied data never selects it."""
    return [sys.executable, str(Path(__file__).resolve().with_name("autocode.py")), "capture"]


def _capture_mode(launcher):
    if not isinstance(launcher, list):
        return None
    if launcher == _capture():
        # Match capture's runtime default for previously offered bare launchers.
        mode = os.environ.get("AUTOCODE_OUTPUT_MODE", "conservative")
    elif launcher[:-2] == _capture() and launcher[-2:-1] == ["--mode"]:
        mode = launcher[-1]
    else:
        return None
    return mode if mode in ("raw", "conservative") else None


def _plan(encoded):
    """Reject malformed or unbounded input before any capture is launched."""
    if len(encoded) > MAX_ENCODED_BYTES:
        raise ValueError("Prepared capture batch plan is too large")
    try:
        raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
        if len(raw) > MAX_PLAN_BYTES:
            raise ValueError("Prepared capture batch plan is too large")
        plan = json.loads(raw)
    except (binascii.Error, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid prepared capture batch plan") from error
    if (
        not isinstance(plan, dict)
        or set(plan) not in ({"mode", "commands", "directory"}, {"capture", "commands", "directory"})
        or not isinstance(plan["directory"], str)
        or "\0" in plan["directory"]
        or not isinstance(plan["commands"], list)
        or not 2 <= len(plan["commands"]) <= 16
        or not all(
            isinstance(command, list)
            and command
            and all(isinstance(word, str) and "\0" not in word for word in command)
            and command[0]
            for command in plan["commands"]
        )
    ):
        raise ValueError("Invalid prepared capture batch plan")
    mode = plan["mode"] if "mode" in plan else _capture_mode(plan["capture"])
    if mode not in ("raw", "conservative"):
        raise ValueError("Invalid prepared capture batch plan")
    return {"mode": mode, "commands": plan["commands"], "directory": plan["directory"]}
