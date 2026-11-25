"""Project one verification run's collected evidence into its public result.

Execution and judgment remain with the caller. These helpers neither run checks
nor change their verdict, receipts or source binding.
"""

SOURCE_UNESTABLISHED = "Runner source resolution is not established"


def classified(fail, unverified):
    """The verdict, and the failure attribution that survives it.

    Source uncertainty is classified before every other classification (#4152): a run whose
    runner may read outside its copy cannot attribute the failure it observed, so the proof is
    unverified rather than failed and no repair is directed from it.
    """
    if any(reason.startswith(SOURCE_UNESTABLISHED) for reason in unverified):
        return "UNVERIFIED", []
    return ("FAIL" if fail else "UNVERIFIED" if unverified else "PASS"), fail


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
        "suite_flakes": proof.get("suite_flakes"),
        "diff_coverage": proof.get("diff_coverage"),
        "test_prerequisites": proof.get("test_prerequisites"),
        "test_support": proof.get("test_support"),
        "checks": run.checks,
    }


def builder_command_warnings(commands: dict) -> list[str]:
    """Unverified notes when a proof command came from the Builder's own report rather than trusted setup."""
    return [
        f"The {kind} command came from the Builder's own report; pass "
        f"--{'regression' if kind == 'regression' else 'test'}-command to verify with a command you trust"
        for kind in ("regression", "suite")
        if commands.get(f"{kind}_source") == "builder"
    ]


def feedback(result, *, limit=3000) -> str:
    """A compact, model-readable account of a failed or unverified verification."""
    lines = [f"Verdict: {result['verdict']}"]
    if (result.get("framework") or {}).get("note"):
        lines.append(f"Note: {result['framework']['note']}")
    lines += [f"- FAIL: {reason}" for reason in result["failures"]]
    lines += [f"- UNVERIFIED: {reason}" for reason in result["unverified"]]
    lines += [f"- Note: {note}" for note in result["notes"]]
    if result.get("fail_to_pass"):
        lines.append("Fail-to-pass tests: " + ", ".join(result["fail_to_pass"][:20]))
    commands = result["commands"]
    lines.append(f"Regression command ({commands['regression_source']}): {commands['regression']}")
    lines.append(f"Suite command ({commands['suite_source']}): {commands['suite']}")
    for label, receipt in result["checks"].items():
        lines.append(f"\n## {label}: exit {receipt['exit_code']}{' (timed out)' if receipt['timed_out'] else ''}")
        lines.append(receipt["tail"][-limit:])
    return "\n".join(lines)
