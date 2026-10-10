"""Keep Investigator report repairs grounded in real files and scratch paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy
from pathlib import Path

try:
    from . import autocode_stuck_job as stuck
except ImportError:
    import autocode_stuck_job as stuck


def evidence_instruction(stage: str) -> str:
    """Apply the stage's evidence policy without granting event citations to the Investigator."""
    if stage == stuck.STAGE:
        return (
            "For investigate_stuck, evidence_refs must name exact existing files in the repository "
            "or this run's directory; event: IDs are invalid for investigate_stuck, including shell "
            "events that read those files. Use investigation_context.evidence_files to pair each "
            "evidence_ref with its probe_path. Cite the real file in evidence_refs and open its "
            "scratch probe_path in probe; never open an absolute live run path or prepend an "
            "archived directory name to the file basename. Only cited run files are copied into "
            "the scratch tree at run/<file basename>; repository files retain their repository "
            "paths. Missing evidence must remain missing; never invent a citation. "
        )
    return (
        "Evidence references must be bare event: IDs or exact file paths, with no appended explanations or line annotations. "
        "For captured checks, use the command and exit_code inside each receipt, not the "
        "outer capture invocation. A Validator check still requires an independently executed "
        "Validator tool event; a capture receipt alone cannot establish that independence. "
        "Preserve executed successful checks; a PASS verdict "
        "requires at least one. If none are supported by the original events and receipts, "
        "report NOT_VERIFIED. "
        "An event: reference must identify a completed shell command in original.events; "
        "event IDs from another stage or MCP/image-viewing calls are not shell-check evidence. "
        "For criterion and end-to-end evidence from MCP images or retained prior stages, "
        "cite the exact existing artifact file path (such as the owning stage JSONL), "
        "not an event: ID from that other stage. Preserve those artifacts and their observations. "
        "Artifact evidence paths must resolve inside the project; for observations retained "
        "only in an external temporary file, cite the original project-contained event log "
        "that records them and preserve the observation and its limitations. "
    )


def evidence_files(refs, workspace, run_dir) -> list[dict]:
    """Describe only valid cited files, using the admission validator's run-file mapping."""
    workspace, run_dir = Path(workspace).resolve(), Path(run_dir).resolve()
    files = []
    for raw in dict.fromkeys(str(ref).strip() for ref in refs if ref):
        if not raw:
            continue
        try:
            copies = stuck.cited_files({"evidence_refs": [raw]}, workspace, run_dir)
        except ValueError:
            continue
        if copies:
            destination = next(iter(copies))
        else:
            ref = Path(raw.rsplit(":", 1)[0] if ":" in raw and raw.rsplit(":", 1)[1].isdigit() else raw)
            source = (ref if ref.is_absolute() else workspace / ref).resolve()
            destination = source.relative_to(workspace).as_posix()
        files.append({"evidence_ref": raw, "probe_path": destination})
    return files


def context(state: RunState, stage: str, state_path, workspace, sources=()) -> dict:
    """Restore the active investigation handoff and exact available probe-file destinations."""
    if stage != stuck.STAGE or not state.get("stuck_investigation"):
        return {}
    packet = copy.deepcopy(stuck.packet(state, state_path))
    refs = [str(state_path)]
    for row in state.get("stages", [])[-12:]:
        refs.extend(row.get(key) for key in ("output", "events", "schema") if row.get(key))
    for source in sources:
        refs.append(source.get("path"))
        content = source.get("content")
        if isinstance(content, dict):
            refs.extend(content.get("evidence_refs") or [])
    packet["evidence_files"] = evidence_files(refs, workspace, Path(state_path).parent)
    return packet
