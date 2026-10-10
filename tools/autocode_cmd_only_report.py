"""A final message that is only a shell command is a known provider defect, never a report.

Issue #512: through a Codex transport, some models end a stage with the arguments of a
shell tool call, ``{"cmd": "ls docs/ && wc -l ..."}``, as their final message instead of
the stage's JSON report. The runner used to reject it as an ordinary invalid report
("$: missing summary"), so every retry and repair started from nothing and repeated it.

``refuse`` recognizes that shape before any report processing and raises
``CommandOnlyReport``: the stage is rejected, the command is never run and never read
as report content. The rejection then takes the existing report-repair route with a
targeted instruction (``CORRECTION`` for the one same-session correction of
autocode_format_correction, ``REPAIR_INSTRUCTION`` for a full report repair), within
the existing correction and repair limits. Its exception class names the defect in the
failure ledger (autocode_failures), so repeats count as one failure.

A command whose text happens to embed a JSON report is still a command: the runner
does not unwrap it. Pure module: no runner imports.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import json
from pathlib import Path

ERROR = (
    'Provider final message is a shell command ({"cmd": ...}) instead of the JSON report; '
    "AutoCode never runs it (a known provider defect, docs/providers.md: if it repeats, "
    "run this role on another model)"
)
COMMAND_KEYS = frozenset({"cmd", "command"})
# The other arguments of a Codex shell tool call; alongside a command they add no report content.
TOOL_ARGUMENT_KEYS = frozenset(
    {
        "workdir",
        "timeout_ms",
        "yield_time_ms",
        "max_output_tokens",
        "shell",
        "login",
        "with_escalated_permissions",
        "justification",
    }
)

CORRECTION = prompts.get("fragments/cmd-only-report/correction.md")
REPAIR_INSTRUCTION = prompts.get("fragments/cmd-only-report/repair-instruction.md")


class CommandOnlyReport(RuntimeError):
    """A stage's final message was a shell command; the class names the defect in the failure ledger."""


def is_command_only(value, schema=None) -> bool:
    """Whether ``value`` is only a shell command: a command key with a command, every other key a
    shell tool argument, and no key the report schema itself declares."""
    if not isinstance(value, dict) or not COMMAND_KEYS & value.keys():
        return False
    if value.keys() - COMMAND_KEYS - TOOL_ARGUMENT_KEYS:
        return False
    if isinstance(schema, dict) and value.keys() & (schema.get("properties") or {}).keys():
        return False
    return any(_command(value[key]) for key in COMMAND_KEYS & value.keys())


def _command(text) -> bool:
    if isinstance(text, str):
        return bool(text.strip())
    return isinstance(text, list) and bool(text) and all(isinstance(part, str) for part in text)


def refuse(value, schema_path=None):
    """Return ``value`` unchanged, or raise CommandOnlyReport when it is only a shell command."""
    if not is_command_only(value):
        return value  # the schema can only clear a command, so most reports never read it
    schema = None
    if schema_path and Path(schema_path).is_file():
        try:
            schema = json.loads(Path(schema_path).read_text())
        except (OSError, ValueError):
            schema = None  # the stage's own validation reports an unreadable schema
    if is_command_only(value, schema):
        raise CommandOnlyReport(ERROR)
    return value


def matches(error) -> bool:
    """Whether a recorded rejection is this defect."""
    return str(error or "").startswith(ERROR)
