"""Run approved unittest controls without converting them into candidate proof.

Each control is a public, self-contained directory containing its own tests and
implementation. A fresh interpreter prevents the reference import from leaking
into the broken control. ERROR/SKIP/expectedFailure cannot stand in for FAIL.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unittest
from pathlib import Path


class Outcomes(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.outcomes = {}
        self.errored = set()

    def startTest(self, test):
        super().startTest(test)
        self.outcomes[test.id()] = "ERROR"

    def addSuccess(self, test):
        self.outcomes[test.id()] = "PASS"

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.outcomes[test.id()] = "FAIL"

    def addError(self, test, err):
        super().addError(test, err)
        self.errored.add(test.id())
        self.outcomes[test.id()] = "ERROR"

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcomes[test.id()] = "SKIP"

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err:
            if not issubclass(err[0], test.failureException):
                self.errored.add(test.id())
            self.outcomes[test.id()] = "ERROR" if test.id() in self.errored else "FAIL"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--control", choices=("baseline", "reference", "broken"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    data = json.loads(args.inventory.read_text())
    if (
        not isinstance(data, dict)
        or set(data) != {"controls", "selectors"}
        or set(data["controls"]) != {"baseline", "reference", "broken"}
        or not isinstance(data["selectors"], list)
        or not data["selectors"]
        or len(set(data["selectors"])) != len(data["selectors"])
    ):
        parser.error("Inventory requires baseline/reference/broken directories and unique selectors")
    if args.control:
        sys.path.insert(0, str(Path.cwd()))
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromNames(data["selectors"])
        result = Outcomes()
        suite.run(result)
        outcomes = {name: result.outcomes.get(name, "ERROR") for name in data["selectors"]}
        # Module/class setup/teardown errors may use synthetic holder identities.
        if loader.errors or any(name not in outcomes for name in result.outcomes):
            outcomes = dict.fromkeys(data["selectors"], "ERROR")
        print("AUTOCODE_CONTROL=" + json.dumps(outcomes))
        return 0
    controls, errors = {}, []
    for name, root in data["controls"].items():
        directory = Path(root)
        if directory.is_absolute() or ".." in directory.parts or not directory.is_dir():
            parser.error("Control directories must exist inside the project/source copy")
        child = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--inventory",
                str(args.inventory.resolve()),
                "--control",
                name,
            ],
            cwd=directory,
            capture_output=True,
            text=True,
        )
        lines = [
            line.removeprefix("AUTOCODE_CONTROL=")
            for line in child.stdout.splitlines()
            if line.startswith("AUTOCODE_CONTROL=")
        ]
        if child.returncode or len(lines) != 1:
            errors.append(f"{name}: control setup failed: {(child.stderr or child.stdout)[-1500:]}")
        else:
            controls[name] = json.loads(lines[0])
            if any(value not in ("PASS", "FAIL") for value in controls[name].values()):
                errors.append(f"{name}: setup ERROR, SKIP or incomplete named outcomes")
    print(
        "AUTOCODE_PREREQUISITE="
        + json.dumps(
            {
                "kind": "prerequisite",
                "status": "BLOCKED" if errors else "READY",
                "setup": not errors,
                "teardown": not errors,
                "controls": controls,
                "errors": errors,
            }
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
