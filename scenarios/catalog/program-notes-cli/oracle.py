from harness.oracle import (Check, changed_since_seed, hidden_tests, non_stdlib_imports, program_checks, python_tests,
                            scratch_copy, tail)

# What each workstream delivers, the integration workstream's journey test included.
PRODUCT = ("notes/__init__.py", "notes/__main__.py", "notes/cli.py", "notes/store.py", "notes/commands/__init__.py",
           "notes/commands/search.py", "notes/commands/export.py", "tests/test_skeleton.py", "tests/test_search.py",
           "tests/test_export.py", "tests/test_journey.py")


def check(project, scenario, run=None):
    """``project`` is the program's product: its integration worktree (the merged branch), or, in
    ``check`` mode, the seed with an overlay laid over it."""
    checks = [Check(f"file[{name}]", (project / name).is_file()) for name in PRODUCT]
    with scratch_copy(project) as copy:
        suite = python_tests(copy)
        checks.append(Check("delivered_tests_pass", suite.returncode == 0 and "Ran 0 tests" not in suite.stderr,
                            tail(suite)))
        hidden = hidden_tests(copy, scenario.dir / "hidden")
        checks.append(Check("hidden_journey_tests_pass", hidden.returncode == 0, tail(hidden)))
    stray = [path for path in changed_since_seed(project) if path not in PRODUCT]
    checks.append(Check("only_planned_files_changed", not stray, f"also changed: {stray}" if stray else ""))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    return checks + program_checks(run, scenario)
