"""Durable, state-addressed handoffs for the v2 planning stages.

Only the runner writes these files.  Stage code prepares immutable bytes and
state identities; the runner flushes them before it persists the new state.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

try:
    from . import autocode_goals as goals, autocode_planning_graph as graph, autocode_support as support
except ImportError:
    import autocode_goals as goals
    import autocode_planning_graph as graph
    import autocode_support as support


STAGE_STEMS = {
    "requirements": "requirements",
    "plan": "plan",
    "plan_review": "review",
    "plan_revise": "revision",
    "plan_finalize": "final-plan",
}
PREDECESSOR_STAGES = {
    "plan": "requirements",
    "plan_review": "plan",
    "plan_revise": "plan_review",
    "plan_finalize": "plan_revise",
}


def _canonical_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _identity(path, data):
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest()}


def _safe_path(run_dir, relative):
    run_dir = Path(run_dir).resolve()
    if not isinstance(relative, str) or not relative.startswith("planning/"):
        raise ValueError("Planning artifact path must be a relative planning/ path")
    path = run_dir / relative
    if path.is_symlink() or not path.resolve().is_relative_to(run_dir):
        raise ValueError("Planning artifact path escapes the run directory")
    return path


def _stored_stage(state, stage):
    entry = state.get("planning_artifacts", {}).get(stage)
    if not isinstance(entry, dict) or not isinstance(entry.get("artifact"), dict) or not isinstance(entry.get("delta"), dict):
        raise ValueError(f"Planning predecessor for {stage} is not state-recorded")
    return entry


def predecessor_for(state, stage):
    previous_stage = PREDECESSOR_STAGES.get(stage)
    return (previous_stage, _stored_stage(state, previous_stage)) if previous_stage else (None, None)


def verify_predecessor(state, stage, run_dir):
    """Read only the state-recorded predecessor and its delta, never a filename scan."""
    previous_stage, entry = predecessor_for(state, stage)
    if previous_stage is None:
        return None
    verified = {}
    for kind in ("artifact", "delta"):
        identity = entry[kind]
        if set(identity) != {"path", "sha256"} or not identity["path"] or not identity["sha256"]:
            raise ValueError(f"Planning predecessor {previous_stage} has an invalid {kind} identity")
        path = _safe_path(run_dir, identity["path"])
        if not path.is_file():
            raise ValueError(f"Planning predecessor {previous_stage} {kind} is missing")
        if support.file_hash(path) != identity["sha256"]:
            raise ValueError(f"Planning predecessor {previous_stage} {kind} hash does not match state")
        verified[kind] = {**identity, "absolute_path": str(path)}
    verified["stage"] = previous_stage
    return verified


def _report_from_artifact(value):
    if isinstance(value, dict) and isinstance(value.get("report"), dict):
        return value["report"]
    return {}


def _changed_paths(previous, current, path="$"):
    if previous == current:
        return []
    if isinstance(previous, dict) and isinstance(current, dict):
        changed = []
        for key in sorted(set(previous) | set(current)):
            child = f"{path}.{key}"
            if key not in previous or key not in current:
                changed.append(child)
            else:
                changed.extend(_changed_paths(previous[key], current[key], child))
        return changed
    return [path]


def _derived_graph(report):
    contract = report.get("contract") if isinstance(report, dict) else None
    return graph.derive(contract) if isinstance(contract, dict) else None


def _graph_diffs(previous, current):
    old = _derived_graph(_report_from_artifact(previous)) if previous else None
    new = _derived_graph(_report_from_artifact(current))
    if old is None and new is None:
        return [], []
    old_nodes = {row["id"]: row for row in (old or {"nodes": []})["nodes"]}
    new_nodes = {row["id"]: row for row in (new or {"nodes": []})["nodes"]}
    node_diffs = [{"id": node, "change": "added" if node not in old_nodes else "removed" if node not in new_nodes else "changed"}
                  for node in sorted(set(old_nodes) | set(new_nodes)) if old_nodes.get(node) != new_nodes.get(node)]
    old_edges = {(row["from"], row["to"]) for row in (old or {"edges": []})["edges"]}
    new_edges = {(row["from"], row["to"]) for row in (new or {"edges": []})["edges"]}
    edge_diffs = [{"from": source, "to": target,
                   "change": "added" if (source, target) not in old_edges else "removed"}
                  for source, target in sorted(old_edges ^ new_edges)]
    return node_diffs, edge_diffs


def _input_artifact(state, stage, run_dir):
    try:
        previous_stage, entry = predecessor_for(state, stage)
    except ValueError:
        # Pure transition tests intentionally have no filesystem-backed handoff.
        # Runner paths always provide run_dir and therefore fail closed.
        if run_dir is None:
            return None, None
        raise
    if entry is None:
        return None, None
    if run_dir is None:
        return entry["artifact"], None
    verified = verify_predecessor(state, stage, run_dir)
    return verified["artifact"], json.loads(Path(verified["artifact"]["absolute_path"]).read_text())


def prepare(state, stage, report, *, origin=None, run_dir=None, input_override=None, record=True):
    """Compute stage handoff bytes, optionally recording identities in candidate state."""
    if stage not in STAGE_STEMS:
        return None
    prior_identity, prior_value = _input_artifact(state, stage, run_dir)
    if input_override is not None:
        prior_identity, prior_value = input_override
    ordinal = 1 + sum(1 for item in state.get("planning_artifact_history", []) if item.get("stage") == stage)
    artifact_path = f"planning/{STAGE_STEMS[stage]}-{ordinal}.json"
    artifact = {"stage": stage, "origin": origin or stage, "report": report}
    artifact_bytes = _canonical_bytes(artifact)
    artifact_identity = _identity(artifact_path, artifact_bytes)
    node_diffs, edge_diffs = _graph_diffs(prior_value, artifact)
    delta = {
        "schema_version": 1,
        "stage": stage,
        "input_path": prior_identity["path"] if prior_identity else "",
        "input_sha256": prior_identity["sha256"] if prior_identity else "",
        "changed_paths": _changed_paths(prior_value, artifact) if prior_value is not None else ["$"],
        "answers_consumed": sorted(state.get("answers", {})),
        "feedback_consumed": [row["id"] for row in state.get("brief_feedback", []) if row.get("id")],
        "concerns": report.get("concerns", []),
        "responses": report.get("responses", []),
        "decisions": report.get("decisions", []),
        "graph_node_diffs": node_diffs,
        "graph_edge_diffs": edge_diffs,
    }
    support.validate_schema(delta, goals.DELTA_SCHEMA)
    delta_path = artifact_path.removesuffix(".json") + ".delta.json"
    delta_bytes = _canonical_bytes(delta)
    delta_identity = _identity(delta_path, delta_bytes)
    entry = {"artifact": artifact_identity, "delta": delta_identity}
    prepared = {**entry, "stage": stage, "artifact_value": artifact, "delta_value": delta,
                "artifact_bytes": artifact_bytes, "delta_bytes": delta_bytes}
    if record:
        record_prepared(state, prepared)
    return prepared


def record_prepared(state, prepared):
    """Attach a previously validated handoff to the candidate state."""
    stage = prepared["stage"]
    entry = {"artifact": prepared["artifact"], "delta": prepared["delta"]}
    state.setdefault("planning_artifacts", {})[stage] = entry
    state.setdefault("planning_artifact_history", []).append({"stage": stage, **entry})
    state.setdefault("pending_planning_artifacts", []).append({
        "artifact": prepared["artifact"], "artifact_bytes": prepared["artifact_bytes"],
        "delta": prepared["delta"], "delta_bytes": prepared["delta_bytes"],
    })


def prepare_final_outputs(state, prepared):
    """Queue canonical final-plan aliases and the graph sealed to this revision."""
    contract = state["goal_contract"]
    final_token = goals.token(contract)
    artifact_value = {**prepared["artifact_value"], "final_token": final_token}
    artifact_bytes = _canonical_bytes(artifact_value)
    delta_bytes = prepared["delta_bytes"]
    graph_identity, graph_bytes = graph.identity(contract["body"], final_token)
    outputs = {
        "artifact": _identity("planning/final-plan.json", artifact_bytes),
        "delta": _identity("planning/final-plan.delta.json", delta_bytes),
        "graph": graph_identity,
    }
    archived = state.get("planning_final_archive", [])
    previous = archived[-1].get("outputs", {}) if archived else {}
    state["planning_final"] = {"final_token": final_token, **outputs}
    state.setdefault("pending_planning_outputs", []).extend([
        {"identity": outputs["artifact"], "bytes": artifact_bytes,
         "replaces_sha256": previous.get("artifact", {}).get("sha256")},
        {"identity": outputs["delta"], "bytes": delta_bytes,
         "replaces_sha256": previous.get("delta", {}).get("sha256")},
        {"identity": outputs["graph"], "bytes": graph_bytes,
         "replaces_sha256": previous.get("graph", {}).get("sha256")},
    ])
    return state["planning_final"]


def prepare_user_cli_edit(state, *, run_dir=None):
    """Turn an accepted v2 --edit-goal candidate into the normal plan handoff."""
    contract = state.get("goal_contract")
    if not contract or state.get("settings", {}).get("planning_flow") != "v2":
        return None
    history = state.get("planning_artifact_history", [])
    prior = next((item for item in reversed(history) if item.get("stage") in ("plan_finalize", "plan_revise", "plan")), None)
    if prior is None:
        raise ValueError("v2 --edit-goal requires a state-recorded prior plan artifact")
    artifact_identity = prior["artifact"]
    if run_dir is not None:
        path = _safe_path(run_dir, artifact_identity["path"])
        if not path.is_file() or support.file_hash(path) != artifact_identity["sha256"]:
            raise ValueError("v2 --edit-goal prior plan artifact hash does not match state")
        delta_identity = prior.get("delta", {})
        delta_path = _safe_path(run_dir, delta_identity.get("path", ""))
        if not delta_path.is_file() or support.file_hash(delta_path) != delta_identity.get("sha256"):
            raise ValueError("v2 --edit-goal prior plan delta hash does not match state")
        prior_value = json.loads(path.read_text())
    else:
        prior_value = None
    state.setdefault("planning", {"astra_calls": 0, "reports": {}, "final_token": None})["derived_graph"] = graph.validate(contract["body"])
    return prepare(state, "plan", {"contract": contract["body"], "summary": "User-edited plan"},
                   origin="user_cli_edit", run_dir=None, input_override=(artifact_identity, prior_value))


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".planning-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def flush_pending(state, run_dir):
    """Atomically materialize prepared handoffs before state.json is persisted."""
    pending = state.get("pending_planning_artifacts", [])
    created = []
    try:
        for entry in pending:
            for kind, data_key in (("artifact", "artifact_bytes"), ("delta", "delta_bytes")):
                identity = entry[kind]
                path = _safe_path(run_dir, identity["path"])
                if path.exists():
                    if not path.is_file() or support.file_hash(path) != identity["sha256"]:
                        raise ValueError(f"Refusing to overwrite unrecorded planning artifact {identity['path']}")
                    continue
                atomic_write(path, entry[data_key])
                created.append(identity)
        for entry in state.get("pending_planning_outputs", []):
            identity = entry["identity"]
            path = _safe_path(run_dir, identity["path"])
            if path.exists():
                current_hash = support.file_hash(path) if path.is_file() else ""
                if current_hash == identity["sha256"]:
                    continue
                if not entry.get("replaces_sha256") or current_hash != entry["replaces_sha256"]:
                    raise ValueError(f"Refusing to overwrite unrecognized planning output {identity['path']}")
                previous_bytes = path.read_bytes()
                atomic_write(path, entry["bytes"])
                created.append({**identity, "previous_bytes": previous_bytes})
                continue
            atomic_write(path, entry["bytes"])
            created.append(identity)
    except Exception:
        rollback_created(created, run_dir)
        raise
    state.pop("pending_planning_artifacts", None)
    state.pop("pending_planning_outputs", None)
    return created


def rollback_created(created, run_dir):
    """Remove only planning files created by the current unpublished commit."""
    directories = set()
    for identity in reversed(created):
        path = _safe_path(run_dir, identity["path"])
        if not path.exists():
            continue
        if not path.is_file() or support.file_hash(path) != identity["sha256"]:
            raise ValueError(f"Refusing to remove changed planning artifact {identity['path']}")
        if "previous_bytes" in identity:
            atomic_write(path, identity["previous_bytes"])
        else:
            path.unlink()
        directories.add(path.parent)
    for directory in directories:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def reconcile_orphans(state, run_dir):
    """Archive direct planning files that have no durable state identity."""
    planning_dir = Path(run_dir) / "planning"
    if not planning_dir.is_dir():
        return []
    referenced = set()
    for entry in state.get("planning_artifact_history", []):
        for kind in ("artifact", "delta"):
            identity = entry.get(kind, {})
            if identity.get("path"):
                referenced.add(identity["path"])
    for entry in state.get("planning_artifacts", {}).values():
        for kind in ("artifact", "delta"):
            identity = entry.get(kind, {})
            if identity.get("path"):
                referenced.add(identity["path"])
    for kind in ("artifact", "delta", "graph"):
        identity = state.get("planning_final", {}).get(kind, {})
        if identity.get("path"):
            referenced.add(identity["path"])
    reconciled = []
    archive = planning_dir / "orphans"
    for path in sorted(planning_dir.glob("*.json")):
        relative = str(path.relative_to(run_dir))
        if relative in referenced:
            continue
        archive.mkdir(parents=True, exist_ok=True)
        destination = archive / f"{path.stem}-{support.file_hash(path)[:12]}{path.suffix}"
        os.replace(path, destination)
        reconciled.append({"path": relative, "archive": str(destination.relative_to(run_dir)), "action": "archived"})
    if reconciled:
        state.setdefault("planning_artifact_reconciliation", []).extend(reconciled)
    return reconciled
