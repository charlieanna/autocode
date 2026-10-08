#!/usr/bin/env python3
"""Run the two-service reference without models; --docker explicitly opts into Docker."""
import argparse
from dataclasses import replace
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scenarios"))
from harness import catalog, verdict


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", action="store_true", help="build/run/tear down real local containers")
    args = parser.parse_args()
    scenario = catalog.load("local-compose-two-services")
    architecture = catalog.load(scenario.components_architecture).reference / "architecture"
    if args.docker:
        sys.path.insert(0, str(ROOT / "tools"))
        from autocode_multicomponent import Architecture
        from autocode_local_run import LocalRun, check_docker, prepare
        parsed = Architecture.load(architecture)
        plan = prepare(architecture, {cid: row.runtime for cid, row in parsed.components.items()})
        plan = replace(plan, endpoint=check_docker())
        directory = ROOT / ".scenario-runs"
        directory.mkdir(exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="local-compose-example-", dir=directory))
        summary = LocalRun(plan, scenario.reference, work, health_timeout=30).run()
        (work / "result.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
        return 0 if summary["status"] == "passed" else 1
    from harness.project import materialize
    with tempfile.TemporaryDirectory(prefix="local-compose-example-") as folder:
        project = materialize(scenario.seed, Path(folder) / "project",
                              catalog.load(scenario.components_architecture).reference, scenario.reference)
        result = verdict.evaluate(scenario, project)
    print(json.dumps({"passed": result.passed, "checks": [dataclasses.asdict(c) for c in result.checks]}, indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
