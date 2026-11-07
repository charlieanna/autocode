"""Pure command recognition and selection for independent verification."""

import shlex
from pathlib import PurePosixPath

try:
    from . import autocode_go_tests as go_tests
    from . import autocode_junit_tests as junit_tests
    from . import autocode_node_tests as node_tests
    from . import autocode_python_tests as python_tests
    from .autocode_test_framework import Framework
except ImportError:
    import autocode_go_tests as go_tests
    import autocode_junit_tests as junit_tests
    import autocode_node_tests as node_tests
    import autocode_python_tests as python_tests
    from autocode_test_framework import Framework


def _go_test(command):
    """A plain `go test ...` command the runner can ask for per-test JSON events (not a shell pipeline)."""
    return go_tests.parse(command) is not None


def _mentions_tests(command, test_paths):
    """True when a shell word of ``command`` names a changed test file (or a pytest node in it)."""
    try:
        words = shlex.split(command, comments=True)
    except ValueError:
        words = command.split()
    for path in test_paths:
        module = str(PurePosixPath(path).with_suffix("")).replace("/", ".")
        for word in words:
            if word in (path, "./" + path, module) or word.startswith((path + "::", module + ".")):
                return True
    return False


def select_commands(framework, test_paths, *, suite_command=None, regression_command=None, reported=None):
    """Choose commands: explicit flags, then detection, then the builder's report.

    A builder-reported command is used only to give the Builder feedback: it
    can never make the verdict PASS, because the candidate would be judged by a
    check it chose. A builder regression command must also name a changed test.
    """
    reported = reported or {}
    notes = []
    suite, suite_source = suite_command, "explicit" if suite_command else None
    if not suite and framework:
        suite, suite_source = framework.suite, f"detected:{framework.name}"
    if not suite and str(reported.get("test_command", "")).strip():
        suite, suite_source = reported["test_command"].strip(), "builder"
    regression, regression_source = regression_command, "explicit" if regression_command else None
    if not regression and framework:
        derived = framework.targeted(test_paths)
        if derived:
            regression, regression_source = derived, f"derived:{framework.name}"
    builder_regression = str(reported.get("regression_command", "")).strip()
    if not regression and builder_regression:
        if _mentions_tests(builder_regression, test_paths):
            regression, regression_source = builder_regression, "builder"
        else:
            notes.append("Ignored the builder's regression command because it does not name a changed test file")
    return {
        "suite": suite,
        "suite_source": suite_source,
        "regression": regression,
        "regression_source": regression_source,
        "notes": notes,
    }


def command_framework(command, *, test_report=None):
    """Recognize a plain runner invocation, never infer coverage from similar text."""
    if test_report is not None:
        return junit_tests.JUnitFramework(command, test_report)
    invocation = python_tests.parse(command)
    if invocation:
        return Framework(invocation.kind, command, python=invocation.python, invocation=invocation)
    invocation = node_tests.parse(command)
    if invocation:
        return Framework("node", command, invocation=invocation)
    invocation = go_tests.parse(command)
    if invocation and not invocation.compile_only:
        return Framework("go", command)
    return None


def configured_targeted(settings, test_paths):
    """The saved ``--test-command`` narrowed to these test files, or None to use the detected suite (#2609).

    Only a plain runner invocation (``command_framework``) is narrowed; any other saved command
    leaves the files to the detected suite, never a guessed spelling.
    """
    command = ((settings or {}).get("regression") or {}).get("test_command")
    framework = command_framework(command) if isinstance(command, str) and command.strip() else None
    return framework.targeted(list(test_paths)) if framework else None


def untargeted(framework, test_paths) -> str:
    """Say which suite could not run these test files, so a wrong binding reads as one (#2609)."""
    found = f"the detected {framework.name} suite `{framework.suite}`" if framework else "no detected test suite"
    return f"no test command runs these files: {', '.join(test_paths)} ({found} cannot run them)"
