"""Authenticate explicit identity fields before giving them replay symbols.

No diagnostic text, command, credential or arbitrary substring is substituted.
Invalid original identity fields deliberately remain invalid in a new run.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path


def semantic_task(task):
    """Preserve task authority while omitting only fresh assignment metadata."""
    result = deepcopy(task or {})
    for key in ("id", "contract_hash", "assigned_at"):
        result.pop(key, None)
    return result


def _pointer(parent, key):
    return parent + "/" + str(key).replace("~", "~0").replace("/", "~1")


def changed_line_gaps(packet):
    """The recorded proof's open changed-line gaps, by id: each one's exact file, bytes and line.

    A gap id digests the proof's source revision and execution context, so a replay in new
    directories computes a new id for the same line (#2500). Only ``reason_required`` rows with a
    complete identity are rebindable; anything else stays as recorded and is refused as stale.
    """
    proof = (packet or {}).get("regression_proof") or {}
    assessment = proof.get("changed_code_evidence") or {}
    result = {}
    for obligation in assessment.get("obligations") or [] if isinstance(assessment, dict) else []:
        if not isinstance(obligation, dict) or obligation.get("status") != "reason_required":
            continue
        identity, path, digest, line = (obligation.get(key) for key in ("gap_id", "path", "sha256", "line"))
        if (
            isinstance(identity, str)
            and identity
            and isinstance(path, str)
            and path
            and isinstance(digest, str)
            and len(digest) == 64
            and type(line) is int
            and line >= 1
        ):
            result[identity] = {"path": path, "sha256": digest, "line": line}
    return result


def proof_path(packet):
    """The recorded run's current regression proof file, named by a fresh directory in every run (#2500)."""
    path = ((packet or {}).get("regression_proof") or {}).get("path")
    return path if isinstance(path, str) and path and Path(path).is_absolute() else None


def bindings(payload, record, row, workspace, run_dir, snapshots, gaps=None, proof=None):
    known = {"task_id": row.get("task_id"), "contract_hash": row.get("contract_hash")}
    gaps = gaps or {}
    revisions = {value.get("revision") for value in snapshots}
    result = []
    capture = record.get("capture_context")
    session = record.get("thread_id") or record.get("expected_session")
    session_values = []
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started":
            session_values.append(event.get("thread_id"))
        elif event.get("type") in ("step_start", "step_finish", "text", "tool_use"):
            session_values.append(event.get("sessionID"))
            part = event.get("part")
            if isinstance(part, dict) and "sessionID" in part:
                session_values.append(part["sessionID"])
    valid_session = bool(
        isinstance(session, str) and session and session_values and all(value == session for value in session_values)
    )

    def add(pointer, symbol, *, suffix=None, gap=None):
        item = {"pointer": pointer, "symbol": symbol, "original_valid": True}
        if suffix is not None:
            item["suffix"] = suffix
        if gap is not None:
            item["gap"] = dict(gap)
        result.append(item)

    def visit(value, pointer, field=None):
        if isinstance(value, dict):
            for key, child in value.items():
                target = _pointer(pointer, key)
                if key in known and known[key] is not None and child == known[key]:
                    add(target, key)
                elif key == "capture_context" and capture and child == capture:
                    for captured_key, symbol in (
                        ("attempt", "output"),
                        ("nonce", "capture_nonce"),
                        ("source_revision", "source_revision"),
                    ):
                        if captured_key in child:
                            add(_pointer(target, captured_key), symbol)
                elif key in ("thread_id", "sessionID") and valid_session and child == session:
                    add(target, "session")
                elif key == "source_revision" and isinstance(child, str) and child in revisions:
                    add(target, "source_revision")
                elif key == "gap_id" and isinstance(child, str) and child in gaps:
                    add(target, "changed_code_gap", gap=gaps[child])
                else:
                    visit(child, target, key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, _pointer(pointer, index), field)
        elif isinstance(value, str) and field in (
            "evidence_refs",
            "evidence_ref",
            "source_refs",
            "output",
            "full_output",
            "receipt",
        ):
            path = Path(value)
            if not path.is_absolute():
                return
            if proof is not None and value == proof:
                # The proof directory's name is fresh in every run, so a citation of the proof is
                # bound to the replay's own current proof, not to a path under its run directory.
                add(pointer, "regression_proof")
                return
            for root, symbol in ((Path(run_dir), "run_dir"), (Path(workspace), "workspace")):
                try:
                    suffix = path.relative_to(root).as_posix()
                except ValueError:
                    continue
                if suffix and all(part not in ("", ".", "..") for part in Path(suffix).parts):
                    add(pointer, symbol, suffix=suffix)
                break

    visit(payload, "")
    return result


def final_message(events, original_report, rebound_report):
    """A native final JSON message follows its exact separately captured report.

    Arbitrary tool output or diagnostic JSON is left unchanged. Conversion occurs
    only for the exact report object, retaining native message/turn boundaries.
    """
    import json

    for event in events:
        part = event.get("part") if isinstance(event, dict) else None
        if event.get("type") != "text" or not isinstance(part, dict) or not isinstance(part.get("text"), str):
            continue
        try:
            value = json.loads(part["text"])
        except ValueError:
            continue
        if value == original_report:
            part["text"] = json.dumps(rebound_report, ensure_ascii=False)
