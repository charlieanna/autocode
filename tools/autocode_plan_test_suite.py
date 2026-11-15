"""The plan's test files and the suite the regression proof will run them with, checked when a plan is drafted (#2889).

A live bug fix in a polyglot repository (idlecampus, 2026-11-07: a Node suite in the root package.json, a Rails
backend/ with RSpec) approved a plan whose every test: and guard: criterion was an RSpec example under
backend/spec/. The suite the proof used could not run one of them, and that surfaced only after a full Builder and
Tester round. It is known when the plan is drafted: the plan names its test files (in a milestone's or the first
task's affected_paths), and the proof chooses its suite from the repository (autocode_regression.prove): the suite
detected at the root, or, when that runs none of the changed tests, the suite of the project around them all
(autocode_package_scope.for_tests).

Before the files exist a runner cannot be asked which of them it would run (a Node suite runs the scripts it
found), so a planned test file is judged by its kind: a Python test module, a Go test, an RSpec spec, or a
JavaScript or TypeScript .test/.spec file. Other files in a test directory (helpers, fixtures, conftest.py) are not
judged. ``problems`` names the planned tests of another kind than the suite the proof will use runs. ``check``
refuses such a draft when it is installed and when it is approved, so the Planner settles it before a Builder
runs. A run whose commands the person chose (--test-command, --regression-command), a --test-root run, a design
job and the user's own edit of the plan are not judged here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

try:
    from . import autocode_command_guards as command_guards
    from . import autocode_command_looks as command_looks
    from . import autocode_named_reporters as reporters
    from . import autocode_native_test_names as native_test_names
    from . import autocode_package_scope as package_scope
    from . import autocode_plan_command_roles as plan_roles
    from . import autocode_project_kinds as project_kinds
    from . import autocode_rust_inline_tests as rust_inline_tests
    from . import autocode_test_cases as test_cases
    from . import autocode_test_homes as test_homes
    from . import autocode_verify as verify
except ImportError:
    import autocode_command_guards as command_guards
    import autocode_command_looks as command_looks
    import autocode_named_reporters as reporters
    import autocode_native_test_names as native_test_names
    import autocode_package_scope as package_scope
    import autocode_plan_command_roles as plan_roles
    import autocode_project_kinds as project_kinds
    import autocode_rust_inline_tests as rust_inline_tests
    import autocode_test_cases as test_cases
    import autocode_test_homes as test_homes
    import autocode_verify as verify

if TYPE_CHECKING:
    from .autocode_run_state import RunState

# The kind of test file each runner AutoCode targets runs (autocode_test_framework.Framework.targeted),
# from the languages' rows in autocode_project_kinds.
KINDS = project_kinds.COLLECTED
RUNNER_KIND = project_kinds.RUNNER_LANGUAGE
SHOWN = {language.name: language.shown for language in project_kinds.LANGUAGES if language.collected}


def kind(path) -> str | None:
    """The kind of test file ``path`` is by its name, or None when its name says no kind."""
    return project_kinds.collected_by(path)


def _plain(path) -> str | None:
    text = str(path).strip()
    text = text[2:] if text.startswith("./") else text
    parts = PurePosixPath(text).parts
    return text if parts and not PurePosixPath(text).is_absolute() and ".." not in parts else None


def planned_tests(body) -> list[str]:
    """The test files of a known kind the plan's milestones and first task name, in path order."""
    return [path for path in _planned(body) if kind(path)]


def problems(workspace, framework, tests, detect_in, *, scoped=False) -> list[str]:
    """The ``tests`` the proof will not run: those of another kind than ``framework`` runs, unless the project
    around them has a suite that runs their kind (never in a ``scoped`` run). That is the project around them
    all when none is of the framework's kind (autocode_package_scope.enclosing), and otherwise, for each, the
    project around it, whose own suite then proves its tests (autocode_companion_suites.test_suites): a change
    with tests in a Python package and in a Node package beside it is proven by both suites. A runner of no
    known kind (``make test``) may run any test, so none is named."""
    expected = RUNNER_KIND.get(framework.name) if framework is not None else None
    if expected is None:
        return []
    other = [test for test in tests if kind(test) != expected]
    if not other or scoped:
        return other
    holds = lambda found: all(kind(test) == RUNNER_KIND.get(found.name) for test in tests)  # noqa: E731
    if len(other) == len(tests) and package_scope.enclosing(workspace, tests, detect_in, runs=holds):
        return []
    return [test for test in other if not _own_suite(workspace, test, detect_in)]


def _own_suite(workspace, test, detect_in) -> bool:
    """Whether a directory around ``test`` has a suite that runs tests of its kind: the places the proof
    runs a test from when the root's suite does not run it (autocode_test_homes.places), with or without a
    manifest. Judged before the file exists, so by its kind and never by targeting or running it."""
    for project in test_homes.places(test):
        found = package_scope.detect(workspace, project, detect_in)
        if found is not None and RUNNER_KIND.get(found.name) == kind(test):
            return True
    return False


def unnamed_cases(workspace, framework, tests, body, detect_in) -> str | None:
    """Why the plan's test: and guard: criteria could never be proven by name here, or None.

    The proof matches such a criterion to a test only from a runner's per-test results
    (autocode_regression.check_cases). A runner that reports only an exit code (``make test``,
    Ruby test files run one by one, a Mocha suite) leaves every such criterion
    unproven after a full Builder and Tester round, and the Resolver then asked the person how to prove
    it (a Ruby Minitest bug fix, 2026-11-13). It is known when the plan is drafted, so the Planner gets
    it instead: the same criterion is proven by the exit code of the command that runs its test file.
    A suite of the project around the planned tests that reports per-test results counts, as the proof
    uses it (autocode_package_scope.for_tests). A project with test code but no runner AutoCode detects
    is judged the same way: its proof runs the approved plan's command (autocode_plan_proof_command). A new
    project, before its first test exists, is not judged: the proof detects its suite on the changed code.
    """
    named = [case["id"] for case in test_cases.plan_cases(body)]
    if not named or reporters.suite_reports(framework, workspace):
        return None
    if framework is None and not _existing_tests(workspace):
        return None
    if tests and package_scope.enclosing(
        workspace, tests, detect_in, runs=lambda found: reporters.suite_reports(found, workspace)
    ):
        return None
    if framework is None:
        # No list knows this project's runner: the plan gives the project's own test command, and the proof
        # runs it on the original code to see that it runs the tests (autocode_plan_command_roles).
        return (
            f"Criteria {', '.join(named)} name a test after test: or guard:, but this project's tests run with no "
            "runner AutoCode reads, so it could never match a test to these criteria. Write each of them without "
            "test: or guard:, as the command this project runs its tests with (the one its README, build file or "
            'CI uses) and what it shows, such as "run `./run_tests.sh`: it fails on the original code and passes '
            "with the fix\", and name the test file in its milestone's affected_paths. For behavior that already "
            'works and must keep working, write "guard: run `./run_tests.sh`" with that same command. The '
            "regression proof runs the command on the original code, there with the change's tests, and with the "
            "change, and judges its exit code"
        )
    return (
        f"Criteria {', '.join(named)} name a test after test: or guard:, but this project's tests run with the "
        f"{framework.name} suite `{framework.suite}`, "
        "which reports only whether the whole run passed, never which test passed, so the runner could never match "
        "a test to these criteria. Write each of them without test: or guard:, as the command that runs its test "
        'file and what it shows, such as "`ruby test/pager_test.rb` fails on the original code and passes with '
        "the fix\", and name the test file in its milestone's affected_paths. For behavior that already works and "
        'must keep working, write "guard: run `ruby test/pager_test.rb`", the command that runs its existing test '
        "file. The regression proof runs each command on the original code and with the change and judges its "
        "exit code"
    )


def _planned(body) -> list[str]:
    """Every plain path the plan's milestones and first task name."""
    paths = [path for row in body.get("milestones") or [] for path in row.get("affected_paths") or []]
    paths += (body.get("initial_task") or {}).get("affected_paths") or []
    return sorted({plain for path in paths if (plain := _plain(path))})


def _in_tests(path, is_test) -> bool:
    """A test file, or a directory that holds tests (``tests/``), which a plan may name instead."""
    return is_test(path) or is_test(path.rstrip("/") + "/_")


def untested_code(workspace, body) -> str | None:
    """Why the proof could find no test for the code this plan changes, or None.

    Every change to product code owes the regression proof a test (autocode_changed_code_trigger): one
    that fails on the original code and passes with the change, or, for a change that alters no
    behavior, a guard on tests the project already has. A project with no test code has neither, so a
    plan there that names code files and no test file cannot pass its proof. A live run asked to add an
    option to a script in such a project planned the script alone, with shell commands as its criteria:
    the proof failed twice with "No regression test was added or changed", the Resolver asked the person
    to allow a test file, and the run took 20 stages (2026-11-13). It is known when the plan is drafted.

    Only that certain case is judged: a project with no test code, and a plan that names at least one
    code file, names no test file or test directory, and has no test: or guard: criterion. A project
    that has tests may guard the change with them or already hold the failing test. ``check`` asks only
    for a build or a bug fix: a review or a discussion may name code it reads.
    """
    if _existing_tests(workspace):
        return None
    paths = _planned(body)
    is_test = project_kinds.test_rule(_workspace_files(workspace))
    code = [path for path in paths if verify.is_code_path(path) and not _in_tests(path, is_test)]
    if not code or any(_in_tests(path, is_test) for path in paths):
        return None
    if test_cases.plan_cases(body) or command_guards.plan_guards(body):
        return None
    return (
        f"This plan changes code ({', '.join(code[:5])}) and plans no test, and the project has no tests yet. "
        "Every change to code needs a test that fails on the original code and passes with the change, so "
        "the regression proof would fail after a full Builder round, and nothing here can guard the change "
        "instead. Plan the test with the change: name a new test file in the affected_paths of the milestone "
        "that changes the code, write the criteria its tests show as test: criteria, and do not restrict the "
        "change to the code files alone"
    )


def _to_look_at(workspace, body) -> dict[str, str]:
    """The code files of the workspace a look for tests tries for this plan, in order: the ones the plan
    names, then the ones beside a file the plan adds or in a directory it names (a new check goes where
    the checks are)."""
    root, found = Path(workspace), []
    for path in _planned(body):
        file = root / path
        if file.is_file():
            found.append(path)
        else:
            folder = file if file.is_dir() else file.parent
            beside = sorted(child for child in folder.iterdir() if child.is_file()) if folder.is_dir() else []
            found += [str(child.relative_to(root)) for child in beside]
    code = [path for path in dict.fromkeys(found) if verify.is_code_path(path) and not (root / path).is_symlink()]
    return dict.fromkeys(code, plan_roles.ORIGINAL)


def seen_to_run_tests(state: RunState, workspace, body, commands) -> dict[str, str]:
    """{command: file}: the ``commands`` of this plan seen to run a file of the project as a test is run,
    among the files the plan is about (``_to_look_at``), with the first such file. The command passes on
    the code as it is, fails when the file cannot be loaded and passes when it is empty
    (autocode_plan_command_roles.leaves): it runs the file, and nothing else needs it. A project that
    keeps its checks in ``checks/`` and runs them with ``ruby run_checks.rb`` has tests, whatever
    ``untested_code`` reads from its paths.

    That is as true of a program's own entry file as of a test, so it never makes a file a test by
    itself: it checks what a plan says (a command guard), or tells the Planner what it may say. Looked at
    only in a run that has a place to look in. A replay runs nothing: it reads what the recording's proof
    found."""
    try:
        from . import autocode_replay_checks as recorded_checks
    except ImportError:
        import autocode_replay_checks as recorded_checks
    if recorded_checks.simulated(state.get("settings") or {}):
        recorded = recorded_checks.kept(state, "plan_command_roles") or {}
        runs = recorded.get("runs_tests") or []
        runs = [*runs, recorded.get("tests_command")]
        return {command: str(recorded.get("existing_test") or "") for command in commands if command in runs}
    run_dir, base = state.get("run_dir"), state.get("base_commit")
    files = _to_look_at(workspace, body)
    # The drafted plan's commands, read as an approved plan's are: a look is no approval.
    runnable = plan_roles.candidates({"body": body, "approval_status": "approved"}, workspace)
    wanted = [command for command in runnable if command in set(commands)][: plan_roles.MAX_COMMANDS]
    if not (run_dir and base and files and wanted):
        return {}
    look = command_looks.looker(state, workspace, run_dir, base, lambda *_, **__: None, limit=plan_roles.RUN_SECONDS)
    probe = lambda command, path, how, where: look(  # noqa: E731
        command, how, {}, {path: plan_roles.UNLOADABLE if how == plan_roles.UNLOADED else ""}, strict=True
    )
    found = {}
    for command in wanted:
        if look(command, plan_roles.ORIGINAL, {}, {}) != 0:
            continue
        for path in list(files)[: plan_roles.MAX_FILES]:
            if plan_roles.leaves(command, {path: plan_roles.ORIGINAL}, probe):
                found[command] = path
                break
    return found


def _how_to_say_so(state: RunState, workspace, body) -> str:
    """What the Planner is told with ``untested_code`` when one of the plan's commands is seen to run a
    file the plan is about as a test is run: how the plan says that this is the project's test command.
    AutoCode does not decide it from the look alone (``seen_to_run_tests``)."""
    try:
        from . import autocode_replay_checks as recorded_checks
    except ImportError:
        import autocode_replay_checks as recorded_checks
    if recorded_checks.simulated(state.get("settings") or {}):
        return ""
    commands = plan_roles.candidates({"body": body, "approval_status": "approved"}, workspace)
    seen = seen_to_run_tests(state, workspace, body, commands)
    if not seen:
        return ""
    command, path = next(iter(seen.items()))
    return (
        f". No rule says where this project's tests are, but `{command}` runs {path} as a test is run: it passes "
        f"on the code as it is, fails when {path} cannot be loaded and passes when {path} is empty. If `{command}` "
        "is the command this project runs its tests with, say so and put the change's check where it runs: write "
        f'the criterion the new check shows as "run `{command}`: it fails on the original code and passes with the '
        'fix", and name the file that gets the new check in the affected_paths of the milestone that changes the '
        "code. If that command runs the program itself, it is no test: plan a new test file"
    )


def _guard_problem(state: RunState, workspace, body, framework) -> str | None:
    """Why the plan's command guards could never be proven, or None (autocode_command_guards.problem).
    Where no rule names a test of this project, a guard's command is usable when it is seen to run one
    (``seen_to_run_tests``): the plan says it is the test command, and the look checks that."""
    guards = command_guards.plan_guards(body)
    judge = lambda observed=(): command_guards.problem(  # noqa: E731
        guards,
        framework,
        _test_paths(workspace),
        per_test=reporters.suite_reports(framework, workspace),
        is_test=project_kinds.test_rule(_workspace_files(workspace)),
        observed=observed,
    )
    unusable = judge()
    if not unusable or framework is not None:
        return unusable
    seen = seen_to_run_tests(state, workspace, body, [guard["command"] for guard in guards])
    unusable = judge(tuple(seen))
    return unusable and (
        f"{unusable}. No rule names a test file in this project, so AutoCode ran each guard's command to see "
        "whether it runs one: the command must pass on the code as it is, fail when a file this plan names (or "
        "one beside a file it adds) cannot be loaded, and pass when that file is empty. Name the file that "
        "gets the change's check in the affected_paths of the milestone that changes the code"
    )


def _workspace_files(workspace) -> list[str]:
    listed = subprocess.run(
        ["git", "-C", str(workspace), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
    )
    return [name for name in listed.stdout.split("\0") if name] if listed.returncode == 0 else []


def _test_paths(workspace) -> list[str]:
    """The workspace's test paths, tracked or not."""
    files = _workspace_files(workspace)
    return list(filter(project_kinds.test_rule(files), files))


def _existing_tests(workspace) -> bool:
    """Whether the workspace already holds test code (a test path or inline Rust tests), tracked or not."""
    files = _workspace_files(workspace)
    if any(verify.is_code_path(name) for name in filter(project_kinds.test_rule(files), files)):
        return True
    root = Path(workspace)
    for name in files:
        if name.endswith(".rs"):
            path = root / name
            if path.is_file() and not path.is_symlink() and rust_inline_tests.test_modules(path.read_bytes()):
                return True
    return False


def check(state: RunState, body, *, origin=None) -> None:
    """Refuse a plan whose test files the regression proof would never run (see the module docstring)."""
    options = (state.get("settings") or {}).get("regression") or {}
    if origin == native_test_names.USER_EDIT or test_cases.design_only(state):
        return
    if any(options.get(key) for key in ("test_command", "regression_command", "test_root")):
        return
    tests, workspace = planned_tests(body), state.get("workspace")
    if not workspace or not Path(workspace).is_dir():
        return
    python = options.get("python") or verify.python_for(state.get("project_workspace") or workspace)
    settings = state.get("settings")
    framework = verify.detect_framework(workspace, python=python, **package_scope.detection(settings))
    detect = lambda directory: verify.detect_framework(directory, python=python)  # noqa: E731
    if unnamed := unnamed_cases(workspace, framework, tests, body, detect):
        raise ValueError(unnamed)
    # A guard that is a command runs tests the project already has, where no runner names them.
    if unusable := _guard_problem(state, workspace, body, framework):
        raise ValueError(unusable)
    builds = (state.get("workflow") or {}).get("kind") in (None, "build", "bugfix")
    # No path rule finds a test here. A plan that says which command runs the project's tests, where that
    # command is seen to run a file the plan is about as a test is run, has planned its test.
    untested = untested_code(workspace, body) if builds else None
    if untested and not seen_to_run_tests(state, workspace, body, plan_roles.declared(body, workspace)):
        raise ValueError(untested + _how_to_say_so(state, workspace, body))
    if not tests:
        return
    unrun = problems(workspace, framework, tests, detect, scoped=package_scope.of(settings) is not None)
    if unrun and framework is not None:  # problems() names none without a framework
        runs = SHOWN[RUNNER_KIND[framework.name]]
        raise ValueError(
            f"The regression proof would never run the plan's tests {', '.join(unrun)}: it runs the plan's tests "
            f"with the {framework.name} suite `{framework.suite}`, which runs {runs} tests, and a test of another "
            "kind with the suite of the project directory around it, and no such directory has a suite that runs "
            f"these. Put every new or changed test where a suite runs it: {runs} tests for `{framework.suite}`, "
            "and a test of another kind inside a project directory that already has its own manifest and a suite "
            "for that kind (a Gemfile, package.json, go.mod or pyproject.toml there), which the proof then runs "
            "for the tests there. Name each test file in the affected_paths of its milestone"
        )
