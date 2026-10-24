"""The chat's live agent log: what each running agent thinks, says and does, from its own event file.

``view["chat"]["log"]`` is the terminal chat's live log (owner decision 2026-10-21): for the attempt
supervised now, and for each parallel Builder whose own attempt is supervised now, its reasoning,
its messages, and the commands, edits and tool calls ``chat.activity`` already names, each tagged with
the agent's screen name. Unlike ``chat.activity`` it copies reasoning and message prose, collapsed to
one line, bounded to its newest words and with credential-shaped text redacted; tool output is never
copied. It is display only: never proof that a command passed, a change is right or a requirement is
met, never a report and never authority to approve or retry. Only the tail of an event file inside
the attempt's own run directory is read, and an attempt that cannot be confirmed as supervised shows
nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_chat_activity as activity
    from . import autocode_roles as roles
    from . import autocode_util as util
except ImportError:
    import autocode_chat_activity as activity
    import autocode_roles as roles
    import autocode_util as util

LIMIT = 40  # entries from one attempt
PROSE_LIMIT = 400
WORKER_LIMIT = 8  # parallel Builders read in one view
PROSE = {"reasoning": "thinking", "agent_message": "message", "text": "message"}  # an event's kind in the log
CODEX_ROWS = ("item.started", "item.updated", "item.completed")


def _prose(value) -> str:
    """Model text as one line: control characters and runs of space collapsed, secrets redacted, the
    newest words kept."""
    text = " ".join("".join(char if char.isprintable() else " " for char in str(value or "")).split())
    text = util.redact(text)
    return text if len(text) <= PROSE_LIMIT else "…" + text[-(PROSE_LIMIT - 1) :]


def prose_event(row: dict) -> tuple[str, dict] | None:
    """A reasoning or message row (a Codex-style item, or an OpenCode or Kilo ``text`` or ``reasoning``
    part) as an event; a later snapshot of the same item has the same key."""
    item, part = row.get("item"), row.get("part")
    if row.get("type") in CODEX_ROWS and isinstance(item, dict):
        kind, identifier, text = PROSE.get(str(item.get("type"))), item.get("id"), item.get("text")
    elif row.get("type") in ("text", "reasoning") and isinstance(part, dict):
        kind, identifier, text = PROSE.get(str(row["type"])), part.get("id"), part.get("text")
    else:
        return None
    if kind is None or not isinstance(identifier, (str, int)) or not isinstance(text, str):
        return None
    shown = _prose(text)
    return (f"{kind}:{identifier}", {"kind": kind, "text": shown}) if shown else None


def progress_event(row: dict) -> tuple[str, dict] | None:
    """An opaque streaming receipt proves activity, never the content of a thought or response."""
    progress = row.get("progress")
    if row.get("type") != "autocode_progress" or not isinstance(progress, dict):
        return None
    kind, identifier, position = progress.get("kind"), progress.get("id"), progress.get("position")
    if (
        kind not in ("reasoning", "text")
        or not isinstance(identifier, (str, int))
        or isinstance(identifier, bool)
        or not str(identifier).strip()
        or not isinstance(position, int)
        or isinstance(position, bool)
        or position <= 0
        or progress.get("nonwhite") is not True
    ):
        return None
    name = "Reasoning" if kind == "reasoning" else "Response"
    shown = PROSE[kind]
    return f"{shown}:{identifier}", {
        "kind": shown,
        "text": f"{name} stream activity received; text unavailable.",
        "status": "streaming",
    }


def _receipts(entries: dict[str, dict]) -> dict[str, dict]:
    """One line per kind of opaque streaming receipt, saying how many parts it stands for.

    A provider that streams no reasoning or reply text sends one receipt per part, and a run's worth of
    identical "text unavailable" lines buries the tool calls a person opens the live log to see (#1999).
    A lone receipt keeps the wording it has today, and the key of the first keeps its place in the list.
    """
    kept: dict[str, dict] = {}
    first: dict[str, str] = {}
    counts: dict[str, int] = {}
    for key, entry in entries.items():
        kind = str(entry["kind"]) if entry.get("status") == "streaming" else None
        if kind is None:
            kept[key] = entry
            continue
        counts[kind] = counts.get(kind, 0) + 1
        if kind not in first:
            first[kind] = key
            kept[key] = dict(entry)
    for kind, key in first.items():
        if counts[kind] > 1:
            kept[key]["text"] = f"{kept[key]['text']} ({counts[kind]} parts streamed)"
    return kept


def read(path: Path, workspace=None) -> list[tuple[str, dict]]:
    """The newest entries in an attempt's event file, oldest first, each with its key in that file."""
    entries: dict[str, dict] = {}
    supplied_prose: set[str] = set()
    for row in activity.rows(path):
        found = prose_event(row)
        if found is not None:
            supplied_prose.add(found[0])
        else:
            found = progress_event(row)
            if found is not None and found[0] in supplied_prose:
                continue  # a late opaque receipt must not replace the provider's actual prose
            if found is None:
                found = (
                    activity.item_event(row, workspace)
                    if isinstance(row.get("item"), dict)
                    else activity.part_event(row, workspace)
                )
                if found is not None:
                    found = (f"{found[1]['kind']}:{found[0]}", found[1])
        if found is not None:
            entries[found[0]] = found[1]  # a later row for the same item replaces it in place
    return list(_receipts(entries).items())[-LIMIT:]


def _attempt(active, root: Path, workspace, agent: str) -> list[dict]:
    """One attempt's entries, each with an id that stays the same across views."""
    if not isinstance(active, dict) or not isinstance(active.get("events"), str):
        return []
    path = Path(active["events"]).resolve()
    if not path.is_relative_to(root.resolve()):
        return []
    tag = hashlib.sha256(str(path).encode()).hexdigest()[:10]  # item ids repeat across attempts
    return [{"id": f"{tag}:{key}", "agent": agent, **entry} for key, entry in read(path, workspace)]


def _agent(stage: str, state: RunState, milestone=None) -> str:
    name = roles.screen_name(stage, state) or "AutoCode"
    return f"{name} · task {milestone}" if milestone and roles.job_for_stage(stage, state) == "builder" else name


def _workers(state: RunState, workspace, supervised: Callable[[dict], bool]) -> list[dict]:
    """The parallel Builders working now, each from its own run's saved attempt."""
    batch = state.get("orchestration_batch")
    if not isinstance(batch, dict) or batch.get("status") != "BUILDING" or not workspace:
        return []
    home = Path(workspace).resolve()
    found: list[dict] = []
    running = [row for row in batch.get("workers") or [] if isinstance(row, dict) and row.get("status") == "RUNNING"]
    for row in running[:WORKER_LIMIT]:
        if not isinstance(row.get("run_dir"), str):
            continue
        run_dir = Path(row["run_dir"]).resolve()
        if not run_dir.is_relative_to(home):
            continue
        try:
            child = json.loads((run_dir / "state.json").read_text())
        except (OSError, ValueError):
            continue
        active = child.get("active_stage") if isinstance(child, dict) else None
        if not isinstance(active, dict) or not supervised(active):
            continue  # a Builder that cannot be confirmed as working shows nothing
        agent = _agent(str(active.get("stage") or "terra"), state, row.get("milestone_id"))
        found.extend(_attempt(active, run_dir, child.get("workspace") or row.get("workspace"), agent))
    return found


def project(
    state: RunState,
    public,
    run_dir,
    *,
    supervised: Callable[[dict], bool],
    finished=False,
    stopped=False,
) -> list[dict]:
    """What the running agents think and do now, or none unless the run works on its own.

    ``supervised(active_stage)`` says whether a parallel Builder's own attempt is supervised now; the
    run's own attempt is confirmed by the view's ``liveness``, as ``chat.activity`` is."""
    if public.get("status") != "RUNNING" or (public.get("needs") or {}).get("kind") != "continue" or stopped:
        return []
    workspace = state.get("workspace") or public.get("workspace")
    found: list[dict] = []
    active = state.get("active_stage")
    if (
        isinstance(active, dict)
        and not finished
        and run_dir is not None
        and (public.get("liveness") or {}).get("kind") == "supervised"
    ):
        milestone = (state.get("current_task") or {}).get("milestone_id")
        found.extend(
            _attempt(active, Path(run_dir), workspace, _agent(str(active.get("stage") or ""), state, milestone))
        )
    return found + _workers(state, workspace, supervised)
