"""Bulky mutation and per-line detail in a review handoff: counted inline, kept whole in the context artifact.

A wave of several Builders can change hundreds of lines. Its regression proof then lists every mutant
per file, every surviving mutant once per criterion, the same mutants again under the evidence's own
inputs, and one obligation row per changed line. On a live two-Builder wave (2026-11-06) those four
fields were 1.6 MB of a 1.99 MB Tester handoff after the receipt bound (``autocode_context.proof_receipts``),
and the model's provider refused the prompt as too long before the Tester could read it.

For a review stage, each of those fields that is over ``LIMIT`` moves whole into the hash-pinned context
artifact, and the handoff keeps a summary: counts and outcomes per file, the tests each criterion's
survivors passed, and per file the tested lines plus every other line's handle, line, status, execution
and mutation outcomes. The gap a Tester decides is named by its handle, which the generation schema already offers and
the runner resolves against the run's own assessment (``autocode_changed_code_review``). Nothing moves
when another part of the handoff points into an obligation, or when the summary would not be smaller.
Pure: the caller passes the original proof, the packet it already copied, and the dict of moved fields.
"""

from __future__ import annotations

import copy
import json
from collections import Counter

LIMIT = 65536
STAGES = ("tester", "review_completion", "review_checkpoint")
FILES = "regression_proof_mutation_files"
SURVIVORS = "regression_proof_mutation_survivors"
INPUTS = "regression_proof_changed_code_inputs"
OBLIGATIONS = "regression_proof_obligations"
RECEIPTS = "regression_proof_receipts"  # moved whole by autocode_context.proof_receipts
POINTER = "#/regression_proof/changed_code_evidence/obligations"
INSTRUCTION = (
    "Large mutation and per-line detail is summarized here and kept whole in context_artifact (path and "
    "SHA-256 below) under {fields}. mutation_check.files keeps each file's mutant count and outcomes; "
    "mutation_check.survivors keeps each criterion's survivor count and the tests that passed with a mutant; "
    "changed_code_evidence.obligations groups the changed lines by file: tested_lines lists the lines whose "
    "mutants the tests already kill, and gaps lists every other line with its handle, line, status, "
    "execution and mutation outcomes. Only a handle under gaps names a gap; a tested line is not one and "
    "takes no changed_code_exceptions decision. Read the complete rows before "
    "relying on a mutant, a reason or an evidence log. Missing, unreadable or hash-mismatched artifacts are "
    "unverified evidence, never permission to infer PASS. Every obligation and gate still applies."
)
RECEIPT_INSTRUCTION = (
    "changed_code_receipts is summarized: the receipt count, the count of each exit code, and every "
    "receipt that timed out or has supervision errors, whole with its index. Every complete receipt is in "
    "context_artifact under regression_proof_receipts at that index. Read the complete receipt before "
    "relying on its command, test results, output tail or supervision evidence. Missing, unreadable or "
    "hash-mismatched artifacts are unverified evidence, never permission to infer PASS. Current "
    "obligations, mutation results and all gates still apply."
)


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _size(value) -> int:
    return len(json.dumps(value))


def _files(rows):
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return None
    summary = []
    for index, row in enumerate(rows):
        mutants = row.get("mutants") if isinstance(row.get("mutants"), list) else []
        outcomes = Counter(str(m.get("outcome")) for m in mutants if isinstance(m, dict))
        summary.append(
            {
                **{key: row[key] for key in ("path", "sha256", "notes") if key in row},
                "mutants": len(mutants),
                "outcomes": dict(sorted(outcomes.items())),
                "details_artifact": {"field": FILES, "index": index},
            }
        )
    return summary


def _survivors(value):
    if not isinstance(value, dict) or not all(isinstance(rows, list) for rows in value.values()):
        return None
    return {
        criterion: {
            "count": len(rows),
            "tests": sorted({str(row.get("test")) for row in rows if isinstance(row, dict) and row.get("test")}),
            "details_artifact": {"field": SURVIVORS, "key": criterion},
        }
        for criterion, rows in value.items()
    }


def _inputs(value):
    if not isinstance(value, dict):
        return None
    return {
        key: entry
        if _size(entry) <= LIMIT // 8
        else {
            "total": len(entry) if isinstance(entry, (list, dict)) else None,
            "details_artifact": {"field": INPUTS, "key": key},
        }
        for key, entry in value.items()
    }


def _obligations(rows):
    if not isinstance(rows, list) or not rows:
        return None
    groups: dict = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("handle"), str) or not row["handle"]:
            return None  # a gap without a handle could only be named by its full id, which stays in the rows
        group = groups.setdefault((row.get("path"), row.get("sha256")), {"tested_lines": [], "gaps": []})
        if row.get("status") == "tested" and isinstance(row.get("line"), int) and "end_line" not in row:
            # A live Tester given every line with a handle decided tested lines too, and the schema
            # refused the whole report: only a gap gets a handle here.
            group["tested_lines"].append(row["line"])
            continue
        group["gaps"].append(
            {
                key: row[key]
                for key in ("handle", "line", "end_line", "status", "executed", "mutation_outcomes")
                if key in row
            }
        )
    return [{"path": path, "sha256": digest, **lines} for (path, digest), lines in groups.items()]


def _receipts(rows):
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return None
    exits = Counter(str(row.get("exit_code")) for row in rows if "exit_code" in row)
    return {
        "total": len(rows),
        "exit_codes": dict(sorted(exits.items())),
        "notable": [
            {**row, "index": index}
            for index, row in enumerate(rows)
            if row.get("timed_out") or row.get("supervision_errors") or "exit_code" not in row
        ],
        "details_artifact": {"field": RECEIPTS},
    }


def move(source, result, moved) -> None:
    """Summarize ``result``'s oversized proof detail in place; put the originals from ``source`` in ``moved``."""
    proof = result.get("regression_proof")
    original = source.get("regression_proof")
    if result.get("stage") not in STAGES or not isinstance(proof, dict) or not isinstance(original, dict):
        return
    if result.get("context_artifact") or "proof_detail_instruction" in proof:
        return
    mutation, evidence = _dict(proof.get("mutation_check")), _dict(proof.get("changed_code_evidence"))
    before, assessed = _dict(original.get("mutation_check")), _dict(original.get("changed_code_evidence"))
    others = {key: value for key, value in result.items() if key != "regression_proof"}
    pointed = POINTER in json.dumps(others) or POINTER in json.dumps(
        {key: value for key, value in proof.items() if key != "changed_code_evidence"}
    )
    plans = (
        (FILES, mutation, "files", before.get("files"), _files),
        (SURVIVORS, mutation, "survivors", before.get("survivors"), _survivors),
        (INPUTS, proof, "changed_code_inputs", original.get("changed_code_inputs"), _inputs),
        (OBLIGATIONS, evidence, "obligations", None if pointed else assessed.get("obligations"), _obligations),
    )
    fields = []
    for field, holder, key, whole, summarize in plans:
        if whole is None or key not in holder or _size(holder[key]) <= LIMIT:
            continue
        summary = summarize(whole)
        if summary is None or _size(summary) + 2048 >= _size(holder[key]):
            continue
        moved[field] = copy.deepcopy(whole)
        holder[key] = summary
        fields.append(field)
    receipts = proof.get("changed_code_receipts")
    if RECEIPTS in moved and isinstance(receipts, list) and _size(receipts) > LIMIT:
        # Even without their execution detail, hundreds of mutant receipts stay large; each one's
        # mutant outcome is already in mutation_check and on its changed line.
        summary = _receipts(receipts)
        if summary is not None and _size(summary) + 2048 < _size(receipts):
            proof["changed_code_receipts"] = summary
            proof["receipt_details_instruction"] = RECEIPT_INSTRUCTION
    if fields:
        proof["proof_detail_instruction"] = INSTRUCTION.format(fields=", ".join(fields))
