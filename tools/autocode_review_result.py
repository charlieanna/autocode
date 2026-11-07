"""Inspect a recorded review result without treating it as build acceptance."""

from __future__ import annotations

import hashlib
from pathlib import Path


def inspect(result, workspace, current_revision):
    """Add read-time freshness; old reviews without a binding remain unknown.

    Only the core-owned report path is read, never an arbitrary saved reference.
    The canonical report is checked separately from the source snapshot.
    """
    reasons = []
    freshness = "unknown"
    root = Path(workspace).resolve()
    report = root / "review/findings.json"
    try:
        safe = result.get("artifact") == "review/findings.json" and report.resolve().is_relative_to(root)
        matches = safe and result.get("artifact_sha256") == hashlib.sha256(report.read_bytes()).hexdigest()
    except OSError:
        matches = False
    if not matches:
        freshness = "stale_or_unverified"
        reasons.append("The saved report is missing, changed or has no recorded digest.")
    if not result.get("source_revision") or current_revision is None:
        reasons.append("No current source binding is available for this recorded review.")
    elif result["source_revision"] != current_revision:
        freshness = "stale_or_unverified"
        reasons.append("The source changed after this review.")
    elif matches:
        freshness = "current"
        reasons.append("The report and source match this recorded review.")
    return {**result, "freshness": freshness, "reasons": reasons}
