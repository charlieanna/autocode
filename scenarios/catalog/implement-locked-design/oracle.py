"""Faithful to the approved design: the specified modules, classes and signatures
exist, time comes only from the injected clock, the hidden behavior tests pass,
and the design document itself is untouched."""
import ast

from harness.oracle import (
    Check,
    changed_paths,
    hidden_tests,
    non_stdlib_imports,
    python_tests,
    run_checks,
    scratch_copy,
    tail,
)

SIGNATURES = {
    "ratelimit/bucket.py": {"TokenBucket": {"__init__": ["self", "capacity", "refill_per_second", "clock"],
                                            "try_acquire": ["self", "tokens"], "available": ["self"]}},
    "ratelimit/registry.py": {"LimiterRegistry": {"__init__": ["self", "capacity", "refill_per_second", "clock"],
                                                  "for_key": ["self", "key"], "keys": ["self"]}},
}


def structure(project):
    checks = []
    for rel, classes in SIGNATURES.items():
        path = project / rel
        if not path.is_file():
            checks.append(Check(f"module_exists[{rel}]", False, "missing"))
            continue
        tree = ast.parse(path.read_text())
        imports = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names}
        imports |= {node.module.split(".")[0] for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module}
        checks.append(Check(f"no_time_import[{rel}]", "time" not in imports, "imports time"))
        found = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        for name, methods in classes.items():
            if name not in found:
                checks.append(Check(f"class[{rel}:{name}]", False, "missing"))
                continue
            defs = {node.name: node for node in found[name].body if isinstance(node, ast.FunctionDef)}
            for method, params in methods.items():
                node = defs.get(method)
                actual = [arg.arg for arg in node.args.args] if node else None
                checks.append(Check(f"signature[{name}.{method}]", actual == params, f"{actual} != {params}"))
    return checks


def check(project, scenario, run=None):
    checks = structure(project)
    with scratch_copy(project) as copy:
        hidden = hidden_tests(copy, scenario.dir / "hidden")
        checks.append(Check("hidden_tests_pass", hidden.returncode == 0, tail(hidden)))
        suite = python_tests(copy)
        checks.append(Check("project_tests_pass", suite.returncode == 0, tail(suite)))
    checks.append(Check("design_document_untouched", "docs/design/rate-limiter.md" not in changed_paths(project)))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    checks += run_checks(run, workflow="build", no_requirements=True, max_questions=0)
    return checks
