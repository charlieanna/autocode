"""How a report's check is matched to the capture receipt it cites.

`autocode capture -- CMD` runs CMD as an argument list, with no shell, and saves it in the
receipt. A report's check restates that command as text. Requiring the text to equal
`shlex.join(receipt["command"])` refused checks that only quoted an argument differently
(`-p "test_*.py"` for `-p 'test_*.py'`) although the same program ran with the same arguments.
Validator reports were sent back for repair for that, and for placeholders and summaries such
as `go build -o <tmpdir>/policy .` or `python3 -c <combined assertions>` (live Claude-model
runs, 2026-09-29). The first is accepted and recorded as the receipt spells it; the second is
still refused, with a message that shows both commands so one repair can copy the right one.

Capture also prints a short attestation line on stderr after the receipt (``attestation_line``).
Stages shortened capture's display (``| tail -c 500``, ``| grep exit_code``, ``>/dev/null``) and
dropped the one-line receipt the runner authenticates; each such report was rejected, repaired
and often re-run (live Claude-model runs, 2026-11-02, docs/bugs/2026-11-02-retry-loops.md). The
line names the SHA-256 of the receipt file's exact bytes, so it attests what the whole receipt
line did and the runner accepts either.

Pure functions; imports nothing from the runner.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex

ATTESTATION = "autocode_receipt_sha256"


def attestation_line(receipt_bytes: bytes) -> str:
    """The line capture prints after the receipt: its exit code and the digest of the receipt file's bytes.

    The exit code comes first and under the receipt's own key, so a display filter that keeps
    lines naming ``"exit_code"`` (``2>&1 | grep -E '"exit_code"|Tests'``, live run 2026-11-02)
    keeps this line whole.
    """
    receipt = json.loads(receipt_bytes)
    return (
        json.dumps({"exit_code": receipt.get("exit_code"), ATTESTATION: hashlib.sha256(receipt_bytes).hexdigest()})
        + "\n"
    )


def attests(value, receipt_bytes: bytes) -> bool:
    """True when a parsed output line is capture's attestation of exactly these receipt bytes."""
    if not isinstance(value, dict) or value.get(ATTESTATION) != hashlib.sha256(receipt_bytes).hexdigest():
        return False
    if set(value) == {ATTESTATION}:  # capture before 2026-11-06 printed the digest alone
        return True
    return set(value) == {"exit_code", ATTESTATION} and value["exit_code"] == json.loads(receipt_bytes).get("exit_code")


# A pipe into a line filter, or a redirect of stdout or stderr, after capture's arguments.
_FILTER = re.compile(r"(?:2>&1\s*)?\|\s*(?:grep|tail|head|cut|sed|awk|jq)\b[^|;&\n]*|[12]?>\s*/dev/null")


def display_filter(commands, receipt_names) -> str | None:
    """The filter a capture command for this receipt sent its display through, if any.

    Said in the rejection, so the next Tester attempt runs capture unfiltered instead of
    repeating the filter that dropped its receipt (docs/bugs/2026-11-02-retry-loops.md).
    """
    for command in commands:
        if not isinstance(command, str):
            continue
        for name in receipt_names:
            at = command.find(name)
            if at >= 0 and "capture" in command[:at]:
                found = _FILTER.search(command, at + len(name))
                if found:
                    return found.group(0).strip()
    return None


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
    return (
        f"Check command/result differs from receipt {check.get('evidence_ref')}: the check says {cited!r}"
        f" (exit {check.get('exit_code')}), the receipt ran {ran!r} (exit {receipt.get('exit_code')}). "
        "Copy the receipt's command exactly: no placeholders, summaries or the capture invocation itself."
    )
