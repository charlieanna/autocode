"""Did one run review pr-184.patch and then, told only "Fix them.", land the PR with
both blocking regressions fixed and a test that catches each, without new
requirements questions and without touching the advisory finding?"""
import shutil

from harness.oracle import (IGNORED, Check, apply_patch, findings_of, hidden_tests, load_json, non_stdlib_imports,
                            python_tests, run_checks, scratch_copy, tail, test_names)


def check(project, scenario, run=None):
    checks = []
    report, error = load_json(project / "review" / "findings.json")
    checks.append(Check("review_findings_kept", isinstance(report, dict) and len(findings_of(report, "blocking")) >= 2,
                        error or "the first turn's review/findings.json should still name the blocking findings"))
    with scratch_copy(project) as copy:
        suite = python_tests(copy)
        checks.append(Check("project_tests_pass", suite.returncode == 0, tail(suite)))
        hidden = hidden_tests(copy, scenario.dir / "hidden")
        checks.append(Check("hidden_tests_pass", hidden.returncode == 0, tail(hidden)))
    # The delivered tests must catch each regression on its own: put back the
    # PR's unfixed version of one file at a time and the suite must fail.
    with scratch_copy(scenario.seed) as unfixed:
        patched = apply_patch(unfixed, scenario.seed / "pr-184.patch")
        for name, module in (("tests_catch_the_timeout_resend", "client.py"),
                             ("tests_catch_the_de_retry", "policies.py")):
            with scratch_copy(project) as copy:
                shutil.copy2(unfixed / "regclient" / module, copy / "regclient" / module)
                against = python_tests(copy)
            ok = patched.returncode == 0 and against.returncode != 0
            checks.append(Check(name, ok, tail(patched) if patched.returncode else
                                f"delivered tests {'fail' if against.returncode else 'still pass'} with the PR's "
                                f"unfixed regclient/{module}"))
    policies = (project / "regclient" / "policies.py").read_text() if (project / "regclient" / "policies.py").is_file() else ""
    checks.append(Check("advisory_finding_left_alone", "def _errors" in policies,
                        "" if "def _errors" in policies else "the advisory _errors helper was rewritten or removed"))
    missing = sorted(test_names(scenario.seed / "tests") - test_names(project / "tests"))
    checks.append(Check("existing_tests_kept", not missing, f"removed: {missing}" if missing else ""))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    checks += conversation_checks(run)
    return checks


def conversation_checks(run):
    if run is None:
        return []
    turns = run.get("turns") or []
    checks = [Check("two_turns_in_one_run", len(turns) == 2, f"{len(turns)} turns recorded")]
    if len(turns) == 2:
        review, fix = turns
        checks += [Check(f"review_turn_{c.name}", c.ok, c.detail)
                   for c in run_checks(review, workflow="review", no_build=True, no_requirements=True)]
        checks += [Check(f"fix_turn_{c.name}", c.ok, c.detail)
                   for c in run_checks(fix, workflow="build", no_requirements=True, max_questions=0)]
    return checks
