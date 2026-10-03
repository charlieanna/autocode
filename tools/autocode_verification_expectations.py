"""Turn explicitly declared exit expectations into zero-exit assertion checks.

Prose alone cannot assign an exit status. Support a terminal confirmation clause
for one command, or an ordered vector for a list of quoted commands. Unrecognized
prose stays with the Validator; conflicting counts never guess an assignment.
Imports only the standard library and writes no run state.
"""
from __future__ import annotations

import re
import shlex

DECLARATION = re.compile(
    r"\s*(?:directly\s+)?(?:[,;]?\s*(?:and|then)\s+)?"
    r"(?:confirm|expect|assert|verify|check)\s+"
    r"(?:exact\s+(?:stdout/stderr|stdout\s+and\s+stderr)\s+bytes\s+and\s+)?"
    r"(?:exit\s+(?:codes?|statuses?)|(?:it\s+)?exits?)\s*"
    r"(?:of\s+|are\s+|[:=]\s*)?"
    r"(?P<codes>[+-]?\d+(?:\s*[/,]\s*[+-]?\d+)*)\s*[.)]*\s*", re.IGNORECASE)


def assertion_commands(method: str, commands: list[str]) -> list[str]:
    """Preserve every command, wrapping nonzero expectations in shell assertions."""
    if not commands:
        return commands
    snippets = list(re.finditer(r"`([^`]+)`", method))
    if not snippets:
        return commands
    declaration = DECLARATION.fullmatch(method[snippets[-1].end():])
    if not declaration:
        return commands
    codes = [int(value.strip()) for value in re.split(r"[/,]", declaration["codes"])]
    quoted = [snippet.group(1).strip() for snippet in snippets]
    if quoted != commands or len(codes) != len(commands) or any(not 0 <= code <= 255 for code in codes):
        raise ValueError("Planned exit codes need one status (0–255) per executable command; "
                         "use an explicit zero-exit assertion check for more complex expectations")
    return [command if code == 0 else "sh -c " + shlex.quote(
        f'({command}); autocode_plan_exit=$?; test "$autocode_plan_exit" -eq {code}')
        for command, code in zip(commands, codes)]
