"""The review must name the case-mismatch defect as blocking and deliver a test
that fails on the patched code but passes once the key is fixed. Both halves
are run for real in a scratch copy."""

import shutil
import sys

from harness.oracle import (
    Check,
    apply_patch,
    finding_matches,
    findings_of,
    load_json,
    only_changed_under,
    run_checks,
    scratch_copy,
    tail,
)
from harness.oracle import run as run_command

# The .at entry is keyed "AT"; policy_for looks up tld.lower().
CASE_DEFECT = dict(
    file="regclient/policies.py",
    lines=(16, 21),
    words=(("lower", "case", "upper", "never match", "not match", "unreachable", "default"),),
)


def delivered_tests(copy, project):
    tests = sorted((project / "review" / "tests").glob("test_*.py")) if (project / "review" / "tests").is_dir() else []
    for path in tests:
        shutil.copy(path, copy / "tests" / f"review_{path.name}")
    return tests


def run_delivered(copy):
    return run_command(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-p", "review_test_*.py"],
        copy,
        timeout=120,
    )


def check(project, scenario, run=None):
    report, error = load_json(project / "review" / "findings.json")
    checks = [Check("findings_json_present_and_valid", isinstance(report, dict), error)]
    if isinstance(report, dict):
        blocking = findings_of(report, "blocking")
        checks.append(
            Check(
                "verdict_requests_changes",
                report.get("verdict") == "request_changes",
                f"verdict={report.get('verdict')!r}",
            )
        )
        checks.append(Check("finds_case_mismatch", any(finding_matches(f, **CASE_DEFECT) for f in blocking)))
    with scratch_copy(project) as copy:
        applied = apply_patch(copy, copy / "pr-197.patch")
        checks.append(Check("patch_applies_to_scratch_copy", applied.returncode == 0, tail(applied)))
        tests = delivered_tests(copy, project)
        checks.append(Check("targeted_test_delivered", bool(tests), "no review/tests/test_*.py"))
        if tests:
            on_pr = run_delivered(copy)
            checks.append(
                Check(
                    "targeted_test_fails_on_patched_code",
                    on_pr.returncode != 0,
                    "passes on the defective code" if on_pr.returncode == 0 else tail(on_pr, 200),
                )
            )
            shutil.copytree(scenario.dir / "hidden" / "fix", copy, dirs_exist_ok=True)
            fixed = run_delivered(copy)
            checks.append(Check("targeted_test_passes_once_fixed", fixed.returncode == 0, tail(fixed)))
    checks.append(only_changed_under(project, "review/"))
    checks += run_checks(run, workflow="review", no_build=True, no_requirements=True)
    return checks
