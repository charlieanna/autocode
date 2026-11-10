"""Bounded historical tool observations for a tool-free planning recovery.

Only stopped, archived reviews of the same contract and unchanged source qualify.
This packet supplies observations, never report, verification or approval authority.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts

MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_CHARS = 4000
MAX_PACKET_BYTES = 32000
MAX_ATTEMPTS = 3


def _read(path, root):
    path = Path(path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("observation artifact outside run")
    with path.open("rb") as stream:
        raw = stream.read(MAX_LOG_BYTES + 1)
    if len(raw) > MAX_LOG_BYTES:
        raise ValueError("observation artifact too large")
    return raw


def _observations(raw):
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        part = event.get("part") or {}
        item = event.get("item") or {}
        if event.get("type") == "tool_use" and isinstance(part, dict):
            state = part.get("state") or {}
            if not isinstance(state, dict) or state.get("status") not in ("completed", "error"):
                continue
            row = {
                "event_id": part.get("id"),
                "tool": part.get("tool"),
                "input": state.get("input"),
                "status": state["status"],
                "output": state.get("output", state.get("error")),
            }
        elif (
            event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "command_execution"
        ):
            row = {
                "event_id": item.get("id"),
                "tool": "command",
                "input": item.get("command"),
                "exit_code": item.get("exit_code"),
                "output": item.get("aggregated_output"),
            }
        else:
            continue
        if not isinstance(row["output"], str):
            continue
        row["truncated"] = len(row["output"]) > MAX_OUTPUT_CHARS
        row["output"] = row["output"][:MAX_OUTPUT_CHARS]
        yield row


def context(state: RunState, stage, run_dir, source_revision):
    contract = (state.get("goal_contract") or {}).get("hash")
    if stage not in ("review_plan", "finalize_plan") or not contract or not source_revision:
        return None
    root = Path(run_dir).resolve()
    attempts: list[dict] = []
    size = 0
    for record in reversed(state.get("stages") or []):
        if len(attempts) >= MAX_ATTEMPTS:
            break
        if not (
            record.get("stage") == stage
            and record.get("contract_hash") == contract
            and record.get("source_revision") == source_revision
            and record.get("changed_files") == []
            and record.get("rejected")
            and record.get("abandoned")
            and record.get("finished_at")
        ):
            continue
        try:
            before = json.loads(_read(record["before_ref"], root))
            after = json.loads(_read(record["after_ref"], root))
            if before.get("revision") != source_revision or after.get("revision") != source_revision:
                continue
            raw = _read(record["events"], root)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        rows, omitted = [], 0
        for row in _observations(raw):
            length = len(json.dumps(row).encode())
            if size + length > MAX_PACKET_BYTES:
                omitted += 1
                continue
            size += length
            rows.append(row)
        if rows:
            attempts.append(
                {
                    "events": record["events"],
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "observations": rows,
                    "omitted_observations": omitted,
                }
            )
    if not attempts:
        return None
    return {
        "source_revision": source_revision,
        "contract_hash": contract,
        "attempts": attempts,
        "instruction": prompts.get("fragments/autoplanner/retained-review-observations.md"),
    }
