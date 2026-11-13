"""Bounded investigation context from a stopped Tester, never accepted evidence."""

import json
from pathlib import Path

try:
    from . import autocode_opencode as opencode
    from . import autocode_recovery_context as recovery_context
    from . import autocode_support as support
except ImportError:
    import autocode_opencode as opencode
    import autocode_recovery_context as recovery_context
    import autocode_support as support

MAX_PACKET_BYTES = 64 * 1024
MAX_COMMANDS = 24
MAX_TEXT = 2048
INSTRUCTION = (
    "Continue the archived Tester's investigation from these completed tool observations and partial notes "
    "rather than rediscovering the same work. Inspect the pinned originals for omitted or truncated detail. "
    "earlier_attempts pins work from preceding stalls; inspect it before repeating that investigation. "
    "These are historical observations, not an accepted Tester report or current-attempt evidence: do not "
    "cite their event IDs as newly executed checks. Reuse only evidence the current runner proof already "
    "authorizes; execute missing checks and produce a fresh independent report. Preserve failures, "
    "acceptance checks, model routes, execution limits and recovery budgets."
)


def capture(record, before, after, *, previous=None):
    """Summarize stopped work, keeping exact archived bytes available for inspection."""
    if before["revision"] != after["revision"]:
        return {"available": False, "reason": "Tester source changed during the stopped attempt"}
    event_path = Path(record["events"])
    if not event_path.is_file():
        return {"available": False, "reason": "Tester event log is missing"}
    try:
        artifacts = {
            key: {"path": str(path), "sha256": support.file_hash(path)}
            for key in ("events", "output", "response_text", "tool_evidence")
            if record.get(key) and (path := Path(record[key])).is_file()
        }
        commands = [
            row["item"]
            for row in support.events(event_path)
            if row.get("type") == "item.completed"
            and isinstance(row.get("item"), dict)
            and row["item"].get("type") in ("command_execution", "tool_output")
        ]
        # OpenCode normalizes tool events but keeps assistant text only in its raw stream.
        texts = {}
        for index, row in enumerate(opencode.raw_events(event_path)):
            item = row.get("part") if row.get("type") == "text" else row.get("item")
            if (
                isinstance(item, dict)
                and (row.get("type") == "text" or item.get("type") == "agent_message")
                and isinstance(item.get("text"), str)
            ):
                texts[item.get("id", index)] = item["text"]
        text = "\n".join(texts.values())
        earlier = (
            [previous["artifacts"], *previous.get("earlier_attempts", [])]
            if previous and previous.get("available")
            else []
        )
        packet = {
            "available": True,
            "verification": "unverified",
            "source_revision": before["revision"],
            "task_id": record.get("task_id"),
            "contract_hash": record.get("contract_hash"),
            "criteria_revision": record.get("criteria_revision"),
            "artifacts": artifacts,
            "earlier_attempts": earlier[:3],
            "omitted_earlier_attempts": max(0, len(earlier) - 3)
            + (previous.get("omitted_earlier_attempts", 0) if previous else 0),
            "earlier_work_unavailable": previous.get("reason") if previous and not previous.get("available") else None,
            "partial_text": text[-4 * MAX_TEXT :],
            "partial_text_truncated": len(text) > 4 * MAX_TEXT,
            "commands": [],
            "omitted_commands": len(commands),
            "instruction": INSTRUCTION,
        }
        for item in commands[-MAX_COMMANDS:]:
            command, output = str(item.get("command") or ""), str(item.get("aggregated_output") or "")
            packet["commands"].append(
                {
                    "event_id": item.get("id"),
                    "command": command[:MAX_TEXT],
                    "exit_code": item.get("exit_code"),
                    "output": output[:MAX_TEXT],
                    "truncated": len(command) > MAX_TEXT or len(output) > MAX_TEXT,
                }
            )
        packet["omitted_commands"] = len(commands) - len(packet["commands"])
        while len(json.dumps(packet).encode()) > MAX_PACKET_BYTES and packet["commands"]:
            packet["commands"].pop(0)
            packet["omitted_commands"] += 1
        if len(json.dumps(packet).encode()) > MAX_PACKET_BYTES:
            return {"available": False, "reason": "Tester continuation metadata exceeds the context bound"}
        return packet
    except (OSError, ValueError) as error:
        return {"available": False, "reason": f"Cannot read stopped Tester evidence: {error}"}


def pending(state, revision):
    """Find the last unfinished Tester's context, even after an intervening review."""
    latest = next(
        (
            row
            for row in reversed(state.get("stages") or [])
            if (row.get("original_stage") or row.get("stage")) == "tester"
            and not row.get("report_only")
            and not row.get("dry_run")
        ),
        None,
    )
    if not latest or not latest.get("timed_out") or not latest.get("automatic_recovery"):
        return None
    if (
        latest.get("source_revision") != revision
        or not latest.get("task_id")
        or latest["task_id"] != (state.get("current_task") or {}).get("id")
        or not latest.get("contract_hash")
        or latest["contract_hash"] != (state.get("goal_contract") or {}).get("hash")
        or latest.get("criteria_revision") != state.get("criteria_revision")
    ):
        return None
    attempt = recovery_context._attempt_id(latest)
    packet = next(
        (
            row.get("partial_review")
            for row in reversed(state.get("automatic_timeout_recoveries") or [])
            if row.get("attempt_id") == attempt
        ),
        None,
    )
    if not packet or not packet.get("available"):
        return packet
    artifacts = [packet["artifacts"], *packet.get("earlier_attempts", [])]
    for artifact in (artifact for group in artifacts for artifact in group.values()):
        path = Path(artifact["path"])
        if not path.is_file():
            return {"available": False, "reason": f"Archived Tester artifact is missing: {path}"}
        try:
            if support.file_hash(path) != artifact["sha256"]:
                return {"available": False, "reason": f"Archived Tester artifact changed: {path}"}
        except OSError as error:
            return {"available": False, "reason": f"Cannot read archived Tester artifact: {error}"}
    return packet
