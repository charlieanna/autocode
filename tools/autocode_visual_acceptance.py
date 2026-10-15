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
    from . import autocode_visual_scope as visual_scope
except ImportError:
    import autocode_design_manifest as design
    import autocode_util as util
    import autocode_visual_scope as visual_scope


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
    try:
        result = _binding_identity(current, record)
        design.verify(record)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Invalid retained design manifest record") from error
    return result


def _binding_identity(current, record):
    """Pure approved identity; current acceptance also needs sealed file hashes."""
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
    _require(
        isinstance(record, dict)
        and not set(record) - {"body", "manifest_hash", "root", "manifest_path", "scope_admission"},
        "Invalid retained design manifest record",
    )
    root = record.get("root")
    _require(
        isinstance(root, (str, Path)) and bool(root) and Path(root).is_absolute(),
        "Missing retained design bundle directory",
    )
    index = record.get("manifest_path")
    _require(
        index is None or (isinstance(index, (str, Path)) and Path(index).is_absolute()),
        "Invalid retained design inventory index",
    )
    body = record["body"]
    if isinstance(body, dict) and body.get("version") == 2:
        util.validate_schema(body, design.SCHEMA_V2)
    else:
        design.validate(body)
    cases = body["cases"]
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
    if body.get("version") == 2:
        # Lexical saved identity only; strict binding verifies physical ownership.
        result["reference_input_identity"] = {
            "root": str(Path(root)),
            "manifest_path": str(Path(index)) if index is not None else None,
            "scope_admission": deepcopy(record.get("scope_admission")),
        }
    if "visual_scope" in current:
        # Structure only. The runner must independently derive this before launch
        # and rederive at acceptance; model-supplied scope is never authority.
        retained_scope = deepcopy(current["visual_scope"])
        visual_scope.check_bound_scope(
            retained_scope,
            case_ids=[case["id"] for case in cases],
            case_criteria=result["case_criteria"],
            criterion_ids=ids,
        )
        result["visual_scope"] = retained_scope
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
    # Seal original complete references before callbacks can change their bytes.
    reference_pins = _reference_pins(manifest)
    for path, expected in reference_pins.items():
        _require(_sha(_file(root, path).read_bytes()) == expected, "Reference artifact changed")
    if manifest.get("manifest_path"):
        index = _file(root, manifest["manifest_path"])
        index_raw = index.read_bytes()
        _require(json.loads(index_raw) == manifest["body"], "Retained design inventory index changed")
        reference_pins[str(index)] = _sha(index_raw)
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
        **reference_pins,
        str(manifest_file): _sha(manifest_raw),
        str(events_path): _sha(events_raw),
        str(report_path): _sha(report_raw),
    }
    cases, images = [], []
    scoped = "visual_scope" in bound
    required_ids = set(bound["visual_scope"]["required_case_ids"]) if scoped else None
    if scoped:
        # Refuse impossible outside claims before any capture/delivery callback.
        for row in rows:
            _require(isinstance(row, dict) and set(row) == _SCOPED_REPORT_KEYS, "Invalid scoped report fields")
            _require(row["criterion_ids"] == bound["case_criteria"][row["id"]], "Wrong visual criterion identity")
            if row["id"] not in required_ids:
                _outside_case(row)
    for case in bound["cases"]:
        row = next(row for row in rows if row["id"] == case["id"])
        _require(row["status"] in ("PASS", "FAIL", "NOT_VERIFIED"), "Unknown visual verdict")
        _require(row["criterion_ids"] == bound["case_criteria"][case["id"]], "Wrong visual criterion identity")
        if scoped and case["id"] not in required_ids:
            cases.append(_outside_case(row))
            continue
        if scoped:
            _require(
                isinstance(row["capture_ref"], str) and row["capture_ref"].strip() and _hash(row["capture_sha256"]),
                "Required visual case lacks capture evidence",
            )
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
        if scoped:
            comparison_ref = row.get("comparison_ref")
            _require(
                isinstance(comparison_ref, str) and (comparison_ref or row["status"] != "PASS"),
                "Required visual PASS lacks comparison evidence",
            )
            comparison_sha256 = None
            if comparison_ref:
                comparison = _file(root, comparison_ref)
                _require(
                    comparison not in (capture_file, candidate, reference), "Comparison must be independent evidence"
                )
                comparison_ref = str(comparison)
                comparison_raw = comparison.read_bytes()
                _require(
                    row["status"] != "PASS" or bool(comparison_raw),
                    "Required visual PASS needs nonempty comparison evidence",
                )
                comparison_sha256 = _sha(comparison_raw)
                _require(
                    str(comparison) not in pins or pins[str(comparison)] == comparison_sha256,
                    "Comparison evidence changed while pinning",
                )
                pins[str(comparison)] = comparison_sha256
            cases.append(
                {
                    "id": case["id"],
                    "verdict": row["status"],
                    "criterion_ids": deepcopy(row["criterion_ids"]),
                    "capture_ref": str(capture_file),
                    "capture_sha256": row["capture_sha256"],
                    "candidate_ref": str(candidate),
                    "candidate_sha256": capture["artifacts"]["candidate"]["sha256"],
                    "comparison_ref": comparison_ref,
                    "comparison_sha256": comparison_sha256,
                    "reference_sha256": case["artifacts"]["screenshot"]["sha256"],
                }
            )
        else:
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
    if scoped:
        selected = [row for row in cases if row["id"] in required_ids]
        receipt.update(
            version=2,
            scope_verdict=_verdict(selected),
            scope_all_accepted=_verdict(selected) == "PASS",
            manifest_ref=str(manifest_file),
            native_evidence={"events_ref": str(events_path), "report_ref": str(report_path), **native},
        )
    receipt["id"] = util.digest({"events": str(events_path), **native})
    receipt["receipt_sha256"] = util.digest(receipt)
    if scoped:
        _check_scoped_receipt(receipt)
    ledger = state.setdefault("visual_acceptance_receipts", [])
    previous = next((item for item in ledger if item.get("id") == receipt["id"]), None)
    _require(previous is None or previous == receipt, "Conflicting acceptance replay")
    if previous is None:
        ledger.append(deepcopy(receipt))
    return deepcopy(receipt)


def _reference_pins(record):
    """Declared immutable reference pins; never observe or replace file hashes."""
    root = Path(record["root"])
    pins = {str(root / artifact["path"]): artifact["sha256"] for artifact in design.all_artifacts(record["body"])}
    saved = record.get("scope_admission")
    if saved is not None:
        _require(
            isinstance(saved, dict)
            and set(saved) == {"path", "sha256"}
            and isinstance(saved["path"], str)
            and _hash(saved["sha256"]),
            "Invalid retained scope metadata",
        )
        design.relative_path(saved["path"])
        pins[str(root / saved["path"])] = saved["sha256"]
    return pins


_SCOPED_CASE_KEYS = {
    "id",
    "verdict",
    "criterion_ids",
    "capture_ref",
    "capture_sha256",
    "candidate_ref",
    "candidate_sha256",
    "comparison_ref",
    "comparison_sha256",
    "reference_sha256",
}
_SCOPED_REPORT_KEYS = {
    "id",
    "status",
    "criterion_ids",
    "capture_ref",
    "capture_sha256",
    "candidate_ref",
    "comparison_ref",
}
_SCOPED_RECEIPT_KEYS = {
    "version",
    "kind",
    "binding",
    "cases",
    "verdict",
    "accepted_at",
    "manifest_ref",
    "reviewer",
    "delivery",
    "evidence_hashes",
    "native_evidence",
    "scope_verdict",
    "scope_all_accepted",
    "id",
    "receipt_sha256",
}


def _verdict(rows):
    return (
        "PASS"
        if rows and all(row["verdict"] == "PASS" for row in rows)
        else "FAIL"
        if any(row["verdict"] == "FAIL" for row in rows)
        else "NOT_VERIFIED"
    )


def _outside_case(row):
    # Reports preserve the existing string schema; retained absence is explicit.
    _require(
        row["status"] == "NOT_VERIFIED"
        and all(row.get(key) == "" for key in ("capture_ref", "capture_sha256", "candidate_ref", "comparison_ref")),
        "Out-of-scope cases must not claim inspection, capture or comparison",
    )
    return {
        "id": row["id"],
        "verdict": "NOT_VERIFIED",
        "criterion_ids": deepcopy(row["criterion_ids"]),
        "capture_ref": "",
        "capture_sha256": None,
        "candidate_ref": "",
        "candidate_sha256": None,
        "comparison_ref": "",
        "comparison_sha256": None,
        "reference_sha256": None,
    }


def _check_scoped_receipt(receipt):
    """Pure structural check. Fresh runtime-authenticated evidence is separate."""
    _require(isinstance(receipt, dict) and set(receipt) == _SCOPED_RECEIPT_KEYS, "Invalid scoped receipt fields")
    _require(
        type(receipt["version"]) is int and receipt["version"] == 2 and receipt["kind"] == "visual_acceptance",
        "Unsupported scoped receipt",
    )
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    _require(
        _hash(receipt["receipt_sha256"]) and receipt["receipt_sha256"] == util.digest(body), "Changed scoped receipt"
    )
    _time(receipt["accepted_at"])
    bound = receipt["binding"]
    cases, scope = bound["cases"], bound["visual_scope"]
    ids = [case["id"] for case in cases]
    visual_scope.check_bound_scope(
        scope,
        case_ids=ids,
        case_criteria=bound["case_criteria"],
        criterion_ids=[row["id"] for row in bound["criteria"]],
    )
    rows = receipt["cases"]
    _require(
        isinstance(rows, list) and [row["id"] for row in rows] == ids,
        "Scoped receipt must retain the ordered complete manifest roster",
    )
    required = set(scope["required_case_ids"])
    images = []
    pins = receipt["evidence_hashes"]
    _require(
        isinstance(pins, dict)
        and pins
        and all(
            isinstance(path, str) and path and Path(path).is_absolute() and _hash(sha) for path, sha in pins.items()
        ),
        "Invalid scoped evidence pins",
    )
    _require(
        isinstance(receipt["manifest_ref"], str)
        and Path(receipt["manifest_ref"]).is_absolute()
        and pins.get(receipt["manifest_ref"]) == bound["manifest_file_sha256"],
        "Missing exact manifest file pin",
    )
    for case, row in zip(cases, rows, strict=False):
        _require(
            isinstance(row, dict)
            and set(row) == _SCOPED_CASE_KEYS
            and row["criterion_ids"] == bound["case_criteria"][case["id"]]
            and row["verdict"] in ("PASS", "FAIL", "NOT_VERIFIED"),
            "Invalid scoped case",
        )
        if case["id"] not in required:
            _require(
                row["verdict"] == "NOT_VERIFIED"
                and all(row[key] == "" for key in ("capture_ref", "candidate_ref", "comparison_ref"))
                and all(
                    row[key] is None
                    for key in ("capture_sha256", "candidate_sha256", "comparison_sha256", "reference_sha256")
                ),
                "Out-of-scope receipt cannot claim inspection or reference evidence",
            )
            continue
        for ref, sha in (("capture_ref", "capture_sha256"), ("candidate_ref", "candidate_sha256")):
            _require(
                isinstance(row[ref], str)
                and Path(row[ref]).is_absolute()
                and _hash(row[sha])
                and pins.get(row[ref]) == row[sha],
                "Missing required capture or candidate pin",
            )
        _require(
            row["capture_ref"] != row["candidate_ref"]
            and row["reference_sha256"] == case["artifacts"]["screenshot"]["sha256"],
            "Wrong required image identity",
        )
        comparison = row["comparison_ref"]
        _require(
            isinstance(comparison, str) and (bool(comparison) or row["verdict"] != "PASS"),
            "Required visual PASS lacks comparison evidence",
        )
        if comparison:
            _require(
                Path(comparison).is_absolute()
                and comparison not in (row["capture_ref"], row["candidate_ref"])
                and _hash(row["comparison_sha256"])
                and pins.get(comparison) == row["comparison_sha256"],
                "Missing comparison pin",
            )
        else:
            _require(row["comparison_sha256"] is None, "Unclaimed comparison has a hash")
        _require(
            row["verdict"] != "PASS" or row["comparison_sha256"] != _sha(b""),
            "Required visual PASS needs nonempty comparison evidence",
        )
        images.extend(
            [
                {"case_id": case["id"], "kind": "reference", "sha256": row["reference_sha256"], "mime": "image/png"},
                {"case_id": case["id"], "kind": "candidate", "sha256": row["candidate_sha256"], "mime": "image/png"},
            ]
        )
    selected = [row for row in rows if row["id"] in required]
    _require(
        receipt["scope_verdict"] == _verdict(selected)
        and type(receipt["scope_all_accepted"]) is bool
        and receipt["scope_all_accepted"] == (_verdict(selected) == "PASS")
        and receipt["verdict"] == _verdict(rows),
        "Stored verdict disagrees with its cases",
    )
    stage, delivery, native = receipt["reviewer"], receipt["delivery"], receipt["native_evidence"]
    _require(
        isinstance(native, dict)
        and set(native) == {"events_ref", "report_ref", "session_id", "message_id", "finish_id"},
        "Missing native evidence identity",
    )
    _require(
        all(isinstance(native[key], str) and native[key] for key in native)
        and all(Path(native[key]).is_absolute() for key in ("events_ref", "report_ref")),
        "Invalid original native evidence",
    )
    _require(
        receipt["id"]
        == util.digest(
            {"events": native["events_ref"], **{key: native[key] for key in ("session_id", "message_id", "finish_id")}}
        ),
        "Wrong native receipt identity",
    )
    _require(
        stage["stage"] == "sol"
        and all(stage.get(key) is True for key in ("accepted", "read_only", "independent", "source_full_gate"))
        and {key: stage[key] for key in ("provider", "model")} == bound["reviewer"]
        and stage["runtime_hash"] == bound["runtime_hash"]
        and stage["finished_at"] == receipt["accepted_at"]
        and stage["session_id"] == native["session_id"],
        "Wrong accepted Validator identity",
    )
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
            and all(_hash(supplied.get(key)) for key in ("source_files_sha256", "approval_event_sha256")),
            "Invalid operator source provenance",
        )
    else:
        _require(
            stage["model"] != stage["producer_model"] and stage["session_id"] != stage["producer_session_id"],
            "Self-reviewed scoped receipt",
        )
    _require(
        type(delivery["version"]) is int
        and delivery["version"] == 1
        and delivery["kind"] == "final_request_image_delivery"
        and all(delivery.get(key) is True for key in ("image_capable", "completed", "after_final_transform"))
        and delivery["binding_sha256"] == util.digest(bound)
        and delivery["images"] == images,
        "Missing exact required image delivery",
    )
    for key in ("session_id", "message_id", "finish_id"):
        _require(delivery[key] == native[key], "Foreign delivery request")
    _require(
        {key: delivery[key] for key in ("provider", "model")} == bound["reviewer"]
        and all(isinstance(delivery.get(key), str) and delivery[key] for key in ("request_id", "response_id")),
        "Missing delivery correlation",
    )
    _require(
        isinstance(delivery["evidence_ref"], str)
        and Path(delivery["evidence_ref"]).is_absolute()
        and len({native["events_ref"], native["report_ref"], delivery["evidence_ref"]}) == 3,
        "Invalid native evidence path identity",
    )
    for ref, sha in (
        (native["events_ref"], delivery["events_sha256"]),
        (native["report_ref"], delivery["report_sha256"]),
        (delivery["evidence_ref"], delivery["evidence_sha256"]),
    ):
        _require(_hash(sha) and pins.get(ref) == sha, "Missing original native or transport evidence pin")
    return required


def _scoped_summary(receipts, *, bound, manifest, evidence_hashes, verified_capture_hashes):
    """One matching fresh receipt, never a union of slices or saved PASS flags."""
    reference_pins = _reference_pins(manifest)
    index = str(Path(manifest["manifest_path"])) if manifest.get("manifest_path") else None
    fresh, capture_hashes = evidence_hashes or {}, set(verified_capture_hashes)
    valid, historical = [], []
    for receipt in receipts:
        try:
            required = _check_scoped_receipt(receipt)
            historical.append(receipt)
            pins = receipt["evidence_hashes"]
            declared_references = set(reference_pins)
            required_rows = [row for row in receipt["cases"] if row["id"] in required]
            if (
                receipt["binding"] == bound
                and all(pins.get(path) == sha for path, sha in reference_pins.items())
                and (not index or index in pins)
                and all(fresh.get(path) == sha for path, sha in pins.items())
                and all(
                    row["capture_ref"] not in declared_references
                    and row["candidate_ref"] not in declared_references
                    and row["comparison_ref"] not in declared_references
                    for row in required_rows
                )
                and all(row["capture_sha256"] in capture_hashes for row in required_rows)
            ):
                valid.append(receipt)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    selected = max(valid, key=lambda receipt: _time(receipt["accepted_at"])) if valid else None
    selected_rows = {row["id"]: row for row in selected["cases"]} if selected else {}
    rows = []
    required = set(bound["visual_scope"]["required_case_ids"])
    for case in bound["cases"]:
        outcome = selected_rows.get(case["id"])
        verdict = outcome["verdict"] if outcome else "NOT_VERIFIED"
        history = [
            receipt["accepted_at"]
            for receipt in historical
            for row in receipt["cases"]
            if row["id"] == case["id"] and row["verdict"] == "PASS"
        ]
        rows.append(
            {
                "id": case["id"],
                "file_key": case["file_key"],
                "node_id": case["node_id"],
                "criterion_ids": deepcopy(bound["case_criteria"][case["id"]]),
                "verdict": verdict,
                "current_accepted": None if verdict == "NOT_VERIFIED" else verdict == "PASS",
                "accepted_at": selected["accepted_at"] if verdict == "PASS" else None,
                "historical_accepted_at": min(history, key=_time) if history else None,
            }
        )
    scoped = [row for row in rows if row["id"] in required]
    scope_verdict = _verdict(scoped)
    scope_all_accepted = bool(selected) and scope_verdict == "PASS"
    integration = bound["visual_scope"]["mode"] == "integration" and required == {row["id"] for row in rows}
    frames = {(row["file_key"], row["node_id"]) for row in rows}
    return {
        "cases": rows,
        "scope_verdict": scope_verdict,
        "scope_all_accepted": scope_all_accepted,
        "scope_coverage_complete": bool(selected) and all(row["current_accepted"] is not None for row in scoped),
        "current_all_accepted": bool(selected) and integration and all(row["current_accepted"] is True for row in rows),
        "accepted_cases": sum(row["current_accepted"] is True for row in rows),
        "accepted_frames": sum(
            all(row["current_accepted"] is True for row in rows if (row["file_key"], row["node_id"]) == frame)
            for frame in frames
        ),
        "coverage_complete": bool(selected) and all(row["current_accepted"] is not None for row in rows),
    }


def summary(receipts, *, current, manifest, evidence_hashes=None, verified_capture_hashes=()):
    """Pure current projection. Runtime supplies freshly checked hashes/captures.

    A saved PASS is only history until the complete current binding, retained
    artifacts and capture authentication are rechecked by the boundary owner.
    No default path reads, timestamps, state writes or metric-derived authority.
    """
    bound = _binding_identity(current, manifest)
    if "visual_scope" in bound:
        return _scoped_summary(
            receipts,
            bound=bound,
            manifest=manifest,
            evidence_hashes=evidence_hashes,
            verified_capture_hashes=verified_capture_hashes,
        )
    reference_pins = _reference_pins(manifest)
    index = str(Path(manifest["manifest_path"])) if manifest.get("manifest_path") else None
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
                and all(receipt["evidence_hashes"].get(path) == sha for path, sha in reference_pins.items())
                and (not index or index in receipt["evidence_hashes"])
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
