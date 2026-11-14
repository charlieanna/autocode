"""Original tests an approved plan replaces because they assert the behavior being fixed (#2748).

A bug fix often meets an existing test that pins the bug (``shipping_fee(5000) == 499``). The original-test
gate (autocode_protected_oracles.replay) re-runs the changed original tests against the fix, so that one assertion
can never pass, and the run used to stop and ask the person for a hand-built gate revision.

An approved criterion may now name the original tests it replaces in ``supersedes``: each entry is the
protected test file and the test's own name (``{"file": "tests/test_shipping.py", "test":
"test_fifty_dollar_order_pays_standard_fee"}``). The plan approval covers that list, the approval screen shows
it, and only a ``test:`` criterion may carry one, so the regression proof still requires its new test to fail
on the original code and pass on the fix. The changed original tests still all run: the result is accepted only when
the run reported complete per-test results and every failure is one of the listed tests in a file the change
edited. Any other failing original test, a crash, a timeout or an unattributed runner keeps the gate closed.
An original Python test file that cannot be loaded against the change is accepted only as ``excused`` says.

Pure functions over run state and runner receipts. Depends only on lower-level helpers.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as contracts
    from . import autocode_protected_store as store
    from . import autocode_test_cases as test_cases
except ImportError:
    import autocode_contract_identity as contracts
    import autocode_protected_store as store
    import autocode_test_cases as test_cases

KEY = "supersedes"
_PARAMETERS = re.compile(r"\[.*\]$")


def _entries(body) -> list[dict]:
    rows = body.get("acceptance_criteria") if isinstance(body, dict) else None
    found = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for entry in row.get(KEY) or []:
            if isinstance(entry, dict):
                found.append({"criterion": row.get("id"), "file": entry.get("file"), "test": entry.get("test")})
    return found


def approved(state: RunState) -> list[dict]:
    """The superseded original tests of the approved plan; none before approval."""
    if not contracts.approved(state):
        return []
    return _entries((state.get("goal_contract") or {}).get("body"))


def _defines(text: str, name: str) -> bool:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text) is not None
    return name in text  # a node:test or Vitest name is a string, such as 'fifty dollars ships free'


def validate(state: RunState, criteria) -> None:
    """Refuse a draft whose ``supersedes`` cannot name exactly one original test.

    Each entry sits on a ``test:`` criterion, names a regular file of this run's original-test binding, and a
    test name that file's original bytes define. A name another original file in the same directory defines
    too is refused: a Go package's results name the package, not the file, so it could not be told apart.
    """
    rows = [row for row in criteria or [] if isinstance(row, dict) and row.get(KEY)]
    if not rows:
        return
    record = (state.get("settings") or {}).get("protected_tests") or {}
    files = record.get("files") or {}
    if not files:
        raise ValueError(
            "supersedes names original tests, but this run has no original-test gate to supersede; leave it empty"
        )
    seen = set()
    wanted = []
    for row in rows:
        if test_cases.mark(row.get("verification_method")) != test_cases.MARK:
            raise ValueError(
                f"Criterion {row.get('id')} supersedes an original test but is not proven by a test: criterion; "
                "only a new test that fails on the original code and passes with the change may replace one"
            )
        for entry in row[KEY]:
            name, test = str(entry.get("file") or ""), str(entry.get("test") or "")
            if not test.strip() or "\n" in test or test != test.strip():
                raise ValueError(f"Criterion {row.get('id')} supersedes a test without a single-line test name")
            value = files.get(name)
            if not isinstance(value, dict) or "symlink" in value:
                raise ValueError(
                    f"Criterion {row.get('id')} supersedes {name}::{test}, but {name!r} is not an original test file "
                    "of this run; name the file exactly as the protected-test inventory lists it"
                )
            if (name, test) in seen:
                raise ValueError(f"{name}::{test} is superseded twice; list it on one criterion")
            seen.add((name, test))
            wanted.append((row.get("id"), name, test))
    with store.opened(record, state.get("workspace") or ".") as root:
        for criterion, name, test in wanted:
            if not _defines((root / name).read_text(errors="replace"), test):
                raise ValueError(
                    f"Criterion {criterion} supersedes {name}::{test}, but the original {name} does not define {test!r}"
                )
            directory = PurePosixPath(name).parent
            for other, value in files.items():
                if (
                    other != name
                    and "symlink" not in value
                    and PurePosixPath(other).parent == directory
                    and _defines((root / other).read_text(errors="replace"), test)
                ):
                    raise ValueError(
                        f"Criterion {criterion} supersedes {name}::{test}, but the original {other} defines "
                        f"{test!r} too, so its results could not be told apart"
                    )


def _same_file(container: str, name: str) -> bool:
    """A runner's file container (relative to the project or to a package run's directory) is ``name``."""
    container = container.strip().removeprefix("./")
    return bool(container) and (container == name or name.endswith("/" + container))


def own_names(rest: str) -> tuple[str, ...]:
    """The names a test may have by itself in the part of a runner's id after its file: the last of
    ``suite::title`` (pytest, node:test) and of ``group > title`` (Vitest, Jest), shortest first."""
    last = rest.rsplit("::", 1)[-1]
    return tuple(dict.fromkeys((last.rsplit(" > ", 1)[-1], last)))


def matches(entry: dict, test_id: str) -> bool:
    """Whether a runner's test id is the superseded test, or one of its parameter sets or Go subtests.

    An RSpec id is the file and the example's full description, its groups' descriptions and then its own
    (autocode_rspec_tests), so the test's name is that description or its last words."""
    name, test = str(entry.get("file") or ""), str(entry.get("test") or "")
    if not name or not test:
        return False
    test_id = _PARAMETERS.sub("", str(test_id))
    if "::" in test_id:
        container, rest = test_id.split("::", 1)
        if name.endswith(".go"):
            # Go names the package (an import path); the file's directory is its tail.
            directory = str(PurePosixPath(name).parent)
            package_ok = directory in ("", ".") or container == directory or container.endswith("/" + directory)
            return package_ok and (rest == test or rest.startswith(test + "/"))
        return _same_file(container, name) and (
            test in own_names(rest) or (name.endswith(".rb") and rest.endswith(" " + test))
        )
    # unittest: package.module.Class.test_name
    module = name[:-3].replace("/", ".") if name.endswith(".py") else ""
    if not module or not test_id.endswith("." + test):
        return False
    owner = test_id[: -len(test) - 1]
    return owner.startswith(module + ".") or ("." + module + ".") in owner or owner == module


def replaced(state: RunState):
    """Whether a runner's test id is a test the approved plan replaces, as a function of the id; None when
    the plan replaces none.

    Such a test may be renamed or removed with the change (owner decision, 2026-11-13): the regression proof
    does not count it among the tests that disappeared (autocode_verify_judgment.judge_suite). Every other
    original test must still be there and pass, and the original tests still run against the change, where
    only these may fail (``excused``).
    """
    try:
        entries = approved(state)
    except (KeyError, TypeError):
        return None  # a contract that was never sealed approves nothing
    return (lambda test_id: any(matches(entry, test_id) for entry in entries)) if entries else None


_UNLOADED = re.compile(r"(?:unittest\.loader\.(?:_FailedTest|ModuleImportFailure)\.|::)(.+)")


def unloaded_file(test_id, changed) -> str | None:
    """The one changed Python test file a runner's id says could not be loaded, else None.

    unittest names the module it could not import (``unittest.loader._FailedTest.tests.test_pager``, or the
    file's own name when it ran the file); pytest names the file or the module after an empty class name."""
    found = _UNLOADED.fullmatch(str(test_id))
    if not found:
        return None
    module = found.group(1).removesuffix(".py").replace("/", ".").strip(".")
    if not module:
        return None
    files = [
        name
        for name in changed or ()
        if name.endswith(".py") and ("." + name[:-3].replace("/", ".")).endswith("." + module)
    ]
    return files[0] if len(files) == 1 else None


def _accounted(file, entries, structure) -> bool:
    """Whether every test the change altered in ``file`` is one the plan replaces, and the rest is as it was
    (autocode_test_file_changes.summary)."""
    found = (structure or {}).get(file)
    if not isinstance(found, dict) or found.get("intact") is not True or not isinstance(found.get("altered"), list):
        return False
    listed = {entry.get("test") for entry in entries if entry.get("file") == file}
    return set(found["altered"]) <= listed


def excused(row, entries, changed, structure=None) -> list[str] | None:
    """The original run's failing test ids when each is a superseded test in a changed file, else None.

    The run must have ended on its own with complete per-test results and at least one failure. A
    superseded test in a file the change left alone is not excused: there it is not replaced.

    Nothing may be uncollected, except a changed original file that could not be loaded against the change
    and whose every altered test the plan replaces (``structure``, by file: what the change did to it, read
    from the two texts). A rename or a removal the plan approved changes what such a file imports, so none
    of its tests can run as they were; the tests the plan does not list are then known to be unchanged, with
    everything around them, and the caller requires them to pass with the change.
    """
    entries = [entry for entry in entries or [] if entry.get("file") in set(changed or ())]
    if not entries or not isinstance(row, dict) or row.get("timed_out") or row.get("error"):
        return None
    if type(row.get("exit_code")) is not int or row["exit_code"] == 0:
        return None
    results = row.get("results")
    if not isinstance(results, dict) or results.get("complete") is not True:
        return None
    failed = results.get("failed")
    unloaded = results.get("uncollected", results.get("collection_errors")) or []
    if (
        not isinstance(failed, list)
        or not failed
        or not isinstance(unloaded, list)
        or type(results.get("total")) is not int
        or results["total"] <= 0
    ):
        return None
    for test in unloaded:
        file = unloaded_file(test, changed)
        if file is None or test not in failed or not _accounted(file, entries, structure):
            return None
    if all(
        isinstance(test, str) and (test in unloaded or any(matches(entry, test) for entry in entries))
        for test in failed
    ):
        return sorted(failed)
    return None
