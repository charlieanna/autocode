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
        ids = [row["slice_id"] for row in progressive.get("demonstrated_slices", [])]
        checks.append(Check("reviewed_split_verified", ids == ["S1", "S2a", "S2b"], str(ids)))
        checks.append(
            Check(
                "one_original_approval", run.get("cli_calls", []).count("approve-plan") == 1, str(run.get("cli_calls"))
            )
        )
        required = {row["id"]: row for row in progressive.get("required_checks", [])}
        checks.append(
            Check(
                "cumulative_a_and_original_product",
                required.get("A", {}).get("method") == "python3 -m unittest test_journey.Skeleton"
                and any(
                    row["method"] == "python3 -m unittest test_journey" and row.get("origin") == "product_contract"
                    for row in required.values()
                ),
                str(required),
            )
        )
        usage = progressive.get("allowance_usage") or {}
        pools = list(usage.get("pools", {}).values())
        checks.append(
            Check(
                "split_shares_default_lineage",
                len(pools) == 2
                and all(
                    row["reviews_used"] == 2 and row["review_limit"] == 2 and row["seconds_limit"] == 5400
                    for row in pools
                )
                and usage.get("run_limit") == 43200,
                str(pools),
            )
        )
        checks.append(
            Check(
                "current_whole_product_proof",
                (progressive.get("current_whole_product_proof") or {}).get("verified") is True,
                str(progressive.get("current_whole_product_proof")),
            )
        )
    return checks
