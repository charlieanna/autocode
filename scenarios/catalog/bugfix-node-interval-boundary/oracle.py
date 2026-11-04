from harness.oracle import bugfix_entry, named_proof_checks, node_change_checks, run_checks


def check(project, scenario, run=None):
    checks = node_change_checks(project, scenario, "src", runner="node")
    checks += named_proof_checks(run, scenario)
    return checks + run_checks(
        run, workflow="bugfix", after_requirements=bugfix_entry(run), plan_approved=True, max_questions=0
    )
