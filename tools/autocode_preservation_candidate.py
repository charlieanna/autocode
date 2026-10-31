"""Pinned guard tests can be submitted unchanged for fresh independent review."""
from pathlib import Path

try:
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_contract_identity as contracts
    from . import autocode_coordination_context as coordination
    from . import autocode_test_cases as test_cases
    from . import autocode_util as util
except ImportError:
    import autocode_acceptance_tests as acceptance
    import autocode_contract_identity as contracts
    import autocode_coordination_context as coordination
    import autocode_test_cases as test_cases
    import autocode_util as util


def candidate(state, record, revision):
    """Recognize exact spec-first preservation input, never grant proof or completion.

    Only authenticated coordinated producers with an approved all-guard assignment
    qualify. The receipt, archive, live files and unchanged Builder snapshots must
    agree. Ordinary product repairs retain their normal no-progress handling.
    """
    task = state.get("current_task") or {}
    contract = state.get("goal_contract") or {}
    cases = test_cases.proof_cases(state)
    if (
        not all(key in contract for key in ("task_id", "revision", "body"))
        or not contracts.approved(state) or not coordination.context(state)
        or not cases or any(case.get("kind") != "preserve" for case in cases)
        or task.get("contract_hash") != contract.get("hash")
        or task.get("kind") != "implement" or task.get("rolled_back")
        or not task.get("id") or record.get("task_id") != task["id"]
        or record.get("changed_files") or record.get("rolled_back")
        or record.get("source_revision") != revision or acceptance.is_blind(state)
    ):
        return None
    try:
        before = util.read_object(record["before_ref"])
        after = util.read_object(record["after_ref"])
        if before != after or after.get("revision") != revision or not isinstance(after.get("files"), dict):
            return None
        receipts = [row for row in acceptance.receipts(state).values()
                    if row.get("contract_hash") == contract["hash"] and row.get("source_revision") == revision]
        if not {case["id"] for case in cases} <= {cid for row in receipts for cid in row.get("case_ids", [])}:
            return None
        files = {path: entry for row in receipts for path, entry in row.get("files", {}).items()}
        owned = task.get("affected_paths") or []
        if not files or any(not any(path == root.rstrip('/') or path.startswith(root.rstrip('/') + '/')
                                     for root in owned) for path in files):
            return None
        workspace = Path(state["workspace"]).resolve()
        for path, entry in files.items():
            target = workspace / path
            if (not target.resolve().is_relative_to(workspace) or target.is_symlink()
                or not entry.get("sha256") or util.file_hash(entry["archive"]) != entry["sha256"]
                or util.file_hash(target) != entry["sha256"]
                or after["files"].get(path) != entry["sha256"]):
                return None
    except (OSError, KeyError, TypeError, ValueError, AttributeError, RuntimeError):
        return None
    return {"source_revision": revision, "retained_paths": sorted(files), "acceptance_guards": True}
