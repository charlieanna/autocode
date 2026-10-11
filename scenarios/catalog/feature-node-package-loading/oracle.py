import json

from harness.oracle import Check, named_proof_checks, node_change_checks, run_checks


def check(project, scenario, run=None):
    checks = node_change_checks(project, scenario, "src", runner="node")
    package = json.loads((project / "package.json").read_text())
    original = json.loads((scenario.seed / "package.json").read_text())
    checks.append(
        Check(
            "original_scripts_preserved",
            package.get("scripts") == original["scripts"],
            "Existing test, start and deploy scripts must remain unchanged",
        )
    )
    checks += named_proof_checks(run, scenario)
    return checks + run_checks(run, workflow="build", plan_approved=True, max_questions=0)
