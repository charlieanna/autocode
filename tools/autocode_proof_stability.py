"""Repeat the proving tests so one lucky pass is not evidence (#700).

Named proof (``test:`` and ``guard:``) runs each test once on the candidate and, for a
``test:`` criterion, once on the original code. A test that passes one time in three
counts whenever that single run passes. This module decides which tests to repeat, how
to run only them, and which disagreed with themselves. A test that does not agree with
itself is ``unstable`` and does not count. The rest of the suite still runs once.

Imports the test-name matcher, the Python, Cargo, RSpec and Minitest command parsers and the package command form
only. The caller owns the scratch trees and the suite runner.
"""

from __future__ import annotations

import copy
import re
import shlex

try:
    from . import autocode_cargo_tests as cargo_tests
    from . import autocode_minitest_tests as minitest_tests
    from . import autocode_package_scope as package_scope
    from . import autocode_python_tests as python_tests
    from . import autocode_rspec_tests as rspec_tests
    from . import autocode_shell_syntax as shell_syntax
    from . import autocode_source_binding as source_binding
    from . import autocode_test_cases as test_cases
except ImportError:
    import autocode_cargo_tests as cargo_tests
    import autocode_minitest_tests as minitest_tests
    import autocode_package_scope as package_scope
    import autocode_python_tests as python_tests
    import autocode_rspec_tests as rspec_tests
    import autocode_shell_syntax as shell_syntax
    import autocode_source_binding as source_binding
    import autocode_test_cases as test_cases

REPEATS = 3
NEXT_STEP = (
    "Fix the unstable test so it passes on every run. A test: criterion must also fail "
    "on every run against the original code. Do not retry the proof until the test is stable."
)
# Shell syntax where the shell reads it (autocode_shell_syntax): a quoted test name is a literal word.
SHELL = ";&|<>`$\n"


def runner(run, trees, framework, run_suite, timeout):
    """The owner's one repeat runner, retaining each repeat's receipt in the proof."""

    def repeat(command, side, label):
        if run.progress:
            run.progress(
                f"Checking regression test stability ({label})", command=command, output=run.run_dir, phase="stability"
            )
        tree = trees["candidate" if side == "candidate" else "base_with_tests"]
        run.checks[label] = run_suite(
            framework, command, tree, run.run_dir, label, timeout=timeout, source_workspace=run.workspace
        )
        return run.checks[label]

    return repeat


def proving_tests(cases, candidate_ids, base_ids, framework_name) -> dict[str, str]:
    """Map each test a due case matches to ``restore`` (``test:``) or ``preserve`` (``guard:``).

    A test matched by both is ``restore``: fail-to-pass needs a reliable failure on the original code.
    """
    ids = sorted(set(candidate_ids) | set(base_ids))
    matched = test_cases.match_cases(list(cases or []), ids, framework=framework_name)
    selected: dict[str, str] = {}
    for case in cases or []:
        kind = "preserve" if case.get("kind") == "preserve" else "restore"
        for test in matched.get(case.get("id")) or []:
            if selected.get(test) != "restore":
                selected[test] = kind
    return selected


def classify(outcomes) -> str:
    """``pass``, ``fail``, ``unstable`` or ``unrepeated`` across one test's runs.

    Only a pass and a fail of the same test disagree. A run that did not report the test (a
    selector that missed it, a run that timed out) proves nothing either way: ``unrepeated``.
    """
    outcomes = list(outcomes or [])
    if "pass" in outcomes and "fail" in outcomes:
        return "unstable"
    if not outcomes or "missing" in outcomes:
        return "unrepeated"
    return outcomes[0]


def outcome_of(receipt, test) -> str:
    """How one receipt recorded ``test``: pass, fail, or missing."""
    if not receipt or not source_binding.established(receipt) or receipt.get("timed_out") or receipt.get("error"):
        return "missing"
    results = receipt.get("results") or {}
    if not results.get("complete"):
        return "missing"
    if test in (results.get("passed") or []):
        return "pass"
    if test in (results.get("failed") or []):
        return "fail"
    return "missing"


def judge(selected, candidate_runs, base_runs) -> tuple[list[dict], list[str]]:
    """(unstable proving tests, tests a repeat did not report). ``guard:`` tests are judged on the
    candidate only."""
    rows, unrepeated = [], set()
    for test, kind in selected.items():
        sides = [("candidate", [outcome_of(run, test) for run in candidate_runs])]
        if kind == "restore":
            sides.append(("base", [outcome_of(run, test) for run in base_runs if run]))
        for side, runs in sides:
            verdict = classify(runs) if runs else None
            if verdict == "unstable":
                rows.append({"test": test, "side": side, "kind": kind, "runs": runs})
            elif verdict == "unrepeated" and runs[0] != "missing" and "missing" in runs[1:]:
                # Reported once, then not: the repeat could not run it. Absent every time (a
                # feature's test on the original code) is expected.
                unrepeated.add(test)
    return rows, sorted(unrepeated)


def without(receipt, unstable):
    """A copy of ``receipt`` whose results no longer list the unstable tests, so they cannot count as
    proof. The receipt itself stays as the runner recorded it."""
    blocked = {row["test"] for row in unstable}
    if not receipt or not blocked or not receipt.get("results"):
        return receipt
    judged = dict(receipt)
    judged["results"] = copy.deepcopy(receipt["results"])
    for key in ("passed", "failed"):
        judged["results"][key] = [test for test in judged["results"].get(key) or [] if test not in blocked]
    return judged


def repeat(
    framework_name, command, cases, on_candidate, on_base, run, *, base_tree, directory=None
) -> tuple[list[dict], dict | None]:
    """Run the proving tests ``REPEATS`` times in all, then judge them: (unstable rows, record or None).

    ``on_candidate`` and ``on_base`` are the judged first runs. ``run(command, side, label)`` runs
    one more narrowed command in the scratch tree for ``side`` (``candidate`` or ``base``) and returns
    its receipt; ``base_tree`` says whether a base tree with the tests exists. Nothing repeats when
    no due case names a test, when the first candidate run did not report its tests, or when the first run
    on the original code timed out: it would again, and the proof is unverified whatever a repeat shows.
    ``directory`` is the run's package when the command is its detected suite (``narrow``).
    """
    results = (on_candidate or {}).get("results") or {}
    if not cases or not command or not results.get("complete") or (on_base or {}).get("timed_out"):
        return [], None
    base_results = (on_base or {}).get("results") or {}
    selected = proving_tests(
        cases,
        [*(results.get("passed") or []), *(results.get("failed") or [])],
        [*(base_results.get("passed") or []), *(base_results.get("failed") or [])],
        framework_name,
    )
    if not selected:
        return [], None
    narrowed = narrow(framework_name, command, list(selected), directory=directory)
    if (
        framework_name == "junit"
        and results.get("total") == len(selected)
        and set(selected) == {test for group in ("passed", "failed", "skipped") for test in results.get(group, [])}
        and not {test for group in ("passed", "failed", "skipped") for test in base_results.get(group, [])}
        - set(selected)
        and not shell_syntax.outside_quotes(command, SHELL)
    ):
        narrowed = command
    if not narrowed:
        return [], record([], note="this runner cannot select the proving tests, so they ran once")
    restore = base_tree and on_base is not None and "restore" in selected.values()
    candidate_runs, base_runs = [on_candidate], [on_base] if on_base is not None else []
    for index in range(2, REPEATS + 1):
        candidate_runs.append(run(narrowed, "candidate", f"stability-candidate-{index}"))
        if restore:
            base_runs.append(run(narrowed, "base", f"stability-base-{index}"))
    unstable, unrepeated = judge(selected, candidate_runs, base_runs)
    return unstable, record(unstable, unrepeated)


def failure_text(rows) -> str:
    parts = []
    for row in rows:
        where = "the candidate" if row["side"] == "candidate" else "the original code"
        parts.append(f"{row['test']} is unstable on {where} ({', '.join(row['runs'])})")
    return "Unstable proving tests do not count as proof: " + "; ".join(parts) + ". " + NEXT_STEP


def record(rows, unrepeated=(), note=None) -> dict:
    if unrepeated and not note:
        note = "a repeat did not report " + ", ".join(unrepeated) + ", so its single run stands as proof"
    return {
        "repeats": REPEATS,
        "unstable": rows,
        "unrepeated": list(unrepeated),
        "next_step": NEXT_STEP if rows else None,
        "note": note,
    }


def narrow(framework_name, command, test_ids, *, directory=None) -> str | None:
    """A command that runs only ``test_ids``, or None when this runner cannot select them.

    The whole suite is never the fallback: a command that cannot name the proving tests is not repeated.
    A package's command (``directory``, autocode_package_scope) is narrowed inside the package.
    """
    inner = package_scope.inner(directory, command) if directory else None
    if inner is not None:
        narrowed = narrow(framework_name, inner, test_ids)
        return package_scope.command(directory, narrowed) if narrowed else None
    if framework_name == "minitest":
        # Read by Minitest's own command parser, which refuses a backslash and parentheses outside quotes too.
        return minitest_tests.narrow(command, test_ids) if command and test_ids else None
    if not command or not test_ids or shell_syntax.outside_quotes(command, SHELL):
        return None
    invocation = python_tests.parse(command, include_owned=True)
    if invocation and invocation.kind == "unittest":
        selectors = [test.replace("::", ".") for test in test_ids]
        if invocation.discovers:
            # Discovery establishes the import root and may invoke package
            # load_tests hooks. Direct dotted imports at cwd lose that context.
            # Keep its collection options and replace its OR-ed -k filters with
            # the independently observed full IDs. Strict mutation admission
            # still verifies that exactly the selected tests reported results.
            arguments, index = [], 0
            while index < len(invocation.arguments):
                word = invocation.arguments[index]
                if word == "-k":
                    if index + 1 == len(invocation.arguments):
                        return None
                    index += 2
                    continue
                if not word.startswith("-k"):
                    arguments.append(word)
                index += 1
            for selector in selectors:
                arguments.extend(("-k", selector))
            return invocation.render(arguments=arguments)
        return shlex.join([*invocation.prefix, invocation.python, "-m", "unittest", "-v", *selectors])
    if invocation and invocation.kind == "pytest":
        names = sorted({test.rsplit("::", 1)[-1].split("[", 1)[0] for test in test_ids})
        return shlex.join([*shlex.split(command), "-k", " or ".join(names)])
    if framework_name == "go" and command.startswith("go test ") and " -run" not in command:
        tops = {test.split("::", 1)[-1].split("/", 1)[0] for test in test_ids}
        # -run selects tests, examples and fuzz targets alike. A name it cannot select leaves no command:
        # a narrowed run that silently left a proving test out would not be the run the proof judged.
        go_names = sorted(tops)
        if not go_names or not all(re.fullmatch(r"(?:Test|Example|Fuzz)[A-Za-z0-9_]*", name) for name in go_names):
            return None
        pattern = "^(" + "|".join(go_names) + ")$"
        return "go test -run " + shlex.quote(pattern) + " " + command[len("go test ") :]
    if framework_name == "node" and command.startswith("node --test ") and "--test-name-pattern" not in command:
        # Unanchored: extra matches are not judged, and an anchor can miss a nested test's full name.
        names = sorted({re.escape(test.split("::")[-1]) for test in test_ids})
        pattern = "|".join(names)
        return "node --test --test-name-pattern " + shlex.quote(pattern) + " " + command[len("node --test ") :]
    if framework_name in ("vitest", "jest", "mocha"):
        return _narrow_js(framework_name, command, test_ids)
    if framework_name == "cargo":
        return _narrow_cargo(command, test_ids)
    if framework_name == "rspec":
        return _narrow_rspec(command, test_ids)
    return None


# Cargo options that take the next word as their value, so that word is not a test-name filter.
_CARGO_VALUES = frozenset(
    {
        "-p",
        "--package",
        "--exclude",
        "-F",
        "--features",
        "--target",
        "--target-dir",
        "--manifest-path",
        "--test",
        "--bench",
        "--bin",
        "--example",
        "-j",
        "--jobs",
        "--profile",
        "--color",
        "--config",
        "-Z",
        "--lockfile-path",
    }
)
_HARNESS_VALUES = frozenset({"--test-threads", "--skip", "--color", "-Z", "--shuffle-seed"})


def _positional(words, values) -> bool:
    """Whether ``words`` hold a word that is no option and no option's value: a test-name filter."""
    index = 0
    while index < len(words):
        word = words[index]
        if word in values:
            index += 2
            continue
        if not word.startswith("-"):
            return True
        index += 1
    return False


def _narrow_cargo(command: str, test_ids: list[str]) -> str | None:
    """``cargo test`` with each proving test's exact harness path (``FILE::module::name`` gives
    ``module::name``). A filter applies to every test binary, so a test of the same path in another
    binary may run too; it is not judged (autocode_mutation_results.selected)."""
    words = cargo_tests.command_words(command)
    if not words:
        return None
    split = words.index("--") if "--" in words else len(words)
    start = words.index("cargo") + 2
    cargo, harness = words[start:split], words[split + 1 :]
    if _positional(cargo, _CARGO_VALUES) or "--exact" in harness or _positional(harness, _HARNESS_VALUES):
        return None
    names = sorted({test.partition("::")[2] for test in test_ids if "::" in test})
    if not names or any(not name or name.startswith("[") for name in names):
        return None
    # rustdoc never matches a documentation test's name (``src/lib.rs - (line 20)``) under --exact, so a
    # selection holding one filters by substring: a test whose name contains it may run too, not judged.
    exact = [] if any(test.startswith("Doc-tests ") for test in test_ids) else ["--exact"]
    return shlex.join([*words[:split], "--", *harness, *exact, *names])


def _narrow_rspec(command: str, test_ids: list[str]) -> str | None:
    """``rspec`` with one ``--example`` per proving test's full description (``FILE::DESCRIPTION``).
    RSpec matches a description as a substring, so another example containing it may run too; it is
    not judged (autocode_mutation_results.selected)."""
    words = rspec_tests.command_words(command)
    if not words or any(
        word in ("-e", "--example", "-E", "--example-matches") or word.startswith(("--example=", "--example-matches="))
        for word in words
    ):
        return None
    names = sorted({test.partition("::")[2] for test in test_ids if "::" in test})
    if not names or any(not name.strip() or name.startswith("[") for name in names):
        return None
    return shlex.join([*words, *(part for name in names for part in ("--example", name))])


_JS_REGEX_META = re.compile(r"([.*+?^${}()|[\]\\])")


def _narrow_js(framework_name: str, command: str, test_ids: list[str]) -> str | None:
    """Narrow a direct Vitest, Jest, or Mocha command to the failing files and test names."""
    if shell_syntax.outside_quotes(command, SHELL):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words:
        return None
    if framework_name == "vitest":
        if "vitest run" not in command or any(w == "-t" or w.startswith("-t=") for w in words):
            return None
        filter_flag = "-t"
    elif framework_name == "jest":
        if not any(w == "jest" or w.endswith(("/jest", "/jest.js")) for w in words):
            return None
        if any(w in ("-t", "--testNamePattern") or w.startswith("--testNamePattern=") for w in words):
            return None
        filter_flag = "-t"
    else:
        if not any(w == "mocha" or w.endswith(("/mocha", "/mocha.js")) for w in words):
            return None
        if any(w in ("-g", "--grep", "-f", "--fgrep") or w.startswith(("--grep=", "--fgrep=")) for w in words):
            return None
        filter_flag = "--grep"
    files = sorted({test.partition("::")[0] for test in test_ids if "::" in test and not test.startswith("[")})
    missing_files = [file for file in files if file not in words]
    raw_names = [test.partition("::")[2] if "::" in test else test for test in test_ids]
    pattern = "|".join(sorted({_JS_REGEX_META.sub(r"\\\1", name.replace(" > ", " ")) for name in raw_names if name}))
    if not pattern:
        return None
    return shlex.join([*words, *missing_files, filter_flag, pattern])
