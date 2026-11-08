"""Project one verification run's collected evidence into its public result.

Execution and judgment remain with the caller. This helper neither runs checks
nor changes their verdict, receipts or source binding.
"""


def assemble(run, *, verdict, source_revision, test_files, source_files, stats, ignored_test_files) -> dict:
    proof, baseline = run.proof, run.base_suite
    return {
        "verdict": verdict,
        "failures": run.fail,
        "unverified": run.unverified,
        "notes": run.notes,
        "review_reasons": run.review_reasons,
        "base": run.base,
        "base_patch": str(run.base_patch) if run.base_patch else None,
        "source_revision": source_revision,
        "changes": run.changes,
        "test_files": test_files,
        "source_files": source_files,
        "stats": stats,
        "commands": {key: run.commands[key] for key in ("suite", "suite_source", "regression", "regression_source")},
        "framework": run.framework.to_dict() if run.framework else None,
        "baseline": (
            {
                "health": baseline["health"],
                "exit_code": baseline["receipt"]["exit_code"],
                "output": baseline["receipt"]["output"],
            }
            if baseline
            else None
        ),
        "fail_to_pass": proof.get("fail_to_pass"),
        "pass_to_pass": proof.get("pass_to_pass"),
        "not_run_on_base": proof.get("not_run_on_base"),
        "failed_on_candidate": proof.get("failed_on_candidate"),
        "suite_pass_to_pass": proof.get("suite_pass_to_pass"),
        "ignored_test_files": ignored_test_files,
        "no_test_changed": run.existing_guards,
        "guard_run_incomplete": bool(proof.get("guard_run_incomplete")),
        "unstable": proof.get("unstable"),
        "proof_stability": proof.get("proof_stability"),
        "diff_coverage": proof.get("diff_coverage"),
        "test_prerequisites": proof.get("test_prerequisites"),
        "checks": run.checks,
    }
