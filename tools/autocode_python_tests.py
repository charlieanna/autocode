"""Parse literal Python test commands and named unittest output without running them."""

import ast
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    from . import autocode_test_setup as test_setup
except ImportError:
    import autocode_test_setup as test_setup

COLLECTION_ERROR = re.compile(r"unittest\.loader\.(_FailedTest|ModuleImportFailure)|^::")
UNITTEST_HEADER = re.compile(r"^(\w+) \(([\w.-]+)\)")
UNITTEST_STATUS = re.compile(r"\.\.\. (ok|FAIL|ERROR|skipped|expected failure|unexpected success)\b")
UNITTEST_BARE_STATUS = re.compile(r"(ok|FAIL|ERROR|expected failure|unexpected success)|skipped( .*)?")
UNITTEST_FIXTURES = frozenset({"setUpClass", "tearDownClass", "setUpModule", "tearDownModule"})

TEST_MODULE = re.compile(r"^(tests|test_.*|.*_tests?)\.py$")


def unittest_package(source: str) -> bool:
    """A package marker alone is not a suite; only declared tests/hooks select Python."""
    try:
        nodes = ast.parse(source).body
    except (SyntaxError, ValueError):
        return False
    for node in nodes:
        if isinstance(node, ast.FunctionDef) and node.name == "load_tests":
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)) and any(
            (alias.asname or alias.name) == "load_tests" for alias in node.names
        ):
            return True
        if isinstance(node, ast.ClassDef) and any(
            (isinstance(base, ast.Attribute) and base.attr == "TestCase")
            or (isinstance(base, ast.Name) and base.id == "TestCase")
            for base in node.bases
        ):
            return True
    return False


@dataclass(frozen=True)
class Invocation:
    prefix: tuple[str, ...]
    python: str
    kind: str
    arguments: tuple[str, ...]

    def targeted(self, files):
        """Keep environment and collection options when replacing suite paths.

        Unknown option arity cannot safely be guessed. In that case execute the
        original suite, which still supplies named base/candidate results.
        """
        if self.kind != "pytest":
            return shlex.join([*self.prefix, self.python, "-m", "unittest", "-v", *files])
        values = {
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
        }
        flags = {
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
            "--pyargs",
            "--continue-on-collection-errors",
        }
        kept, index = [], 0
        while index < len(self.arguments):
            word = self.arguments[index]
            if word in values:
                if index + 1 == len(self.arguments):
                    return None
                kept.extend(self.arguments[index : index + 2])
                index += 2
                continue
            if word in flags or ("=" in word and word.split("=", 1)[0] in values):
                kept.append(word)
            elif word.startswith("-"):
                return shlex.join([*self.prefix, self.python, "-m", self.kind, *self.arguments])
            index += 1
        return shlex.join([*self.prefix, self.python, "-m", "pytest", *kept, *files])


def parse(command):
    """Accept Python directly or behind env with literal NAME=value assignments."""
    if not isinstance(command, str) or re.search(r"[;&|<>`$\n]", command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    offset = 0
    if words and words[0] in ("env", "/usr/bin/env", "/bin/env"):
        offset = 1
        while offset < len(words) and re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", words[offset]):
            offset += 1
    tail = words[offset:]
    if (
        len(tail) < 3
        or not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(tail[0]).name)
        or tail[1] != "-m"
        or tail[2] not in ("pytest", "unittest")
    ):
        return None
    if tail[2] == "unittest" and not {"-v", "--verbose"} & set(tail[3:]):
        return None
    return Invocation(tuple(words[:offset]), tail[0], tail[2], tuple(tail[3:]))


def verbose_unittest(command):
    """A plain unittest command that names every test, or ``command`` unchanged.

    ``-q`` and the default dot reporter print a count, not test names, so a
    passing run cannot show which tests the base ran. ``-v`` only changes that
    report. ``discover`` has to stay the first argument. A shell program is
    left alone: its text is not a single unittest invocation.
    """
    if not isinstance(command, str) or re.search(r"[;&|<>`$\n]", command):
        return command
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    offset = 0
    if words and words[0] in ("env", "/usr/bin/env", "/bin/env"):
        offset = 1
        while offset < len(words) and re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", words[offset]):
            offset += 1
    tail = words[offset:]
    if (
        len(tail) < 3
        or not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(tail[0]).name)
        or tail[1] != "-m"
        or tail[2] != "unittest"
    ):
        return command
    args = [word for word in tail[3:] if word not in ("-q", "--quiet")]
    if not {"-v", "--verbose"} & set(args):
        if args and args[0] == "discover":
            args = ["discover", "-v", *args[1:]]
        else:
            args = ["-v", *args]
    rewritten = [*words[:offset], tail[0], "-m", "unittest", *args]
    if rewritten == words:
        return command
    return shlex.join(rewritten)


def _unittest_id(name, owner):
    return owner if owner.endswith("." + name) else f"{owner}::{name}"


def unittest_results(
    text: str, *, component: bool = False, classify_setup: Callable[[str], str | None] | None = None
) -> dict | None:
    """Read named unittest outcomes from text without executing commands or reading files.

    Component suites combine the counts of their separate unittest summaries.
    The caller may classify each failure's isolated traceback as a setup error.
    """
    passed: set[str] = set()
    failed: set[str] = set()
    skipped: set[str] = set()
    collection: set[str] = set()
    setup_errors = {}
    ran = re.findall(r"^Ran (\d+) tests? in ", text, re.M)
    if not ran:
        return None
    total = sum(map(int, ran)) if component else int(ran[-1])
    current = None
    for line in text.splitlines():
        if line.startswith(("=====", "-----")):
            current = None  # the failure details section follows
            continue
        match = UNITTEST_HEADER.match(line)
        if match:
            current, rest = _unittest_id(*match.groups()), line[match.end() :]
        elif current is None:
            continue
        else:
            rest = line
        # "name (id) ... ok", a docstring line ending "... ok", or the word alone after test output
        found = UNITTEST_STATUS.search(rest)
        word = found.group(1) if found else None
        if word is None and not match:
            bare = UNITTEST_BARE_STATUS.fullmatch(rest.strip())
            word = bare and ("skipped" if bare.group(0).startswith("skipped") else bare.group(1))
        if word:
            (passed if word == "ok" else skipped if word in ("skipped", "expected failure") else failed).add(current)
            current = None
    for name, owner, detail in test_setup.failure_details(text):
        test = _unittest_id(name, owner)
        failed.add(test)
        reason = classify_setup(detail) if classify_setup is not None else None
        if reason:
            setup_errors[test] = reason
    passed -= failed
    # "setUpClass (m.C) ... skipped" or ERROR (also setUpModule, tearDown*) is outside "Ran N": a
    # skipped fixture names no test (its tests are absent from N); an error is one more failure, but
    # never a test: "Ran 0" with only fixture errors stays zero tests, so it proves nothing.
    fixtures = {test for test in skipped | failed if test.rpartition("::")[2] in UNITTEST_FIXTURES}
    skipped -= fixtures
    total += len(fixtures & failed) if total else 0
    collection = {test for test in failed if COLLECTION_ERROR.search(test)}
    complete = len(passed) + len(skipped) + len(failed) >= total

    results = {
        "passed": sorted(passed),
        "failed": sorted(failed),
        "skipped": sorted(skipped),
        "collection_errors": sorted(collection),
        "uncollected": sorted(collection),
        "total": total,
        "complete": complete,
    }
    if setup_errors:
        results["setup_errors"] = setup_errors
    return results
