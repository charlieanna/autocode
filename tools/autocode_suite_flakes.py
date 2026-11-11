"""Recheck a suite test the original code passed before calling its failure a regression (#2942).

The preservation check (autocode_verify_judgment.judge_suite) runs the project's whole suite once
on the original code and once on the candidate. A test that passed on the original code and fails
on the candidate is a regression. On a real project's suite that single comparison also catches a
flaky test, one that fails now and then whatever the change, and holds an honest change.

Before that judgment, the failures it would report are rechecked deterministically:

1. Only a *suspect* is rechecked: a test that passed in a complete base run and failed in a complete
   candidate run, that no due case names (its own proof is the stability repeat's,
   autocode_proof_stability) and whose name no changed file mentions (a changed test file runs other
   content on the original code). At most ``MAX_SUSPECTS`` are rechecked; more than that at once is
   treated as a regression, not as flakiness.
2. The suspects alone (autocode_proof_stability.narrow, never the whole suite) run ``RERUNS`` more
   times on the candidate. A suspect that fails every time stays a regression.
3. A suspect that passed again on the candidate runs ``RERUNS`` more times on the original code. Only
   when it also fails there, so it fails without the change, is it ``quarantined``: left out of the
   preservation comparison, with its runs and this reason recorded in the proof's evidence and a
   review reason for the judging stages. One that passed every run on the original code stays a
   regression: the change may have made it flaky.

Missing evidence never quarantines: a rerun that did not report the test, a runner that cannot select
it, or an incomplete run leaves the regression standing. ``recheck`` runs no command itself; the
caller supplies ``run(command, side, label)``. ``for_proof`` is autocode_verify's entry: it is given
verify's own tree and suite helpers, and imports nothing from it.
"""

from __future__ import annotations

from pathlib import Path

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_proof_stability as proof_stability
    from . import autocode_test_cases as test_cases
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_proof_stability as proof_stability
    import autocode_test_cases as test_cases
    import autocode_verification_schedule as schedule

RERUNS = proof_stability.REPEATS - 1
MAX_SUSPECTS = 10
RULE = (
    "A suite test that passed on the original code and failed on the candidate is run alone "
    f"{RERUNS} more times on the candidate; if it passes there, {RERUNS} more times on the original code. "
    "It is quarantined only when it also fails on the original code; otherwise it stays a regression."
)
QUARANTINE_REASON = "fails intermittently on the original code too, so its failure is not evidence against the change"


def _complete(receipt) -> bool:
    return bool(
        isinstance(receipt, dict)
        and command_receipt.completed(receipt)
        and not receipt.get("timed_out")
        and schedule.complete_results(receipt)
    )


def suspects(on_candidate, base_receipt, *, protected=(), changed_texts=()) -> list[str]:
    """The candidate's failures that passed on the original code and may be rechecked, in order.

    Both runs must be complete; ``protected`` are tests a due case names; a test whose function name
    any of ``changed_texts`` (the changed files' sources) mentions is left to the ordinary judgment.
    """
    if not _complete(on_candidate) or not _complete(base_receipt):
        return []
    failed = set(on_candidate["results"].get("failed") or [])
    passed = set(base_receipt["results"].get("passed") or [])
    text = "\n".join(changed_texts)
    return sorted(
        test for test in failed & passed if test not in set(protected) and not test_cases.named_in(test, text)
    )


def _verdict(candidate, base) -> str:
    if "pass" not in candidate[1:]:
        return "regression"
    if "missing" in candidate or not base[1:] or "missing" in base:
        return "regression"
    return "quarantined" if "fail" in base[1:] else "regression"


def recheck(framework_name, command, tests, run, *, directory=None) -> dict | None:
    """Rerun ``tests`` and decide each one; None when there is nothing to recheck.

    ``run(command, side, label)`` runs the narrowed command in the ``candidate`` or ``base`` tree and
    returns its receipt. The record lists every suspect's runs on each side (the first entry is the
    judged suite run) and which were quarantined.
    """
    tests = list(tests)
    if not tests:
        return None
    if len(tests) > MAX_SUSPECTS:
        return record(
            tests,
            note=f"{len(tests)} tests newly failed at once, more than the {MAX_SUSPECTS} rechecked for flakiness, "
            "so they are judged as regressions",
        )
    narrowed = proof_stability.narrow(framework_name, command, tests, directory=directory)
    if not narrowed:
        return record(tests, note="this runner cannot select the failing tests alone, so they were not rechecked")
    candidate_runs = [run(narrowed, "candidate", f"flake-candidate-{index}") for index in range(2, RERUNS + 2)]
    outcomes = {test: ["fail", *(proof_stability.outcome_of(r, test) for r in candidate_runs)] for test in tests}
    again = [test for test in tests if "pass" in outcomes[test][1:]]
    base_runs = []
    if again:
        retry = proof_stability.narrow(framework_name, command, again, directory=directory) or narrowed
        base_runs = [run(retry, "base", f"flake-base-{index}") for index in range(2, RERUNS + 2)]
    rows = []
    for test in tests:
        base = ["pass", *(proof_stability.outcome_of(r, test) for r in base_runs)] if test in again else ["pass"]
        verdict = _verdict(outcomes[test], base)
        rows.append({"test": test, "candidate": outcomes[test], "base": base, "verdict": verdict})
    return record(tests, rows, command=narrowed)


def record(tests, rows=(), *, command=None, note=None) -> dict:
    rows = list(rows)
    return {
        "rule": RULE,
        "reruns": RERUNS,
        "suspects": list(tests),
        "command": command,
        "tests": rows,
        "quarantined": [
            {"test": row["test"], "reason": QUARANTINE_REASON} for row in rows if row["verdict"] == "quarantined"
        ],
        "note": note,
    }


def quarantined(recheck_record) -> list[str]:
    """The tests the preservation judgment leaves out."""
    return [row["test"] for row in (recheck_record or {}).get("quarantined") or []]


def explain(recheck_record) -> tuple[list[str], list[str]]:
    """(notes, review reasons) a recheck adds to the proof; both empty when it quarantined nothing."""
    names = quarantined(recheck_record)
    notes = []
    if recheck_record and recheck_record.get("note"):
        notes.append("Flaky-test recheck: " + recheck_record["note"])
    if not names:
        return notes, []
    rows = {row["test"]: row for row in recheck_record["tests"]}
    detail = "; ".join(
        f"{test} (candidate {', '.join(rows[test]['candidate'])}; original code {', '.join(rows[test]['base'])})"
        for test in names
    )
    notes.append("Quarantined as flaky, not counted as preserved or as regressed: " + detail)
    return notes, [
        "suite tests that fail intermittently on the original code too were quarantined from the preservation "
        "check: " + ", ".join(names)
    ]


def for_proof(run, trees, on_candidate, comparable, helpers) -> list[str]:
    """Recheck one verify() preservation run (autocode_verify._Run); return the tests it quarantined.

    ``helpers`` are verify's ``(make_tree, run_suite, remove_tree)``. Only the failing tests run: in the
    candidate tree and, once one passed again there, in a fresh tree of the effective base (the one the
    baseline ran, ``base_patch`` applied), removed afterwards. The record is the proof's ``suite_flakes``;
    its notes and review reasons join the proof's. Without a comparable base run nothing is rechecked.
    """
    make_tree, run_suite, remove_tree = helpers
    framework, proof = run.framework, run.proof
    if framework is None or comparable is None or comparable.get("base") != run.base:
        return []
    named = [test for key in ("fail_to_pass", "pass_to_pass", "not_run_on_base") for test in proof.get(key) or []]
    texts = ["\n".join([*(case.get("test_name") or "" for case in run.cases or ()), *(run.guards or ())])]
    for path, status in run.changes.items():
        try:
            texts += [] if status == "deleted" else [(Path(trees["candidate"]) / path).read_text(errors="replace")]
        except OSError:
            continue
    tests = suspects(on_candidate, comparable.get("receipt"), protected=named, changed_texts=texts)
    if not tests:
        return []
    base_tree: list = []

    def rerun(command, side, label):
        if side == "base" and not base_tree:
            destination = Path(run.run_dir) / "scratch" / "flake-base"
            base_tree.append(
                make_tree(
                    run.workspace,
                    run.base,
                    destination,
                    run.workspace,
                    {},
                    dependencies_from=run.dependencies_from,
                    patch=run.base_patch,
                    generated_record=run.generated_record,
                    generated_unrecorded=run.generated_unrecorded,
                    ignored_inputs=run.ignored_inputs,
                )
            )
        tree = trees["candidate"] if side == "candidate" else base_tree[0]
        run.checks[label] = run_suite(framework, command, tree, run.run_dir, label, timeout=run.timeout)
        return run.checks[label]

    try:
        found = recheck(
            framework.name, run.commands["suite"], tests, rerun, directory=getattr(framework, "directory", None)
        )
    finally:
        if base_tree:
            remove_tree(run.workspace, base_tree[0])
    proof["suite_flakes"] = found
    notes, reasons = explain(found)
    run.notes.extend(notes)
    run.review_reasons.extend(reasons)
    return quarantined(found)
