"""What the approved plan's commands do in a project with no runner AutoCode reads, found by running them.

AutoCode knows how a project's tests run from a list of runners (autocode_toolchain_suite.detect). A project
outside the list had one fallback: the approved plan's own command, taken by a rule about its words
(autocode_plan_proof_command.choose). The command had to begin with a program from a second list
(autocode_verification_plan.RUNNERS) and to name a test file, "so it can tell which role the command
plays". ``dotnet test``, ``mix test``, ``swift test``, ``bun test``, ``make tests`` and ``./run_tests.sh``
name none, and half of them begin with a program the list does not hold. The proof then had no command
("pass --regression-command", "pass --test-command") and the person was asked to type AutoCode's own
flags. Each runner was added to the lists by its own fix after a run stopped on it: Minitest, Maven,
Gradle, CMake, PHPUnit, Perl's prove, ``make check`` (task-kind survey, sections 1, 22, 29, 30 and 32).
The owner asked for the shared cause to be fixed once (2026-11-15, AGENTS.md).

Neither fact has to be guessed. A command is something this machine can run: its first word is a program
on the PATH or a file of the project (``program``). And what it does shows when it is run:

- it passes on the original code, and fails there once the change's test files are added: it runs those
  tests, and they fail without the change. It is the suite and the regression command;
- it passes on the original code, and fails there when the project's own test files are made
  unloadable: it runs the tests the project already has. It is the suite, and a guard may use it.

``settle`` asks the rules first, so a plan they already understand costs no run. It then runs the plan's
commands in the plan's order until the roles are filled, at most MAX_COMMANDS of them, and keeps what it
saw for the run (``plan_command_roles`` in its state). The proof, the plan's guards and the gate of the
original tests read that one answer (``adopted``, ``runs_tests``, ``suite``), and a replay, which runs
nothing, takes the recorded proof's (``restore``).

Finding a command's role proves nothing about the change. The proof still runs the command on the original
code, there with the change's tests, and with the change, and judges each exit code; a guard must still
pass before and after. A command that fails on the original code is never given a role here: one that
names a test file the change adds keeps the rule it had.

It imports no controller and runs nothing itself: ``observe(command, where)`` runs one command on a tree
of the original code and returns its exit code, or None when it did not finish.
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from . import autocode_plan_proof_command as plan_command
    from . import autocode_util as util
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_plan_proof_command as plan_command
    import autocode_util as util
    import autocode_verification_plan as verification_plan

if TYPE_CHECKING:
    from .autocode_run_state import RunState

VERSION = 1
# One settling looks at this many of the plan's commands, in the plan's order.
MAX_COMMANDS = 6
# The longest one such run may take, in seconds.
RUN_SECONDS = 300
# The trees a command is run on: the original code, the original code with the change's test files, and
# the original code with its own test files made unloadable.
ORIGINAL, WITH_TESTS, WITHOUT_TESTS = "original", "with_tests", "without_tests"
# What an original test file is overwritten with for WITHOUT_TESTS: no language loads it.
UNLOADABLE = ")]}<<<<<<< AutoCode made this test file unloadable to see whether a command reads it\n\x00\n"
_SHOWN = {
    ORIGINAL: "on the original code",
    WITH_TESTS: "on the original code with the change's tests",
    WITHOUT_TESTS: "on the original code with its test files made unloadable",
}


def program(command, tree) -> bool:
    """Whether ``command`` begins with something this machine can run: a program on the PATH, or, when the
    word is a path (``./run_tests.sh``, ``scripts/test``), a file of ``tree`` that may be executed. A
    command for a directory of the project (``cd backend && ./run_tests.sh``) is read inside it, and
    ``env NAME=value`` in front is skipped, as a plan check's is. Never ``autocode`` itself: a check is no
    permission to start a run."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return False
    tree = Path(tree)
    if len(words) > 3 and words[0] == "cd" and words[2] == "&&":
        tree, words = tree / words[1], words[3:]
        if not tree.is_dir():
            return False
    if words and words[0] in verification_plan.ENV_WORDS:
        words = words[1:]
        while words and verification_plan.ASSIGNMENT.fullmatch(words[0]):
            words = words[1:]
    if not words or Path(words[0]).name == "autocode":
        return False
    if "/" in words[0]:
        file = tree / words[0]
        return file.is_file() and os.access(file, os.X_OK)
    return shutil.which(words[0]) is not None


def candidates(contract, tree) -> list[str]:
    """The commands of the approved plan's criteria that can be run here, in the plan's order: the ones a
    plan check runs, and any other its criteria ask for that begins with a program (``program``)."""
    strict = plan_command.approved_commands(contract)
    found = plan_command.approved_commands(
        contract,
        runs=lambda text: (
            verification_plan.executable(text) or (program(text, tree) and not verification_plan.shell_error(text))
        ),
    )
    return list(dict.fromkeys([*found, *strict]))


def _saved(state: RunState, base, contract) -> dict:
    """What an earlier settling of this plan on this original code kept, or {}."""
    saved = state.get("plan_command_roles")
    if (
        isinstance(saved, dict)
        and saved.get("version") == VERSION
        and saved.get("base") == base
        and saved.get("contract") == _identity(contract)
    ):
        return saved
    return {}


def _identity(contract) -> str | None:
    return util.digest(contract.get("body")) if isinstance(contract, dict) else None


def _rules(contract, original_tests, changed_tests) -> dict:
    """The roles the rules give the plan's commands (autocode_plan_proof_command.choose), or {}."""
    strict = plan_command.approved_commands(contract)
    return plan_command.choose(strict, original_tests=original_tests, changed_tests=changed_tests) or {}


def _unknown(runs, guards, original_tests) -> list[str]:
    """The guards' commands no rule and no earlier run says run tests the project already has."""
    return [
        command for command in guards if command not in runs and not plan_command.test_files(command, original_tests)
    ]


def wanted(state: RunState, base, contract, *, original_tests, changed_tests, guards=()) -> bool:
    """Whether ``settle`` has anything to find: a role the rules and an earlier settling left empty, or a
    guard's command not yet known to run the project's tests."""
    saved, rules = _saved(state, base, contract), _rules(contract, original_tests, changed_tests)
    suite = rules.get("suite") or saved.get("suite")
    regression = rules.get("regression") or saved.get("regression")
    return (
        not suite
        or (bool(changed_tests) and not regression)
        or bool(_unknown(set(saved.get("runs_tests") or []), guards, original_tests))
    )


def settle(state: RunState, base, contract, *, original_tests, changed_tests, guards=(), tree, observe) -> None:
    """Find the roles the plan's commands can take and keep them in ``state``.

    ``original_tests`` are the test files of the original code, ``changed_tests`` those the change adds or
    edits, ``guards`` the commands of the plan's guards that are due, ``tree`` the changed code (where a
    command's program is looked for). ``observe(command, where)`` runs ``command`` on ORIGINAL, WITH_TESTS
    or WITHOUT_TESTS and returns its exit code, or None when it did not finish.
    """
    saved, rules = _saved(state, base, contract), _rules(contract, original_tests, changed_tests)
    # What a command does on the original code does not change during a run; what it does with the
    # change's tests does, so that is looked at again.
    seen: dict[str, dict[str, Any]] = {
        command: {where: code for where, code in row.items() if where != WITH_TESTS}
        for command, row in (saved.get("seen") or {}).items()
    }
    runs = set(saved.get("runs_tests") or [])
    found: dict[str, str] = dict(saved.get("found") or {})
    suite = rules.get("suite") or saved.get("suite")
    regression = rules.get("regression") or saved.get("regression")
    unknown = _unknown(runs, guards, original_tests)

    def look(command, where):
        row = seen.setdefault(command, {})
        if where not in row:
            row[where] = observe(command, where)
        return row[where]

    def failed(command, where) -> bool:
        return look(command, where) not in (0, None)

    tried = 0
    for command in dict.fromkeys([*unknown, *candidates(contract, tree)]):
        wants = not suite or (bool(changed_tests) and not regression) or command in unknown
        if not wants or tried == MAX_COMMANDS:
            continue
        tried += 1
        if look(command, ORIGINAL) != 0:
            continue
        if changed_tests and failed(command, WITH_TESTS):
            runs.add(command)
            found[command] = WITH_TESTS
            suite, regression = suite or command, regression or command
        elif (not suite or command in unknown) and original_tests and failed(command, WITHOUT_TESTS):
            runs.add(command)
            found[command] = WITHOUT_TESTS
            suite = suite or command
    state["plan_command_roles"] = {
        "version": VERSION,
        "base": base,
        "contract": _identity(contract),
        "suite": suite,
        "regression": regression,
        "runs_tests": sorted(runs),
        "found": {command: where for command, where in found.items() if command in (suite, regression, *runs)},
        "seen": seen,
    }


def adopted(state: RunState, base, contract, *, original_tests, changed_tests) -> dict | None:
    """The plan's command in the roles the proof gives it, as autocode_plan_proof_command.choose returns it,
    or None. The rules' answer first; what ``settle`` found fills a role they left empty, and is named in
    ``observed`` with what was seen."""
    saved, rules = _saved(state, base, contract), _rules(contract, original_tests, changed_tests)
    suite = rules.get("suite") or saved.get("suite")
    regression = rules.get("regression") or (saved.get("regression") if changed_tests else None)
    if not suite and not regression:
        return None
    result = {"command": suite or regression, "suite": suite, "regression": regression}
    filled = {
        command
        for command, by_rule in ((suite, rules.get("suite")), (regression, rules.get("regression")))
        if command and command != by_rule
    }
    if observed := {command: where for command, where in (saved.get("found") or {}).items() if command in filled}:
        result["observed"] = observed
    return result


def suite(state: RunState, contract) -> str | None:
    """The command ``settle`` found to run the project's existing tests, for the gate of the original tests."""
    saved = state.get("plan_command_roles")
    if not isinstance(saved, dict) or saved.get("version") != VERSION or saved.get("contract") != _identity(contract):
        return None
    return saved.get("suite") if saved.get("suite") in (saved.get("runs_tests") or []) else None


def runs_tests(state: RunState, contract) -> set[str]:
    """The plan's commands ``settle`` saw run tests the project already has, for its guards."""
    saved = state.get("plan_command_roles")
    if not isinstance(saved, dict) or saved.get("version") != VERSION or saved.get("contract") != _identity(contract):
        return set()
    return set(saved.get("runs_tests") or [])


def why(command, where) -> str:
    """What was seen of ``command`` that gave it a role."""
    if where == WITH_TESTS:
        return f"`{command}` passes on the original code and fails there with the change's tests, so it runs them"
    return (
        f"`{command}` passes on the original code and fails there when the project's test files cannot be "
        "loaded, so it runs them"
    )


def note(chosen) -> str | None:
    """What the proof records when a role was found by running the plan's commands, or None."""
    observed = chosen.get("observed") if chosen else None
    if not observed:
        return None
    return "AutoCode ran the approved plan's commands to see what they do: " + "; ".join(
        why(command, where) for command, where in observed.items()
    )


def unsettled(state: RunState, base, contract, tree) -> str | None:
    """Why none of the plan's commands became the project's test command, for a proof that has none, or
    None when the plan gave no command to try."""
    saved = _saved(state, base, contract)
    seen = saved.get("seen") or {}
    rows = []
    for command in candidates(contract, tree)[:MAX_COMMANDS]:
        row = seen.get(command) or {}
        if ORIGINAL not in row:
            continue
        code = row[ORIGINAL]
        if code is None:
            rows.append(f"`{command}` did not finish on the original code")
        elif code == 127:
            rows.append(f"`{command}` exits 127 on the original code (command not found)")
        elif code:
            rows.append(f"`{command}` exits {code} on the original code")
        else:
            others = [where for where in (WITH_TESTS, WITHOUT_TESTS) if where in row and row[where] == 0]
            rows.append(
                f"`{command}` passes on the original code"
                + "".join(f", and still passes {_SHOWN[where][len('on the original code ') :]}" for where in others)
            )
    if not rows:
        return None
    return (
        "AutoCode found no test runner it reads in this project and ran the approved plan's commands to find "
        "the one that runs its tests; none could be used: " + "; ".join(rows)
    )


def kept(state: RunState) -> dict | None:
    """What a proof's receipt keeps of the roles found by running: enough for a replay to choose the same."""
    saved = state.get("plan_command_roles")
    if not isinstance(saved, dict) or saved.get("version") != VERSION or not saved.get("found"):
        return None
    return {key: saved[key] for key in ("version", "base", "contract", "suite", "regression", "runs_tests", "found")}


def restore(state: RunState, recorded, base, contract) -> None:
    """A replay runs nothing: it takes the roles the recorded proof kept, for this plan."""
    if not isinstance(recorded, dict) or recorded.get("version") != VERSION:
        return
    if recorded.get("contract") != _identity(contract):
        return
    # The replay's original code is the recording's, in a new repository whose commit differs.
    state["plan_command_roles"] = {**recorded, "base": base, "seen": {}}
