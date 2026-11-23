"""Ignored version files a project's own build writes during an in-place run.

An in-place run's proof copies into its clean test copies only the ignored generated sources the
checkout held, byte for byte, when the run started (autocode_launch_inputs, #529): an ignored file the
run wrote could otherwise poison the base copy or repair a broken candidate. A Python project built
with hatch-vcs or setuptools-scm, though, imports a version module its build writes and git ignores
(Humanize's ``src/humanize/_version.py``). A source checkout has none until something builds or
installs the project, and a Builder that runs ``pip install -e .`` writes it, or rewrites it with a new
local version. Every clean copy then lacked the module, the planned test command failed there at
collection, and the run stopped asking the person about AutoCode's own machinery (benchmark pilot,
2026-11-23).

Such a file is admitted to the test copies, from its current bytes, only when both of these hold:

- ``declared``: the base revision's own packaging configuration (``pyproject.toml``, ``setup.cfg``
  or ``setup.py`` in the file's directory or any directory above it, as committed at the run's base)
  names the file's path, quoted, relative to that configuration. The base is pinned before any
  Builder runs, so the run cannot add the declaration, and it never names a path another module
  could be shadowed from.
- ``inert``: the file is a Python module that holds only data: constants, tuples, lists, dicts and
  type annotations, names bound to those, ``if`` blocks over names, and imports from ``typing`` and
  ``__future__``. Importing it calls nothing, so it cannot patch the code under test.

Anything else stays out, as before. The decision is AutoCode's own and is recorded in the proof's notes.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import PurePosixPath

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

PACKAGING = ("pyproject.toml", "setup.cfg", "setup.py")
READ_LIMIT = 1_000_000
_IMPORTABLE = frozenset({"typing", "__future__"})


def _committed(checkout, base, path) -> str | None:
    """The text of ``path`` at revision ``base``, or None when it is absent or unreadable."""
    try:
        shown = subprocess.run(
            ["git", "-C", str(checkout), "cat-file", "blob", f"{base}:{path}"],
            capture_output=True,
            timeout=30,
            env=util.git_environment(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if shown.returncode or len(shown.stdout) > READ_LIMIT:
        return None
    return shown.stdout.decode("utf-8", errors="replace")


def declaration(checkout, base, name) -> str | None:
    """The base revision's packaging file that names ``name`` as a quoted path, or None."""
    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base):
        return None
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    for directory in reversed(path.parents):  # the repository root first
        relative = path.relative_to(directory).as_posix()
        quoted = re.compile(r"""(["'])(?:\./)?""" + re.escape(relative) + r"\1")
        for config in PACKAGING:
            location = config if directory == PurePosixPath(".") else f"{directory.as_posix()}/{config}"
            text = _committed(checkout, base, location)
            if text is not None and quoted.search(text):
                return location
    return None


def _data(node, bound) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_data(item, bound) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and _data(key, bound) for key in node.keys) and all(
            _data(value, bound) for value in node.values
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return isinstance(node.operand, ast.Constant)
    if isinstance(node, ast.Name):
        return node.id in bound or node.id == "object"
    if isinstance(node, ast.Subscript):  # a type alias: Tuple[Union[int, str], ...]
        return isinstance(node.value, ast.Name) and node.value.id in bound and _annotation(node.slice)
    return False


def _annotation(node) -> bool:
    """A type expression made only of names, constants and subscripts; never evaluated at import."""
    if isinstance(node, (ast.Name, ast.Constant)):
        return True
    if isinstance(node, ast.Attribute):
        return _annotation(node.value)
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_annotation(item) for item in node.elts)
    if isinstance(node, ast.Subscript):
        return _annotation(node.value) and _annotation(node.slice)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _annotation(node.left) and _annotation(node.right)
    return False


def _target(node) -> bool:
    if isinstance(node, ast.Name):
        return not (node.id.startswith("__") and node.id.endswith("__")) or node.id in (
            "__all__",
            "__version__",
            "__version_tuple__",
            "__commit_id__",
        )
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_target(item) for item in node.elts)
    return False


def _names(node):
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, (ast.Tuple, ast.List)):
        for item in node.elts:
            yield from _names(item)


def _statements(body, bound) -> bool:
    for statement in body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue
        if isinstance(statement, ast.ImportFrom):
            if statement.level or statement.module not in _IMPORTABLE:
                return False
            if any(alias.name == "*" for alias in statement.names):
                return False
            bound |= {alias.asname or alias.name for alias in statement.names}
        elif isinstance(statement, ast.Import):
            if any(alias.name not in _IMPORTABLE for alias in statement.names):
                return False
            bound |= {alias.asname or alias.name for alias in statement.names}
        elif isinstance(statement, ast.Assign):
            if not all(_target(target) for target in statement.targets) or not _data(statement.value, bound):
                return False
            bound |= {name for target in statement.targets for name in _names(target)}
        elif isinstance(statement, ast.AnnAssign):
            if not _target(statement.target) or not _annotation(statement.annotation):
                return False
            if statement.value is not None and not _data(statement.value, bound):
                return False
            bound |= set(_names(statement.target))
        elif isinstance(statement, ast.If):
            if not isinstance(statement.test, (ast.Name, ast.Constant)):
                return False
            if not (_statements(statement.body, bound) and _statements(statement.orelse, bound)):
                return False
        else:
            return False
    return True


def inert(name, data) -> bool:
    """Whether ``data``, the bytes of ignored file ``name``, is a Python module that holds only data."""
    if not str(name).endswith(".py") or not isinstance(data, bytes) or len(data) > READ_LIMIT:
        return False
    try:
        tree = ast.parse(data.decode("utf-8"), filename=str(name))
    except (UnicodeDecodeError, SyntaxError, ValueError, RecursionError):
        return False
    return _statements(tree.body, set())


def admitted(checkout, base, name, data) -> str | None:
    """The packaging file that declares ``name`` when its current ``data`` may enter the test copies."""
    if not inert(name, data):
        return None
    return declaration(checkout, base, name)
