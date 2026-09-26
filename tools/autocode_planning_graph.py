"""Deterministic, data-only milestone dependency graph validation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    from . import autocode_support as support
except ImportError:
    import autocode_support as support


GRAPH_PATH = "planning/graph.json"


def _boundaries(milestone):
    values = milestone.get("boundaries", [])
    return set(values)


def _reachable(edges, start, target):
    todo, seen = [start], set()
    while todo:
        node = todo.pop()
        if node == target:
            return True
        if node not in seen:
            seen.add(node)
            todo.extend(edges[node])
    return False


def derive(body):
    """Build the only graph accepted by the runner from contract milestones."""
    milestones = body.get("milestones", [])
    ids = [row.get("id", "") for row in milestones]
    if not ids or any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("Milestones need unique nonempty IDs")
    if any("depends_on" not in row for row in milestones):
        raise ValueError("Every milestone must declare depends_on (use [] for independent work)")
    edges = {row["id"]: list(row["depends_on"]) for row in milestones}
    for node, dependencies in edges.items():
        if len(dependencies) != len(set(dependencies)) or any(dep not in edges for dep in dependencies):
            raise ValueError(f"Milestone {node} depends on an unknown or duplicate milestone")
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            raise ValueError("Milestone dependencies contain a cycle")
        if node not in visited:
            visiting.add(node)
            for dependency in edges[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)
    for node in edges:
        visit(node)
    criteria = {row["id"] for row in body.get("acceptance_criteria", [])}
    covered = {criterion for row in milestones for criterion in row.get("acceptance_criteria", [])}
    if covered != criteria:
        raise ValueError("Implementation milestones must cover every acceptance criterion")
    initial = body.get("initial_task", {})
    if initial.get("kind") in ("implement", "validate") and edges.get(initial.get("milestone_id")):
        raise ValueError("initial_task milestone has unmet prerequisites")
    nodes = [{"id": row["id"], "boundaries": sorted(_boundaries(row))} for row in milestones]
    pairs, shared = [], []
    for index, left in enumerate(milestones):
        for right in milestones[index + 1:]:
            a, b = left["id"], right["id"]
            if _reachable(edges, a, b) or _reachable(edges, b, a):
                continue
            overlap = sorted(_boundaries(left) & _boundaries(right))
            pair = {"milestones": [a, b]}
            if overlap:
                shared.append({**pair, "boundaries": overlap})
            else:
                pairs.append(pair)
    return {"nodes": nodes, "edges": [{"from": node, "to": dependency}
            for node in sorted(edges) for dependency in sorted(edges[node])],
            "parallel_eligible": pairs, "shared_boundaries": shared,
            "validation": {"acyclic": True, "criteria_covered": True},
            "automatic_execution": False}


def validate(body, supplied=None):
    graph = derive(body)
    if supplied is not None and supplied != graph:
        raise ValueError("Model-emitted graph disagrees with contract milestones")
    return graph


def serialize(body, supplied=None):
    return json.dumps(validate(body, supplied), sort_keys=True, separators=(",", ":"))


def sealed_payload(body, final_token):
    return {"schema_version": 1, "final_token": final_token, **derive(body)}


def sealed_bytes(body, final_token):
    return (json.dumps(sealed_payload(body, final_token), indent=2, sort_keys=True) + "\n").encode()


def identity(body, final_token):
    data = sealed_bytes(body, final_token)
    return {"path": GRAPH_PATH, "sha256": hashlib.sha256(data).hexdigest(),
            "final_token": final_token}, data


def consume(state, run_dir):
    """Inspect a finalized graph without authorizing or launching any work."""
    contract = state.get("goal_contract")
    final = state.get("planning_final")
    if not contract or not final:
        return {"status": "stale", "reason": "No current finalized planning graph"}
    current_token = f"r{contract['revision']}:{contract['hash']}"
    graph_identity = final.get("graph", {})
    if final.get("final_token") != current_token or graph_identity.get("final_token") != current_token:
        return {"status": "stale", "reason": "Graph seal does not match the current contract token"}
    if graph_identity.get("path") != GRAPH_PATH:
        return {"status": "tampered", "reason": "Graph identity does not use the canonical path"}
    path = Path(run_dir) / GRAPH_PATH
    if not path.is_file() or support.file_hash(path) != graph_identity.get("sha256"):
        return {"status": "tampered", "reason": "Graph bytes do not match the finalized identity"}
    try:
        payload = json.loads(path.read_text())
        expected = sealed_payload(contract["body"], current_token)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {"status": "tampered", "reason": f"Graph cannot be validated: {error}"}
    if payload != expected:
        return {"status": "tampered", "reason": "Graph disagrees with the current contract"}
    try:
        from . import autocode_goals as goals
    except ImportError:
        import autocode_goals as goals
    if not goals.approved(state):
        return {"status": "not-approved", "final_token": current_token, "graph": payload}
    return {"status": "ready", "final_token": current_token, "graph": payload}
