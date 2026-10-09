"""Reject provably inert Python test suites used as behavioral evidence.

This is a narrow sanity check, not proof of semantic coverage. Calls, fixtures,
and dynamic tests are left to execution and independent review. Empty tests
and literal true assertions cannot establish an application's behavior.
"""
import ast
import shlex
from pathlib import Path


def inert(statement):
    if isinstance(statement, ast.Pass):
        return True
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
        return True
    if isinstance(statement, ast.Return):
        return statement.value is None or isinstance(statement.value, ast.Constant)
    if isinstance(statement, ast.Assert) and isinstance(statement.test, ast.Constant):
        return bool(statement.test.value)
    return False


def python_test_files(workspace, command):
    try:
        words = shlex.split(command)
    except ValueError:
        return []
    try:
        at = words.index("-m")
    except ValueError:
        return []
    if words[at + 1:at + 2] != ["unittest"]:
        return []
    args = words[at + 2:]
    root = Path(workspace).resolve()
    if "discover" in args:
        start, pattern = ".", "test*.py"
        for i, word in enumerate(args[:-1]):
            if word in ("-s", "--start-directory"):
                start = args[i + 1]
            if word in ("-p", "--pattern"):
                pattern = args[i + 1]
        directory = (root / start).resolve()
        return sorted(directory.rglob(pattern)) if directory.is_relative_to(root) else []
    files = []
    for arg in args:
        if arg.startswith("-"):
            continue
        candidates = [root / arg, root / (arg.replace(".", "/") + ".py")]
        files += [path for path in candidates if path.is_file() and path.resolve().is_relative_to(root)]
    return list(dict.fromkeys(files))


def require_behavioral_tests(workspace, commands):
    workspace = Path(workspace).resolve()
    for command in commands:
        files = python_test_files(workspace, command)
        tests, uncertain = [], False
        for path in files:
            try:
                tree = ast.parse(path.read_text())
            except (OSError, SyntaxError, UnicodeError):
                uncertain = True
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    known = any(isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                                and base.value.id == "unittest" and base.attr == "TestCase"
                                or isinstance(base, ast.Name) and base.id == "TestCase" for base in node.bases)
                    if node.bases and not known or node.decorator_list:
                        uncertain = True  # inherited checks and custom decorators may assert in fixtures
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    uncertain |= bool(node.decorator_list)
                    if node.name.startswith("test"):
                        tests.append((path, node))
                    elif node.name in ("setUp", "setUpClass", "setUpModule", "tearDown", "tearDownClass", "tearDownModule", "load_tests"):
                        uncertain |= any(not inert(statement) for statement in node.body)
        if tests and not uncertain and all(all(inert(statement) for statement in node.body) for _, node in tests):
            names = ", ".join(f"{path.relative_to(Path(workspace))}:{node.name}" for path, node in tests)
            raise ValueError(f"Verification command `{command}` only exercises vacuous test bodies ({names}). "
                             "Passing empty tests cannot verify required behaviors; add assertions or executed "
                             "behavioral probes for the approved examples and rerun validation.")
