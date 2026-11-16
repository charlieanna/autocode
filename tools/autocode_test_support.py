"""A bug fix whose whole change is test support: a fixture, a test helper, test data.

A bug can be in what the tests are given rather than in the code they test: "test_a_week_is_seven_days
fails. The code is right: the `week` fixture in tests/conftest.py ends a day early. Fix the fixture." The
right change edits ``tests/conftest.py`` alone. The proof refused it, "Only test files changed; a fix must
change product code", the rule that keeps a Builder from making a failing test pass by editing tests. A
real run made the one-line fix, all three tests passed, and the run asked the person whether to make an
exception to that rule (task-kind survey, section 56).

Two things tell such a change from a test edited to pass, and both are seen, not guessed:

- the approved plan changes test files only (``planned``): the person approved a fix of the tests'
  support, as they approve every path a plan names;
- no changed file defines a test (``defines``): the base suite's per-test results name no test in any
  of them, and neither do the candidate's (``check``). A test's own assertions are unchanged; only what it
  is given changes.

The fix is then proven as a repair the project's tests already show (autocode_first_suite.existing_flip):
tests that ran and failed on the original code pass with the change, and nothing that passed there fails.
A runner that names no test cannot show that a file defines none, so the rule holds there as before.

The proof records those files as its ``test_support``. The original-test gate (autocode_protected_oracles)
reads that one answer, through ``proven``: it overlays the original bytes of every other changed test file,
and keeps the approved support the proof showed, so it still runs the original tests' own assertions.
"""

from __future__ import annotations

from pathlib import PurePosixPath


def _names(path) -> list[str]:
    """The prefixes a test id of ``path`` begins with: ``tests/conftest.py::`` (Node, Jest, RSpec, Go),
    ``tests.test_dates::`` (pytest, by module) and ``tests.helpers.`` (unittest), by the module's dotted
    name from any start directory."""
    pure = PurePosixPath(path)
    parts = [*pure.parent.parts, pure.stem] if pure.suffix else list(pure.parts)
    dotted = [".".join(parts[start:]) for start in range(len(parts))]
    return [f"{path}::", *(name + mark for name in dotted for mark in (".", "::"))]


def defines(paths, results) -> list[str]:
    """The tests of ``results`` (a runner's per-test results) that one of ``paths`` defines. A test id that
    could be one of theirs counts, so a doubt keeps the ordinary rule."""
    if not isinstance(results, dict):
        return []
    ids = {*results.get("passed", ()), *results.get("failed", ()), *(results.get("skipped") or ())}
    ids |= {*(results.get("collection_errors") or ()), *(results.get("uncollected") or ())}
    prefixes = [prefix for path in paths for prefix in _names(path)]
    return sorted(test for test in ids if any(str(test).startswith(prefix) for prefix in prefixes))


def support(tests, sources, ambiguous, changes, base_suite, planned) -> list[str]:
    """The changed test files, when the change is test support alone: the approved plan changes test files
    only (``planned``), no product file or file of unknown role changed, none was deleted, and the base
    suite's complete per-test results name no test any of them defines. Otherwise []."""
    results = ((base_suite or {}).get("receipt") or {}).get("results")
    if not (planned and tests) or sources or ambiguous or any(changes.get(path) == "deleted" for path in tests):
        return []
    if not (isinstance(results, dict) and results.get("complete")) or defines(tests, results):
        return []
    return sorted(tests)


def check(support_files, on_candidate, fail, notes, proof) -> None:
    """After the candidate's suite ran: a support file that now defines a test is a test edited to pass.
    Otherwise, when the project's tests showed the repair (``proof``'s fail_to_pass), say what it rests on."""
    if not support_files:
        return
    found = defines(support_files, (on_candidate or {}).get("results"))
    if found:
        fail.append(
            "The change edits only test files, and they define tests on the candidate ("
            + ", ".join(found[:5])
            + "): a fix must change product code, or leave the tests' own assertions as they were"
        )
        return
    if not proof.get("fail_to_pass"):
        return
    proof["test_support"] = sorted(support_files)
    notes.append(
        "The change edits only test support the approved plan names ("
        + ", ".join(support_files[:5])
        + "), which defines no test: the tests the project already has show the repair"
    )


def planned(contract, is_test) -> bool:
    """Whether the approved plan changes test files only: every path its milestones name is one
    ``is_test`` (the proof's role rule) calls a test file, and it names at least one."""
    body = (contract or {}).get("body") if isinstance(contract, dict) else None
    if (contract or {}).get("approval_status") != "approved" or not isinstance(body, dict):
        return False
    paths = [
        str(path).removeprefix("./")
        for milestone in body.get("milestones") or []
        if isinstance(milestone, dict)
        for path in milestone.get("affected_paths") or []
        if str(path).strip()
    ]
    return bool(paths) and all(is_test(path) for path in paths)


def proven(proof, revision) -> list[str]:
    """The test support a passing regression proof of this very candidate (``revision``, the source
    snapshot) showed, or []: the one answer the original-test gate reads."""
    proof = proof if isinstance(proof, dict) else {}
    if proof.get("verdict") != "PASS" or proof.get("source_revision") != revision:
        return []
    return sorted(str(path) for path in proof.get("test_support") or ())
