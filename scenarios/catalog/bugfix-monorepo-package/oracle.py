"""A bug fixed in one package of a two-package repository, and nothing outside it (#711).

From the files alone: the invoice package's own suite and the hidden tests pass, run from the package's
directory as the root README says; its delivered tests fail against the original pager.py; the seed's
tests are kept; and nothing outside packages/invoices/ changed since the seed (packages/search/ is another
team's, with its own copy of the bug), apart from the bug-fix workflow's diagnosis note under docs/bugs/,
which the runner writes. With a run: the bug-fix workflow, its Investigator before any other model stage
but the job recognizer, then the fix planned with plan review and the user's approval, no requirements
gathering and no questions. With a run started with --package (the scripted run's [fake] flags), the
status view must also report every file the run left changed outside the package in an open blocking
finding with source ``runner``, and report none when there is none.
"""

import shutil

from harness.oracle import (
    Check,
    changed_since_seed,
    hidden_tests,
    non_stdlib_imports,
    python_tests,
    run_checks,
    scratch_copy,
    tail,
    test_names,
)

PACKAGE = "packages/invoices"
# Where the runner writes the Investigator's diagnosis note (the bug-fix workflow's notes directory).
NOTES = "docs/bugs/"
# How the runner's finding begins (tools/autocode_package_writes.py).
FINDING = f"Files outside the package {PACKAGE}/ changed: "


def check(project, scenario, run=None):
    checks = []
    with scratch_copy(project) as copy:
        suite = python_tests(copy / PACKAGE, start=".")
        checks.append(Check("package_tests_pass", suite.returncode == 0, tail(suite)))
        hidden = hidden_tests(copy / PACKAGE, scenario.dir / "hidden")
        checks.append(Check("hidden_tests_pass", hidden.returncode == 0, tail(hidden)))
    with scratch_copy(project) as copy:
        shutil.copy2(scenario.seed / PACKAGE / "pager.py", copy / PACKAGE / "pager.py")
        original = python_tests(copy / PACKAGE, start=".")
        checks.append(
            Check(
                "new_tests_fail_on_original_code",
                original.returncode != 0,
                "delivered tests fail on the original code"
                if original.returncode
                else "delivered tests still pass on the original code",
            )
        )
    missing = sorted(test_names(scenario.seed / PACKAGE) - test_names(project / PACKAGE))
    checks.append(Check("existing_tests_kept", not missing, f"removed: {missing}" if missing else ""))
    foreign = non_stdlib_imports(project / PACKAGE)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    outside = [path for path in changed_since_seed(project) if not path.startswith((PACKAGE + "/", NOTES))]
    checks.append(Check("nothing_changed_outside_the_package", not outside, f"changed outside {PACKAGE}/: {outside}"))
    checks += run_checks(run, workflow="bugfix", no_requirements=True, plan_approved=True, max_questions=0)
    checks += investigated_first(run)
    checks += reported(run, outside)
    return checks


def investigated_first(run):
    """The Investigator ran, and no model stage but the job recognizer ran before it."""
    if run is None:
        return []
    stages = run.get("stages") or []
    if "investigate_bug" not in stages:
        return [Check("investigated_first", False, f"no Investigator stage ran: {stages}")]
    early = [stage for stage in stages[: stages.index("investigate_bug")] if stage != "recognize_workflow"]
    return [Check("investigated_first", not early, f"ran before the Investigator: {early}" if early else "")]


def reported(run, outside):
    """The run's own report of its changes outside the package, from the final status view. Nothing to
    judge in check mode, or in a run not started with --package (a live profile gets no [fake] flags)."""
    view = (run or {}).get("view") or {}
    if view.get("package") != PACKAGE:
        return []
    rows = [
        row
        for row in (view.get("evidence") or {}).get("findings") or []
        if row.get("source") == "runner"
        and row.get("status") == "open"
        and row.get("blocking")
        and str(row.get("finding") or "").startswith(FINDING)
    ]
    text = " ".join(str(row["finding"]) for row in rows)
    if not outside:
        return [
            Check("outside_changes_reported", not rows, f"reported with nothing outside: {text[:300]}" if rows else "")
        ]
    unnamed = [path for path in outside if f"{path} (" not in text]
    if not rows:
        detail = "no open runner finding names them"
    elif unnamed:
        detail = f"not named in an open runner finding: {unnamed}"
    else:
        detail = f"{', '.join(row['id'] for row in rows)}: {text[:300]}"
    return [Check("outside_changes_reported", bool(rows) and not unnamed, detail)]
