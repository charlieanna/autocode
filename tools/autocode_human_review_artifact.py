"""The current artifact/evidence binding shared by human acceptance and rejection."""

from pathlib import Path

try:
    from . import autocode_contract_identity as identity
    from . import autocode_human_review_flow as human_review_flow
    from . import autocode_util as s
except ImportError:
    import autocode_contract_identity as identity
    import autocode_human_review_flow as human_review_flow
    import autocode_util as s


def token(state):
    val = state.get("validation", {})
    if not identity.approved(state) or not val.get("source_revision"):
        return None
    return identity.token(state["goal_contract"]) + "@" + val["source_revision"] + ":" + s.digest(val)


def validate(state, criterion, selected, current, *, action="approval"):
    """Require a displayed, technically verified artifact and unchanged evidence."""
    val = state.get("validation", {})
    human_only_gap = human_review_flow.human_only_pending_validation(
        state["goal_contract"]["body"]["acceptance_criteria"], val, criterion
    )
    if (
        selected != token(state)
        or selected != state.get("displayed_review")
        or val.get("source_revision") != current["revision"]
        or (val.get("verdict") != "PASS" and not human_only_gap)
        or val.get("contract_revision") != state["goal_contract"]["revision"]
        or val.get("contract_hash") != state["goal_contract"]["hash"]
        or val.get("criteria_revision") != state.get("criteria_revision")
        or (state.get("current_task") and val.get("task_id") != state["current_task"]["id"])
        or not val.get("evidence_hashes")
        or not val.get("checks")
        or any(c["exit_code"] != 0 for c in val["checks"])
        or any(f.get("blocking", True) for f in val.get("findings", []))
        or not (
            human_only_gap
            or any(
                c["id"] == criterion and c["status"] == "PASS" and c["evidence_refs"]
                for c in val.get("criterion_results", [])
            )
        )
        or any(not Path(p).is_file() or s.file_hash(p) != h for p, h in val.get("evidence_hashes", {}).items())
    ):
        raise ValueError(f"Human {action} needs the displayed, current validated artifact")
    required = {c["id"] for c in state["goal_contract"]["body"]["acceptance_criteria"] if c["human_review"]}
    if criterion not in required:
        raise ValueError("No such required human-review criterion")
