"""Runner-owned visual receipts, not pixel comparison or model self-attestation.

Only accept() writes state.visual_acceptance_receipts, at the accepted Validator
boundary. Completion and accounting consume summary(); neither can mint proof.
There is deliberately NO built-in delivery adapter: OpenCode read attachments
precede image normalization/message transforms and do not prove delivery.
Callbacks are runner dependencies, never fields decoded from a model report.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path

try:
    from . import autocode_design_manifest as design
    from . import autocode_util as util
except ImportError:
    import autocode_design_manifest as design
    import autocode_util as util


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _hash(value):
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None, "Stage finish must have a timezone")
    return parsed.timestamp()


def _file(root, name):
    root = Path(root).resolve()
    path = Path(name)
    path = path if path.is_absolute() else root / path
    _require(path.is_relative_to(root), "Visual evidence must belong to its owner")
    _require(
        not any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)),
        "Visual evidence must not follow symlinks",
    )
    _require(path.resolve().is_relative_to(root) and path.is_file(), "Missing owned visual evidence")
    return path.resolve()


def binding(current, record):
    """Canonical prelaunch identity to pin in the stage's image audit manifest."""
    keys = (
        "task_id",
        "contract_revision",
        "contract_hash",
        "source_revision",
        "runtime_hash",
        "criteria",
        "case_criteria",
        "reviewer",
        "manifest_body_hash",
        "manifest_file_sha256",
    )
    result = {key: deepcopy(current[key]) for key in keys}
    _require(isinstance(result["task_id"], str) and result["task_id"], "Missing current task")
    _require(
        type(result["contract_revision"]) is int and result["contract_revision"] >= 1,
        "Missing approved contract revision",
    )
    for key in ("contract_hash", "source_revision", "runtime_hash", "manifest_body_hash", "manifest_file_sha256"):
        _require(_hash(result[key]), "Missing current identity: " + key)
    criteria = result["criteria"]
    ids = [item["id"] for item in criteria]
    _require(
        ids and len(ids) == len(set(ids)) and all(isinstance(cid, str) and cid for cid in ids),
        "Criteria need complete unique identities",
    )
    _require(
        all(
            all(isinstance(item.get(key), str) and item[key].strip() for key in ("criterion", "verification_method"))
            for item in criteria
        ),
        "Criteria need their approved behavior and verification method",
    )
    design.validate(record["body"])
    cases = record["body"]["cases"]
    _require(set(result["case_criteria"]) == {case["id"] for case in cases}, "Incomplete approved case mapping")
    for mapped in result["case_criteria"].values():
        _require(
            isinstance(mapped, list) and mapped and len(mapped) == len(set(mapped)) and set(mapped) <= set(ids),
            "Invalid approved visual criterion mapping",
        )
    _require(
        record["manifest_hash"] == result["manifest_body_hash"] == util.digest(record["body"]),
        "Approved manifest parsed-body identity changed",
    )
    _require(
        set(result["reviewer"]) == {"provider", "model"} and all(result["reviewer"].values()),
        "Missing independent reviewer route",
    )
    result["cases"] = deepcopy(cases)
    return result


def _terminal(raw, report, session):
    """Strict native envelope, not prose claiming that an image was inspected."""
    _require(raw.endswith(b"\n"), "Truncated native events")
    rows = [json.loads(line) for line in raw.splitlines()]
    _require(rows and all(isinstance(row, dict) for row in rows), "Invalid native events")
    _require(
        all(row.get("sessionID") == session and row.get("type") != "error" for row in rows),
        "Failed or foreign reviewer events",
    )
    parts = {}
    for row in rows:
        part = row.get("part", {})
        _require(part.get("sessionID", session) == session, "Foreign native part")
        if row.get("type") in ("step_start", "step_finish", "text"):
            _require(part.get("id") and part.get("messageID"), "Missing native part identity")
            previous = parts.get(part["id"])
            _require(previous is None or previous == row, "Conflicting native part updates")
            parts[part["id"]] = row
    phases = [row for row in parts.values() if row["type"] in ("step_start", "step_finish")]
    _require(phases and phases[-1]["type"] == "step_finish", "Unfinished reviewer request")
    finish = phases[-1]["part"]
    _require(finish.get("reason") == "stop", "Reviewer failed or exhausted its output limit")
    starts = [
        row["part"] for row in phases if row["type"] == "step_start" and row["part"]["messageID"] == finish["messageID"]
    ]
    _require(len(starts) == 1, "Missing or ambiguous reviewer request start")
    text = "\n".join(
        row["part"].get("text", "")
        for row in parts.values()
        if row["type"] == "text" and row["part"]["messageID"] == finish["messageID"]
    )
    _require(json.loads(text) == report, "Original report differs from the finished reviewer message")
    return {"session_id": session, "message_id": finish["messageID"], "finish_id": finish["id"]}


def accept(state, record, *, run_dir, current, manifest_path, verify_stage, verify_capture, verify_delivery=None):
    """Persist once, or return None when no authenticated delivery adapter exists.

    current is a runner snapshot with task/contract/source/runtime identities,
    full criteria, case_criteria and reviewer {provider, model}. Its manifest
    hashes distinguish util.digest(parsed body) from SHA256(file bytes).

    verify_stage(record, current) must check the accepted read-only Validator,
    dispatcher/model independence and full source gate, returning its provenance.
    verify_capture(ref, sha, case=case, current=current) wraps visual_evidence.verify.
    verify_delivery(record, events=raw, report=raw, images=expected, binding=binding)
    must authenticate a final transformed request and successful response against
    protected transport records. Its normalized contract is checked below. Tool
    output, preflight, transform-hook observations and model fields are NOT that
    callback. Without a qualified native adapter this API cannot accept images.
    """
    if verify_delivery is None:
        return None
    root = Path(state["workspace"]).resolve()
    run = Path(run_dir).resolve()
    _require(run.is_relative_to(root / ".autocode" / "runs"), "Missing actual run ownership")
    manifest = state["settings"]["design_manifest"]
    bound = binding(current, manifest)
    _require(
        record.get("engine") == "opencode"
        and type(record.get("exit_code")) is int
        and record["exit_code"] == 0
        and not any(
            record.get(key) for key in ("timed_out", "interrupted", "dry_run", "report_only", "truncated_output")
        ),
        "Visual acceptance requires the successful original native stage",
    )
    for key in ("task_id", "contract_revision", "contract_hash", "source_revision"):
        _require(record.get(key) == bound[key], "Stage has a different " + key)
    design.verify(manifest)
    manifest_file = _file(root, manifest_path)
    manifest_raw = manifest_file.read_bytes()
    _require(
        json.loads(manifest_raw) == manifest["body"] and _sha(manifest_raw) == bound["manifest_file_sha256"],
        "Approved manifest file bytes changed",
    )
    stage = verify_stage(record, current)
    for key in ("accepted", "read_only", "independent", "source_full_gate"):
        _require(stage.get(key) is True, "Unverified Validator prerequisite: " + key)
    _require(record.get("stage") == "sol" and stage.get("stage") == "sol", "Not a Validator boundary")
    if stage.get("producer_kind") == "operator_supplied_source":
        supplied = stage.get("operator_source") or {}
        _require(
            stage["producer_model"] is None
            and stage["producer_session_id"] is None
            and supplied.get("kind") == "operator_supplied_source"
            and all(
                supplied.get(key) == bound[key]
                for key in ("task_id", "contract_revision", "contract_hash", "source_revision")
            )
            and all(
                isinstance(supplied.get(key), str) and len(supplied[key]) == 64
                for key in ("source_files_sha256", "approval_event_sha256")
            ),
            "Operator source requires authenticated source and approval provenance, not a fabricated Builder",
        )
    else:
        _require(
            stage["model"] != stage["producer_model"] and stage["session_id"] != stage["producer_session_id"],
            "Builder cannot accept its own images",
        )
    _require({key: stage[key] for key in ("provider", "model")} == bound["reviewer"], "Wrong reviewer route")
    _require(stage["runtime_hash"] == bound["runtime_hash"], "Reviewer runtime changed")
    _require(stage["finished_at"] == record["finished_at"], "Missing actual stage finish timestamp")
    _time(stage["finished_at"])
    events_path = _file(run, record["events"])
    report_path = _file(run, record.get("reported_output") or record["output"])
    events_raw, report_raw = events_path.read_bytes(), report_path.read_bytes()
    report = json.loads(report_raw)
    for key in ("task_id", "contract_revision", "contract_hash"):
        _require(report[key] == bound[key], "Reviewer report has a different " + key)
    _require(report["design_manifest_hash"] == bound["manifest_body_hash"], "Foreign report reference")
    native = _terminal(events_raw, report, stage["session_id"])
    rows = report["design_results"]
    ids = [row["id"] for row in rows]
    _require(len(ids) == len(set(ids)) and set(ids) == set(bound["case_criteria"]), "Missing or duplicate design case")
    pins = {
        str(manifest_file): _sha(manifest_raw),
        str(events_path): _sha(events_raw),
        str(report_path): _sha(report_raw),
    }
    cases, images = [], []
    for case in bound["cases"]:
        row = next(row for row in rows if row["id"] == case["id"])
        _require(row["status"] in ("PASS", "FAIL", "NOT_VERIFIED"), "Unknown visual verdict")
        _require(row["criterion_ids"] == bound["case_criteria"][case["id"]], "Wrong visual criterion identity")
        capture, refs = verify_capture(row["capture_ref"], row["capture_sha256"], case=case, current=current)
        _require(
            capture["reference_hash"] == bound["manifest_body_hash"]
            and capture["source_revision"] == bound["source_revision"],
            "Stale implementation capture",
        )
        _require(
            capture["case"] == {key: case[key] for key in ("id", "route", "state", "viewport")},
            "Wrong capture case, state, route or viewport",
        )
        capture_file = _file(root, row["capture_ref"])
        capture_raw = capture_file.read_bytes()
        _require(
            _sha(capture_raw) == row["capture_sha256"] and json.loads(capture_raw) == capture, "Capture receipt changed"
        )
        pins[str(capture_file)] = row["capture_sha256"]
        candidate = _file(root, capture["artifacts"]["candidate"]["path"])
        _require(_file(root, row["candidate_ref"]) == candidate, "Wrong candidate image")
        reference = _file(manifest["root"], case["artifacts"]["screenshot"]["path"])
        _require(candidate != reference, "Reference cannot be the implementation capture")
        for artifact in case["artifacts"].values():
            path = _file(manifest["root"], artifact["path"])
            _require(_sha(path.read_bytes()) == artifact["sha256"], "Reference artifact changed")
            pins[str(path)] = artifact["sha256"]
        for kind, path, expected in [
            ("reference", reference, case["artifacts"]["screenshot"]["sha256"]),
            ("candidate", candidate, capture["artifacts"]["candidate"]["sha256"]),
        ]:
            data = path.read_bytes()
            _require(data.startswith(b"\x89PNG\r\n\x1a\n") and _sha(data) == expected, "Image bytes differ")
            pins[str(path)] = expected
            images.append({"case_id": case["id"], "kind": kind, "sha256": expected, "mime": "image/png"})
        for ref in [*refs, str(capture_file), str(candidate), str(reference)]:
            path = _file(root, ref)
            observed = _sha(path.read_bytes())
            _require(str(path) not in pins or pins[str(path)] == observed, "Visual evidence changed while pinning")
            pins[str(path)] = observed
        cases.append(
            {
                "id": case["id"],
                "verdict": row["status"],
                "criterion_ids": deepcopy(row["criterion_ids"]),
                "capture_sha256": row["capture_sha256"],
            }
        )
    delivery = verify_delivery(
        record, events=events_raw, report=report_raw, images=deepcopy(images), binding=deepcopy(bound)
    )
    _require(isinstance(delivery, dict), "No authenticated image delivery")
    _require(
        delivery.get("version") == 1 and delivery.get("kind") == "final_request_image_delivery",
        "Unsupported delivery evidence",
    )
    _require(
        delivery.get("image_capable") is True
        and delivery.get("completed") is True
        and delivery.get("after_final_transform") is True,
        "Unverified or failed image request",
    )
    for key, expected in {
        **native,
        **bound["reviewer"],
        "binding_sha256": util.digest(bound),
        "events_sha256": _sha(events_raw),
        "report_sha256": _sha(report_raw),
    }.items():
        _require(delivery.get(key) == expected, "Delivery identity mismatch: " + key)
    _require(
        all(isinstance(delivery.get(key), str) and delivery[key] for key in ("request_id", "response_id")),
        "Missing actual request/response correlation",
    )
    _require(delivery.get("images") == images, "Both exact image payloads must reach the accepted review context")
    delivery_path = _file(run, delivery["evidence_ref"])
    _require(_sha(delivery_path.read_bytes()) == delivery["evidence_sha256"], "Transport evidence changed")
    pins[str(delivery_path)] = delivery["evidence_sha256"]
    # Recheck after callbacks; a newly calculated hash cannot replace earlier pins.
    _require(
        all(_sha(_file(root, path).read_bytes()) == sha for path, sha in pins.items()),
        "Visual evidence changed during acceptance",
    )
    receipt = {
        "version": 1,
        "kind": "visual_acceptance",
        "binding": bound,
        "cases": cases,
        "verdict": (
            "PASS"
            if all(row["verdict"] == "PASS" for row in cases)
            else "FAIL"
            if any(row["verdict"] == "FAIL" for row in cases)
            else "NOT_VERIFIED"
        ),
        "accepted_at": stage["finished_at"],
        "reviewer": deepcopy(stage),
        "delivery": deepcopy(delivery),
        "evidence_hashes": pins,
    }
    receipt["id"] = util.digest({"events": str(events_path), **native})
    receipt["receipt_sha256"] = util.digest(receipt)
    ledger = state.setdefault("visual_acceptance_receipts", [])
    previous = next((item for item in ledger if item.get("id") == receipt["id"]), None)
    _require(previous is None or previous == receipt, "Conflicting acceptance replay")
    if previous is None:
        ledger.append(deepcopy(receipt))
    return deepcopy(receipt)


def summary(receipts, *, current, manifest, evidence_hashes=None, verified_capture_hashes=()):
    """Pure current projection. Runtime supplies freshly checked hashes/captures.

    A saved PASS is only history until the complete current binding, retained
    artifacts and capture authentication are rechecked by the boundary owner.
    No default path reads, timestamps, state writes or metric-derived authority.
    """
    bound = binding(current, manifest)
    evidence_hashes = evidence_hashes or {}
    captures = set(verified_capture_hashes)
    valid, historical = [], []
    for receipt in receipts:
        try:
            body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
            if (
                receipt["version"] != 1
                or receipt["kind"] != "visual_acceptance"
                or receipt["receipt_sha256"] != util.digest(body)
            ):
                continue
            _time(receipt["accepted_at"])
            historical.append(receipt)
            if (
                receipt["binding"] == bound
                and receipt["evidence_hashes"]
                and all(evidence_hashes.get(path) == sha for path, sha in receipt["evidence_hashes"].items())
                and all(row["capture_sha256"] in captures for row in receipt["cases"])
            ):
                valid.append(receipt)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    valid.sort(key=lambda receipt: _time(receipt["accepted_at"]))
    rows = []
    for case in bound["cases"]:
        matches = [(receipt, row) for receipt in valid for row in receipt["cases"] if row["id"] == case["id"]]
        history = [
            receipt["accepted_at"]
            for receipt in historical
            for row in receipt["cases"]
            if row["id"] == case["id"] and row["verdict"] == "PASS"
        ]
        receipt, outcome = matches[-1] if matches else (None, None)
        verdict = outcome["verdict"] if outcome else "NOT_VERIFIED"
        rows.append(
            {
                "id": case["id"],
                "file_key": case["file_key"],
                "node_id": case["node_id"],
                "criterion_ids": deepcopy(bound["case_criteria"][case["id"]]),
                "verdict": verdict,
                "current_accepted": None if verdict == "NOT_VERIFIED" else verdict == "PASS",
                "accepted_at": receipt["accepted_at"] if verdict == "PASS" else None,
                "historical_accepted_at": min(history, key=_time) if history else None,
            }
        )
    frames = {(row["file_key"], row["node_id"]) for row in rows}
    return {
        "cases": rows,
        "current_all_accepted": all(row["current_accepted"] is True for row in rows),
        "accepted_cases": sum(row["current_accepted"] is True for row in rows),
        "accepted_frames": sum(
            all(row["current_accepted"] is True for row in rows if (row["file_key"], row["node_id"]) == frame)
            for frame in frames
        ),
        "coverage_complete": all(row["current_accepted"] is not None for row in rows),
    }
