from harness.oracle import go_change_checks, named_proof_checks, run_checks


def check(project, scenario, run=None):
    return (
        go_change_checks(project, scenario, package="window")
        + named_proof_checks(run, scenario)
        + run_checks(run, workflow="bugfix", no_requirements=True, max_questions=0)
    )
