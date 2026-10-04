"""The same independent CLI contract, exercised against a JavaScript product."""
import hashlib
import shutil
import sys

from harness.oracle import Check, run as command, scratch_copy, tail


def check(project, scenario, run=None):
    checks = []
    for name, args, code, out, err in (
            ("Ada", ["Ada"], 0, "Hello, Ada\n", ""),
            ("empty", [""], 2, "", "usage: greet.py NAME\n"),
            ("whitespace", [" \t "], 2, "", "usage: greet.py NAME\n"),
            ("noargs", [], 2, "", "usage: greet.py NAME\n"),
            ("twoargs", ["Ada", "Lovelace"], 2, "", "usage: greet.py NAME\n")):
        proc = command(["node", "greet.js", *args], project)
        checks.append(Check(name + ".exact", (proc.returncode, proc.stdout, proc.stderr) == (code, out, err),
                            repr((proc.returncode, proc.stdout, proc.stderr))))
    for path in ("test_greet.py", "greet.py"):
        digest = lambda root: hashlib.sha256((root / path).read_bytes()).hexdigest()
        checks.append(Check(path + ".unchanged", digest(project) == digest(scenario.seed)))
    with scratch_copy(project) as copy:
        proc = command([sys.executable, "-m", "unittest", "test_greet.py"], copy)
        checks.append(Check("project_tests_pass", proc.returncode == 0, tail(proc)))
        shutil.copy2(scenario.seed / "greet.js", copy / "greet.js")
        proc = command([sys.executable, "-m", "unittest", "test_greet.py"], copy)
        checks.append(Check("tests_reject_seed_javascript", proc.returncode != 0, tail(proc)))
    return checks
