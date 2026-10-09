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


def check(project, scenario):
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
