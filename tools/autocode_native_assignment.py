"""Exact native handoff facts from the existing task assignment and JSON sink.

This module starts no process, reads no source/state file and imports no
controller or factory. The original caller supplies authenticated decision
facts and invokes the ordinary lifecycle operation. Only its actual generated
task can be captured; the original owner publishes after its durable sink ACK.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_route_fit as route_fit
    from . import autocode_util as util
except ImportError:
    import autocode_route_fit as route_fit
    import autocode_util as util


def require(value, reason):
    if not value:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native assignment: " + reason)


@dataclass(frozen=True)
class PreparedAssignment:
    owner: object
    nonce: str
    row_id: str
    body: bytes


def writer_paths(body):
    """Explicit immutable approved writer leaves, separate from readonly input scope."""
    marker = "VISUAL_WRITER_PATHS="
    values = [
        value[len(marker) :]
        for value in body.get("constraints", [])
        if isinstance(value, str) and value.startswith(marker)
    ]
    if values:
        require(len(values) == 1, "multiple approved writer inventories")
        paths = json.loads(values[0])
        require(
            values[0] == json.dumps(paths, ensure_ascii=True, separators=(",", ":")),
            "writer inventory must use canonical JSON",
        )
    else:
        # Existing first-implement contracts already approve their exact writer.
        first = body["initial_task"]
        require(first["kind"] == "implement", "initial validation needs explicit approved writer leaves")
        paths = first["affected_paths"]
    require(
        isinstance(paths, list)
        and paths
        and len(paths) == len(set(paths))
        and all(
            isinstance(name, str)
            and name
            and not PurePosixPath(name).is_absolute()
            and ".." not in PurePosixPath(name).parts
            and "." not in PurePosixPath(name).parts
            and str(PurePosixPath(name)) == name
            and not any(char in name for char in "*?[]\\\x00")
            and not set(PurePosixPath(name).parts) & {".git", ".autocode", ".autocode-ui"}
            for name in paths
        )
        and all(name in body["milestones"][0]["affected_paths"] for name in paths),
        "writer leaves must stay inside the approved owner input inventory",
    )
    return deepcopy(paths)


def normalized(state: RunState, decision):
    """Match ordinary lifecycle normalization, without granting broader scope."""
    value = deepcopy(decision)
    old, body = cast(dict, state["current_task"]), cast(dict, state["goal_contract"])["body"]
    rows = body["milestones"]
    require(
        len(rows) == 1 and rows[0]["id"] == old["milestone_id"],
        "only the existing single owner final-validation/repair handoff is enabled",
    )
    spec = value["next_task"]
    require(
        value.get("status") in ("CONTINUE", "REWORK")
        and spec.get("milestone_id") == old["milestone_id"]
        and spec.get("kind") in ("implement", "validate")
        and not old.get("milestone_ids"),
        "foreign owner, batch or missing concrete task",
    )
    ids = rows[0]["acceptance_criteria"]
    require(
        spec["acceptance_criteria"] == ids and set(ids) == {row["id"] for row in body["acceptance_criteria"]},
        "the same owner must retain every approved criterion",
    )
    inputs = rows[0]["affected_paths"]
    require(
        old["affected_paths"] and all(path in inputs for path in old["affected_paths"]),
        "current task escaped original owner input scope",
    )
    require(
        (spec["kind"], value["status"]) in (("validate", "CONTINUE"), ("implement", "REWORK")),
        "CONTINUE cannot grant a repair writer or REWORK masquerade as final validation",
    )
    require(
        value.get("user_request", {}).get("kind") == "none" and not body.get("open_blocking_questions"),
        "human decisions cannot be delegated by native evidence",
    )
    if value["status"] == "REWORK":
        paths = value.get("affected_paths")
        grants = writer_paths(body)
        require(
            isinstance(paths, list)
            and paths
            and len(paths) == len(set(paths))
            and all(path in grants for path in paths),
            "repair cannot broaden the explicit approved writer leaves",
        )
    else:
        paths = inputs
        require(
            all(path in inputs for path in value.get("affected_paths", [])),
            "validation cannot escape owner input scope",
        )
    value["affected_paths"] = deepcopy(paths)
    return value


def capture(before, after, decision, snapshot):
    """Capture the actual assign_task result, never generate a task ID here."""
    old, new = before["current_task"], after["current_task"]
    spec = decision["next_task"]
    require(
        before["goal_contract"] == after["goal_contract"] and before["settings"] == after["settings"],
        "ordinary handoff changed contract, routes, limits or reference inventory",
    )
    findings = (
        []
        if spec["kind"] == "validate"
        else list(dict.fromkeys(spec.get("findings") or []))
        or sorted(row["id"] for row in before.get("findings_ledger", []) if row.get("status") == "open")
    )
    expected = {
        **deepcopy(spec),
        "findings": findings,
        "objective": decision["next_objective"],
        "affected_paths": deepcopy(decision["affected_paths"]),
        "contract_hash": before["goal_contract"]["hash"],
        "contract_revision": before["goal_contract"]["revision"],
        "source_revision": snapshot["revision"],
        "decision": decision["status"],
    }
    require(
        isinstance(new.get("id"), str)
        and new["id"].startswith("task-")
        and new["id"] != old["id"]
        and isinstance(new.get("assigned_at"), str)
        and bool(new["assigned_at"])
        and {key: value for key, value in new.items() if key not in ("id", "assigned_at")} == expected
        and after.get("task_archive", []) == [*before.get("task_archive", []), old]
        and after.get("affected_paths") == decision["affected_paths"],
        "actual normalized task, ID, archive or source differs from the exact accepted decision",
    )
    return deepcopy(new)


def check_candidate(body, candidate, snapshot):
    bound = body["binding"]
    require(
        candidate["goal_contract"] == bound["contract"]
        and route_fit.same(candidate["settings"], bound["settings"])
        and candidate["current_task"] == body["new_task"]
        and snapshot == bound["source"]
        and candidate.get("task_archive", []) == body["task_archive"]
        and candidate.get("affected_paths") == body["decision"]["affected_paths"]
        and sum(
            row.get("output") == body["record"]["output"] and row.get("stage") == body["record"]["stage"]
            for row in candidate.get("stages", [])
        )
        == 1,
        "final normalized parent candidate changed its actual assignment or decision stage",
    )


def committed_chain(rows, binding, *, historical=False):
    """Replay the whole chain before selecting even an old writer's identity.

    The native owner authenticates the retained decision pins and actual sink
    identity separately. A prepared assignment without its original ACK and
    commit remains uncertainty, including when an older writer is replayed.
    """
    original = [
        row
        for row in rows
        if row["kind"] == "approval-committed"
        and row["body"]["contract"] == binding["contract"]
        and route_fit.same(row["body"]["settings"], binding["settings"])
        and row["body"]["runtime_sha256"] == binding["runtime_sha256"]
    ]
    require(len(original) == 1, "missing exact original committed operator approval")
    origin = original[0]
    current, source = origin["body"]["current_task"], origin["body"]["snapshot"]
    selected, selected_source, selected_chain = origin, source, ()
    matches = [(origin, source, ())] if current == binding["task"] else []
    by_id, used, chain, pending = {row["id"]: row for row in rows}, set(), [], set()
    for row in rows[origin["sequence"] + 1 :]:
        kind, body = row["kind"], row["body"]
        if kind == "finished":
            source = body["after"]  # The original owner validates each launch delta.
        elif kind == "assignment-prepared":
            require(
                not pending
                and body["binding"]["contract"] == binding["contract"]
                and route_fit.same(body["binding"]["settings"], binding["settings"])
                and body["binding"]["runtime_sha256"] == binding["runtime_sha256"]
                and body["binding"]["task"] == current
                and body["binding"]["source"] == source,
                "assignment preparation branches, overlaps or changes its actual source",
            )
            pending.add(row["id"])
        elif kind == "assignment-committed":
            require(
                body["binding"]["contract"] == binding["contract"]
                and route_fit.same(body["binding"]["settings"], binding["settings"])
                and body["binding"]["runtime_sha256"] == binding["runtime_sha256"]
                and body["binding"]["task"] == current
                and body["binding"]["source"] == source,
                "retained assignment branches or changes immutable inputs/source",
            )
            prepared, ack = by_id.get(body["prepared_id"]), by_id.get(body["parent_commit_id"])
            require(
                prepared is not None
                and prepared["kind"] == "assignment-prepared"
                and ack is not None
                and ack["kind"] == "parent-commit-acknowledged"
                and prepared["sequence"] < ack["sequence"] < row["sequence"]
                and pending == {prepared["id"]}
                and prepared["id"] not in used
                and body == {**prepared["body"], "prepared_id": prepared["id"], "parent_commit_id": ack["id"]},
                "assignment lacks the original preparation and durable ACK chronology",
            )
            intent = by_id.get(ack["body"]["intent_id"])
            selector = {"kind": "assignments", "id": prepared["id"]}
            require(
                intent is not None
                and intent["kind"] == "parent-commit-prepared"
                and prepared["sequence"] < intent["sequence"] < ack["sequence"]
                and ack["body"]
                == {**intent["body"], "intent_id": intent["id"], "sink_identity": ack["body"]["sink_identity"]}
                and ack["body"]["publications"] == [selector]
                and ack["body"]["task"] == body["new_task"]
                and ack["body"]["source"] == source
                and ack["body"]["contract"] == binding["contract"]
                # The ACK hashed the approval's own settings, which may already have carried fit
                # evidence a later launch then extended (#2057); both readings are the same routes.
                and ack["body"]["settings_sha256"]
                in (*route_fit.digests(binding["settings"]), util.digest(origin["body"]["settings"]))
                and ack["body"]["runtime_sha256"] == binding["runtime_sha256"]
                and not any(
                    item["kind"] == "parent-commit-invalidated" and item["body"]["parent_commit_id"] == ack["id"]
                    for item in rows[ack["sequence"] + 1 : row["sequence"]]
                ),
                "different or invalidated sink ACK cannot commit an assignment",
            )
            used.add(prepared["id"])
            pending.clear()
            current = body["new_task"]
            chain.append(row)
            selected, selected_source, selected_chain = row, source, tuple(chain)
            if current == binding["task"]:
                matches.append((selected, selected_source, selected_chain))
    require(not pending, "unfinished assignment preparation/ACK has no task authority")
    if historical:
        require(len(matches) == 1, "missing or repeated historical causal task identity")
        selected, selected_source, selected_chain = matches[0]
    else:
        require(current == binding["task"], "task differs from the latest actual causal assignment")
    return origin, selected, selected_source, selected_chain


def readonly_lineage(rows, binding):
    """Select the real producer through durable same-owner validation handoffs.

    Pure chronology is not review authority. The original owner separately
    authenticates every retained decision and sink ACK before using this fact.
    Readonly CONTINUE tasks neither invent a writer nor replace its identity.
    """
    origin, _selected, _source, chain = committed_chain(rows, binding)
    task, goal = binding["task"], binding["contract"]["body"]
    require(
        task.get("kind") in ("implement", "validate") and not task.get("milestone_ids"),
        "readonly lineage needs one concrete implementation or validation task",
    )
    milestones = goal["milestones"]
    require(
        len(milestones) == 1 and task["milestone_id"] == milestones[0]["id"],
        "readonly lineage changed the original single owner",
    )
    owner = milestones[0]
    producer = task
    for handoff in reversed(chain):
        if producer["kind"] != "validate":
            break
        body = handoff["body"]
        require(
            body["new_task"] == producer
            and body["decision"]["status"] == "CONTINUE"
            and body["binding"]["source"] == binding["source"]
            and producer["affected_paths"] == owner["affected_paths"]
            and producer["acceptance_criteria"] == owner["acceptance_criteria"]
            and producer["milestone_id"] == body["binding"]["task"]["milestone_id"]
            and not body["binding"]["task"].get("milestone_ids"),
            "readonly handoff changed source, full criteria, input scope or causal owner",
        )
        producer = body["binding"]["task"]
    if producer["kind"] == "implement":
        return {"kind": "native_builder", "task": deepcopy(producer), "approval": origin}
    require(
        producer == origin["body"]["current_task"]
        and goal["initial_task"]["kind"] == producer["kind"] == task["kind"] == "validate"
        and origin["body"]["snapshot"] == binding["source"]
        and producer["milestone_id"] == owner["id"]
        and producer["affected_paths"] == owner["affected_paths"]
        and producer["acceptance_criteria"] == owner["acceptance_criteria"],
        "operator readonly lineage lacks its unchanged original full-scope approval",
    )
    return {"kind": "operator_supplied_source", "task": deepcopy(producer), "approval": origin}
