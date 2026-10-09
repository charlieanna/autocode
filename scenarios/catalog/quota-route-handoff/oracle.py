"""The greeting CLI (checks as in greenfield-greeting-cli), delivered after the Tester's quota ran out
and the person named another model: the run must record exactly that one route assignment (#184)."""
import sys

from harness.oracle import Check, non_stdlib_imports, run

# (case, argv, expected exit, text the combined output must contain)
CASES = [
    ("no-arg", [], 2, "usage"),
    ("one-name", ["Ada"], 0, "Hello, Ada"),
    ("name-with-space", ["Ada Lovelace"], 0, "Hello, Ada Lovelace"),
    ("unicode", ["Zoë"], 0, "Hello, Zoë"),
    ("two-args", ["Ada", "Lovelace"], 2, "usage"),
]
DELIVERABLES = ("greet.py", "test_greet.py", "README.md")


def product_checks(project):
    if not (project / "greet.py").is_file():
        return [Check("greet.py", False, "not delivered")]
    checks = []
    for case, argv, exit_code, text in CASES:
        proc = run([sys.executable, "greet.py", *argv], project, timeout=30)
        output = proc.stdout + proc.stderr
        checks.append(Check(f"{case}.exit", proc.returncode == exit_code, f"exit {proc.returncode}"))
        checks.append(Check(f"{case}.output", text.lower() in output.lower(), output.strip()[:200]))
    missing = [name for name in DELIVERABLES if not (project / name).is_file()]
    checks.append(Check("deliverables", not missing, f"missing: {missing}" if missing else ""))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    return checks


SPENT, NAMED = "gpt-5.6-sol", "gpt-6-luna"


def check(project, scenario, run=None):
    checks = product_checks(project)
    if run is None:
        return checks
    view = run.get("view") or {}
    assignments = view.get("route_assignments") or []
    recorded = [row for row in assignments if row.get("role") == "sol"]
    checks.append(Check("one_route_assignment", len(assignments) == 1 and len(recorded) == 1,
                        f"route_assignments: {assignments}"))
    row = recorded[0] if recorded else {}
    checks.append(Check("assignment_from_spent_to_named",
                        (row.get("from"), row.get("to"), row.get("pause_status"), row.get("actor"))
                        == (SPENT, NAMED, "PAUSED_BUDGET", "user_cli"), str(row)))
    route = (view.get("routes") or {}).get("sol") or {}
    checks.append(Check("tester_route_is_the_named_model", route.get("model") == NAMED, str(route)))
    asked = [answer for answer in run.get("answers") or [] if answer.get("id") == "route-sol"]
    checks.append(Check("model_named_by_the_person", len(asked) == 1 and asked[0].get("explicit") is True
                        and asked[0].get("answer") == NAMED, str(asked)))
    return checks
