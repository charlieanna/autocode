"""Report-only mutation check of named test proof (#699).

Once a named proof passes, the runner changes the product lines the change touched, one small
mutation at a time (autocode_mutants), and runs the proving tests against each mutant in the proof's
candidate tree. A mutant those tests still pass is reported beside the criterion they prove: the
test passes with that line changed, so it may not pin what the line does. It complements the
check replay's sanity gate against empty test bodies (autocode_test_quality), which a test that
asserts something, but not enough, gets past.

Report only: nothing here changes the verdict, the failures, the unverified reasons or the proof's
receipts, and no mutant run is one of the proof's ``checks``. One deadline, ``BUDGET_SECONDS``,
covers every mutant: the same limit as one check replay. Python unittest and pytest proofs only for
now. Imports nothing from the runner; the caller owns the scratch tree and runs the commands.
"""

from __future__ import annotations

import shlex
import time
from pathlib import Path

try:
    from . import autocode_diff_coverage as diff_coverage
    from . import autocode_mutants as mutants
    from . import autocode_proof_stability as proof_stability
    from . import autocode_python_tests as python_tests
    from . import autocode_test_cases as test_cases
except ImportError:
    import autocode_diff_coverage as diff_coverage
    import autocode_mutants as mutants
    import autocode_proof_stability as proof_stability
    import autocode_python_tests as python_tests
    import autocode_test_cases as test_cases

BUDGET_SECONDS = 900
# A mutant that never returns (a negated loop condition) may not spend every other mutant's time:
# each gets at most this many times the unmutated run's duration, and never less than the floor.
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
    stale cached compile of the original."""
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


def collect(framework_name, command, cases, proof, tree, evidence_dir, files, diff_text, run, *, clock=time.monotonic):
    """Mutate the changed product lines in ``tree`` and run the proving tests against each mutant.

    ``proof`` holds the judged test lists (fail_to_pass, pass_to_pass, not_run_on_base, unstable);
    ``files`` is the changed product code as ``(path, status)``; ``diff_text(files)`` is their
    zero-context diff against the base; ``run(command, label, timeout)`` runs a command in ``tree``
    and returns its receipt with per-test results. Each mutated file gets its exact bytes back after
    its mutant runs, whatever happens, and an error becomes the record's note instead of reaching the
    proof. The proving command is narrowed to the proving tests; when the runner cannot narrow it,
    nothing runs (the whole suite is never the fallback).
    """
    if framework_name not in FRAMEWORKS:
        return record(
            "the mutation check runs Python unittest and pytest proofs only for now; this proof ran "
            + (framework_name or "no named-test runner")
        )
    try:
        return _collect(framework_name, command, cases, proof, tree, Path(evidence_dir), files, diff_text, run, clock)
    except Exception as error:  # report only: whatever goes wrong here, the proof itself stands
        return record(f"the mutation check stopped: {type(error).__name__}: {error}")


def _collect(framework_name, command, cases, proof, tree, evidence, files, diff_text, run, clock) -> dict:
    tests = proving_tests(cases, proof, framework_name)
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
        source = target.read_bytes()
        changed = mutants.changed_lines(diff_coverage.hunks(diff_text([(path, status)])))
        made, notes = mutants.python_mutants(path, source, set().union(*changed.values()))
        row["notes"] += notes
        planned += [(row, target, source, mutant) for mutant in made]
    if not planned:
        return record("no changed product line has a mutation site, so the mutation check ran no test", rows)
    evidence.mkdir(parents=True, exist_ok=True)
    deadline = clock() + BUDGET_SECONDS
    reference = run(isolated(narrowed, evidence / "pycache" / "unmutated"), "unmutated", BUDGET_SECONDS)
    outcome, passed = judge(reference, tests)
    if outcome != "survived":
        return record(
            "the proving tests did not pass unmutated in the mutation check's own run, so no mutant ran", rows
        )
    bound = max(MUTANT_FLOOR_SECONDS, MUTANT_FACTOR * float(reference.get("duration_seconds") or 0))
    unrun = 0
    for number, (row, target, source, mutant) in enumerate(planned, 1):
        entry = {"line": mutant.line, "operator": mutant.operator, "before": mutant.before, "after": mutant.after}
        remaining = deadline - clock()
        if remaining <= 0:
            unrun += 1
            row["mutants"].append({**entry, "outcome": "not_run", "survived_tests": []})
            continue
        label = f"mutant-{number:02d}"
        try:
            target.write_bytes(mutants.apply(source, mutant))
            receipt = run(isolated(narrowed, evidence / "pycache" / label), label, min(remaining, bound))
        finally:
            target.write_bytes(source)
        outcome, survived = judge(receipt, passed)
        row["mutants"].append({**entry, "outcome": outcome, "survived_tests": survived})
    note = (
        f"the {BUDGET_SECONDS}-second budget ran out with {unrun} of {len(planned)} mutants not run" if unrun else None
    )
    return record(note, rows, collected=True)


def survivors(check, case_tests) -> dict:
    """``{case id: rows}``: each mutant a criterion's proving test passed with. Only cases with one appear,
    so ``{}`` means every mutant that ran was killed. A row says which test passed with which change."""
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
    """The record a proof keeps once its verdict is final: a proof that did not pass reports no mutant
    (a ``NOT_PASSED`` note); a collected check gains ``survivors``, keyed by the final ``case_tests``."""
    if verdict != "PASS":
        return record(NOT_PASSED)
    if not isinstance(check, dict) or not check.get("collected"):
        return check
    return {**check, "survivors": survivors(check, case_tests)}
