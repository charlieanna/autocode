"""Report-only mutation check of named test proof (#699).

Once a named proof's verdict is final and a pass, the runner changes the product lines the change
touched, one small mutation at a time (autocode_mutants), and runs the proving tests against each
mutant in the proof's candidate tree. A mutant those tests still pass is reported beside the test
case they prove: the test passes with that line changed, so it may not pin what the line does. It
complements the check replay's sanity gate against empty test bodies (autocode_test_quality), which
a test that asserts something, but not enough, gets past.

Report only: nothing here changes the verdict, the failures, the unverified reasons or the proof's
receipts, and no mutant run is one of the proof's ``checks``. One deadline, ``BUDGET_SECONDS``,
covers the unmutated run and every mutant: the same limit as one check replay. Python unittest and
pytest proofs only for now. Imports nothing from the runner; the caller owns the scratch tree and
runs the commands.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import time
from pathlib import Path

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_diff_coverage as diff_coverage
    from . import autocode_mutants as mutants
    from . import autocode_proof_stability as proof_stability
    from . import autocode_python_tests as python_tests
    from . import autocode_test_cases as test_cases
    from . import autocode_verification_schedule as verification_schedule
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_diff_coverage as diff_coverage
    import autocode_mutants as mutants
    import autocode_proof_stability as proof_stability
    import autocode_python_tests as python_tests
    import autocode_test_cases as test_cases
    import autocode_verification_schedule as verification_schedule

BUDGET_SECONDS = 900
# A mutant that never returns (a negated loop condition) should not spend every other mutant's time:
# each gets this many times the unmutated run's duration, at least the floor, and never more than the
# budget left. No mutant starts with less than the floor left; one the budget cut short is not_run.
MUTANT_FACTOR = 10
MUTANT_FLOOR_SECONDS = 60
FRAMEWORKS = ("unittest", "pytest")
OUTCOMES = ("killed", "survived", "timeout", "not_run")
NOT_PASSED = "not run: the proof did not pass"


def record(note=None, files=(), *, collected=False) -> dict:
    return {
        "collected": collected,
        "note": note,
        "limit_per_file": mutants.PER_FILE_LIMIT,
        "budget_seconds": BUDGET_SECONDS,
        "files": list(files),
    }


def proving_tests(cases, proof, framework_name) -> list[str]:
    """The judged tests the due cases name (autocode_proof_stability.proving_tests); unstable ones never count."""
    named = [test for key in ("fail_to_pass", "pass_to_pass", "not_run_on_base") for test in proof.get(key) or []]
    unstable = {row.get("test") for row in proof.get("unstable") or [] if isinstance(row, dict)}
    selected = proof_stability.proving_tests(cases, named, [], framework_name)
    return sorted(test for test in selected if test not in unstable)


def isolated(command, cache) -> str:
    """``command`` reading and writing compiled bytecode only under ``cache``, which starts empty: a
    mutant the same size as the original, written in the same second, could otherwise run from a
    stale cached compile of the original. A Python the tests start without this environment is
    covered by each mutant's own modification time (_collect)."""
    invocation = python_tests.parse(command)
    prefix = list(invocation.prefix) or ["env"]
    return shlex.join(
        [*prefix, f"PYTHONPYCACHEPREFIX={cache}", invocation.python, "-m", invocation.kind, *invocation.arguments]
    )


def judge(receipt, tests) -> tuple[str, list[str]]:
    """(outcome, the proving tests that passed). ``killed`` when a proving test failed or the run failed
    before it could report them; ``survived`` when none failed and some passed; else ``timeout`` or
    ``not_run``."""
    if not receipt or receipt.get("error"):
        return "not_run", []
    if receipt.get("timed_out"):
        return "timeout", []
    seen = [proof_stability.outcome_of(receipt, test) for test in tests]
    if "fail" in seen or receipt.get("exit_code") not in (0, None):
        return "killed", []
    passed = [test for test, outcome in zip(tests, seen, strict=True) if outcome == "pass"]
    return ("survived", passed) if passed and receipt.get("exit_code") == 0 else ("not_run", [])


def proved_outcome(receipt, tests) -> tuple[str, list[str]]:
    """A completed named test must observe a mutation; a broken runner proves nothing.

    Advisory studies keep their historical outcome vocabulary. Completion enforcement
    additionally requires complete results for exactly the selected tests and refuses
    collection/setup failures, missing tests and skipped tests.
    """
    if not receipt or receipt.get("error") or receipt.get("interrupted"):
        return "not_run", []
    if receipt.get("timed_out"):
        return "timeout", []
    results = receipt.get("results") or {}
    if not verification_schedule.complete_results(receipt) or any(
        results.get(key) for key in ("setup_errors", "skipped", "collection_errors", "uncollected")
    ):
        return "not_run", []
    passed, failed = set(results.get("passed") or []), set(results.get("failed") or [])
    if passed | failed != set(tests):
        return "not_run", []
    if failed and type(receipt.get("exit_code")) is int and receipt["exit_code"] != 0:
        return "killed", sorted(failed)
    if not failed and receipt.get("exit_code") == 0:
        return "survived", sorted(passed)
    return "not_run", []


def collect(
    framework_name,
    command,
    cases,
    proof,
    tree,
    evidence_dir,
    files,
    diff_text,
    run,
    *,
    clock=time.monotonic,
    enforcement=False,
):
    """Mutate the changed product lines in ``tree`` and run the proving tests against each mutant.

    ``proof`` holds the judged test lists (fail_to_pass, pass_to_pass, not_run_on_base, unstable);
    ``files`` is the changed product code as ``(path, status)``; ``diff_text(files)`` is their
    zero-context diff against the base; ``run(command, label, timeout)`` runs a command in ``tree``
    and returns its receipt with per-test results. Each mutated file gets its exact bytes and
    modification time back after its mutant runs, whatever happens, and an error becomes the record's
    note (a file's own, when only that file could not be read or mutated) instead of reaching the
    proof. The proving command is narrowed to the proving tests; when the runner cannot narrow it,
    nothing runs (the whole suite is never the fallback).
    """
    if framework_name not in FRAMEWORKS:
        return record(
            "the mutation check runs Python unittest and pytest proofs only for now; this proof ran "
            + (framework_name or "no named-test runner")
        )
    try:
        tests = proving_tests(cases, proof, framework_name)
        return _collect(
            framework_name,
            command,
            tests,
            tree,
            Path(evidence_dir),
            files,
            diff_text,
            run,
            clock,
            strict=enforcement,
            enforcement=enforcement,
        )
    except command_receipt.OwnershipUncertain as error:
        if enforcement:
            raise
        return record(f"the mutation check stopped: {type(error).__name__}: {error}")
    except Exception as error:  # report only: whatever goes wrong here, the proof itself stands
        return record(f"the mutation check stopped: {type(error).__name__}: {error}")


def collect_selected_tests(
    framework_name,
    command,
    test_ids,
    tree,
    evidence_dir,
    files,
    diff_text,
    run,
    *,
    clock=time.monotonic,
    deadline=None,
    enforcement=False,
):
    """Report mutations against independently observed passing tests, without inventing named cases.

    A review supplies exact test IDs observed in the change's own tests. Every selected ID must
    pass again, with complete results, before any mutant runs. An optional absolute deadline includes
    the caller's discovery and initial test run in the same 900-second allowance. Unknown command
    ownership is never converted into a report-only note or permission to clean up its tree.
    """
    if framework_name not in FRAMEWORKS:
        return record("the mutation check runs Python unittest and pytest tests only for now")
    try:
        tests = list(test_ids)
        if any(not isinstance(test, str) or not test for test in tests):
            return record("the selected tests do not contain ordinary test IDs")
        tests = sorted(set(tests))
        maximum = clock() + BUDGET_SECONDS
        deadline = min(deadline, maximum) if deadline is not None else maximum
        return _collect(
            framework_name,
            command,
            tests,
            tree,
            Path(evidence_dir),
            files,
            diff_text,
            run,
            clock,
            deadline=deadline,
            strict=True,
            enforcement=enforcement,
        )
    except command_receipt.OwnershipUncertain:
        raise
    except Exception as error:
        return record(f"the mutation check stopped: {type(error).__name__}: {error}")


def _collect(
    framework_name,
    command,
    tests,
    tree,
    evidence,
    files,
    diff_text,
    run,
    clock,
    *,
    deadline=None,
    strict=False,
    enforcement=False,
) -> dict:
    if not tests:
        return record("no judged test is named after a due case, so the mutation check ran no test")
    narrowed = proof_stability.narrow(framework_name, command, tests)
    if not narrowed or python_tests.parse(narrowed) is None:
        return record("this runner cannot select the proving tests alone, so the mutation check ran no test")
    root = Path(tree).resolve()
    rows, planned = [], []
    for path, status in files:
        if not path.endswith((".py", ".pyi")):
            continue
        row: dict = {"path": path, "mutants": [], "notes": []}
        rows.append(row)
        target = Path(tree) / path
        if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(root):
            row["notes"].append(f"{path}: not a regular file inside the proof tree; not mutated")
            continue
        try:
            source, times = target.read_bytes(), target.stat()
            changed = mutants.changed_lines(diff_coverage.hunks(diff_text([(path, status)])))
            made, notes = mutants.python_mutants(
                path,
                source,
                set().union(*changed.values()),
                **({"limit": max(1, len(source))} if enforcement else {}),
            )
        except Exception as error:  # this file's note; the other files are still mutated
            if strict and isinstance(error, command_receipt.OwnershipUncertain):
                raise
            row["notes"].append(f"{path}: not mutated: {type(error).__name__}: {error}")
            continue
        row["notes"] += notes
        if enforcement:
            row["sha256"] = hashlib.sha256(source).hexdigest()
        planned += [(row, target, source, times, mutant) for mutant in made]
    if not planned:
        return record("no changed product line has a mutation site, so the mutation check ran no test", rows)
    evidence.mkdir(parents=True, exist_ok=True)
    if deadline is None:
        deadline, reference_limit = clock() + BUDGET_SECONDS, BUDGET_SECONDS
    else:
        reference_limit = min(BUDGET_SECONDS, deadline - clock())
        if reference_limit <= 0:
            return record("the 900-second mutation budget expired before the unmutated run", rows)
    reference = run(isolated(narrowed, evidence / "pycache" / "unmutated"), "unmutated", reference_limit)
    outcome, passed = (proved_outcome if enforcement else judge)(reference, tests)
    if outcome != "survived" or (strict and set(passed) != set(tests)):
        return record(
            "the proving tests did not pass unmutated in the mutation check's own run, so no mutant ran", rows
        )
    bound = max(MUTANT_FLOOR_SECONDS, MUTANT_FACTOR * float(reference.get("duration_seconds") or 0))
    unrun = 0
    for number, (row, target, source, times, mutant) in enumerate(planned, 1):
        entry = {"line": mutant.line, "operator": mutant.operator, "before": mutant.before, "after": mutant.after}
        if enforcement:
            entry.update(end_line=mutant.end_line, source_sha256=mutant.source_sha256, failed_tests=[], evidence=[])
        remaining = deadline - clock()
        if remaining < MUTANT_FLOOR_SECONDS:
            unrun += 1
            row["mutants"].append({**entry, "outcome": "not_run", "survived_tests": []})
            continue
        label, limit = f"mutant-{number:02d}", min(remaining, bound)
        ownership_uncertain = False
        try:
            target.write_bytes(mutants.apply(source, mutant))
            # Each version its own whole second, so a cached compile keyed by modification time and size
            # (a test's own Python, started with a minimal environment, -E or -I) is never reused.
            os.utime(target, ns=(times.st_atime_ns, times.st_mtime_ns - number * 1_000_000_000))
            receipt = run(isolated(narrowed, evidence / "pycache" / label), label, limit)
        except command_receipt.OwnershipUncertain:
            ownership_uncertain = strict
            raise
        finally:
            if not ownership_uncertain:
                target.write_bytes(source)
                os.utime(target, ns=(times.st_atime_ns, times.st_mtime_ns))
        outcome, observed = (proved_outcome if enforcement else judge)(receipt, passed)
        survived = observed if outcome == "survived" else []
        if enforcement:
            entry.update(failed_tests=observed if outcome == "killed" else [], evidence=[receipt["output"]])
        if strict and outcome == "survived" and set(survived) != set(passed):
            outcome, survived = "not_run", []
        if outcome == "timeout" and limit < bound:  # the budget cut it short, not the mutant
            outcome, unrun = "not_run", unrun + 1
        row["mutants"].append({**entry, "outcome": outcome, "survived_tests": survived})
    note = (
        f"the {BUDGET_SECONDS}-second budget ran out with {unrun} of {len(planned)} mutants not run" if unrun else None
    )
    result = record(note, rows, collected=True)
    if enforcement:
        result.update(
            selected_tests=tests,
            reference_output=reference["output"],
            selection="all_eligible_changed_sites",
            limit_per_file=max(len(source) for _, _, source, _, _ in planned),
        )
    return result


def survivors(check, case_tests) -> dict:
    """``{case id: rows}``: each mutant a due test case's proving test passed with. The keys are the
    test-case ids of ``case_tests``: a diagnosis's ``T1`` for a bug fix, the acceptance criterion's id
    for a planned build. Only cases with one appear, so ``{}`` means every mutant that ran was killed. A row says which
    test passed with which change."""
    found: dict[str, list] = {}
    for case_id, names in (case_tests or {}).items():
        for item in check.get("files") or []:
            for mutant in item.get("mutants") or []:
                if mutant.get("outcome") != "survived":
                    continue
                for test in names or []:
                    if test not in (mutant.get("survived_tests") or []):
                        continue
                    where = f"{item['path']}:{mutant['line']}"
                    found.setdefault(case_id, []).append(
                        {
                            "test": test,
                            "path": item["path"],
                            "line": mutant["line"],
                            "operator": mutant["operator"],
                            "before": mutant["before"],
                            "after": mutant["after"],
                            "text": f"{test_cases.function_name(test)} passes with {where} changed "
                            f"(`{mutant['before']}` -> `{mutant['after']}`)",
                        }
                    )
    return found


def final(check, verdict, case_tests):
    """The record a proof keeps once its verdict is final: every proof that did not pass, including one
    refused before any test ran, has the ``NOT_PASSED`` note and no mutant; a collected check gains
    ``survivors``, keyed by the final ``case_tests``; a passing proof that ran no check (no due case)
    keeps None."""
    if verdict != "PASS":
        return record(NOT_PASSED)
    if not isinstance(check, dict) or not check.get("collected"):
        return check
    return {**check, "survivors": survivors(check, case_tests)}
