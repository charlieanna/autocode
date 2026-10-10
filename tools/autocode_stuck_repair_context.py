"""Keep Investigator report repairs grounded in real files and scratch paths."""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import copy
from pathlib import Path

try:
    from . import autocode_stuck_job as stuck
except ImportError:
    import autocode_stuck_job as stuck


def evidence_instruction(stage: str) -> str:
    """Apply the stage's evidence policy without granting event citations to the Investigator."""
    if stage == stuck.STAGE:
        return prompts.get("fragments/stuck-repair-context/evidence-instruction-02.md")
    return prompts.get("fragments/stuck-repair-context/evidence-instruction.md")


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


def context(state: dict, stage: str, state_path, workspace, sources=()) -> dict:
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
