"""The regression proof as a stage that diagnoses one failure reads it (#4219).

The Resolver (``resolve`` and its ``diagnose`` stage) borrows the Completion Reviewer's handoff, and with it
the regression proof that judging stages read whole: a record of every mutant, the same mutants again in
the measured inputs, one command receipt for each mutant run, and every changed line, proven or not. That
record grows with the size of the change. On a large build it filled most of a 623 KB Resolver prompt that
was re-read on every tool request, while the diagnosis needed the failure, not the matrix.

``view`` keeps what a diagnosis acts on whole: the verdict, failures, tests, commands and checks; every
changed line still open, with its source line and handle; every mutant that was not killed; and every
command receipt that timed out or broke supervision. What is settled (a killed mutant, a proven line, an
ordinary receipt) is listed by file and line, or counted, and the whole record stays in the proof file at
``path``, which the Resolver can read. Nothing is cut by size, and no judging stage reads this view: the
Tester and the Completion Reviewer keep the full proof.
"""

from __future__ import annotations

try:
    from . import autocode_changed_code_evidence as changed_code_evidence
except ImportError:
    import autocode_changed_code_evidence as changed_code_evidence

# A mutant whose outcome needs no reading: a test killed it, or the budget never ran it.
SETTLED_MUTANTS = ("killed", "not_run")
# Measured inputs that repeat the per-line and per-mutant record (or its raw outputs) kept in the proof file.
REPEATED_INPUTS = ("command_outputs", "coverage", "inventory", "line_texts", "mutation")
WHERE = "the proof file at regression_proof.path"


def view(proof):
    """``proof`` (``autocode_regression.handoff``'s answer) as the Resolver reads it."""
    if not isinstance(proof, dict):
        return proof
    result = dict(proof)
    listed = []
    if isinstance(proof.get("changed_code_evidence"), dict):
        result["changed_code_evidence"] = _lines(proof["changed_code_evidence"])
        listed.append("changed_code_evidence")
    if isinstance(proof.get("mutation_check"), dict):
        result["mutation_check"] = _mutants(proof["mutation_check"])
        listed.append("mutation_check")
    if isinstance(proof.get("changed_code_inputs"), dict):
        inputs = proof["changed_code_inputs"]
        result["changed_code_inputs"] = {key: value for key, value in inputs.items() if key not in REPEATED_INPUTS}
        listed.append("changed_code_inputs")
    if isinstance(proof.get("changed_code_receipts"), list):
        receipts = proof["changed_code_receipts"]
        kept = [row for row in receipts if _abnormal(row)]
        result["changed_code_receipts"] = kept
        result["changed_code_receipts_listed"] = {"total": len(receipts), "shown": len(kept)}
        listed.append("changed_code_receipts")
    if listed:
        result["incident_view"] = {
            "listed": listed,
            "shown": "open changed lines, mutants that were not killed and abnormal receipts, whole",
            "read": f"every mutant, changed line and command receipt is in {WHERE}",
        }
    return result


def _lines(assessment):
    """Every open changed line whole; each proven or excepted line by number under its file."""
    rows = assessment.get("obligations")
    if not isinstance(rows, list):
        return assessment
    result = {key: value for key, value in assessment.items() if key != "obligations"}
    result["obligations"], settled = [], {}
    for row in rows:
        if isinstance(row, dict) and row.get("status") in changed_code_evidence.SETTLED:
            settled.setdefault(row["status"], {}).setdefault(row.get("path"), []).append(row.get("line"))
        else:
            result["obligations"].append(row)
    if settled:
        result["settled_lines"] = settled
    return result


def _mutants(report):
    """Each mutant a test did not kill whole; killed and unrun mutants by line under their file."""
    files = report.get("files")
    if not isinstance(files, list):
        return report
    result = {key: value for key, value in report.items() if key != "files"}
    result["files"] = []
    for file in files:
        if not isinstance(file, dict) or not isinstance(file.get("mutants"), list):
            result["files"].append(file)
            continue
        row = {key: value for key, value in file.items() if key != "mutants"}
        row["mutants"], settled = [], {}
        for mutant in file["mutants"]:
            outcome = mutant.get("outcome") if isinstance(mutant, dict) else None
            if outcome in SETTLED_MUTANTS:
                settled.setdefault(outcome, []).append(mutant.get("line"))
            else:
                row["mutants"].append(mutant)
        if settled:
            row["settled_mutant_lines"] = settled
        result["files"].append(row)
    return result


def _abnormal(receipt) -> bool:
    """A command receipt a diagnosis must read: it timed out or its supervision recorded an error."""
    return not isinstance(receipt, dict) or receipt.get("timed_out") is True or bool(receipt.get("supervision_errors"))
