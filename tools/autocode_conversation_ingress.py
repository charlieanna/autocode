"""Validate a conversation transfer under the task lock before any stage runs.

Handoff ingestion writes conversation_handoff: its immutable source pointer
lets the dashboard find the runner-owned conversation journal. A handoff contains
draft context and delivery receipts, never a goal approval. Validated explicit
Build actions append their separate audit record to the existing user_events.
"""

from __future__ import annotations

from pathlib import Path

try:
    from . import autocode_conversation as protocol
    from . import autocode_workspaces as workspaces
except ImportError:
    import autocode_conversation as protocol
    import autocode_workspaces as workspaces


def ingest(state, run_dir, workspace, source, *, existing_run=False):
    """Link exactly one staged receipt; a repeated identical link is harmless."""
    if source is None:
        return False
    workspace = Path(workspace).resolve()
    metadata = workspaces.metadata(workspace)
    project = Path(metadata["project_workspace"]).resolve() if metadata else workspace
    if state.get("project_workspace") and Path(state["project_workspace"]).resolve() != project:
        raise protocol.ConversationProtocolError("Conversation project does not match task workspace ownership.")
    source = Path(source)
    source = source if source.is_absolute() else project / source
    handoff = protocol.load_handoff(source, workspace=project)
    previous = state.get("conversation_handoff")
    if previous and (
        previous.get("digest") != handoff["digest"] or previous.get("conversation_id") != handoff["conversation_id"]
    ):
        raise protocol.ConversationProtocolError("A different conversation already owns this task.")
    journal_path = protocol.journal_file(run_dir)
    if existing_run and not previous and not journal_path.exists():
        raise protocol.ConversationProtocolError("Conversation handoff must be supplied when creating the task.")
    protocol.ingest_handoff(run_dir, handoff, source_path=source.resolve())
    pointer = {
        "conversation_id": handoff["conversation_id"],
        "digest": handoff["digest"],
        "path": str(source.resolve()),
        "journal": str(journal_path.resolve()),
    }
    if previous == pointer:
        return False
    state["conversation_handoff"] = pointer
    return True


def require_expected_goal(state, expected, *, token_for, is_approved):
    """Reject a stale dashboard Build request without changing any state."""
    if expected is None:
        return
    contract = state.get("goal_contract")
    try:
        current = (
            isinstance(expected, str)
            and isinstance(contract, dict)
            and expected == token_for(contract)
            and is_approved(state)
            and not state.get("pending_questions")
        )
    except (KeyError, TypeError, ValueError):
        current = False
    if state.get("settings", {}).get("joint_planning"):
        current = current and state.get("planning", {}).get("final_token") == expected
    if not current:
        raise ValueError(
            "The approved plan changed or is incomplete. Reload the task and approve its current plan before building."
        )


def record_build_start(state, expected, *, token_for, is_approved):
    """Audit an explicit Build separately from approval, once per approved token."""
    require_expected_goal(state, expected, token_for=token_for, is_approved=is_approved)
    if expected is None:
        return False
    events = state.setdefault("user_events", [])
    if any(
        row.get("kind") == "build_start" and row.get("token") == expected and row.get("actor") == "user_cli"
        for row in events
    ):
        return False
    events.append({"kind": "build_start", "actor": "user_cli", "token": expected, "at": protocol.now()})
    return True
