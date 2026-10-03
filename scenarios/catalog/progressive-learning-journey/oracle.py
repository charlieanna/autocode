import sys

from harness.oracle import Check, run as command, run_checks, scratch_copy, tail


def check(project, scenario, run=None):
    checks = []
    with scratch_copy(project) as copy:
        # Use the oracle-owned fixture, not a delivered test a Builder could weaken.
        (copy / "test_journey.py").write_text((scenario.seed / "test_journey.py").read_text())
        for suite in ("test_journey.Skeleton", "test_journey.Product"):
            result = command([sys.executable, "-m", "unittest", suite], copy)
            checks.append(Check(suite, result.returncode == 0, tail(result)))
    checks += run_checks(run, workflow="build", plan_approved=True)
    if run is not None:
        view = run.get("view") or {}
        progressive = view.get("progressive") or {}
        verified = progressive.get("demonstrated_slices") or []
        ids = [row.get("slice_id", row.get("id")) if isinstance(row, dict) else row for row in verified]
        checks.append(Check("two_independently_verified_slices", ids == ["S1", "S2"], str(progressive)))
        checks.append(Check("current_whole_product_proof", (progressive.get("current_whole_product_proof") or {})
                            .get("verified") is True, str(progressive.get("current_whole_product_proof"))))
        checks.append(Check("approved_run_continued", run.get("cli_calls", []).count("resume") >= 1,
                            str(run.get("cli_calls"))))
        checks.append(Check("no_manual_paused_recovery", "resume-paused" not in run.get("cli_calls", [])))
    return checks
