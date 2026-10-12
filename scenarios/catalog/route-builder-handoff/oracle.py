"""Greeting CLI delivered after a Builder route change: work kept, new route used, verifiers independent."""

import json
import sys
from pathlib import Path

from harness.oracle import Check, non_stdlib_imports, run

CASES = [
    ("no-arg", [], 2, "usage"),
    ("one-name", ["Ada"], 0, "Hello, Ada"),
    ("name-with-space", ["Ada Lovelace"], 0, "Hello, Ada Lovelace"),
    ("unicode", ["Zoë"], 0, "Hello, Zoë"),
    ("two-args", ["Ada", "Lovelace"], 2, "usage"),
]
DELIVERABLES = ("greet.py", "test_greet.py", "README.md")
SPENT, NAMED = "gpt-5.6-terra", "gpt-6-luna"


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


def check(project, scenario, run=None):
    checks = product_checks(project)
    if run is None:
        return checks
    view = run.get("view") or {}
    assignments = view.get("route_assignments") or []
    recorded = [row for row in assignments if row.get("role") == "terra"]
    checks.append(
        Check(
            "one_builder_route_assignment",
            len(recorded) >= 1,
            f"route_assignments: {assignments}",
        )
    )
    row = recorded[0] if recorded else {}
    checks.append(
        Check(
            "assignment_from_spent_to_named",
            (row.get("from"), row.get("to")) == (SPENT, NAMED),
            str(row),
        )
    )
    route = (view.get("routes") or {}).get("terra") or {}
    checks.append(Check("builder_route_is_the_named_model", route.get("model") == NAMED, str(route)))
    # Verifiers must not inherit the new Builder model.
    for role in ("sol", "completion"):
        other = (view.get("routes") or {}).get(role) or {}
        checks.append(
            Check(
                f"{role}_did_not_inherit_builder_route",
                other.get("model") not in (None, NAMED),
                str(other),
            )
        )
    asked = [answer for answer in run.get("answers") or [] if answer.get("id") == "route-terra"]
    checks.append(
        Check(
            "model_named_by_the_person",
            len(asked) == 1 and asked[0].get("explicit") is True and asked[0].get("answer") == NAMED,
            str(asked),
        )
    )
    # Partial work from the spent attempt was kept (file present before the successful build overwrote it,
    # or an archived before_ref / abandoned terra stage exists).
    state_path = Path(run.get("run_dir") or "") / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    abandoned = [
        stage
        for stage in state.get("stages") or []
        if stage.get("stage") == "terra"
        and (stage.get("abandoned") or stage.get("timed_out") or stage.get("exit_code") not in (0, None))
    ]
    checks.append(
        Check(
            "spent_builder_attempt_was_set_aside",
            bool(abandoned) or bool(recorded),
            f"abandoned terra stages: {len(abandoned)}",
        )
    )
    return checks
