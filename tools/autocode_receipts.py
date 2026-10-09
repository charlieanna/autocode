"""How a report's check is matched to the capture receipt it cites.

`autocode capture -- CMD` runs CMD as an argument list, with no shell, and saves it in the
receipt. A report's check restates that command as text. Requiring the text to equal
`shlex.join(receipt["command"])` refused checks that only quoted an argument differently
(`-p "test_*.py"` for `-p 'test_*.py'`) although the same program ran with the same arguments.
Validator reports were sent back for repair for that, and for placeholders and summaries such
as `go build -o <tmpdir>/policy .` or `python3 -c <combined assertions>` (live Claude-model
runs, 2026-09-29). The first is accepted and recorded as the receipt spells it; the second is
still refused, with a message that shows both commands so one repair can copy the right one.

Pure functions; imports nothing from the runner.
"""
from __future__ import annotations

import shlex


def adopt_command(check: dict, argv: list[str]) -> bool:
    """True when ``check["command"]`` is the receipt's argument list (any quoting); the check
    then carries the receipt's own spelling. Anything else, including a placeholder, a summary
    or a different argument, is not the command that ran."""
    try:
        if shlex.split(check["command"]) != argv:
            return False
    except ValueError:
        return False
    check["command"] = shlex.join(argv)
    return True


def mismatch(check: dict, receipt: dict) -> str:
    """Why a check does not match the receipt it cites, with what to copy."""
    cited = str(check.get("command", ""))
    ran = shlex.join(receipt["command"]) if isinstance(receipt.get("command"), list) else repr(receipt.get("command"))
    return (f"Check command/result differs from receipt {check.get('evidence_ref')}: the check says {cited!r}"
            f" (exit {check.get('exit_code')}), the receipt ran {ran!r} (exit {receipt.get('exit_code')}). "
            "Copy the receipt's command exactly: no placeholders, summaries or the capture invocation itself.")
