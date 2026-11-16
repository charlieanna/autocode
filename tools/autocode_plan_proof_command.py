"""The approved plan's own test command, for a regression proof that found no runner it knows.

The proof takes its commands from three places (autocode_verification_commands.select_commands): a
command the person typed, a suite AutoCode detects from the repository, or the Builder's report. The
Builder's is never trusted, because the candidate would be judged by a check it chose. Detection is a
list of runners per language (autocode_toolchain_suite.detect), so every language or layout it did
not list left the proof with no trusted command at all. The proof then said "pass --test-command",
and the Resolver asked the person to type it: a Ruby Minitest bug fix did exactly that on Claude
models (2026-11-13, docs/bugs/2026-11-13-task-kind-survey.md), although the approved plan already
said `ruby test/pager_test.rb` runs the tests before and after the fix.

A command written in the approved plan is fixed before any Builder runs and was reviewed with the
plan, so the candidate is not judged by a check it chose. When nothing is typed and nothing is
detected, ``choose`` takes the first approved command that runs a test file, deterministically:

- as the suite when every test file it names exists in the original code, so it can pass there;
- as the regression command when it names a test file the change adds or edits.

The proof still runs it on the original code and with the change and judges its exit code, and the
caller records the choice in the proof's notes and review reasons. Pure: callers pass the plan body
and the test paths.
"""

from __future__ import annotations

import fnmatch
import re
import shlex
from pathlib import PurePosixPath

try:
    from . import autocode_test_cases as test_cases
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_test_cases as test_cases
    import autocode_verification_plan as verification_plan


def _commands(method, runs=None) -> list[str]:
    """The commands a criterion's method runs; a guard that is a command (autocode_command_guards) runs it."""
    guard = test_cases.guard_command(method)
    try:
        return [guard] if guard else verification_plan.commands(method, runs=runs)
    except ValueError:
        if runs is None:
            raise
        return []  # exit codes the plan check would refuse: no command to try


def approved_commands(contract, *, runs=None) -> list[str]:
    """The commands the approved plan's acceptance criteria run, in criterion order; [] unless approved. A
    guard that is a command (autocode_command_guards) runs that command. ``runs`` says which text is a
    command (autocode_verification_plan.commands), for a caller that looks for one to try."""
    if not isinstance(contract, dict) or contract.get("approval_status") != "approved":
        return []
    rows = (contract.get("body") or {}).get("acceptance_criteria") or []
    found = [
        command
        for row in rows
        if isinstance(row, dict) and not row.get("human_review")
        for command in _commands(row.get("verification_method") or "", runs)
    ]
    return list(dict.fromkeys(found))


def approved_test_commands(contract, *, runs=None) -> list[str]:
    """Commands from an approved plan's acceptance criteria and initial validation_plan.

    Only an approved contract supplies trusted selectors; candidate/unapproved tasks never do.
    """
    if not isinstance(contract, dict) or contract.get("approval_status") != "approved":
        return []
    found = list(approved_commands(contract, runs=runs))
    body = contract.get("body") or {}
    raw_initial = body.get("initial_task")
    initial = raw_initial if isinstance(raw_initial, dict) else {}
    for entry in initial.get("validation_plan") or []:
        if not isinstance(entry, str) or not entry.strip():
            continue
        try:
            found.extend(_commands(entry, runs))
        except ValueError:
            continue
    return list(dict.fromkeys(found))


# Maven and Gradle name the tests a command runs by class and by module, never by file.
_BUILD_TOOLS = ("mvn", "mvnw", "gradle", "gradlew")
# The source files of the classes their selectors name.
_CLASS_SUFFIXES = (".java", ".kt", ".scala", ".groovy")
# Maven builds, and so tests, more modules than the ones it was given.
_ALSO_MAKE = ("-am", "--also-make", "-amd", "--also-make-dependents")
_SKIPS_TESTS = re.compile(r"-D(skipTests|maven\.test\.skip)(=true)?")
_GRADLE_TEST = re.compile(r":?(?:([\w.-]+(?::[\w.-]+)*):)?(?:test|check)")
# Where one command of a shell line ends, and a variable set for the command that follows it.
_SEPARATORS = ("&&", "||", ";", "|", "&", "(", ")")
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=.*")


def _build_command(command) -> list[str]:
    """The words of the first Maven or Gradle command in ``command``, from the tool to the end of that
    command, or []. The tool is the command itself (``cd shop && JAVA_HOME=/jdk ./mvnw test``), never a word
    another command is given (``echo mvn test``)."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return []
    start = True
    for index, word in enumerate(words):
        if start and PurePosixPath(word).name in _BUILD_TOOLS:
            rest = words[index:]
            return rest[: next((n for n, later in enumerate(rest) if later in _SEPARATORS), len(rest))]
        start = word in _SEPARATORS or start and bool(_ASSIGNMENT.fullmatch(word))
    return []


def _runs_tests(words) -> bool:
    """Whether the Maven or Gradle command ``words`` runs tests: the ``test`` or ``verify`` phase, or a
    ``test`` or ``check`` task, not skipped (``-DskipTests``) or left out (``-x test``)."""
    if PurePosixPath(words[0]).name in ("mvn", "mvnw"):
        return any(word in ("test", "verify") for word in words) and not any(map(_SKIPS_TESTS.fullmatch, words))
    return any(
        _GRADLE_TEST.fullmatch(word) and words[index - 1] not in ("-x", "--exclude-task")
        for index, word in enumerate(words)
        if index
    )


def _modules(words) -> set[str] | None:
    """The module directories ``words`` select: Maven's ``-pl core,app`` (``--projects``) and a Gradle task
    path, ``:core:test``. None when a selection names no directory: a module left out (``!core``) or named
    by its artifact (``:core``), whose tests this cannot tell."""
    listed: list[str] = []
    for index, word in enumerate(words):
        if word in ("-pl", "--projects"):
            listed += words[index + 1].split(",") if index + 1 < len(words) else [""]
        elif word.startswith("--projects="):
            listed += word[len("--projects=") :].split(",")
        elif (task := _GRADLE_TEST.fullmatch(word)) and task.group(1):
            listed.append(task.group(1).replace(":", "/"))
    if not all(re.fullmatch(r"[\w.-]+(/[\w.-]+)*/?", module) for module in listed):
        return None
    return {module.strip("/") for module in listed}


def _selected(command, paths) -> set[str]:
    """The ``paths`` that hold a test class a Maven or Gradle command in ``command`` runs.

    They select tests by class and by module, not by file: ``-Dtest=PagerTest,ReportTest#heading`` and
    ``--tests example.PagerTest.heading`` pick classes, where a class is the file of its simple name (the
    part that begins with a capital letter, between its package and its method) and a ``*`` or ``?`` is the
    tool's own wildcard; ``-pl core`` and ``:core:test`` pick the test classes of a module,
    the files under its ``src/test/``. With both, the classes are those of the modules, and of any module
    when Maven also builds the ones they depend on (``-am``). With neither, the command runs every test
    class (``mvn -q test``, whatever its other options).
    """
    words = _build_command(command)
    if not words or not _runs_tests(words):
        return set()
    selectors = []
    for index, word in enumerate(words):
        if word.startswith("-Dtest="):
            selectors += word[len("-Dtest=") :].split(",")
        elif word.startswith("--tests="):
            selectors.append(word[len("--tests=") :])
        elif word == "--tests":
            if index + 1 == len(words):
                return set()  # a selector with nothing to select
            selectors.append(words[index + 1])
    modules = _modules(words)
    if modules is None:
        return set()
    classes = {path for path in paths if PurePosixPath(path).suffix in _CLASS_SUFFIXES}
    if modules and not (selectors and any(word in _ALSO_MAKE for word in words)):
        classes = {path for path in classes if any(path.startswith(f"{module}/src/test/") for module in modules)}
    if not selectors:
        return classes
    # A selector that begins with ! leaves a class out, so it names none.
    picked = [selector.split("#")[0] for selector in selectors if not selector.startswith("!")]
    parts = {part for selector in picked for part in re.split(r"[./$]", selector) if re.match(r"[A-Z*?]", part)}
    return {path for path in classes if any(fnmatch.fnmatchcase(PurePosixPath(path).stem, part) for part in parts)}


def _path_selected(word: str, paths: set[str]) -> str | None:
    """Resolve a path token (with optional `./`, `::node`, or `:line`) against ``paths``."""
    clean = word.removeprefix("./")
    if clean in paths:
        return clean
    base = clean.split("::", 1)[0]
    if base in paths:
        return base
    cur = base
    while True:
        head, sep, tail = cur.rpartition(":")
        if not sep or not tail.isdigit():
            return None
        if head in paths:
            return head
        cur = head


def _shell_segments(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        words = list(lexer)
    except ValueError:
        words = command.split()
    segments: list[list[str]] = []
    current: list[str] = []
    for word in words:
        if word in _SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        if not current and (bool(_ASSIGNMENT.fullmatch(word)) or PurePosixPath(word).name == "env"):
            continue
        current.append(word)
    if current:
        segments.append(current)
    return segments


def _python_dotted_files(command: str, paths: set[str]) -> set[str]:
    """Resolve dotted module/class/method selectors from unittest or pytest --pyargs."""
    py_paths = {path for path in paths if path.endswith(".py")}
    if not py_paths:
        return set()
    found: set[str] = set()
    unittest_opts = frozenset({"-k"})
    pytest_opts = frozenset(
        {"-k", "-m", "-c", "-o", "--rootdir", "--confcutdir", "-W", "-n", "--maxfail", "--tb", "-p"}
    )
    for segment in _shell_segments(command):
        mode, rest = None, []
        for idx, word in enumerate(segment[:-1]):
            if word == "-m" and segment[idx + 1] == "unittest":
                mode, rest = "unittest", segment[idx + 2 :]
                break
            if word == "-m" and segment[idx + 1] == "pytest" and "--pyargs" in segment[idx + 2 :]:
                mode, rest = "pytest", segment[idx + 2 :]
                break
        if mode is None:
            head = PurePosixPath(segment[0]).name
            if head in ("unittest", "unittest3", "unit2"):
                mode, rest = "unittest", segment[1:]
            elif head in ("pytest", "py.test") and "--pyargs" in segment[1:]:
                mode, rest = "pytest", segment[1:]
        if mode is None:
            continue
        value_opts = unittest_opts if mode == "unittest" else pytest_opts
        skip_next = False
        saw_positional = False
        stop_opts = False
        for token in rest:
            if skip_next:
                skip_next = False
                continue
            if not stop_opts and token == "--":
                stop_opts = True
                continue
            if not stop_opts and token.startswith("-"):
                if "=" not in token and token in value_opts:
                    skip_next = True
                continue
            if mode == "unittest" and not saw_positional:
                saw_positional = True
                if token == "discover":
                    break
            if "/" in token or "\\" in token:
                continue
            dotted = token.split("::", 1)[0].strip(".")
            if not dotted:
                continue
            parts = dotted.split(".")
            for end in range(len(parts), 0, -1):
                rel = "/".join(parts[:end]) + ".py"
                if rel in py_paths:
                    found.add(rel)
                    break
                if end >= 2:
                    matches = [path for path in py_paths if path.endswith("/" + rel)]
                    if len(matches) == 1:
                        found.add(matches[0])
                        break
    return found


def _named(command, paths) -> set[str]:
    """The ``paths`` ``command`` names: path/node/line words, Python dotted targets, or JVM selectors."""
    try:
        words = shlex.split(command, comments=True)
    except ValueError:
        words = command.split()
    path_set = set(paths)
    matched = {resolved for word in words if (resolved := _path_selected(word, path_set)) is not None}
    return matched | _python_dotted_files(command, path_set) | _selected(command, path_set)


def test_files(command, original_tests) -> set[str]:
    """The ``original_tests`` a shell word of ``command`` names: the existing tests it runs."""
    return _named(command, set(original_tests))


def choose(commands, *, original_tests, changed_tests) -> dict | None:
    """The first of ``commands`` that runs a test file, with the roles it can take, or None.

    ``original_tests`` are the test files of the original code, ``changed_tests`` those the change adds or
    edits (not deleted). The result's ``suite`` is the command when every test file it names is an original
    one, ``regression`` when it names a changed one; a command that can take neither is skipped.
    """
    original, changed = set(original_tests), set(changed_tests)
    for command in commands:
        named = _named(command, original | changed)
        suite = bool(named) and named <= original
        regression = bool(named & changed)
        if suite or regression:
            return {
                "command": command,
                "suite": command if suite else None,
                "regression": command if regression else None,
            }
    return None


def note(chosen) -> str:
    """What the proof records about the choice, for the report and the Completion Reviewer."""
    roles = " and ".join(
        role for role, key in (("the suite", "suite"), ("the regression command", "regression")) if chosen.get(key)
    )
    return (
        f"AutoCode found no test runner it reads in this project, so it ran the approved plan's command "
        f"`{chosen['command']}` as {roles}; the proof rests on that command's exit code"
    )
