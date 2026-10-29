"""Decide whether each changed executable line has current behavioral evidence.

Pure policy: callers classify product lines, authenticate runner observations and
independent review decisions, and bind them to the current proof context. This
module does not execute tools, infer assertion quality from prose, or authenticate
a model's claims. Existing advisory coverage/mutation summaries need a trusted
projection into these envelopes before they can satisfy this policy.

Inventory rows contain path, sha256 and lines (changed executable line numbers).
Coverage and mutation envelopes contain source_revision, proof_context and files.
Each file has path and sha256. Coverage files carry executed_lines; mutation files
carry mutants with line, outcome, evidence references and failed_tests. A killed
mutant counts only when a selected test demonstrably failed, not when its harness
failed to start or collect. All observations at that line must be demonstrated
kills. Missing, sampled-out, unsupported and unsuccessful observations stay open.

Exceptions are already independently authenticated decisions supplied by the
caller. They must name the exact gap, source, context, file hash and line, and
carry accepted=True, a reason and evidence references. Their semantic adequacy
belongs to that reviewer; nonempty prose alone never creates review authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import PurePosixPath

_SHA256 = re.compile(r"[0-9a-f]{64}")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _texts(value):
    return isinstance(value, list) and bool(value) and all(_text(item) for item in value)


def _lines(value):
    return isinstance(value, list) and all(type(line) is int and line > 0 for line in value)


def _same_context(value, source_revision, proof_context):
    return (
        isinstance(value, dict)
        and value.get("source_revision") == source_revision
        and value.get("proof_context") == proof_context
    )


def _files(value, source_revision, proof_context):
    if not _same_context(value, source_revision, proof_context) or value.get("collected") is False:
        return {}
    rows = value.get("files")
    if not isinstance(rows, list):
        return {}
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not _text(row.get("path")):
            return {}
        path = row["path"]
        result[path] = None if path in result else row
    return result


def gap_id(source_revision, proof_context, path, sha256, line):
    """Identity shared by the assessor and independently authenticated review decisions."""
    binding = [source_revision, proof_context, path, sha256, line]
    return "changed-code:" + hashlib.sha256(json.dumps(binding, separators=(",", ":")).encode()).hexdigest()


def _inventory(value):
    if not isinstance(value, (list, tuple)):
        return None
    result, paths = [], set()
    for row in value:
        if not isinstance(row, dict):
            return None
        path, digest, lines = row.get("path"), row.get("sha256"), row.get("lines")
        if (
            not _text(path)
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or "\\" in path
            or path in paths
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
            or not _lines(lines)
            or len(set(lines)) != len(lines)
        ):
            return None
        paths.add(path)
        result.append({"path": path, "sha256": digest, "lines": sorted(lines)})
    return sorted(result, key=lambda row: row["path"])


def _mutation(row, line):
    mutants = row.get("mutants") if isinstance(row, dict) else None
    if not isinstance(mutants, list) or any(
        not isinstance(item, dict) or type(item.get("line")) is not int or item["line"] < 1 for item in mutants
    ):
        return False, [], []
    found = [item for item in mutants if item["line"] == line]
    demonstrated = bool(found) and all(
        item.get("outcome") == "killed" and _texts(item.get("evidence")) and _texts(item.get("failed_tests"))
        for item in found
    )
    outcomes = sorted(str(item.get("outcome") or "unknown") for item in found)
    evidence = sorted({ref for item in found if _texts(item.get("evidence")) for ref in item["evidence"]})
    return demonstrated, outcomes, evidence


def _exception(exceptions, obligation, source_revision, proof_context):
    matches = [row for row in exceptions if isinstance(row, dict) and row.get("gap_id") == obligation["gap_id"]]
    if len(matches) != 1:
        return None
    row = matches[0]
    if (
        not _same_context(row, source_revision, proof_context)
        or type(row.get("line")) is not int
        or any(row.get(key) != obligation[key] for key in ("path", "sha256", "line"))
        or row.get("accepted") is not True
        or not _text(row.get("reason"))
        or not _texts(row.get("evidence"))
    ):
        return None
    return {
        "reason": row["reason"].strip(),
        "evidence": sorted(set(row["evidence"])),
        **{key: deepcopy(row[key]) for key in ("reason_kind", "reviewer") if key in row},
    }


def assess(inventory, coverage, mutation, *, source_revision, proof_context, exceptions=()):
    """Return deterministic PASS/UNVERIFIED, per-line obligations and chosen fallbacks.

    Empty, well-formed inventories have no executable obligation. Malformed or
    unknown inventories fail closed. A mutation-only fallback needs the same
    demonstrated test failures as normal mutation evidence, for each exact line;
    collection success or an empty survivor list is never enough.
    """
    result = {
        "schema": 1,
        "source_revision": source_revision,
        "proof_context": proof_context,
        "status": "UNVERIFIED",
        "reason": "Changed-code inventory or current proof binding is missing or malformed.",
        "obligations": [],
        "fallback": [],
    }
    files = _inventory(inventory)
    if files is None or not _text(source_revision) or not _text(proof_context):
        return result
    covered = _files(coverage, source_revision, proof_context)
    mutated = _files(mutation, source_revision, proof_context)
    exceptions = exceptions if isinstance(exceptions, (list, tuple)) else ()
    for file in files:
        path, digest = file["path"], file["sha256"]
        execution, changes = covered.get(path), mutated.get(path)
        if not isinstance(execution, dict) or execution.get("sha256") != digest:
            execution = {}
        if not isinstance(changes, dict) or changes.get("sha256") != digest:
            changes = {}
        executed = execution.get("executed_lines")
        for line in file["lines"]:
            ran = _lines(executed) and line in executed
            demonstrated, outcomes, evidence = _mutation(changes, line)
            obligation = {
                "gap_id": gap_id(source_revision, proof_context, path, digest, line),
                "path": path,
                "sha256": digest,
                "line": line,
                "status": "tested" if demonstrated else "reason_required",
                "executed": bool(ran),
                "mutation_demonstrated": demonstrated,
                "mutation_outcomes": outcomes,
                "evidence": evidence,
                "fallback": "none" if ran else "mutation_only" if demonstrated else "reviewer_reason_required",
                "reason": "Current selected tests detect the changed line's mutants."
                if demonstrated
                else "No complete behavioral mutation evidence for this changed line."
                if ran
                else "No measured execution or complete behavioral mutation evidence for this changed line.",
            }
            if not demonstrated:
                decision = _exception(exceptions, obligation, source_revision, proof_context)
                if decision is not None:
                    obligation = {
                        **obligation,
                        "status": "excepted",
                        "reason": decision["reason"],
                        "exception": decision,
                    }
                obligation["fallback"] = "reviewer_reason" if decision is not None else "reviewer_reason_required"
            result["obligations"].append(obligation)
    result["fallback"] = sorted({row["fallback"] for row in result["obligations"] if row["fallback"] != "none"})
    gaps = [row for row in result["obligations"] if row["status"] == "reason_required"]
    if not gaps:
        result = {
            **result,
            "status": "PASS",
            "reason": "Every changed executable line has current behavioral evidence or an independently checked reason."
            if result["obligations"]
            else "No changed executable product lines.",
        }
    else:
        result["reason"] = (
            "Changed product lines still need behavioral evidence or an independently checked reason: "
            + ", ".join(f"{row['path']}:{row['line']}" for row in gaps)
        )
    return result
