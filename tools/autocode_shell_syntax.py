"""Whether a command holds shell syntax where the shell reads it as syntax, not inside a quoted word.

The command parsers read a test command as plain words only when the shell would run it as one
program with literal arguments. Each checked the raw text for a set of shell characters, so a quoted
literal was refused like an operator: the commands AutoCode narrows to named tests itself
(``autocode_proof_stability.narrow``) quote exactly such text, ``go test -run '^(TestA|TestB)$'`` and a
Rust documentation test's name ``'src/lib.rs - (line 20)'``. Their per-test results could not be read,
and the coverage tool could not wrap them, so a Go proof never had diff coverage and a Rust proof that
named a documentation test had no mutation check (setup sweep, docs/setup-sweep.md).

One answer for every parser: a character of the given set counts only where the shell reads it,
outside single quotes, and inside double quotes only for what the shell still expands there. Pure
function; imports nothing from AutoCode, so any layer may use it.
"""

from __future__ import annotations

# The characters the parsers refuse: control operators, redirection, subshells, expansion, escapes and
# line breaks. A parser that reads Windows paths leaves out the backslash.
DEFAULT = ";&|<>`$()\n\r\\"
# What a POSIX shell still reads inside double quotes.
_EXPANDED_IN_DOUBLE_QUOTES = "`$\\"
_LINE_BREAKS = "\n\r"


def outside_quotes(command: str, characters: str = DEFAULT) -> bool:
    """True when one of ``characters`` in ``command`` is shell syntax, or a quote is left open.

    A line break counts anywhere when it is in ``characters``: a quoted one still splits a command
    line where a reader looks for one command.
    """
    quote: str | None = None
    for character in command:
        if character in _LINE_BREAKS and character in characters:
            return True
        if quote is None:
            if character in ("'", '"'):
                quote = character
            elif character in characters:
                return True
        elif character == quote:
            quote = None
        elif quote == '"' and character in _EXPANDED_IN_DOUBLE_QUOTES and character in characters:
            return True
    return quote is not None
