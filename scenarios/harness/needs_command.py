"""Check that a status-view next_command is argv the public CLI accepts (#1010).

The harness stays black-box for the run: this helper only parses argv in a
subprocess with tools/ on PYTHONPATH, never imports AutoCode into the driver
process. Person-filled placeholders (N after int flags) are substituted the
same way tests/test_needs_contract.py does.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Flags whose N the person fills in; "10" parses for each (--max-cost-usd takes a float).
_PERSON_INT = (
    "--planning-review-call-limit",
    "--no-progress-limit",
    "--max-seconds",
    "--max-idle-seconds",
    "--max-cost-usd",
)

_CHECK = r"""
import shlex
import sys
from unittest import mock

import autocode_args
import autocode_configure

PERSON_INT = {person_int!r}
text = sys.argv[1]
words = shlex.split(text)
environ = {{}}
while words and words[0] != "autocode":
    name, value = words.pop(0).split("=", 1)
    environ[name] = value
if not words or words[0] != "autocode":
    raise SystemExit("next_command does not start with autocode")
words.pop(0)
filled = list(words)
for index, word in enumerate(filled[:-1]):
    if word in PERSON_INT and filled[index + 1] == "N":
        filled[index + 1] = "10"
with mock.patch.dict("os.environ", environ):
    autocode_args.parse(None, filled, autocode_configure.DEFAULT_ROLE_MODELS)
"""


class Rejected(ValueError):
    """The displayed next_command is not accepted by autocode_args."""


def accept(text: str) -> None:
    """Raise Rejected when ``text`` is not a parseable autocode invocation."""
    if not text:
        return
    env = {**os.environ, "PYTHONPATH": str(REPO / "tools") + os.pathsep + env_path(os.environ.get("PYTHONPATH"))}
    proc = subprocess.run(
        [sys.executable, "-c", _CHECK.format(person_int=_PERSON_INT), text],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[-500:]
        raise Rejected(detail)


def env_path(value: str | None) -> str:
    return value or ""
