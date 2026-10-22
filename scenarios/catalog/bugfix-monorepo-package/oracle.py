"""A bug fixed in one package of a two-package repository, and nothing outside it (#711).

From the files alone: the invoice package's own suite and the hidden tests pass, run from the package's
directory as the root README says; its delivered tests fail against the original pager.py; the seed's
tests are kept; and nothing outside packages/invoices/ changed since the seed (packages/search/ is another
team's, with its own copy of the bug). With a run, the bug-fix workflow's diagnosis note, which the runner
writes, is not counted: only the path the run's saved investigation names (``note_path`` in the run's
state.json), left out by path, so any other file under docs/bugs/ counts. A run must also use the bug-fix
workflow, its Investigator before any other model stage but Requirements, which runs once and names
the job (or a repair of its report), then the fix planned with plan review and the user's approval, and
no questions. With a run started with --package (the scripted run's [fake] flags), the
status view must also report every file the run left changed outside the package in an open blocking
finding with source ``runner``, and report none when there is none.
"""

import json
import shutil
from pathlib import Path

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
REQUIREMENTS = "requirements_gather"
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
    note = runner_note(run)
    outside = [path for path in changed_since_seed(project) if not path.startswith(PACKAGE + "/") and path != note]
    checks.append(Check("nothing_changed_outside_the_package", not outside, f"changed outside {PACKAGE}/: {outside}"))
    checks += run_checks(
        run, workflow="bugfix", after_requirements="investigate_bug", plan_approved=True, max_questions=0
    )
    checks += investigated_first(run)
    checks += reported(run, outside)
    return checks


def runner_note(run):
    """The diagnosis note the runner wrote for the run: the ``note_path`` of the investigation saved in the
    run's state.json, or None (check mode, or no investigation saved)."""
    path = Path(run["run_dir"]) / "state.json" if run and run.get("run_dir") else None
    try:
        state = json.loads(path.read_text()) if path and path.is_file() else {}
    except (OSError, ValueError):
        return None
    note = (state.get("investigation") or {}).get("note_path") if isinstance(state, dict) else None
    return note if isinstance(note, str) and note else None


def investigated_first(run):
    """The Investigator ran, and no model stage but Requirements (or a repair of its report) ran before
    it. Model stages are the run's ``model_stages`` (runner-owned records left out), else its ``stages``."""
    if run is None:
        return []
    stages = run.get("model_stages")
    if stages is None:
        stages = run.get("stages") or []
    if "investigate_bug" not in stages:
        return [Check("investigated_first", False, f"no Investigator stage ran: {stages}")]
    early = [
        stage
        for stage in stages[: stages.index("investigate_bug")]
        if str(stage).removesuffix("_report_repair") != REQUIREMENTS
    ]
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
