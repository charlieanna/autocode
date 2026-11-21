"""Display bounded historical suite reports without granting them proof authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_named_collection as named
    from . import autocode_requirements_observations as observations
    from . import autocode_requirements_review as review
except ImportError:
    import autocode_named_collection as named
    import autocode_requirements_observations as observations
    import autocode_requirements_review as review

MAX_REPORTS = 8
MAX_PATHS = 40


def project(state: RunState, run_dir, current_source):
    """Describe retained complete outputs; neither their text nor this view is execution proof."""
    if (state.get("settings") or {}).get("replay_recording"):
        return []
    handoff = review.current_turn_handoff(state)
    packet = observations._handoff_packet(state, "claim_evidence", handoff, run_dir, current_source)
    if not packet:
        return []
    try:
        raw = observations._read(Path(run_dir), packet["artifact"]["path"])
        if hashlib.sha256(raw).hexdigest() != packet["artifact"]["sha256"]:
            return []
        payload = json.loads(raw)
        retained = payload.get("retained_outputs", [])
        if not isinstance(retained, list):
            return []
        rows = []
        for item in retained:
            if not isinstance(item, dict):
                continue
            command, output, digest = item.get("command"), item.get("output"), item.get("sha256")
            if (
                not isinstance(command, str)
                or not observations._direct(command)
                or len(command) > 512
                or not isinstance(output, str)
                or hashlib.sha256(output.encode()).hexdigest() != digest
            ):
                continue
            parsed = named.pytest_collection(command, output) or named.pytest_execution(command, output)
            if not parsed:
                continue
            paths = sorted({node.split("::", 1)[0] for node in parsed["node_ids"]})
            if any(len(path) > 256 for path in paths):
                continue
            rows.append(
                {
                    "kind": "collection" if parsed["kind"] == "reported_collection" else "execution",
                    "command": command,
                    "reported_count": len(parsed["node_ids"]),
                    "paths": paths[:MAX_PATHS],
                    "omitted_paths": max(0, len(paths) - MAX_PATHS),
                    "independently_verified": False,
                }
            )
            if len(rows) == MAX_REPORTS:
                break
        return rows
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
