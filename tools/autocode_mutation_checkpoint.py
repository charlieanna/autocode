"""Retain committed mutation observations when a refresh cannot replace them.

A failed refresh is evidence of an unsuccessful attempt, never a replacement
for the last authenticated study or authority to complete a changed line.
"""

from __future__ import annotations

from copy import deepcopy

FAILURE = "mutation_refresh_failure"


def _sites(report):
    if not isinstance(report, dict) or not isinstance(report.get("files"), list):
        return None
    sites = {}
    for file in report["files"]:
        if not isinstance(file, dict) or not isinstance(file.get("mutants"), list):
            return None
        for mutant in file["mutants"]:
            if not isinstance(mutant, dict):
                return None
            key = (file.get("path"), file.get("sha256"), mutant.get("site_id"), mutant.get("source_sha256"))
            if not all(isinstance(value, str) and value for value in key) or key in sites:
                return None
            sites[key] = mutant
    return sites


def replacement_error(previous, candidate):
    """A successful refresh includes the exact inventory and every attempted observation."""
    if not isinstance(candidate, dict) or candidate.get("collected") is not True:
        note = candidate.get("note") if isinstance(candidate, dict) else None
        return note if isinstance(note, str) and note else "The new mutation measurement did not complete"
    if not isinstance(previous, dict):
        return "The retained mutation study is unavailable"
    for key in ("selected_tests", "execution_policy"):
        if candidate.get(key) != previous.get(key):
            return "The refreshed mutation study changed its selected tests or execution policy"
    if not isinstance(candidate.get("reference_output"), str) or not candidate["reference_output"]:
        return "The refreshed mutation study has no current reference capture"
    old, new = _sites(previous), _sites(candidate)
    if old is None or new is None or not old or old.keys() != new.keys():
        return "The refreshed mutation study did not retain the exact site inventory"
    if any((row.get("outcome") != "not_run" or row.get("evidence")) and new[key] != row for key, row in old.items()):
        return "The refreshed mutation study changed a previously attempted observation"
    return None


def retain(previous, attempted, report, reason, *, deadline_expired):
    """Return prior facts plus the failed attempt, with a mandatory review blocker."""
    receipts = deepcopy(previous.get("changed_code_receipts") or [])
    outputs = {row["output"]: row for row in receipts}
    new_outputs, conflicts = [], []
    for row in attempted.get("changed_code_receipts") or []:
        if not isinstance(row, dict) or not isinstance(row.get("output"), str) or not row["output"]:
            conflicts.append(deepcopy(row))
            continue
        output = row.get("output")
        if output in outputs:
            if outputs[output] != row:
                conflicts.append(deepcopy(row))
        else:
            receipts.append(deepcopy(row))
            outputs[output] = row
            new_outputs.append(output)
    reference = report.get("reference_attempt") if isinstance(report, dict) else None
    reference = reference if isinstance(reference, dict) else {}
    budget = (
        deadline_expired
        and (reference.get("timed_out") is True or reference.get("not_started") is True)
        and reference.get("budget_limited") is True
        and not conflicts
    )
    failure = {
        "kind": "window_budget_exhausted" if budget else "reference_unavailable",
        "reason": reason,
        "command_outputs": new_outputs,
        "reference_attempt": deepcopy(reference),
        "attempted_report": deepcopy(report),
        "receipt_conflicts": conflicts,
    }
    inputs = deepcopy(previous.get("changed_code_inputs") or {})
    inputs["command_outputs"] = [row["output"] for row in receipts]
    inputs[FAILURE] = failure
    return deepcopy(previous["mutation_check"]), receipts, inputs, failure
