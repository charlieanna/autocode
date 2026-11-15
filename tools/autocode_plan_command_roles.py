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

Which files are tests is a rule over paths too (``tests/``, ``test_*.py``, ``*_spec.rb``), and a project
that keeps its checks elsewhere (``checks/pager_check.rb``, ``verify/pager.sh``) had no test at all by it:
the plan check said "the project has no tests yet", the proof said "No regression test was added or
changed", and a real bug fix in such a Ruby project ended, after 18 stages, asking the person to correct a
test command (2026-11-15). What a command does shows this much of a file (``leaves``), for a command that
passes on the original code:

- the command fails once the file is made unloadable: it runs the file;
- the command passes once the file is emptied: nothing else needs what the file defines.

Code other files need fails the second, and a file the command never loads fails the first. But a
program's own entry file passes both as a test does, and so does a module a program loads when it is there:
no run tells a file that checks the code from a file that is the code. So the looks never make a file a
test by themselves. Two things are needed (``find_tests``):

- the approved plan says which command runs this project's tests (``declared``): a criterion that the
  command "fails on the original code and passes with the fix", the form a plan is asked to write where
  no runner names a test, or a command guard (``guard: run `ruby run_checks.rb```), which runs the tests
  the project already has. That is the plan's word, which the person approves, as a file under ``tests/``
  is the project's. A criterion to "run `./app.sh` and see the new line" says no such thing;
- the looks show that this command runs the file and that nothing else needs it. A file the command names
  is left out: ``sh pager.sh`` runs pager.sh, and that is the program.

A file the change edits is looked at on the original code, where the change cannot have arranged the
answer, and one it adds on the changed code. Such a file is taken with the tests where the proof keeps
tests apart from the code (autocode_verify's ``test_side``): the original code the command must fail on
holds it, and the check of the original tests keeps it as the original code has it. It stays code for the
evidence of changed lines, which a rule decides, so nothing is excused by it. The guard itself still needs
a test of the original code: the edited file when the original code has it, or one beside a new file.

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
import re
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from . import autocode_plan_proof_command as plan_command
    from . import autocode_test_cases as test_cases
    from . import autocode_util as util
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_plan_proof_command as plan_command
    import autocode_test_cases as test_cases
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
# A file of the original code, or of the changed code, made unloadable or emptied (``find_tests``).
CHANGE = "change"
UNLOADED, EMPTIED = "unloadable", "emptied"
# How a command got its role when a file was seen to be a test it runs.
OBSERVED_TESTS = "observed_tests"
# One look for tests tries this many files.
MAX_FILES = 6
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


# A criterion that says its command shows a regression: it "fails on the original code and passes with the
# fix", the form a plan is asked to write where no runner names a test (autocode_plan_test_suite).
_FAILS_FIRST = re.compile(r"\bfails?\b.*\bpass(?:es)?\b", re.DOTALL | re.IGNORECASE)


def declared(body, tree) -> list[str]:
    """The commands the plan says run this project's tests, in criterion order: the command of a criterion
    that says it fails on the original code and passes with the change, and a command guard's
    (autocode_command_guards), which runs the tests the project already has. That is the plan's word, which
    the person approves; ``find_tests`` checks it by running the command."""
    found: list[str] = []
    for row in (body.get("acceptance_criteria") if isinstance(body, dict) else None) or []:
        if not isinstance(row, dict) or row.get("human_review"):
            continue
        method = str(row.get("verification_method") or "")
        if test_cases.guard_command(method) or (not test_cases.mark(method) and _FAILS_FIRST.search(method)):
            found += candidates({"approval_status": "approved", "body": {"acceptance_criteria": [row]}}, tree)
    return list(dict.fromkeys(found))


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
        **{key: saved[key] for key in ("probes", "tests", "tests_command", "existing_test") if key in saved},
    }


def _named(command, files) -> set[str]:
    """The ``files`` a word of ``command`` names, also when it names them from inside a directory of the
    project (``cd backend && ./run_checks.sh`` names backend/run_checks.sh)."""
    named = plan_command.test_files(command, files)
    words = command.split()
    if len(words) > 3 and words[0] == "cd" and words[2] == "&&":
        folder = words[1].removeprefix("./").strip("/") + "/"
        inside = {path[len(folder) :]: path for path in files if path.startswith(folder)}
        named |= {inside[name] for name in plan_command.test_files(" ".join(words[3:]), inside)}
    return named


def leaves(command, files, probe) -> dict[str, dict]:
    """The ``files`` ({path: ORIGINAL or CHANGE}, in the order to look) that are tests ``command`` runs:
    it fails when the file is unloadable and passes when the file is empty. ``probe(command, path, how,
    where)`` runs it on the original or the changed code with ``path`` made UNLOADED or EMPTIED and
    returns its exit code, or None. At most MAX_FILES files are looked at. {path: what was seen}.

    A file the command names is left out: ``sh pager.sh`` runs pager.sh and nothing else needs it, and
    that is the program, not a test of it. Where a path rule calls the named file a test the rules have
    answered already (autocode_plan_proof_command.choose)."""
    found: dict[str, dict] = {}
    named = _named(command, files)
    for path, where in [item for item in files.items() if item[0] not in named][:MAX_FILES]:
        unloaded = probe(command, path, UNLOADED, where)
        if unloaded in (0, None):
            continue
        emptied = probe(command, path, EMPTIED, where)
        if emptied == 0:
            found[path] = {"where": where, UNLOADED: unloaded, EMPTIED: emptied}
    return found


def find_tests(state: RunState, base, contract, *, commands, files, tree, observe, probe, beside=()) -> dict[str, dict]:
    """See which of ``files`` are tests a command of ``commands`` runs, where no path rule names a test,
    and keep them in ``state`` with the command, which takes the roles still empty.

    ``commands`` are the ones the approved plan says run this project's tests (``declared``): what a
    command does shows that it runs a file and that nothing else needs the file, which is as true of a
    program's own entry file as of a test, so the plan says which command is the test command and the
    looks check it. ``files`` are the code files to look at, as ``leaves`` takes them: the change's.
    ``observe`` is ``settle``'s; ``probe`` is ``leaves``'. The first such command that passes on the
    original code and runs one of the files is taken, of at most MAX_COMMANDS. When every file it runs is
    new, the original code's files ``beside`` them are looked at for one it runs too: only a test of the
    original code shows that the command runs tests the project already has. What a command does on the
    original code is kept; what it does on the changed code is looked at again each time."""
    saved = _saved(state, base, contract)
    seen: dict[str, dict[str, Any]] = {command: dict(row) for command, row in (saved.get("seen") or {}).items()}
    probes: dict[str, Any] = dict(saved.get("probes") or {})
    found_tests: dict[str, dict] = {}
    chosen = None

    def look(command):
        row = seen.setdefault(command, {})
        if ORIGINAL not in row:
            row[ORIGINAL] = observe(command, ORIGINAL)
        return row[ORIGINAL]

    def looked(command, path, how, where):
        key = f"{command}\n{path}\n{how}"
        if where == ORIGINAL and key in probes:
            return probes[key]
        code = probe(command, path, how, where)
        if where == ORIGINAL:
            probes[key] = code
        return code

    declared = [command for command in candidates(contract, tree) if command in set(commands)]
    existing = None
    for command in declared[:MAX_COMMANDS] if files else []:
        if look(command) != 0:
            continue
        if found_tests := leaves(command, files, looked):
            chosen = command
            existing = next((path for path, row in found_tests.items() if row["where"] == ORIGINAL), None)
            for path in () if existing else list(beside)[:MAX_FILES]:
                if leaves(command, {path: ORIGINAL}, looked):
                    existing = path
                    break
            break
    runs = set(saved.get("runs_tests") or [])
    found = {command: how for command, how in (saved.get("found") or {}).items() if how != OBSERVED_TESTS}
    suite, regression, before = saved.get("suite"), saved.get("regression"), saved.get("tests_command")
    if before and before != chosen and before not in found:
        # The command an earlier look took for files that are tests no longer: its roles go with them.
        suite = None if suite == before else suite
        regression = None if regression == before else regression
        runs.discard(before)
    if chosen:
        suite, regression = suite or chosen, regression or chosen
        found.setdefault(chosen, OBSERVED_TESTS)
        if existing:
            runs.add(chosen)
    state["plan_command_roles"] = {
        "version": VERSION,
        "base": base,
        "contract": _identity(contract),
        "suite": suite,
        "regression": regression,
        "runs_tests": sorted(runs),
        "found": found,
        "seen": seen,
        "probes": probes,
        "tests": found_tests,
        "tests_command": chosen,
        "existing_test": existing,
    }
    return found_tests


def tests(state: RunState, base, contract) -> dict[str, dict]:
    """The files ``find_tests`` saw to be tests the plan's command runs, with what was seen of each."""
    found = _saved(state, base, contract).get("tests")
    return dict(found) if isinstance(found, dict) else {}


def has_tests(state: RunState) -> bool:
    """Whether this run saw the plan's guard command run a file of the project as a test: the project has
    tests and a command for them, whatever the path rules read."""
    saved = state.get("plan_command_roles")
    return isinstance(saved, dict) and saved.get("version") == VERSION and bool(saved.get("tests_command"))


def tests_note(state: RunState, base, contract) -> str | None:
    """What the proof records of the files taken with the tests because of what a command does, or None."""
    saved = _saved(state, base, contract)
    found, command = saved.get("tests") or {}, saved.get("tests_command")
    if not found or not command:
        return None
    names, one = ", ".join(sorted(found)), len(found) == 1
    return (
        f"No rule says {names} {'is a test file' if one else 'are test files'}. AutoCode ran `{command}` to "
        f"see: it fails when {'that file' if one else 'each of them'} cannot be loaded and passes when "
        f"{'it is' if one else 'each is'} empty, so the command runs {'it' if one else 'them'} and nothing "
        f"else needs {'it' if one else 'them'}; and the approved plan says that command runs this project's "
        f"tests. So {names} {'is' if one else 'are'} taken with the change's "
        f"tests: the original code `{command}` must fail on holds {'it' if one else 'them'}, and the original "
        "tests are run with the change as the original code has them. The changed lines there are still "
        "checked as code"
    )


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
    if where == OBSERVED_TESTS:
        return f"`{command}` passes on the original code and runs files it shows to be tests"
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
    keys = ("version", "base", "contract", "suite", "regression", "runs_tests", "found")
    return {key: saved[key] for key in (*keys, "tests", "tests_command", "existing_test") if key in saved}


def restore(state: RunState, recorded, base, contract) -> None:
    """A replay runs nothing: it takes the roles the recorded proof kept, for this plan."""
    if not isinstance(recorded, dict) or recorded.get("version") != VERSION:
        return
    if recorded.get("contract") != _identity(contract):
        return
    # The replay's original code is the recording's, in a new repository whose commit differs.
    state["plan_command_roles"] = {**recorded, "base": base, "seen": {}}
