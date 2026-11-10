import hashlib
import sys

from harness.oracle import Check, run_checks, scratch_copy, tail
from harness.oracle import run as command


def check(project, scenario, run=None):
    checks = []
    with scratch_copy(project) as copy:
        (copy / "test_journey.py").write_text((scenario.seed / "test_journey.py").read_text())
        for suite in ("test_journey.Skeleton", "test_journey.Product"):
            result = command([sys.executable, "-m", "unittest", suite], copy)
            checks.append(Check(suite, result.returncode == 0, tail(result)))
    checks += run_checks(run, workflow="build", plan_approved=True)
    if run is not None:
        progressive = (run.get("view") or {}).get("progressive") or {}
        verified = progressive.get("demonstrated_slices") or []
        ids = [row.get("slice_id", row.get("id")) if isinstance(row, dict) else row for row in verified]
        checks.append(Check("two_independently_verified_slices", ids == ["S1", "S2"], str(progressive)))
        checks.append(
            Check(
                "current_whole_product_proof",
                (progressive.get("current_whole_product_proof") or {}).get("verified") is True,
                str(progressive.get("current_whole_product_proof")),
            )
        )
        stages = run.get("stages", [])
        # Changed-code evidence now sends the first bounded repair directly to the Builder.
        # Require the actual planted line to have been rejected, not an incidental extra attempt.
        corrupt = (
            (scenario.reference / "lessons.py")
            .read_bytes()
            .replace(
                b"if response != LESSONS[lesson][1]:", b"if lesson == 'addition' or response != LESSONS[lesson][1]:"
            )
        )
        pin = hashlib.sha256(corrupt).hexdigest()
        verification = (run.get("view") or {}).get("verification") or {}
        current = verification.get("source_revision")
        rejected = any(
            row.get("action") == "builder"
            and row.get("source_revision")
            and current
            and row["source_revision"] != current
            and any(
                gap.get("path") == "lessons.py"
                and gap.get("sha256") == pin
                and gap.get("line") == 12
                and gap.get("status") == "reason_required"
                and "survived" in (gap.get("mutation_outcomes") or [])
                for gap in (row.get("assessment") or {}).get("obligations") or []
            )
            for row in verification.get("changed_code_repairs") or []
        )
        checks.append(Check("planted_regression_rejected_before_repair", bool(rejected), str(stages)))
        checks.append(
            Check(
                "fresh_repair_and_validation", stages.count("builder") >= 3 and stages.count("tester") >= 3, str(stages)
            )
        )
        checks.append(Check("no_manual_paused_recovery", "resume-paused" not in run.get("cli_calls", [])))
    return checks
