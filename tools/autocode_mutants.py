"""Deterministic first-order mutants of a Python file's changed lines (#699).

Pure: bytes in, mutant descriptions out. A mutant replaces the bytes of one AST node's span, so
comments, formatting, docstrings and every line number stay as they were; the AST only finds the
sites and checks the result. Every mutant compiles, keeps the file's line count, and changes its
statement exactly as its operator intends; a site that cannot be built or checked (a statement too
deep to copy, an integer too long to print) is skipped with a note. The same bytes and lines give
the same mutants in the same order on every supported Python. Imports nothing from AutoCode: the
caller says which lines changed and runs the tests.

Operators, each only on a span that touches a changed line:

* ``negate_condition``: the test of an ``if``, ``while``, conditional expression, ``assert``,
  comprehension filter or ``case`` guard becomes ``not (...)``.
* ``swap_bound``: ``<`` and ``<=``, or ``>`` and ``>=``, trade places in a comparison.
* ``drop_branch``: an ``if`` body, or its ``else`` block (not an ``elif``), becomes ``pass``.
* ``constant_return``: a returned value becomes a constant (a bool its opposite, a number 0 or 1,
  a string empty or ``"x"``, anything else ``None``).
* ``shift_bound``: an integer literal on either side of ``<``, ``<=``, ``>`` or ``>=`` becomes one more
  (an ``==``, ``!=``, ``is`` or ``in`` operand is not a bound and is left alone).
* ``constant_assignment``: a bool, number or string literal assigned to a name (``TEXT = "hello"``,
  ``LIMIT: int = 3``) becomes another constant, as ``constant_return`` chooses it. A line whose whole
  behavior is the value it binds has no other site, and a test that reads the value sees the change.

``TYPE_CHECKING`` and ``__main__`` guards, f-strings and annotations are never mutated, and a
branch that already does nothing is never dropped: those mutants change nothing a test can see.
A file with a carriage return that no line feed follows is not mutated: Python counts it as a line
break and git does not, so the changed line numbers cannot be mapped.
"""

from __future__ import annotations

import ast
import codecs
import copy
import hashlib
import io
import re
import tokenize
import warnings
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

OPERATORS = ("negate_condition", "swap_bound", "drop_branch", "constant_return", "shift_bound", "constant_assignment")
PER_FILE_LIMIT = 6
DISPLAY_CHARS = 60
NEWLINE = re.compile(rb"\r\n|\r|\n")
LONE_CR = re.compile(rb"\r(?!\n)")
IDENTIFIER = re.compile(rb"[A-Za-z0-9_\x80-\xff]")


def _keyword(body: bytes, start: int, word: bytes) -> bool:
    """Whether ``word`` starts at ``start`` as a whole word (``elif``, not ``elif_x``)."""
    end = start + len(word)
    return body.startswith(word, start) and not IDENTIFIER.fullmatch(body[end : end + 1])


UNBUILT = (RecursionError, ValueError, MemoryError)  # a statement too deep to copy, an integer too long to print
UTF8_COMPATIBLE = ("utf-8", "utf-8-sig", "ascii")  # ascii: Python already refused any byte above 0x7f
BOM = b"\xef\xbb\xbf"
COMPARISON = re.compile(rb"<=|>=|<|>")
COMMENT = re.compile(rb"#[^\r\n]*")
SWAPS = {ast.Lt: (ast.LtE, b"<="), ast.LtE: (ast.Lt, b"<"), ast.Gt: (ast.GtE, b">="), ast.GtE: (ast.Gt, b">")}


@dataclass(frozen=True)
class Mutant:
    """``source[start:end]`` becomes ``replacement``; ``line`` is the first line it replaces.

    ``before`` and ``after`` are the replaced and replacing text for display, on one line and
    truncated. ``source_sha256`` names the exact bytes the offsets belong to (see apply).
    """

    id: str
    path: str
    line: int
    end_line: int
    operator: str
    before: str
    after: str
    start: int
    end: int
    replacement: bytes
    source_sha256: str


@dataclass(frozen=True)
class _Site:
    operator: str
    start: int
    end: int
    replacement: bytes
    target: ast.AST
    detail: object
    shown: bytes = b""


def apply(source: bytes, mutant: Mutant) -> bytes:
    """``source`` with ``mutant`` applied. Refuses bytes other than the ones the mutant was made from."""
    if hashlib.sha256(source).hexdigest() != mutant.source_sha256:
        raise ValueError(f"{mutant.path} is not the file its mutants were made from")
    return source[: mutant.start] + mutant.replacement + source[mutant.end :]


def changed_lines(file_hunks) -> dict[str, frozenset[int]]:
    """``{path: changed line numbers}`` from autocode_diff_coverage.hunks rows (``path``, ``hunks``)."""
    lines: dict[str, set[int]] = {}
    for item in file_hunks or []:
        numbers = lines.setdefault(item["path"], set())
        for hunk in item.get("hunks") or []:
            numbers.update(range(int(hunk["start"]), int(hunk["end"]) + 1))
    return {path: frozenset(numbers) for path, numbers in lines.items()}


def python_mutants(
    path: str, source_bytes: bytes, changed_lines: Iterable[int], limit: int = PER_FILE_LIMIT
) -> tuple[list[Mutant], list[str]]:
    """At most ``limit`` mutants of ``source_bytes`` (the file at ``path``) on ``changed_lines``, and notes.

    Sites are ordered by position, then picked round-robin over the operators, each pick preferring a
    line no earlier pick used, so a small limit still spreads over operators and lines. A site whose
    splice does not parse or compile, changes the line count or changes more than intended, or that
    cannot be checked at all, is skipped with a note and the next one takes its place. The result is
    sorted by line.
    """
    if path.endswith(".pyi"):
        return [], [f"{path}: a type stub has no runtime behavior to mutate"]
    if not path.endswith(".py"):
        return [], [f"{path}: not a Python source file"]
    try:
        encoding = tokenize.detect_encoding(io.BytesIO(source_bytes).readline)[0]
    except SyntaxError:
        return [], [f"{path}: its encoding cannot be read (not UTF-8, or a bad declaration); no mutants"]
    if _codec(encoding) not in UTF8_COMPATIBLE:
        return [], [f"{path}: declares the {encoding} encoding; only UTF-8 files are mutated"]
    body = source_bytes[len(BOM) :] if source_bytes.startswith(BOM) else source_bytes
    prefix = len(source_bytes) - len(body)
    try:
        body.decode("utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # the product's own SyntaxWarnings are not the runner's
            tree = ast.parse(body)
    except UnicodeDecodeError:
        return [], [f"{path}: is not valid UTF-8; no mutants"]
    except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
        return [], [f"{path}: does not parse with this Python ({type(error).__name__}); no mutants"]
    lines = sorted(set(changed_lines))
    if not lines:
        return [], []
    if LONE_CR.search(body):
        return [], [
            f"{path}: has a carriage return without a line feed, which git does not count as a line break; no mutants"
        ]
    starts = [0] + [match.end() for match in NEWLINE.finditer(body)]
    notes: list[str] = []
    sites = sorted(
        _sites(tree, body, starts, lines, notes, path),
        key=lambda site: (site.start, OPERATORS.index(site.operator), site.end, site.replacement),
    )
    ordinals: dict[tuple[int, str], int] = {}
    numbered = []
    for site in sites:
        line = bisect_right(starts, site.start)
        ordinals[(line, site.operator)] = ordinals.get((line, site.operator), 0) + 1
        numbered.append((line, ordinals[(line, site.operator)], site))
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    digest = hashlib.sha256(source_bytes).hexdigest()
    chosen: list[Mutant] = []
    seen: set[tuple] = set()
    for line, ordinal, site in _spread(numbered):
        if len(chosen) >= limit:
            break
        if site.start and IDENTIFIER.fullmatch(body[site.start - 1 : site.start]):
            site = replace(site, replacement=b" " + site.replacement)  # `return(a, b)` must not become `returnNone`
        try:
            problem, key = _check(tree, body, site, parents)
        except UNBUILT as error:
            problem, key = f"could not be checked ({type(error).__name__})", None
        if problem:
            notes.append(f"{path}:{line}: {site.operator} {problem}; skipped")
            continue
        if key is None or key in seen:
            continue
        seen.add(key)
        chosen.append(
            Mutant(
                id=f"{path}:{line}:{site.operator}:{ordinal}",
                path=path,
                line=line,
                end_line=bisect_right(starts, max(site.start, site.end - 1)),
                operator=site.operator,
                before=_display(body[site.start : site.end]),
                after=_display(site.shown or site.replacement),
                start=prefix + site.start,
                end=prefix + site.end,
                replacement=site.replacement,
                source_sha256=digest,
            )
        )
    if len(numbered) > limit:
        notes.append(f"{path}: {len(numbered)} mutation sites on the changed lines; {len(chosen)} were chosen")
    chosen.sort(key=lambda mutant: (mutant.line, OPERATORS.index(mutant.operator), mutant.start))
    return chosen, notes


def _codec(encoding: str) -> str:
    """The codec's canonical name: a cookie may spell UTF-8 ``utf8`` or ``U8``."""
    try:
        return codecs.lookup(encoding).name
    except LookupError:
        return encoding


def _display(data: bytes) -> str:
    text = " ".join(data.decode("utf-8", "replace").split())
    return text if len(text) <= DISPLAY_CHARS else text[: DISPLAY_CHARS - 3] + "..."


def _touches(lines: list[int], first: int, last: int) -> bool:
    index = bisect_left(lines, first)
    return index < len(lines) and lines[index] <= last


def _guard(test: ast.AST) -> bool:
    """``if TYPE_CHECKING:`` or ``if __name__ == "__main__":``: no test can see either change."""
    names = {node.id for node in ast.walk(test) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(test) if isinstance(node, ast.Attribute)}
    constants = {node.value for node in ast.walk(test) if isinstance(node, ast.Constant)}
    return "TYPE_CHECKING" in names or ("__name__" in names and "__main__" in constants)


def _inert(block: list[ast.stmt]) -> bool:
    return all(
        isinstance(statement, ast.Pass)
        or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
        for statement in block
    )


def _pad(new: bytes, old: bytes, *, expression: bool) -> bytes:
    """Keep the line count: an expression keeps its line breaks inside parentheses, a block after ``pass``."""
    breaks = NEWLINE.findall(old)
    if not breaks:
        return new
    return (b"(" + new + b"".join(breaks) + b")") if expression else new + b"".join(breaks)


def _constant(value: ast.expr) -> tuple[object, bytes]:
    if isinstance(value, ast.Constant):
        literal = value.value
        if type(literal) is bool:
            return (not literal), repr(not literal).encode()
        if type(literal) in (int, float):
            return (0, b"0") if literal else (1, b"1")
        if type(literal) is str:
            return ("", b'""') if literal else ("x", b'"x"')
    return None, b"None"


def _sites(tree, body, starts, lines, notes, path):
    """Every candidate splice on the changed lines, in ast.walk order. A node whose sites cannot be built
    is skipped with a note; the other nodes still give theirs."""
    excluded: set[int] = set()
    for node in ast.walk(tree):
        roots = [node] if isinstance(node, ast.JoinedStr) else []
        roots += [getattr(node, name, None) for name in ("annotation", "returns")]
        for root in roots:
            if isinstance(root, ast.AST):
                excluded.update(id(inner) for inner in ast.walk(root))

    def offset(line, column):
        return starts[line - 1] + column

    def span(node):
        return offset(node.lineno, node.col_offset), offset(node.end_lineno, node.end_col_offset)

    def statement_start(statement):
        """Where a statement's source begins: a decorated def or class begins at its first ``@``."""
        decorators = getattr(statement, "decorator_list", None)
        if decorators:
            return body.rfind(b"@", 0, offset(decorators[0].lineno, decorators[0].col_offset))
        return offset(statement.lineno, statement.col_offset)

    def node_sites(node):
        tests = []
        if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            tests.append(node.test)
        elif isinstance(node, ast.comprehension):
            tests.extend(node.ifs)
        elif isinstance(node, ast.match_case) and node.guard is not None:
            tests.append(node.guard)
        for test in tests:
            if id(test) not in excluded and not _guard(test) and _touches(lines, test.lineno, test.end_lineno):
                start, end = span(test)
                yield _Site("negate_condition", start, end, b"not (" + body[start:end] + b")", test, None)
        if isinstance(node, ast.Compare) and id(node) not in excluded and _touches(lines, node.lineno, node.end_lineno):
            operands = [node.left, *node.comparators]
            for index, op in enumerate(node.ops):
                if type(op) not in SWAPS:
                    continue
                between_start = offset(operands[index].end_lineno, operands[index].end_col_offset)
                between_end = offset(operands[index + 1].lineno, operands[index + 1].col_offset)
                between = COMMENT.sub(lambda match: b" " * len(match.group()), body[between_start:between_end])
                found = list(COMPARISON.finditer(between))
                if len(found) != 1:
                    notes.append(f"{path}:{node.lineno}: comparison operator not located; skipped")
                    continue
                start, end = between_start + found[0].start(), between_start + found[0].end()
                yield _Site("swap_bound", start, end, SWAPS[type(op)][1], node, index)
            for index, operand in enumerate(operands):
                bounds = any(type(op) in SWAPS for op in node.ops[max(index - 1, 0) : index + 1])
                if bounds and isinstance(operand, ast.Constant) and type(operand.value) is int:
                    start, end = span(operand)
                    yield _Site("shift_bound", start, end, str(operand.value + 1).encode(), operand, None)
        if isinstance(node, ast.If) and not _guard(node.test):
            for branch in ("body", "orelse"):
                block = getattr(node, branch)
                start = statement_start(block[0]) if block else None
                if start is None or (branch == "orelse" and _keyword(body, start, b"elif")):
                    continue  # no else, or an elif: that If is mutated on its own
                first = node.test.lineno if branch == "body" else bisect_right(starts, start)
                if _inert(block) or not _touches(lines, first, block[-1].end_lineno):
                    continue
                end = span(block[-1])[1]
                yield _Site("drop_branch", start, end, _pad(b"pass", body[start:end], expression=False), node, branch)
        if isinstance(node, ast.Return) and node.value is not None and _touches(lines, node.lineno, node.end_lineno):
            if isinstance(node.value, ast.Constant) and node.value.value is None:
                return
            constant, text = _constant(node.value)
            start, end = span(node.value)
            replacement = _pad(text, body[start:end], expression=True)
            yield _Site("constant_return", start, end, replacement, node.value, constant, text)
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(node.value, ast.Constant)
            and type(node.value.value) in (bool, int, float, str)
            and id(node.value) not in excluded
            and _touches(lines, node.value.lineno, node.value.end_lineno)
        ):
            constant, text = _constant(node.value)
            start, end = span(node.value)
            replacement = _pad(text, body[start:end], expression=True)
            yield _Site("constant_assignment", start, end, replacement, node.value, constant, text)

    for node in ast.walk(tree):
        try:
            yield from node_sites(node)
        except UNBUILT as error:
            notes.append(
                f"{path}:{getattr(node, 'lineno', '?')}: a mutation site could not be built ({type(error).__name__}); skipped"
            )


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


def _check(tree, body, site, parents) -> tuple[str | None, tuple | None]:
    """(why the splice is not the intended mutant, or None; a key that identifies the mutant)."""
    spliced = body[: site.start] + site.replacement + body[site.end :]
    if len(NEWLINE.findall(spliced)) != len(NEWLINE.findall(body)):
        return "would change the line count", None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            parsed = ast.parse(spliced)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return "does not parse", None
        try:  # the symbol table too: a dropped binding can leave a `nonlocal` with nothing to name
            compile(parsed, "<mutant>", "exec", dont_inherit=True)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return "does not compile", None
    holder = site.target
    while holder is not None and not isinstance(holder, ast.stmt):
        holder = parents.get(id(holder))
    if holder is None or len(parsed.body) != len(tree.body):
        return "changes more than its statement", None
    twin = next(
        (
            node
            for node in ast.walk(parsed)
            if type(node) is type(holder) and (node.lineno, node.col_offset) == (holder.lineno, holder.col_offset)
        ),
        None,
    )
    if twin is None:
        return "changes more than its statement", None
    mutated = ast.dump(twin)
    if mutated != _intended(holder, site) or mutated == ast.dump(holder):
        return "does not make the intended change", None
    return None, (holder.lineno, holder.col_offset, mutated)


def _intended(holder, site) -> str:
    """The dump of ``holder`` with the site's mutation applied to a copy of its AST."""
    clone = copy.deepcopy(holder)
    twin: Any = next(
        copied for original, copied in zip(ast.walk(holder), ast.walk(clone), strict=True) if original is site.target
    )
    if site.operator == "negate_condition":
        _replace(clone, twin, ast.UnaryOp(op=ast.Not(), operand=twin))
    elif site.operator == "swap_bound":
        twin.ops[site.detail] = SWAPS[type(twin.ops[site.detail])][0]()
    elif site.operator == "shift_bound":
        twin.value = twin.value + 1
    elif site.operator == "drop_branch":
        setattr(twin, site.detail, [ast.Pass()])
    else:
        _replace(clone, twin, ast.Constant(value=site.detail))
    return ast.dump(clone)


def _replace(tree, old, new) -> None:
    for parent in ast.walk(tree):
        for field, value in ast.iter_fields(parent):
            if value is old:
                setattr(parent, field, new)
                return
            if isinstance(value, list):
                for index, item in enumerate(value):
                    if item is old:
                        value[index] = new
                        return
    raise LookupError("mutation target not found")
