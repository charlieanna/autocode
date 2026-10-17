"""A milestone's failure evidence, and whether a new failure adds any (#611).

Pure functions over plain values: the caller passes the latest validation, the findings ledger and the
changed files, and keeps the rows. A failure's items are the checks that exited nonzero, the criteria
not met (FAIL or UNVERIFIED) and the open blocking findings; its signature is those items with the
changed files. Against the milestone's earlier failures a new one is:

- ``replay``: its evidence is already recorded, so it charges nothing again;
- ``repeat``: it has failing items and the signature of an earlier failure, so it adds no evidence;
- ``progress``: every earlier failing item is now resolved (the check exited 0, the criterion passed,
  the finding was closed) and it fails only on items none of them had;
- ``new``: anything else, including a failure with no failing items or after one recorded without them.
"""

from __future__ import annotations

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def _rows(value) -> list[dict]:
    return [row for row in value or () if isinstance(row, dict)]


def observe(validation, findings, changed_files) -> dict:
    """The failing and resolved items and the changed files, each sorted and unique."""
    failing: set[str] = set()
    resolved: set[str] = set()
    validation = validation if isinstance(validation, dict) else {}
    for check in _rows(validation.get("checks")):
        command, code = check.get("command"), check.get("exit_code")
        if isinstance(command, str) and type(code) is int:
            (resolved if code == 0 else failing).add("check:" + command)
    for criterion in _rows(validation.get("criterion_results")):
        ident, status = criterion.get("id"), criterion.get("status")
        if isinstance(ident, str) and status in ("PASS", "FAIL", "UNVERIFIED"):
            (resolved if status == "PASS" else failing).add("criterion:" + ident)
    for finding in _rows(findings):
        ident, status = finding.get("id"), finding.get("status")
        if not isinstance(ident, str) or not isinstance(status, str):
            continue
        if status != "open":
            resolved.add("finding:" + ident)
        elif finding.get("blocking", True):
            failing.add("finding:" + ident)
    changed = {path for path in changed_files or () if isinstance(path, str)}
    return {"failing": sorted(failing), "resolved": sorted(resolved - failing), "changed": sorted(changed)}


def signature(seen: dict) -> str:
    """What failed and what changed; what was resolved is not part of it."""
    return util.digest({"failing": sorted(set(seen["failing"])), "changed": sorted(set(seen["changed"]))})


def assess(rows, evidence, seen: dict) -> str:
    """How a failure with ``evidence`` and observation ``seen`` relates to the earlier ``rows``.

    A row is ``{"evidence", "signature", "failing"}``; a failure recorded before the ledger has only
    ``evidence``, and rules out progress since nothing shows what it failed on.
    """
    rows = _rows(rows)
    if any(row.get("evidence") == evidence for row in rows):
        return "replay"
    failing = set(seen["failing"])
    if not failing:
        return "new"
    ident = signature(seen)
    if any(row.get("signature") == ident for row in rows):
        return "repeat"
    earlier = [set(row.get("failing") or ()) for row in rows]
    if earlier and all(earlier) and all(row.get("signature") for row in rows):
        before = set().union(*earlier)
        if before <= set(seen["resolved"]) and before.isdisjoint(failing):
            return "progress"
    return "new"
