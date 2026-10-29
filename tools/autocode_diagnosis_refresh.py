"""Refresh a same-turn diagnosis after an authenticated, exact case-kind correction.

A draft's prose never overrides regression proof. Only a uniquely named case whose
kind changed under a saved user-feedback receipt can request a fresh investigation.
The old report remains effective until ordinary Investigator admission succeeds.
This module owns investigation_refresh (pending pins and completed history).
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as identity
    from . import autocode_source_log as source_log
    from . import autocode_test_cases as cases
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as identity
    import autocode_source_log as source_log
    import autocode_test_cases as cases
    import autocode_util as util

KEY: Final = "investigation_refresh"


def note(value: dict) -> dict:
    """The diagnosis as saved in the repository. ``changed`` is always empty: nothing is fixed yet."""
    return {
        "reproduced": value["outcome"] == "reproduced",
        "observed": value["observed"],
        "reproduction": value["reproduction"],
        "root_cause": value["root_cause"],
        "affected_paths": value["affected_paths"],
        "test_paths": value["test_paths"],
        "invariant": value["invariant"],
        "test_cases": list(value.get("test_cases") or []),
        "conclusion": value["conclusion"],
        "fix_size": value["fix_size"],
        "fix_plan": value["fix_plan"],
        "questions": value["questions"],
        "tests_run": value["tests_run"],
        "changed": [],
        "probe": value.get("probe", ""),
        "untestable": value.get("untestable", ""),
    }


def conflicts(state: RunState) -> list[dict]:
    """Exact named proof-kind conflicts, never English similarity or prose overrides."""
    if (state.get("workflow") or {}).get("kind") != "bugfix":
        return []
    diagnosis = cases.diagnosis_cases(state)
    result = []
    bindings: dict[str, int] = {}
    for criterion in cases.plan_cases((state.get("goal_contract") or {}).get("body")):
        name = criterion.get("test_name")
        if not name:
            continue
        matched = cases.match_cases(diagnosis, [name], framework="go")
        owners = [case for case in diagnosis if matched[case["id"]]]
        for case in owners:
            bindings[case["id"]] = bindings.get(case["id"], 0) + 1
            if (case.get("kind") == "preserve") != (criterion.get("kind") == "preserve"):
                result.append(
                    {
                        "case_id": case["id"],
                        "criterion_id": criterion["id"],
                        "kind": "preserve" if criterion.get("kind") == "preserve" else "restore",
                        "unique": len(owners) == 1,
                    }
                )
    for change in result:
        change["unique"] = change["unique"] and bindings[change["case_id"]] == 1
    return result


def _definition(row):
    # current_behavior is separately runner-derived; source quote representation may
    # expand between wire and saved contracts. Neither changes the corrected proof.
    return {key: row.get(key) for key in ("id", "criterion", "verification_method", "human_review")}


def _record(state: RunState, diagnosis):
    found = [
        row
        for row in state.get("stages", [])
        if row.get("output") == diagnosis.get("output")
        and (row.get("original_stage") or row.get("stage")) == "investigate_bug"
        and not row.get("rejected")
    ]
    if len(found) != 1:
        raise ValueError("Diagnosis refresh needs the accepted same-turn Investigator record")
    return found[0]


def _newer(at, before):
    try:
        return dt.datetime.fromisoformat(at) > dt.datetime.fromisoformat(before)
    except (TypeError, ValueError):
        return False


def _report(diagnosis):
    output = Path(diagnosis.get("output") or "")
    try:
        if (
            output.is_symlink()
            or not diagnosis.get("output_hash")
            or util.file_hash(output) != diagnosis["output_hash"]
        ):
            raise ValueError("Accepted diagnosis report changed or is missing")
        report = util.read_object(output)
    except (OSError, RuntimeError) as error:
        raise ValueError("Accepted diagnosis report changed or is missing") from error
    if any(diagnosis.get(key) != value for key, value in report.items()):
        raise ValueError("Accepted diagnosis no longer matches its original report")
    return report


def proposal(state: RunState) -> dict | None:
    """Authenticate a bounded correction; missing authority leaves the mismatch closed."""
    changes = conflicts(state)
    if not changes:
        return None
    contract = state.get("goal_contract") or {}
    if contract.get("approval_status") != "draft" or not identity.sealed(contract):
        raise ValueError("Diagnosis correction requires an unapproved sealed draft")
    if contract["body"].get("open_blocking_questions"):
        raise ValueError("Resolve the draft's blocking questions before refreshing its diagnosis")
    diagnosis = cases.current_diagnosis(state)
    record = _record(state, diagnosis)
    report = _report(diagnosis)
    feedback = state.get("brief_feedback") or []
    latest = feedback[-1] if feedback else {}
    store = state.get(KEY) or {}
    if any(row.get("feedback_id") == latest.get("id") for row in store.get("attempts", [])) and not (
        store.get("pending") or {}
    ).get("feedback", {}).get("id") == latest.get("id"):
        raise ValueError("This corrective feedback already requested its diagnosis refresh")
    source_id = "feedback:" + str(latest.get("id", ""))
    source = next((row for row in source_log.sources(state) or [] if row["id"] == source_id), None)
    if (
        not source
        or latest not in state.get("user_events", [])
        or latest.get("actor") != "user_cli"
        or not _newer(latest.get("at"), record.get("finished_at"))
    ):
        raise ValueError("Diagnosis conflict lacks current saved user feedback newer than the accepted investigation")
    rows = {row["id"]: row for row in contract["body"]["acceptance_criteria"]}
    history = [*state.get("contract_history", []), contract]
    bound = []
    seen = set()
    for change in changes:
        cid = change["criterion_id"]
        if not change["unique"] or change["case_id"] in seen:
            raise ValueError("Diagnosis correction needs one unambiguous named criterion per case")
        seen.add(change["case_id"])
        row = rows[cid]
        if not any(cite.get("source_id") == source_id for cite in row.get("sources", [])):
            raise ValueError("Corrected criterion must cite the same saved user feedback")
        admitted = None
        for revision in reversed(history):
            if not identity.sealed(revision):
                continue
            old = next(
                (entry for entry in revision["body"].get("acceptance_criteria", []) if entry.get("id") == cid), None
            )
            if old is None or _definition(old) != _definition(row):
                continue
            receipt = next(
                (
                    entry
                    for entry in revision.get("declared_changes", [])
                    if entry.get("item") == cid
                    and entry.get("basis") == "user_feedback"
                    and entry.get("answer_id") == latest.get("id")
                    and entry.get("change") == "reworded"
                ),
                None,
            )
            if receipt:
                admitted = {"contract_token": identity.token(revision), "change": copy.deepcopy(receipt)}
                break
        if admitted is None:
            raise ValueError("Diagnosis conflict lacks an admitted user-feedback correction for " + cid)
        bound.append({**change, "criterion": copy.deepcopy(row), "admitted": admitted})
    return {
        "diagnosis": copy.deepcopy(diagnosis),
        "original_report": report,
        "contract_token": identity.token(contract),
        "contract": copy.deepcopy(contract),
        "requirements_handoff": copy.deepcopy(state.get("requirements_handoff")),
        "feedback": copy.deepcopy(latest),
        "source": copy.deepcopy(source),
        "cases": bound,
    }


def _note_hash(workspace, diagnosis):
    """Exempt only an unchanged runner note, never arbitrary bytes at its path."""
    root = Path(workspace).resolve()
    relative = Path(diagnosis["note_path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Accepted diagnosis note has an unsafe path")
    target = root / relative
    try:
        if any(path.is_symlink() for path in (target, *target.parents) if path != root and root in path.parents):
            raise ValueError("Accepted diagnosis note must not follow a symlink")
        if target.stat().st_mode & 0o111:
            raise ValueError("Accepted diagnosis note must not be executable")
        probe = diagnosis.get("probe", "").strip()
        if probe and not diagnosis.get("probe_result"):
            raise ValueError("Accepted diagnosis is missing its reproduction receipt")
        raw = target.read_bytes()
        if json.loads(raw) != {**note(diagnosis), "proven_by": probe}:
            raise ValueError("Accepted diagnosis note changed")
        return hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Accepted diagnosis note changed or is missing") from error


def _source(state: RunState, workspace, diagnosis):
    """Exclude only the runner-owned diagnosis note, whose post-admission write changes source."""
    record = _record(state, diagnosis)
    try:
        previous = util.read(record["after_ref"])
        current = util.snapshot(Path(workspace))
    except (KeyError, OSError, ValueError) as error:
        raise ValueError("Diagnosis refresh needs its retained source snapshot") from error

    def definition(snapshot):
        return {
            "head": snapshot["head"],
            "files": {key: value for key, value in snapshot["files"].items() if key != diagnosis["note_path"]},
        }

    if definition(current) != definition(previous):
        raise ValueError("Source changed since the accepted diagnosis; refresh cannot authorize that change")
    return {**definition(current), "diagnosis_note_hash": _note_hash(workspace, diagnosis)}


def prepare(state: RunState, workspace) -> dict | None:
    proposed = proposal(state)
    if proposed is not None:
        proposed["source_snapshot"] = _source(state, workspace, proposed["diagnosis"])
        proposed["id"] = util.digest(proposed)
    return proposed


def queue(state: RunState, proposed):
    store = state.setdefault(KEY, {"history": []})
    if store.get("pending"):
        if store["pending"] != proposed:
            raise ValueError("Pending diagnosis refresh no longer matches its original correction")
        return
    store.setdefault("attempts", []).append({"feedback_id": proposed["feedback"]["id"], "refresh_id": proposed["id"]})
    store["pending"] = copy.deepcopy(proposed)


def context(state: RunState, workspace=None) -> dict | None:
    pending = (state.get(KEY) or {}).get("pending")
    if not pending:
        return None
    current = prepare(state, workspace or state.get("workspace"))
    if current != pending:
        raise ValueError("Pending diagnosis refresh has stale report, feedback, contract or source pins")
    return copy.deepcopy(pending)


def prompt_context(state: RunState) -> dict:
    """Project checked correction evidence; keep manifests and internal pins off the wire."""
    pending = context(state)
    if not pending:
        return {}
    handoff = pending.get("requirements_handoff") or {}
    return {
        "prior_investigation": copy.deepcopy(pending["original_report"]),
        "diagnosis_correction": {
            "feedback": copy.deepcopy(pending["feedback"]),
            "source": copy.deepcopy(pending["source"]),
            "contract_token": pending["contract_token"],
            "corrected_contract": copy.deepcopy(pending["contract"]["body"]),
            "historical_requirements": copy.deepcopy(handoff.get("report")),
            "cases": [{key: row[key] for key in ("case_id", "criterion_id", "kind")} for row in pending["cases"]],
        },
    }


def check(state: RunState, value, workspace):
    pending = context(state, workspace)
    if not pending:
        return
    before = pending["original_report"]
    if value.get("outcome") != "reproduced":
        raise ValueError("Diagnosis refresh must retain the still-required reproduced bug and its proof cases")
    for key in ("note_path", "affected_paths", "test_paths"):
        if value.get(key) != before.get(key):
            raise ValueError("Diagnosis refresh cannot change " + key)
    previous, proposed = before["test_cases"], value.get("test_cases") or []
    if [row.get("id") for row in proposed] != [row.get("id") for row in previous]:
        raise ValueError("Diagnosis refresh must retain every original case in order")
    corrected = {row["case_id"]: row for row in pending["cases"]}
    for old, new in zip(previous, proposed, strict=True):
        correction = corrected.get(old["id"])
        if correction is None:
            if new != old:
                raise ValueError("Diagnosis refresh changed unaffected case " + old["id"])
        elif (
            any(new.get(key) != old.get(key) for key in ("id", "given", "when"))
            or new.get("kind", "restore") != correction["kind"]
        ):
            raise ValueError("Diagnosis refresh must retain the corrected case identity and adopt its exact proof kind")
    if before.get("probe", "").strip() and not value.get("probe", "").strip():
        raise ValueError("Diagnosis refresh cannot replace executable reproduction proof with an untestable claim")


def accepted(state: RunState, diagnosis):
    store = state.get(KEY) or {}
    pending = store.get("pending")
    if pending:
        store.setdefault("history", []).append({**copy.deepcopy(pending), "accepted": copy.deepcopy(diagnosis)})
        store.pop("pending")
