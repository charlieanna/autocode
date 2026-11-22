"""Display bounded historical suite reports without granting them proof authority."""

from __future__ import annotations

import hashlib
import json
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_named_collection as named
    from . import autocode_requirements_observations as observations
    from . import autocode_requirements_review as review
except ImportError:
    import autocode_named_collection as named
    import autocode_requirements_observations as observations
    import autocode_requirements_review as review

MAX_REPORTS = 8
MAX_PATHS = 40
MAX_CLAIMS = 8
MAX_CLAIM_NODES = 40
# A suite-coverage claim names the examples it says the project command exercises (#3939). Only the
# recorded collection answer decides whether those examples are collected; a runner flag alone does not.
CLAIM_KINDS = ("all_suite_pass", "named_tests_pass")
UNBOUND_REASON = (
    "The named examples are not established by the recorded collection; a runner flag alone does not "
    "prove the project command exercises them."
)


def claims(state: RunState, run_dir, current_source):
    """Bind current-turn suite-coverage claims to the recorded collection (#3939), display only.

    Each claim is a model-proposed ``suite_claim`` on a proposed assumption naming the examples it
    says the suite exercises. The binding reads the same sealed collection answer the suite reports
    read (``structured_collection_claim``); a claim whose named examples are not collected stays
    unverified and unbound, never presented as established coverage. Collection alone never proves
    execution, so even a bound claim remains unverified.
    """
    if (state.get("settings") or {}).get("replay_recording"):
        return []
    handoff = review.current_turn_handoff(state)
    report = handoff.get("report") if isinstance(handoff, dict) else None
    if not isinstance(report, dict):
        return []
    rows = []
    for raw in report.get("proposed_assumptions") or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("suite_claim"), dict):
            continue
        assumption_id, text = raw.get("id"), raw.get("text")
        if not isinstance(assumption_id, str) or not assumption_id or not isinstance(text, str) or not text.strip():
            continue
        proposed = raw["suite_claim"]
        claim = {
            "assumption_id": assumption_id,
            "claim_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "kind": proposed.get("kind"),
            "node_ids": proposed.get("node_ids"),
            "collection_command": proposed.get("collection_command"),
        }
        bound = observations.structured_collection_claim(state, run_dir, current_source, claim)
        rows.append(_claim_row(assumption_id, claim, bound))
        if len(rows) == MAX_CLAIMS:
            break
    return rows


def _claim_row(assumption_id, claim, bound):
    wanted = claim.get("node_ids")
    wanted = [node for node in wanted if isinstance(node, str) and len(node) <= 256] if isinstance(wanted, list) else []
    kind = claim.get("kind") if claim.get("kind") in CLAIM_KINDS else None
    if bound:
        return {
            "assumption_id": assumption_id,
            "status": "unverified",
            "bound": True,
            "kind": bound["claim_kind"],
            "claimed_node_ids": bound["claimed_node_ids"][:MAX_CLAIM_NODES],
            "omitted_claimed": max(0, len(bound["claimed_node_ids"]) - MAX_CLAIM_NODES),
            "collected_count": len(bound["node_ids"]),
            "reason": bound["reason"],
        }
    return {
        "assumption_id": assumption_id,
        "status": "unverified",
        "bound": False,
        "kind": kind,
        "claimed_node_ids": wanted[:MAX_CLAIM_NODES],
        "omitted_claimed": max(0, len(wanted) - MAX_CLAIM_NODES),
        "collected_count": 0,
        "reason": UNBOUND_REASON,
    }


def project(state: RunState, run_dir, current_source):
    """Describe retained complete outputs; neither their text nor this view is execution proof."""
    if (state.get("settings") or {}).get("replay_recording"):
        return []
    if state.get("status") == "AWAITING_GOAL_APPROVAL":
        packets = observations.plan_packets(state, run_dir, cache(current_source))
    else:
        handoff = review.current_turn_handoff(state)
        packets = (observations._handoff_packet(state, "claim_evidence", handoff, run_dir, current_source),)
    rows = []
    for packet in packets:
        if packet:
            for row in _reports(packet, run_dir):
                if row not in rows:
                    rows.append(row)
                if len(rows) == MAX_REPORTS:
                    return rows
    return rows


def _reports(packet, run_dir):
    try:
        raw = observations._read(Path(run_dir), packet["artifact"]["path"])
        if hashlib.sha256(raw).hexdigest() != packet["artifact"]["sha256"]:
            return []
        payload = json.loads(raw)
        retained = payload.get("retained_outputs", [])
        if not isinstance(retained, list):
            return []
        rows = []
        for item in retained:
            if not isinstance(item, dict):
                continue
            command, output, digest = item.get("command"), item.get("output"), item.get("sha256")
            if (
                not isinstance(command, str)
                or not observations._direct(command)
                or len(command) > 512
                or not isinstance(output, str)
                or hashlib.sha256(output.encode()).hexdigest() != digest
            ):
                continue
            parsed = named.pytest_collection(command, output) or named.pytest_execution(command, output)
            selected = False
            if parsed is None and (parsed := named.pytest_selected_execution(command, output)):
                selected = True
            if not parsed:
                continue
            paths = sorted({node.split("::", 1)[0] for node in parsed["node_ids"]})
            if any(len(path) > 256 for path in paths):
                continue
            rows.append(
                {
                    "kind": "collection" if parsed["kind"] == "reported_collection" else "execution",
                    "command": command,
                    "reported_count": len(parsed["node_ids"]),
                    "paths": paths[:MAX_PATHS],
                    "omitted_paths": max(0, len(paths) - MAX_PATHS),
                    "independently_verified": False,
                    **({"selected": True} if selected else {}),
                }
            )
            if len(rows) == MAX_REPORTS:
                break
        return rows
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
