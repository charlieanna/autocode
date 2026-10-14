"""Accumulate changed Python tests in a literal milestone proof command (#1105).

The caller decides when milestone accumulation is appropriate. This helper only
adds workspace-relative files to a supported collector's existing selection; it
never removes options, selectors or filtering authority, and changes no settings.
"""

import shlex
from pathlib import PurePosixPath

try:
    from . import autocode_python_tests as python_tests
except ImportError:
    import autocode_python_tests as python_tests

_PYTEST_VALUES = {
    "-p",
    "-o",
    "-c",
    "-k",
    "-m",
    "--override-ini",
    "--rootdir",
    "--confcutdir",
    "--import-mode",
    "--tb",
    "--assert",
    "--maxfail",
    "--ignore",
    "--ignore-glob",
    "--deselect",
    "--color",
    "--capture",
    "--junitxml",
    "--junit-xml",
}
_PYTEST_FLAGS = {
    "-q",
    "-v",
    "-vv",
    "-s",
    "-x",
    "--verbose",
    "--quiet",
    "--disable-warnings",
    "--strict-markers",
    "--strict-config",
    "--continue-on-collection-errors",
}
_UNITTEST_VALUES = {"-k"}
_UNITTEST_FLAGS = {"-v", "--verbose", "-q", "--quiet", "-f", "--failfast", "-c", "--catch", "-b", "--buffer"}


def _relative(path):
    if not isinstance(path, str) or not path or any(char in path for char in "\\\0*?["):
        return None
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or path.startswith("-"):
        return None
    return parsed.as_posix()


def _selectors(invocation):
    values = _PYTEST_VALUES if invocation.kind == "pytest" else _UNITTEST_VALUES
    flags = _PYTEST_FLAGS if invocation.kind == "pytest" else _UNITTEST_FLAGS
    selectors, index, positional = [], 0, False
    while index < len(invocation.arguments):
        word = invocation.arguments[index]
        if not positional and word == "--":
            positional = True
        elif not positional and word in values:
            if index + 1 == len(invocation.arguments):
                return None
            index += 1
        elif not positional and (word in flags or ("=" in word and word.split("=", 1)[0] in values)):
            pass
        elif not positional and word.startswith("-"):
            return None  # Unknown option arity or collection mode cannot authorize a rewrite.
        else:
            if invocation.kind == "unittest" and word == "discover":
                return None
            if _relative(word.split("::", 1)[0]) is None:
                return None
            selectors.append(word)
        index += 1
    return selectors


def _covered(path, selectors, kind):
    module = str(PurePosixPath(path).with_suffix("")).replace("/", ".")
    for selector in selectors:
        # A class/test selector cannot cover later cases in the same file.
        if "::" in selector:
            continue
        normalized = _relative(selector)
        if normalized == path or (kind == "unittest" and selector == module):
            return True
        if kind == "pytest" and (normalized == "." or path.startswith(normalized.rstrip("/") + "/")):
            return True
    return False


def widen(command, changes, *, framework, test_root=None):
    """Union cumulative changed test files with a supported literal selection.

    Opaque commands, unknown option arity, discovery and implicit full-suite
    collection retain their existing behavior. Commands always run from the
    workspace/scratch-tree root, so appended paths retain that relative origin.
    """
    invocation = python_tests.parse(command)
    if not invocation or invocation.kind != framework:
        return command
    selectors = _selectors(invocation)
    if not selectors:
        return command
    root = _relative(test_root) if test_root else None
    if test_root and root is None:
        return command
    additions = []
    for path, change in sorted(changes.items()):
        relative = _relative(path)
        if (
            change != "deleted"
            and relative
            and python_tests.TEST_MODULE.fullmatch(PurePosixPath(relative).name)
            and (not root or relative.startswith(root + "/"))
            and not _covered(relative, selectors, invocation.kind)
        ):
            additions.append(relative)
    if not additions:
        return command
    return shlex.join([*invocation.prefix, invocation.python, "-m", invocation.kind, *invocation.arguments, *additions])
