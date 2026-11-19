"""Deterministic first-order mutants of changed Go, JavaScript and TypeScript lines (#3817).

The Python mutants (autocode_mutants) find their sites in the AST. These languages have no parser
here, so this module reads the source as tokens: a scanner marks which bytes are code and which are
strings, comments, template literals or regular expressions, and each operator matches only code
bytes on a changed line. A mutant never adds or removes a line break, so every line number stays as
it was.

A splice cannot be compiled here. The run decides instead: a mutant that does not build or load
reports no complete result for its selected tests, so it ends ``not_run`` and never counts as
``killed`` (autocode_mutation_results.outcome). Only a selected test that ran and failed kills one.
Pure: bytes in, mutant descriptions out. Imports nothing from AutoCode except the Mutant record.

Operators, each only on a changed line and only on code bytes:

* ``negate_condition``: the condition of a one-line ``if`` becomes its negation (Go ``if !(c) {``,
  JavaScript ``if (!(c))``). A Go ``if`` with an init statement (``if err := f(); err != nil``) is left
  to ``negate_equality``.
* ``swap_bound``: a spaced ``<`` and ``<=``, or ``>`` and ``>=``, trade places. Spaces on both sides
  keep generics (``Map<K, V>``), shifts, arrows and Go's channel ``<-`` out.
* ``negate_equality``: a spaced ``==`` and ``!=`` (``===`` and ``!==``) trade places.
* ``swap_logic``: a spaced ``&&`` and ``||`` trade places.
* ``swap_arithmetic``: a spaced ``+`` and ``-`` trade places, except next to a string literal.
* ``flip_boolean``: the word ``true`` becomes ``false`` and the reverse (not a member, ``.true``).
* ``constant_return``: a one-line ``return VALUE`` returns nothing useful: JavaScript ``undefined``;
  Go's last returned value becomes ``nil`` when it is a plain name (``return nil, err`` becomes
  ``return nil, nil``). A name that cannot be ``nil`` fails to build, which proves nothing.
* ``drop_call``: a call made as a whole statement on its own line (``mu.Lock()``, ``save(x);``) is
  blanked, so it is never made. Only when the previous code line ended a statement.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

try:
    from .autocode_mutants import Mutant, scaled_limit
except ImportError:
    from autocode_mutants import Mutant, scaled_limit

OPERATORS = (
    "negate_condition",
    "swap_bound",
    "negate_equality",
    "swap_logic",
    "swap_arithmetic",
    "flip_boolean",
    "constant_return",
    "drop_call",
)
LANGUAGES = {
    ".go": "go",
    **{suffix: "js" for suffix in (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx")},
}
DISPLAY_CHARS = 60
# A regular expression literal may start after one of these, or after one of the keywords below.
REGEX_AFTER = set(b"(,=:[!&|?{};+-*%<>~^")
REGEX_KEYWORDS = (b"return", b"typeof", b"case", b"do", b"else", b"in", b"of", b"void", b"yield", b"await")
WORD = re.compile(rb"[A-Za-z0-9_$]")
SPACED = {
    "swap_bound": {b"<": b"<=", b"<=": b"<", b">": b">=", b">=": b">"},
    "negate_equality": {b"==": b"!=", b"!=": b"==", b"===": b"!==", b"!==": b"==="},
    "swap_logic": {b"&&": b"||", b"||": b"&&"},
    "swap_arithmetic": {b"+": b"-", b"-": b"+"},
}
SPACED_TOKEN = re.compile(rb"(?<=[ \t])(===|!==|==|!=|<=|>=|&&|\|\||<|>|\+|-)(?=[ \t])")
BOOLEAN = re.compile(rb"(?<![A-Za-z0-9_$.])(true|false)(?![A-Za-z0-9_$])")
GO_IF = re.compile(rb"^(\s*(?:\}\s*else\s+)?if\s+)(.+?)(\s*\{\s*)$")
JS_IF = re.compile(rb"^\s*(?:\}\s*)?(?:else\s+)?if\s*\(")
RETURN = re.compile(rb"^(\s*return\s+)(.+?)(;?\s*)$")
CALL = re.compile(rb"^(\s*)((?:await\s+)?[A-Za-z_$][A-Za-z0-9_$]*(?:\??\.[A-Za-z_$][A-Za-z0-9_$]*)*\(.*\));?\s*$")
STATEMENT_END = set(b"{};)]\"'`") | set(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")
NOT_VALUES = {b"nil", b"true", b"false", b"undefined", b"null", b"iota"}


@dataclass(frozen=True)
class _Site:
    operator: str
    start: int
    end: int
    replacement: bytes
    shown: bytes = b""


def language(path: str) -> str | None:
    """``go``, ``js`` (JavaScript and TypeScript) or None for a path this module does not mutate."""
    dot = path.rfind(".")
    return LANGUAGES.get(path[dot:].lower()) if dot > path.rfind("/") else None


def text_mutants(
    path: str, source_bytes: bytes, changed_lines: Iterable[int], limit: int | None = None
) -> tuple[list[Mutant], list[str]]:
    """At most ``limit`` mutants of the file at ``path`` on ``changed_lines``, and notes.

    The same contract as ``autocode_mutants.python_mutants``: the limit scales with changed lines when
    None, sites spread over operators and lines, and the result is sorted by line.
    """
    kind = language(path)
    if kind is None:
        return [], [f"{path}: not a Go, JavaScript or TypeScript source file"]
    if path.endswith((".d.ts", ".d.mts", ".d.cts")):
        return [], [f"{path}: a type declaration has no runtime behavior to mutate"]
    try:
        source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return [], [f"{path}: is not valid UTF-8; no mutants"]
    if b"\r" in source_bytes.replace(b"\r\n", b""):
        return [], [
            f"{path}: has a carriage return without a line feed, which git does not count as a line break; no mutants"
        ]
    lines = sorted(set(changed_lines))
    if not lines:
        return [], []
    if limit is None:
        limit = scaled_limit(len(lines))
    code = _code_mask(source_bytes, kind)
    starts = [0] + [index + 1 for index, byte in enumerate(source_bytes) if byte == 0x0A]
    wanted = set(lines)
    numbered = []
    for number in range(1, len(starts) + 1):
        if number not in wanted:
            continue
        begin = starts[number - 1]
        end = starts[number] - 1 if number < len(starts) else len(source_bytes)
        text = source_bytes[begin:end].rstrip(b"\r")
        ordinals: dict[str, int] = {}
        for site in _sites(kind, source_bytes, code, starts, number, begin, text):
            ordinals[site.operator] = ordinals.get(site.operator, 0) + 1
            numbered.append((number, ordinals[site.operator], site))
    numbered.sort(key=lambda row: (row[2].start, OPERATORS.index(row[2].operator)))
    digest = hashlib.sha256(source_bytes).hexdigest()
    chosen: list[Mutant] = []
    for number, ordinal, site in _spread(numbered):
        if len(chosen) >= limit:
            break
        chosen.append(
            Mutant(
                id=f"{path}:{number}:{site.operator}:{ordinal}",
                path=path,
                line=number,
                end_line=number,
                operator=site.operator,
                before=_display(source_bytes[site.start : site.end]),
                after=_display(site.shown or site.replacement),
                start=site.start,
                end=site.end,
                replacement=site.replacement,
                source_sha256=digest,
            )
        )
    notes = []
    if len(numbered) > limit:
        notes.append(f"{path}: {len(numbered)} mutation sites on the changed lines; {len(chosen)} were chosen")
    chosen.sort(key=lambda mutant: (mutant.line, OPERATORS.index(mutant.operator), mutant.start))
    return chosen, notes


def _sites(kind, body, code, starts, number, begin, text):
    """Every site on one line, in operator order. ``text`` is the line without its line break.

    The patterns read ``shape``: the line with every byte that is not code (a string, a comment) as
    NUL, so a quoted ``<`` or ``//`` is never a site and a string argument never ends a statement.
    """
    shape = bytes(byte if code[begin + index] else 0 for index, byte in enumerate(text))
    if not shape.strip(b" \t\0") or not shape.strip(b" \t")[:1].strip(b"\0"):
        return
    yield from _condition(kind, text, shape, begin)
    for match in SPACED_TOKEN.finditer(shape):
        token = match.group(1)
        operator = next(name for name, table in SPACED.items() if token in table)
        if operator == "swap_arithmetic" and _beside_string(text, match.start(), match.end()):
            continue
        yield _Site(operator, begin + match.start(), begin + match.end(), SPACED[operator][token])
    for match in BOOLEAN.finditer(shape):
        flipped = b"false" if match.group(1) == b"true" else b"true"
        yield _Site("flip_boolean", begin + match.start(), begin + match.end(), flipped)
    statement = _without_comment(text, shape)
    if not _balanced(statement):
        return
    returned = RETURN.match(statement)
    if returned:
        yield from _return(kind, text, returned, begin)
    call = CALL.match(statement)
    if call and _ends_statement(body, code, starts, number):
        dropped = text[call.start(2) : len(statement.rstrip())]
        start = begin + call.start(2)
        yield _Site("drop_call", start, start + len(dropped), b" " * len(dropped), b"(removed)")


def _without_comment(text, shape):
    """``shape`` up to a trailing ``//`` or ``/*`` comment, so a commented statement keeps its sites."""
    code_end = len(shape.rstrip(b" \t\0"))
    tail = text[code_end:].lstrip(b" \t")
    return shape[:code_end] if tail.startswith((b"//", b"/*")) else shape


def _condition(kind, text, shape, begin):
    if kind == "go":
        match = GO_IF.match(shape)
        if not match or b";" in match.group(2) or not _balanced(match.group(2)):
            return
        condition = text[match.start(2) : match.end(2)]
        yield _Site("negate_condition", begin + match.start(2), begin + match.end(2), b"!(" + condition + b")")
        return
    match = JS_IF.match(shape)
    if not match:
        return
    opening = match.end() - 1
    closing = _closing(shape, opening)
    if closing is None:
        return
    condition = text[opening + 1 : closing]
    if condition.strip():
        yield _Site("negate_condition", begin + opening + 1, begin + closing, b"!(" + condition + b")")


def _return(kind, text, match, begin):
    value = text[match.start(2) : match.end(2)].rstrip()
    if kind == "js":
        if value not in NOT_VALUES and value != b"void 0":
            yield _Site("constant_return", begin + match.start(2), begin + match.start(2) + len(value), b"undefined")
        return
    last = value.rsplit(b",", 1)[-1]
    offset = len(value) - len(last)
    name = last.strip()
    if re.fullmatch(rb"[A-Za-z_][A-Za-z0-9_]*", name) and name not in NOT_VALUES:
        start = begin + match.start(2) + offset + (len(last) - len(last.lstrip()))
        yield _Site("constant_return", start, start + len(name), b"nil")


def _ends_statement(body, code, starts, number):
    """Whether the code before line ``number`` ends a statement, so the line is a statement of its own."""
    index = starts[number - 1] - 1
    while index >= 0:
        byte = body[index]
        if byte in b" \t\r\n" or not code[index]:
            index -= 1
            continue
        return byte in STATEMENT_END
    return True


def _beside_string(text, start, end):
    before, after = text[:start].rstrip()[-1:], text[end:].lstrip()[:1]
    return before in (b'"', b"'", b"`") or after in (b'"', b"'", b"`")


def _balanced(text):
    depth = 0
    for byte in text:
        if byte in b"([{":
            depth += 1
        elif byte in b")]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _closing(text, opening):
    depth = 0
    for index in range(opening, len(text)):
        if text[index] in b"([{":
            depth += 1
        elif text[index] in b")]}":
            depth -= 1
            if depth == 0:
                return index if text[index] == ord(")") else None
    return None


def _code_mask(body: bytes, kind: str) -> list[bool]:
    """``mask[i]``: whether byte ``i`` is code, not part of a string, comment or literal pattern."""
    mask = [True] * len(body)
    index, size = 0, len(body)

    def mark(start, stop):
        for position in range(start, min(stop, size)):
            mask[position] = False

    while index < size:
        byte = body[index]
        pair = body[index : index + 2]
        if pair == b"//":
            stop = body.find(b"\n", index)
            stop = size if stop < 0 else stop
            mark(index, stop)
            index = stop
        elif pair == b"/*":
            stop = body.find(b"*/", index + 2)
            stop = size if stop < 0 else stop + 2
            mark(index, stop)
            index = stop
        elif byte == ord("`"):
            stop = _quoted(body, index, b"`", escapes=kind == "js", lines=True)
            mark(index, stop)
            index = stop
        elif byte in b"\"'":
            stop = _quoted(body, index, bytes([byte]), escapes=True, lines=False)
            mark(index, stop)
            index = stop
        elif kind == "js" and byte == ord("/") and _regex_start(body, mask, index):
            stop = _regex_end(body, index)
            if stop is None:
                index += 1
                continue
            mark(index, stop)
            index = stop
        else:
            index += 1
    return mask


def _quoted(body, start, quote, *, escapes, lines):
    index = start + 1
    while index < len(body):
        byte = body[index]
        if escapes and byte == ord("\\"):
            index += 2
            continue
        if byte == quote[0]:
            return index + 1
        if byte == ord("\n") and not lines:
            return index
        index += 1
    return len(body)


def _regex_start(body, mask, index):
    previous = index - 1
    while previous >= 0 and body[previous] in b" \t":
        previous -= 1
    if previous < 0 or body[previous] in b"\r\n":
        return True
    if not mask[previous]:
        return False
    if body[previous] in REGEX_AFTER:
        return True
    word_end = previous + 1
    while previous >= 0 and WORD.fullmatch(body[previous : previous + 1]):
        previous -= 1
    return body[previous + 1 : word_end] in REGEX_KEYWORDS


def _regex_end(body, start):
    index, in_class = start + 1, False
    while index < len(body) and body[index] != ord("\n"):
        byte = body[index]
        if byte == ord("\\"):
            index += 2
            continue
        if byte == ord("["):
            in_class = True
        elif byte == ord("]"):
            in_class = False
        elif byte == ord("/") and not in_class:
            index += 1
            while index < len(body) and WORD.fullmatch(body[index : index + 1]):
                index += 1
            return index
        index += 1
    return None


def _spread(numbered):
    """Round-robin over the operators; each pick is that operator's first site on a line not yet used."""
    queues = {operator: [row for row in numbered if row[2].operator == operator] for operator in OPERATORS}
    used: set[int] = set()
    while any(queues.values()):
        for operator in OPERATORS:
            queue = queues[operator]
            if not queue:
                continue
            index = next((position for position, row in enumerate(queue) if row[0] not in used), 0)
            row = queue.pop(index)
            used.add(row[0])
            yield row


def _display(data: bytes) -> str:
    text = " ".join(data.decode("utf-8", "replace").split())
    return text if len(text) <= DISPLAY_CHARS else text[: DISPLAY_CHARS - 3] + "..."
