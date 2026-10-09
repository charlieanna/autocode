from harness.oracle import named_proof_checks, node_change_checks, run_checks


def check(project, scenario, run=None):
    return (node_change_checks(project, scenario, 'src', runner='vitest')
            + named_proof_checks(run, scenario)
            + run_checks(run, workflow='bugfix', no_requirements=True,
                         plan_approved=True, max_questions=0))
