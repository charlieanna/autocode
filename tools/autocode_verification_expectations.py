"""Turn explicitly declared exit expectations into zero-exit assertion checks.

Prose alone cannot assign an exit status. Support a terminal confirmation clause
for one command, or an ordered vector for a list of quoted commands; there a
conflicting count raises rather than guess an assignment. One narrower form is
also read: a single run list of commands, the same declaration, then plain output
or file expectations the Validator keeps ("run `a` and `b` and assert exit 2,
empty stdout"). There one status applies to every command of the list, and
anything that does not fit leaves the commands bare, as before the form was read,
never an error. Its limit: a setup command placed in the same run list as the
commands that must fail gets their status too, so its check fails on correct code
(the planner is told to give one status per command). Unrecognized prose stays
with the Validator.
Imports only the standard library and writes no run state.
"""
from __future__ import annotations

import re
import shlex

DECLARED = (
    r"\s*(?:directly\s+)?(?:[,;]?\s*(?:and|then)\s+)?"
    r"(?:confirm|expect|assert|verify|check)\s+"
    r"(?:exact\s+(?:stdout/stderr|stdout\s+and\s+stderr)\s+bytes\s+and\s+)?"
    r"(?:exit\s+(?:codes?|statuses?)|(?:it\s+)?exits?)\s*"
    r"(?:of\s+|are\s+|[:=]\s*)?"
    r"(?P<codes>[+-]?\d+(?:\s*[/,]\s*[+-]?\d+)*)")
DECLARATION = re.compile(DECLARED + r"\s*[.)]*\s*", re.IGNORECASE)
# Live program-notes-cli S4 (Claude models, 2026-10-06): "(also: after one `add x`, run `python3 -m notes add`
# and `python3 -m notes frobnicate` and assert exit 2, empty stdout and byte-identical notes file content)".
# DECLARATION cannot end there, so both usage errors were replayed bare, as checks that must exit 0.
STATUS = re.compile(DECLARED, re.IGNORECASE)
RUN_VERB = re.compile(r"\b(?:runs?|executes?|invokes?)\s*$", re.IGNORECASE)
RUN_LIST = ("and", ",", ", and")
SEPARATOR = re.compile(r"\s*(?:[,;]\s*(?:and\b)?|\band\b)\s*", re.IGNORECASE)
# A clause after the status may say nothing that could state, qualify or redirect a status, or single out one
# command: no literal, number, sentence break, status word, exception, ordinal or condition.
NOT_PLAIN = re.compile(
    r"[`\d\n\r!?]|\.(?:\s|$)|\b(?:"
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
    r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|once|twice|"
    r"exit|exits|exited|exiting|returns?|returned|returning|codes?|status|statuses|succeed|succeeds|succeeded|"
    r"success|successful|successfully|pass|passes|passed|passing|fail|fails|failed|failing|failures?|"
    r"otherwise|unless|except|but|only|first|second|third|last|initial|former|latter|respectively|other|"
    r"nonzero|or|if|when|whenever|instead|else|excluding|exclude|excludes|aside|save|besides|exception|alone|"
    r"just|final|preceding|previous|earlier|provided|given|assuming|until|while|depending|alternatively|either|"
    r"possibly|skip|skips|skipping)\b", re.IGNORECASE)
# ... and must be a plain expectation about output or files.
PLAIN = re.compile(r"\b(?:stdout|stderr|outputs?|files?|contents?|messages?|lines?|usage|errors?|notes)\b",
                   re.IGNORECASE)


def wrapped(commands: list[str], codes: list[int]) -> list[str]:
    return [command if code == 0 else "sh -c " + shlex.quote(
        f'({command}); autocode_plan_exit=$?; test "$autocode_plan_exit" -eq {code}')
        for command, code in zip(commands, codes)]


def assertion_commands(method: str, commands: list[str]) -> list[str]:
    """Preserve every command, wrapping nonzero expectations in shell assertions."""
    if not commands:
        return commands
    snippets = list(re.finditer(r"`([^`]+)`", method))
    if not snippets:
        return commands
    declaration = DECLARATION.fullmatch(method[snippets[-1].end():])
    if not declaration:
        return run_list_commands(method, snippets, commands)
    codes = [int(value.strip()) for value in re.split(r"[/,]", declaration["codes"])]
    quoted = [snippet.group(1).strip() for snippet in snippets]
    if quoted != commands or len(codes) != len(commands) or any(not 0 <= code <= 255 for code in codes):
        raise ValueError("Planned exit codes need one status (0–255) per executable command; "
                         "use an explicit zero-exit assertion check for more complex expectations")
    return wrapped(commands, codes)


def run_list_commands(method: str, snippets: list, commands: list[str]) -> list[str]:
    """The narrow form: wrapped commands, or the commands bare (never an error)."""
    status = STATUS.match(method, snippets[-1].end())
    if not (status and is_run_list(method, snippets, commands) and plain_clauses(method, status.end())):
        return commands
    values = [value.strip() for value in re.split(r"[/,]", status["codes"])]
    if any(len(value.lstrip("+-")) > 3 for value in values):
        return commands  # no exit status has more than three digits (and int() refuses thousands of them)
    codes = [int(value) for value in values]
    codes = codes * len(commands) if len(codes) == 1 else codes
    if len(codes) != len(commands) or any(not 0 <= code <= 255 for code in codes):
        return commands
    return wrapped(commands, codes)


def is_run_list(method: str, snippets: list, commands: list[str]) -> bool:
    """The commands are the last quoted snippets, the first after run/execute/invoke, joined by and/,."""
    trailing = snippets[len(snippets) - len(commands):]
    if [snippet.group(1).strip() for snippet in trailing] != commands:
        return False
    start = snippets[-len(commands) - 1].end() if len(snippets) > len(commands) else 0
    return bool(RUN_VERB.search(method[start:trailing[0].start()])) and all(
        method[left.end():right.start()].strip().lower() in RUN_LIST for left, right in zip(trailing, trailing[1:]))


def plain_clauses(method: str, start: int) -> bool:
    """After the status: only clauses introduced by , ; or "and" that each state plain output or files."""
    # Trailing stops and one closing parenthesis, stripped by hand: a regex with a lazy group before optional
    # whitespace backtracked for seconds on a long run of spaces (2026-10-06 review).
    rest = method[start:].rstrip().rstrip(".").rstrip()
    if rest.endswith(")"):
        if method[:start].count("(") <= method[:start].count(")"):
            return False  # a ")" the method never opened
        rest = rest[:-1].rstrip().rstrip(".").rstrip()
    first, *clauses = SEPARATOR.split(rest)
    return not first and all(PLAIN.search(clause) and not NOT_PLAIN.search(clause) for clause in clauses)
