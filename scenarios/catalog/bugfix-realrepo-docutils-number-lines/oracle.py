"""A bug fix in Docutils, a mid-size real repository (RELIABILITY.md's fourth case, #2944).

The project is the whole repository; Docutils itself is its docutils/ directory. The checks are
python_change_checks' for a project with a suite of its own: Docutils' full suite (docutils/test/
alltests.py) passes, the hidden tests pass, the delivered test modules fail on the seed's docutils/docutils/
package, and no existing test was removed. Docutils has optional third-party imports of its own, so the
standard-library check is not applied to the repository; only the delivered changes are read for new
imports.
"""

import ast
import shutil
import sys

from harness.oracle import (
    IGNORED,
    Check,
    bugfix_entry,
    changed_since_seed,
    hidden_tests,
    run,
    run_checks,
    scratch_copy,
    tail,
    test_names,
)

ROOT = "docutils"  # the Docutils project inside the repository
PACKAGE = "docutils/docutils"
# The full suite (about 2,300 cases) takes seconds; the bound leaves room for a delivered change that hangs.
SUITE_SECONDS = 900


def delivered_test_modules(project):
    """Dotted names, from docutils/ as Docutils' own tests import each other, of added or changed tests."""
    return [
        path.removeprefix(f"{ROOT}/").removesuffix(".py").replace("/", ".")
        for path in changed_since_seed(project)
        if path.startswith(f"{ROOT}/test/") and path.rsplit("/", 1)[-1].startswith("test_") and path.endswith(".py")
        if (project / path).is_file()
    ]


def new_imports(project, scenario):
    """Top-level modules imported by delivered Python files that the seed's version did not import."""

    def imported(path):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            return set()
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module.split(".")[0])
        return names

    local = {path.name for root in (project, project / ROOT) for path in root.iterdir()}
    local |= {path.stem for root in (project, project / ROOT) for path in root.glob("*.py")}
    foreign = []
    for path in changed_since_seed(project):
        if not path.endswith(".py") or not (project / path).is_file():
            continue
        before = imported(scenario.seed / path) if (scenario.seed / path).is_file() else set()
        for name in sorted(imported(project / path) - before):
            if name not in sys.stdlib_module_names and name not in local:
                foreign.append(f"{path}: imports {name}")
    return foreign


def check(project, scenario, run_record=None):
    checks = []
    with scratch_copy(project) as copy:
        suite = run([sys.executable, "alltests.py"], copy / ROOT / "test", timeout=SUITE_SECONDS)
        checks.append(Check("project_tests_pass", suite.returncode == 0, tail(suite)))
        hidden = hidden_tests(copy / ROOT, scenario.dir / "hidden")
        checks.append(Check("hidden_tests_pass", hidden.returncode == 0, tail(hidden)))
    modules = delivered_test_modules(project)
    if not modules:
        checks.append(Check("new_tests_fail_on_original_code", False, "no test module was added or changed"))
    else:
        with scratch_copy(project) as copy:
            shutil.rmtree(copy / PACKAGE)
            shutil.copytree(scenario.seed / PACKAGE, copy / PACKAGE, ignore=IGNORED)
            against_seed = run([sys.executable, "-m", "unittest", *modules], copy / ROOT, timeout=SUITE_SECONDS)
        checks.append(
            Check(
                "new_tests_fail_on_original_code",
                against_seed.returncode != 0,
                f"delivered tests {'fail' if against_seed.returncode else 'still pass'} on the original code: "
                + ", ".join(modules),
            )
        )
    missing = sorted(test_names(scenario.seed / ROOT / "test") - test_names(project / ROOT / "test"))
    checks.append(Check("existing_tests_kept", not missing, f"removed: {missing}" if missing else ""))
    foreign = new_imports(project, scenario)
    checks.append(Check("no_new_third_party_imports", not foreign, "; ".join(foreign)))
    checks += run_checks(
        run_record,
        workflow="bugfix",
        after_requirements=bugfix_entry(run_record),
        plan_approved=True,
        max_questions=0,
    )
    return checks
