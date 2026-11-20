"""Recognize installed pytest console launchers without executing a PATH wrapper.

Only the pytest entry call and pure, nonexpanding program-name cleanup qualify.
The paired interpreter and launcher's version probe stay contained.
"""

import ast
import re
from pathlib import Path


def _bodies():
    for module, entry in (("pytest", "console_main"), ("_pytest.config", "_console_main")):
        for imports in ("import sys", "import re\nimport sys"):
            text = f"{imports}\nfrom {module} import {entry}\nif __name__ == '__main__':\n    sys.exit({entry}())\n"
            yield ast.dump(ast.parse(text))


_BODIES = frozenset(_bodies())
_ARGV0 = ast.dump(ast.parse("sys.argv[0]", mode="eval").body)
_REMOVALS = frozenset(("removesuffix", "removeprefix", "rstrip", "lstrip", "strip"))


def _bound(node):
    return (
        node is None
        or isinstance(node, ast.Constant)
        and (node.value is None or type(node.value) is int)
        or isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.USub, ast.UAdd))
        and isinstance(node.operand, ast.Constant)
        and type(node.operand.value) is int
    )


def _string(node):
    """A string derived only from argv[0], without expansion or external calls."""
    if ast.dump(node) == _ARGV0:
        return True
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        return (
            _string(node.value)
            and all(_bound(value) for value in (node.slice.lower, node.slice.upper, node.slice.step))
            and not (isinstance(node.slice.step, ast.Constant) and node.slice.step.value == 0)
        )
    if not isinstance(node, ast.Call) or node.keywords:
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in _REMOVALS:
        arity = len(node.args) == 1 if func.attr in ("removesuffix", "removeprefix") else len(node.args) <= 1
        return (
            _string(func.value)
            and arity
            and all(isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in node.args)
        )
    # The historical distlib suffix regex is also a pure removal. Arbitrary
    # regexes are not a new launcher capability.
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "re"
        and func.attr == "sub"
        and len(node.args) == 3
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == r"(-script\.pyw|\.exe)?$"
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == ""
        and _string(node.args[2])
    )


def _cleanup(node):
    if isinstance(node, ast.Assign):
        return (
            len(node.targets) == 1
            and ast.dump(node.targets[0]).replace("Store()", "Load()") == _ARGV0
            and _string(node.value)
        )
    if not isinstance(node, ast.If) or node.orelse or len(node.body) != 1:
        return False
    test = node.test
    return (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Attribute)
        and test.func.attr in ("endswith", "startswith")
        and _string(test.func.value)
        and not test.keywords
        and len(test.args) == 1
        and isinstance(test.args[0], ast.Constant)
        and isinstance(test.args[0].value, str)
        and _cleanup(node.body[0])
    )


def _entry_body(body):
    tree = ast.parse(body)
    # Strip only proven-pure program-name cleanup inside the main guard. The
    # remaining imports, guard and exact pytest call must still match.
    if tree.body and isinstance(tree.body[-1], ast.If):
        guard = tree.body[-1]
        guard.body = [node for node in guard.body if not _cleanup(node)]
    return ast.dump(tree)


def pytest_interpreter(launcher: Path) -> Path:
    """Return the exact paired interpreter, refusing modified or unknown launchers."""
    problem = f"Cannot qualify the selected pytest console launcher: {launcher}; no model launched"
    try:
        with launcher.open(encoding="utf-8") as stream:
            text = stream.read(16385)
        if len(text) > 16384:
            raise ValueError("oversized launcher")
        header, _, body = text.partition("\n")
        interpreter = Path(header[2:])
        if (
            not header.startswith("#!/")
            or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", interpreter.name)
            or interpreter.parent.resolve() != launcher.resolve().parent
            or _entry_body(body) not in _BODIES
        ):
            raise ValueError("unknown launcher")
        return interpreter
    except (OSError, UnicodeError, SyntaxError, ValueError, RecursionError) as error:
        raise RuntimeError(problem) from error
