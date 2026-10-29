"""Repeat the proving tests so one lucky pass is not evidence (#700).

Named proof (``test:`` and ``guard:``) runs each test once on the candidate and, for a
``test:`` criterion, once on the original code. A test that passes one time in three
counts whenever that single run passes. This module decides which tests to repeat, how
to run only them, and which disagreed with themselves. A test that does not agree with
itself is ``unstable`` and does not count. The rest of the suite still runs once.

Imports the test-name matcher, the Python command parser and the package command form only. The
caller owns the scratch trees and the suite runner.
"""

from __future__ import annotations

import copy
import re
import shlex

try:
    from . import autocode_package_scope as package_scope
    from . import autocode_python_tests as python_tests
    from . import autocode_test_cases as test_cases
except ImportError:
    import autocode_package_scope as package_scope
    import autocode_python_tests as python_tests
    import autocode_test_cases as test_cases

REPEATS = 3
NEXT_STEP = (
    "Fix the unstable test so it passes on every run. A test: criterion must also fail "
    "on every run against the original code. Do not retry the proof until the test is stable."
)
SHELL = re.compile(r"[;&|<>`$\n]")


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
    if not receipt or receipt.get("timed_out") or receipt.get("error"):
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
    no due case names a test, or when the first candidate run did not report its tests. ``directory``
    is the run's package when the command is its detected suite (``narrow``).
    """
    results = (on_candidate or {}).get("results") or {}
    if not cases or not command or not results.get("complete"):
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
        and not SHELL.search(command)
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
    if not command or not test_ids or SHELL.search(command):
        return None
    invocation = python_tests.parse(command)
    if invocation and invocation.kind == "unittest":
        selectors = [test.replace("::", ".") for test in test_ids]
        if invocation.arguments and invocation.arguments[0] == "discover":
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
            return shlex.join([*invocation.prefix, invocation.python, "-m", "unittest", *arguments])
        return shlex.join([*invocation.prefix, invocation.python, "-m", "unittest", "-v", *selectors])
    if invocation and invocation.kind == "pytest":
        names = sorted({test.rsplit("::", 1)[-1].split("[", 1)[0] for test in test_ids})
        return shlex.join([*shlex.split(command), "-k", " or ".join(names)])
    if framework_name == "go" and command.startswith("go test ") and " -run" not in command:
        tops = {test.split("::", 1)[-1].split("/", 1)[0] for test in test_ids}
        go_names = sorted(name for name in tops if re.fullmatch(r"Test[A-Za-z0-9_]+", name))
        if not go_names:
            return None
        pattern = "^(" + "|".join(go_names) + ")$"
        return "go test -run " + shlex.quote(pattern) + " " + command[len("go test ") :]
    if framework_name == "node" and command.startswith("node --test ") and "--test-name-pattern" not in command:
        # Unanchored: extra matches are not judged, and an anchor can miss a nested test's full name.
        names = sorted({re.escape(test.split("::")[-1]) for test in test_ids})
        pattern = "|".join(names)
        return "node --test --test-name-pattern " + shlex.quote(pattern) + " " + command[len("node --test ") :]
    if framework_name == "vitest" and "vitest run" in command and " -t " not in command:
        # Vitest matches -t against the full name, describe blocks included, so it is not anchored.
        names = sorted({re.escape(test.split("::")[-1]) for test in test_ids})
        return command + " -t " + shlex.quote("|".join(names))
    return None
