"""What a change did to one original Python test file, read from its two texts and never executed.

A rename or a removal the person asked for changes what a test file imports (`from pager import page_count`
becomes `count_pages`; `legacy_total` goes). The original-test gate re-runs the original file against the
change (autocode_protected_oracles.replay), and that file can no longer load: no test in it runs, so no
result says which of its tests the change left alone. On real runs (2026-11-14) a rename across files and
the removal of a deprecated function with its tests both delivered correct code and stopped for the person.

``summary`` answers the question the run could not: which of the original file's tests the change altered
(``altered``: changed, moved or gone), and whether everything around the others is as it was (``intact``).
The gate accepts an original file that cannot load only when every altered test is one the approved plan
replaces and the rest is intact (autocode_superseded_tests.excused), so a test the plan does not list is
still the test the project had, with the fixtures and module code it had, and it passed with the change.

Intact means, compared statement by statement as syntax trees (comments and layout do not count):

- every other top-level statement (an assignment, a call, an ``if`` block, a guarded import) is unchanged
  and none was added;
- a class the candidate keeps has the header and every member that is not a test unchanged (``setUp``, a
  helper, a class attribute); it may gain new tests;
- a function that is not a test is unchanged if kept; one may be removed when nothing else bound its name;
- an import may go or lose names; a new import, class or function may only bind a name the original file
  did not bind, so nothing a kept test reads is rebound.

Pure: imports nothing from AutoCode.
"""

from __future__ import annotations

import ast
import copy

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_Function = ast.FunctionDef | ast.AsyncFunctionDef


def _is_test(node) -> bool:
    return isinstance(node, _FUNCTIONS) and node.name.startswith("test")


def _dump(node) -> str:
    return ast.dump(node)


def _imports(node) -> list[tuple]:
    """Each name an import statement binds, as (local name, what it is bound to)."""
    if isinstance(node, ast.Import):
        return [
            (alias.asname or alias.name.split(".")[0], ("import", alias.name, alias.asname)) for alias in node.names
        ]
    return [
        (alias.asname or alias.name, ("from", node.level, node.module, alias.name, alias.asname))
        for alias in node.names
    ]


class _File:
    """A test file's top-level statements by kind."""

    def __init__(self, text: str):
        self.imports: list[tuple] = []
        self.classes: dict[str, ast.ClassDef] = {}
        self.functions: dict[str, _Function] = {}
        self.other: list[str] = []
        self.bound: list[str] = []
        self.readable = True
        for node in ast.parse(text).body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.imports += _imports(node)
                self.bound += [name for name, _ in _imports(node)]
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in self.classes or node.name in self.functions:
                    self.readable = False  # a name defined twice: which definition a test is, is not ours to say
                if isinstance(node, ast.ClassDef):
                    self.classes[node.name] = node
                else:
                    self.functions[node.name] = node
                self.bound.append(node.name)
            else:
                self.other.append(_dump(node))
                self.bound += [
                    found.id
                    for found in ast.walk(node)
                    if isinstance(found, ast.Name) and isinstance(found.ctx, ast.Store)
                ]


def _frame(node: ast.ClassDef) -> str:
    """A class without its tests: its header and every other member."""
    frame = copy.copy(node)
    frame.body = [member for member in node.body if not _is_test(member)]
    return _dump(frame)


def _class_tests(node: ast.ClassDef) -> dict[str, str] | None:
    tests: dict[str, str] = {}
    for member in node.body:
        if isinstance(member, _FUNCTIONS) and _is_test(member):
            if member.name in tests:
                return None
            tests[member.name] = _dump(member)
    return tests


def summary(original: str, candidate: str | None) -> dict | None:
    """``tests`` (the original file's test names), ``altered`` (those the candidate text does not define as
    the original did) and ``intact`` (see the module docstring); None when a text is not Python this reads.
    A ``candidate`` of None is a deleted file: every test is altered and nothing is left to keep intact."""
    try:
        before = _File(original)
        after = _File(candidate) if candidate is not None else None
    except (SyntaxError, ValueError, RecursionError):
        return None
    if not before.readable or (after is not None and not after.readable):
        return None
    tests: set[str] = set()
    altered: set[str] = set()
    intact = True
    for name, node in before.classes.items():
        own = _class_tests(node)
        if own is None:
            return None
        tests |= set(own)
        kept = after.classes.get(name) if after is not None else None
        if kept is None:
            altered |= set(own)
            # A class with no test is a helper: removing it is a change to the rest of the file.
            intact = intact and (bool(own) or after is None)
            continue
        now = _class_tests(kept)
        if now is None:
            return None
        altered |= {test for test, code in own.items() if now.get(test) != code}
        intact = intact and _frame(node) == _frame(kept)
    for name, function in before.functions.items():
        still = after.functions.get(name) if after is not None else None
        same = still is not None and _dump(still) == _dump(function)
        if _is_test(function):
            tests.add(name)
            if not same:
                altered.add(name)
        elif still is not None:
            intact = intact and same
        elif after is not None:
            intact = intact and before.bound.count(name) == 1
    if after is not None:
        intact = intact and before.other == after.other
        had = set(before.imports)
        bound = set(before.bound)
        for local, target in after.imports:
            intact = intact and ((local, target) in had or (local not in bound and local != "*"))
        for name in [*after.classes, *after.functions]:
            if name not in before.classes and name not in before.functions:
                intact = intact and name not in bound
        # A class the original had as a function, or the other way round, is a new definition of a bound name.
        intact = intact and not (
            set(after.classes) & set(before.functions) or set(after.functions) & set(before.classes)
        )
    return {"tests": sorted(tests), "altered": sorted(altered), "intact": bool(intact)}
