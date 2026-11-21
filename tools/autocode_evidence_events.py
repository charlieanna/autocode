"""Read and normalize a stage event log for evidence consumers."""

import json
from pathlib import Path


def events(path):
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except ValueError:
            continue
    if any(
        row.get("sessionID") and row.get("type") in ("step_start", "step_finish", "tool_use", "text", "error")
        for row in rows
    ):
        try:
            from .providers import opencode_events
        except ImportError:
            from providers import opencode_events
        return opencode_events.normalized_events(rows)
    return rows
