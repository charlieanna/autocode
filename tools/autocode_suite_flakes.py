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
it, or an incomplete run leaves the regression standing.

The other side of the same comparison is rechecked too (``pre_existing``). A test that failed in the
original code's one suite run is subtracted as a pre-existing failure, and the baseline is reused for
the rest of the run, so a test that failed there only by chance would hide a change that really breaks
it. A test that failed in both complete runs (with the same exclusions, and at most ``MAX_SUSPECTS``)
runs ``RERUNS`` more times on the original code. One that passed there runs ``RERUNS`` more times on the
candidate, and when it fails on every run with the change it is a regression after all. One that fails
on every run of the original code stays a pre-existing failure, as does one whose evidence is missing,
which is what the comparison already decided. ``recheck`` runs no command itself; the
caller supplies ``run(command, side, label)``. ``for_proof`` is autocode_verify's entry: it is given
verify's own tree and suite helpers, and imports nothing from it.
"""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_project_suite_health as project_suite_health
    from . import autocode_proof_stability as proof_stability
    from . import autocode_test_cases as test_cases
    from . import autocode_util as util
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_project_suite_health as project_suite_health
    import autocode_proof_stability as proof_stability
    import autocode_test_cases as test_cases
    import autocode_util as util
    import autocode_verification_schedule as schedule

RERUNS = proof_stability.REPEATS - 1
MAX_SUSPECTS = 10
RULE = (
    "A suite test that passed on the original code and failed on the candidate is run alone "
    f"{RERUNS} more times on the candidate; if it passes there, {RERUNS} more times on the original code. "
    "It is quarantined only when it also fails on the original code; otherwise it stays a regression."
)
QUARANTINE_REASON = "fails intermittently on the original code too, so its failure is not evidence against the change"
PRE_EXISTING_RULE = (
    "A suite test that failed on both the original code and the candidate is run alone "
    f"{RERUNS} more times on the original code; if it passes there, {RERUNS} more times on the candidate. "
    "It is a regression when it then failed on every run with the change; otherwise it stays a pre-existing failure."
)
UNMASKED = (
    "Tests that failed once on the original code pass there when rerun alone, but fail on every run with the change"
)


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
    return _eligible(on_candidate, base_receipt, "passed", protected, changed_texts)


def masked(on_candidate, base_receipt, *, protected=(), changed_texts=()) -> list[str]:
    """The candidate's failures that also failed on the original code, which the comparison subtracts.

    The same rules as ``suspects``: both runs complete, no due case names it, no changed file mentions it.
    """
    return _eligible(on_candidate, base_receipt, "failed", protected, changed_texts)


def _eligible(on_candidate, base_receipt, base_outcome, protected, changed_texts) -> list[str]:
    if not _complete(on_candidate) or not _complete(base_receipt):
        return []
    failed = set(on_candidate["results"].get("failed") or [])
    on_base = set(base_receipt["results"].get(base_outcome) or [])
    text = "\n".join(changed_texts)
    return sorted(
        test for test in failed & on_base if test not in set(protected) and not test_cases.named_in(test, text)
    )


def _verdict(candidate, base) -> str:
    if "pass" not in candidate[1:]:
        return "regression"
    if "missing" in candidate or not base[1:] or "missing" in base:
        return "regression"
    return "quarantined" if "fail" in base[1:] else "regression"


def recheck(framework_name, command, tests, run, *, directory=None, quarantined_entries=None) -> dict | None:
    """Rerun ``tests`` and decide each one; None when there is nothing to recheck.

    ``run(command, side, label)`` runs the narrowed command in the ``candidate`` or ``base`` tree and
    returns its receipt. The record lists every suspect's runs on each side (the first entry is the
    judged suite run) and which were quarantined.

    When ``quarantined_entries`` supplies valid prior base+candidate flake evidence for a suspect
    that still passes on a candidate rerun without missing results, the candidate check still runs
    and deterministic failures still remain regressions, while redundant ``base`` reruns are skipped.
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
    known_map = quarantined_entries if isinstance(quarantined_entries, dict) else {}
    known = {
        test: known_map[test]
        for test in again
        if test in known_map and "missing" not in outcomes[test] and "fail" in (known_map[test].get("base_runs") or [])
    }
    need_base = [test for test in again if test not in known]
    base_runs = []
    if need_base:
        retry = proof_stability.narrow(framework_name, command, need_base, directory=directory) or narrowed
        base_runs = [run(retry, "base", f"flake-base-{index}") for index in range(2, RERUNS + 2)]
    rows, q_rows = [], []
    for test in tests:
        if test in known:
            entry = known[test]
            base = list(entry["base_runs"])
            verdict = _verdict(outcomes[test], base)
            origin = str(entry.get("base_commit") or "")
            row: dict = {
                "test": test,
                "candidate": outcomes[test],
                "base": base,
                "verdict": verdict,
            }
            if verdict == "quarantined":
                row["source"] = "project_quarantine"
                row["origin_base"] = origin
                q_rows.append(
                    {
                        "test": test,
                        "reason": str(entry.get("reason") or QUARANTINE_REASON),
                        "source": "project_quarantine",
                        "origin_base": origin,
                    }
                )
            rows.append(row)
            continue
        base = ["pass", *(proof_stability.outcome_of(r, test) for r in base_runs)] if test in need_base else ["pass"]
        verdict = _verdict(outcomes[test], base)
        rows.append({"test": test, "candidate": outcomes[test], "base": base, "verdict": verdict})
        if verdict == "quarantined":
            q_rows.append({"test": test, "reason": QUARANTINE_REASON})
    return record(tests, rows, command=narrowed, quarantined_rows=q_rows)


def _pre_existing_verdict(base, candidate) -> str:
    if "pass" not in base[1:]:
        return "pre_existing"
    if not candidate[1:] or "missing" in candidate:
        return "pre_existing"
    return "flaky" if "pass" in candidate[1:] else "regression"


def recheck_pre_existing(framework_name, command, tests, run, *, directory=None, confirmed_entries=None) -> dict | None:
    """Rerun failures shared with the original code's suite run; None when there are none.

    ``run`` is ``recheck``'s. The original code runs first; only a test that passed there runs on the
    candidate. Each row's first entry on each side is the judged suite run. Tests already confirmed
    deterministically failing every run on the same base commit (``confirmed_entries``) stay
    pre-existing without repeating base reruns.
    """
    tests = list(tests)
    if not tests:
        return None
    confirmed_map = confirmed_entries if isinstance(confirmed_entries, dict) else {}
    known = {
        test: confirmed_map[test]
        for test in tests
        if test in confirmed_map
        and (confirmed_map[test].get("base_runs") or [])
        and "pass" not in confirmed_map[test]["base_runs"][1:]
        and "fail" in confirmed_map[test]["base_runs"]
    }
    unknown = [test for test in tests if test not in known]

    note = None
    narrowed = None
    on_base: dict[str, list[str]] = {}
    candidate_runs: list = []
    passed: list[str] = []
    if unknown:
        if len(unknown) > MAX_SUSPECTS:
            note = (
                f"{len(unknown)} tests failed on both the original code and the candidate, more than the "
                f"{MAX_SUSPECTS} rechecked, so they stay pre-existing failures without a recheck"
            )
        else:
            narrowed = proof_stability.narrow(framework_name, command, unknown, directory=directory)
            if not narrowed:
                note = (
                    "this runner cannot select the tests that failed on both sides alone, so they stay "
                    "pre-existing failures without a recheck"
                )
            else:
                base_runs = [run(narrowed, "base", f"pre-existing-base-{index}") for index in range(2, RERUNS + 2)]
                on_base = {
                    test: ["fail", *(proof_stability.outcome_of(r, test) for r in base_runs)] for test in unknown
                }
                passed = [test for test in unknown if "pass" in on_base[test][1:]]
                if passed:
                    retry = proof_stability.narrow(framework_name, command, passed, directory=directory) or narrowed
                    candidate_runs = [
                        run(retry, "candidate", f"pre-existing-candidate-{index}") for index in range(2, RERUNS + 2)
                    ]

    if note is not None and not known:
        return pre_existing_record(tests, note=note)

    rows = []
    for test in tests:
        if test in known:
            rows.append(
                {
                    "test": test,
                    "base": list(known[test]["base_runs"]),
                    "candidate": ["fail"],
                    "verdict": "pre_existing",
                    "source": "project_baseline",
                }
            )
            continue
        if test not in on_base:
            continue
        candidate = (
            ["fail", *(proof_stability.outcome_of(r, test) for r in candidate_runs)] if test in passed else ["fail"]
        )
        verdict = _pre_existing_verdict(on_base[test], candidate)
        rows.append({"test": test, "base": on_base[test], "candidate": candidate, "verdict": verdict})
    return pre_existing_record(tests, rows, command=narrowed, note=note)


def pre_existing_record(tests, rows=(), *, command=None, note=None) -> dict:
    rows = list(rows)
    return {
        "rule": PRE_EXISTING_RULE,
        "reruns": RERUNS,
        "suspects": list(tests),
        "command": command,
        "tests": rows,
        "regressed": [row["test"] for row in rows if row["verdict"] == "regression"],
        "note": note,
    }


def unmasked(recheck_record) -> list[str]:
    """Tests the original code's suite run failed by chance that fail on every run with the change."""
    return list(((recheck_record or {}).get("pre_existing") or {}).get("regressed") or [])


def record(tests, rows=(), *, command=None, note=None, quarantined_rows=None) -> dict:
    rows = list(rows)
    if quarantined_rows is None:
        quarantined_list = [
            {"test": row["test"], "reason": QUARANTINE_REASON} for row in rows if row["verdict"] == "quarantined"
        ]
    else:
        quarantined_list = list(quarantined_rows)
    return {
        "rule": RULE,
        "reruns": RERUNS,
        "suspects": list(tests),
        "command": command,
        "tests": rows,
        "quarantined": quarantined_list,
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
    if ((recheck_record or {}).get("pre_existing") or {}).get("note"):
        notes.append("Pre-existing failure recheck: " + recheck_record["pre_existing"]["note"])
    if not names:
        return notes, []
    rows = {row["test"]: row for row in recheck_record["tests"]}

    def format_entry(test: str) -> str:
        row = rows[test]
        suffix = (
            f"; reused from project quarantine at {row['origin_base'][:8]}"
            if row.get("source") == "project_quarantine" and row.get("origin_base")
            else ""
        )
        return f"{test} (candidate {', '.join(row['candidate'])}; original code {', '.join(row['base'])}{suffix})"

    detail = "; ".join(format_entry(test) for test in names)
    notes.append("Quarantined as flaky, not counted as preserved or as regressed: " + detail)
    return notes, [
        "suite tests that fail intermittently on the original code too were quarantined from the preservation "
        "check: " + ", ".join(names)
    ]


def _git_changed_files(workspace, origin: str, current: str) -> set[str] | None:
    if not origin or not current or origin.startswith("-") or current.startswith("-"):
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(workspace), "diff", "--name-only", "-z", "--no-renames", origin, current],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=util.git_environment(),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return {path for path in completed.stdout.split("\0") if path}


def for_proof(run, trees, on_candidate, comparable, helpers) -> list[str]:
    """Recheck one verify() preservation run (autocode_verify._Run); return the tests it quarantined.

    ``helpers`` are verify's ``(make_tree, run_suite, remove_tree)``. Only the failing tests run: in the
    candidate tree and, once one passed again there, in a fresh tree of the effective base (the one the
    baseline ran, ``base_patch`` applied), removed afterwards. A failure the original code's suite run
    shared is rechecked the other way round (``recheck_pre_existing``, base first, kept as the record's
    ``pre_existing``), and one it shows the change really breaks is added to the proof's failures.
    The record is the proof's ``suite_flakes``; its notes and review reasons join the proof's.
    Confirmed deterministic pre-existing base failures and intermittent base+candidate flaky tests
    are persisted per project via ``autocode_project_suite_health`` (only without ``base_patch`` or
    unpinned local inputs) and reused with staleness/edit invalidation across runs.
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
    project_eligible = run.base_patch is None and not (
        run.ignored_inputs
        and (
            getattr(run.ignored_inputs, "generated", None)
            or getattr(run.ignored_inputs, "vendor", None)
            or getattr(run.ignored_inputs, "unverified", None)
        )
    )
    proj_root: Path | None = None
    known_quarantined: dict[str, dict] = {}
    known_pre_existing: dict[str, dict] = {}
    if project_eligible:
        try:
            proj_root = project_suite_health.project_root(run.dependencies_from or run.workspace)
            known_quarantined = project_suite_health.active_quarantine(
                proj_root,
                framework.name,
                run.commands["suite"],
                run.base,
                git_changed_files_fn=lambda a, b: _git_changed_files(run.workspace, a, b),
            )
            known_pre_existing = project_suite_health.confirmed_pre_existing(
                proj_root,
                base=run.base,
                command=run.commands["suite"],
                framework_name=framework.name,
            )
        except (OSError, ValueError):
            proj_root = None
            known_quarantined = {}
            known_pre_existing = {}

    tests = suspects(on_candidate, comparable.get("receipt"), protected=named, changed_texts=texts)
    shared = masked(on_candidate, comparable.get("receipt"), protected=named, changed_texts=texts)

    def clean_quarantine_passes() -> set[str]:
        if not known_quarantined or not _complete(on_candidate) or not _complete(comparable.get("receipt")):
            return set()
        cand_passed = set(on_candidate["results"].get("passed") or [])
        base_passed = set(comparable["receipt"]["results"].get("passed") or [])
        return (cand_passed & base_passed & set(known_quarantined)) - set(named)

    run_name = (
        project_suite_health.run_name_from(Path(run.run_dir).parent.parent)
        if Path(run.run_dir).parent.name == "regression"
        else None
    )

    if not tests and not shared:
        clean_passed = clean_quarantine_passes()
        if proj_root is not None and clean_passed:
            with contextlib.suppress(OSError, ValueError):
                project_suite_health.record_observations(
                    proj_root,
                    base=run.base,
                    command=run.commands["suite"],
                    framework_name=framework.name,
                    clean_passed_tests=clean_passed,
                    run_name=run_name,
                )
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

    directory = getattr(framework, "directory", None)
    try:
        found = recheck(
            framework.name,
            run.commands["suite"],
            tests,
            rerun,
            directory=directory,
            quarantined_entries=known_quarantined,
        ) or record([])
        found["pre_existing"] = recheck_pre_existing(
            framework.name,
            run.commands["suite"],
            shared,
            rerun,
            directory=directory,
            confirmed_entries=known_pre_existing,
        )
    finally:
        if base_tree:
            remove_tree(run.workspace, base_tree[0])
    proof["suite_flakes"] = found
    if proj_root is not None and command_receipt.completed(comparable.get("receipt")):
        new_quarantine = [
            {
                "test": row["test"],
                "base_runs": row["base"],
                "candidate_runs": row["candidate"],
                "reason": QUARANTINE_REASON,
            }
            for row in found.get("tests") or ()
            if row.get("verdict") == "quarantined" and row.get("source") != "project_quarantine"
        ]
        pre_existing_rows = (found.get("pre_existing") or {}).get("tests") or ()
        new_quarantine.extend(
            {
                "test": row["test"],
                "base_runs": row["base"],
                "candidate_runs": row["candidate"],
                "reason": "fails intermittently on both the original code and the candidate",
            }
            for row in pre_existing_rows
            if row.get("verdict") == "flaky"
        )
        new_pre_existing = [
            {"test": row["test"], "base_runs": row["base"]}
            for row in pre_existing_rows
            if row.get("verdict") == "pre_existing"
            and row.get("source") != "project_baseline"
            and row.get("base")
            and all(outcome == "fail" for outcome in row["base"])
        ]
        clean_passed = clean_quarantine_passes()
        if new_quarantine or new_pre_existing or clean_passed:
            with contextlib.suppress(OSError, ValueError):
                project_suite_health.record_observations(
                    proj_root,
                    base=run.base,
                    command=run.commands["suite"],
                    framework_name=framework.name,
                    quarantine_rows=new_quarantine,
                    pre_existing_rows=new_pre_existing,
                    clean_passed_tests=clean_passed,
                    run_name=run_name,
                )
    if unmasked(found):
        run.fail.append(UNMASKED + ": " + ", ".join(unmasked(found)[:20]))
    notes, reasons = explain(found)
    run.notes.extend(notes)
    run.review_reasons.extend(reasons)
    return quarantined(found)
