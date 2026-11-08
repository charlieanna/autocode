"""Describe unsupported capture claims without reading paths or granting authority.

The caller supplies already-read original events and source snapshots. A digest
here identifies historical bytes or a claim, never an authenticated receipt.
Missing custody is explicit even for internally consistent, self-forged data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import PurePosixPath

try:
    from . import autocode_util as util
    from .providers import opencode_events
except ImportError:
    import autocode_util as util
    from providers import opencode_events

MAX_DEPENDENCIES = 128
MAX_LINES = 16384
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _pin(value):
    return value if isinstance(value, str) and _HASH.fullmatch(value) else None


def _path(value, workspace):
    path = PurePosixPath(value)
    return os.path.normpath(str(path if path.is_absolute() else PurePosixPath(workspace) / path))


def _location(value, workspace, run_dir):
    """Lexical classification only: even an in-run path may be a symlink."""
    path = PurePosixPath(value)
    if "\x00" in value or "\\" in value or ".." in path.parts or path.as_posix() != value:
        return "noncanonical"
    path = PurePosixPath(_path(value, workspace))
    if path.is_relative_to(run_dir):
        return "run"
    if path.is_relative_to(PurePosixPath(workspace) / ".autocode" / "runs"):
        return "other_run"
    if path.is_relative_to(PurePosixPath(workspace) / ".autocode"):
        return "project_evidence"
    return "outside_evidence_roots"


def _claims(rows):
    if any(isinstance(row, dict) and row.get("type") == "tool_use" for row in rows):
        rows = opencode_events.normalized_events(rows)
    for event_index, event in enumerate(rows):
        item = event.get("item") if isinstance(event, dict) and event.get("type") == "item.completed" else None
        if not isinstance(item, dict) or item.get("type") not in ("command_execution", "tool_output"):
            continue
        output = item.get("aggregated_output")
        for line_index, line in enumerate(output.splitlines() if isinstance(output, str) else []):
            # Yield every inspected line so malformed/non-capture noise counts
            # against the scan bound too. No executable command is interpreted.
            try:
                value = json.loads(line)
            except (ValueError, RecursionError):
                value = None
            yield event_index, line_index, line, value


def _describe(value, line, *, record, snapshot, workspace, run_dir):
    issues = []
    command = value.get("command")
    command_valid = isinstance(command, list) and bool(command) and all(isinstance(arg, str) for arg in command)
    if not command_valid or type(value.get("exit_code")) is not int:
        issues.append("malformed_receipt")
    copy = value.get("verification_copy")
    copy = copy if isinstance(copy, dict) else {}
    copy_pin, output_pin = _pin(copy.get("manifest_sha256")), _pin(value.get("full_output_sha256"))
    if copy_pin is None:
        issues.append("missing_or_invalid_copy_pin")
    if output_pin is None:
        issues.append("missing_or_invalid_output_pin")
    expected_source = util.digest({"head": snapshot["head"], "files": snapshot["files"]})
    source = _pin(copy.get("source_revision"))
    source_match = "unavailable" if source is None else "match" if source == expected_source else "mismatch"
    if source_match != "match":
        issues.append("source_revision_" + source_match)
    context = record.get("capture_context")
    context_known = isinstance(context, dict) and all(
        isinstance(context.get(key), str) and context[key] for key in ("attempt", "nonce", "source_revision")
    )
    context_match = (
        "unavailable" if not context_known else "match" if context == value.get("capture_context") else "mismatch"
    )
    if context_match != "match":
        issues.append("capture_context_" + context_match)
    location = _location(value["full_output"], workspace, run_dir)
    if location in ("noncanonical", "other_run", "outside_evidence_roots"):
        issues.append("output_path_" + location)
    # No independent receipt/log/copy selectors are supplied by today's public
    # projection. Never discover them through model-selected absolute paths.
    issues.extend(("receipt_not_authenticated", "raw_output_not_inspected", "copy_manifest_not_supplied"))
    return {
        "receipt_claim_sha256": hashlib.sha256(line.encode("utf-8", "surrogatepass")).hexdigest(),
        "command_claim_sha256": util.digest(command) if command_valid else None,
        "copy_manifest_sha256_claim": copy_pin,
        "raw_output_sha256_claim": output_pin,
        "output_path_sha256": hashlib.sha256(value["full_output"].encode("utf-8", "surrogatepass")).hexdigest(),
        "output_location": location,
        "source_revision_claim": source,
        "before_source_revision": expected_source,
        "source_selection_sha256": util.digest(snapshot["source_paths"]),
        "source_revision_match": source_match,
        "capture_context_match": context_match,
        "issues": issues,
    }


def inventory(*, document, originals, records, references, workspace, run_dir, contexts):
    """Bounded v1 refusal diagnostics over retained data, with zero file access.

    All entries remain unverified. The inventory is incomplete as a portable
    closure: ordinary capture receipt files, raw logs, copy manifests and their
    input bytes have no independent selectors here. Import is not supported.
    """
    result = {
        "version": 1,
        "kind": "native_capture_claim_inventory",
        "execution_authority": False,
        "closure_status": "unverified",
        "truncated": False,
        "dependencies": [],
    }
    lines = 0
    paths = {}
    for row, record in zip(document["dispatches"], records, strict=True):
        for event_index, line_index, line, value in _claims(originals[row["events"]]):
            lines += 1
            if lines > MAX_LINES:
                result["truncated"] = True
                return result
            if not isinstance(value, dict) or "verification_copy" not in value:
                continue
            output = value.get("full_output")
            if not isinstance(output, str) or not output or _path(output, workspace) not in references:
                continue
            if len(result["dependencies"]) == MAX_DEPENDENCIES:
                result["truncated"] = True
                return result
            entry = _describe(
                value,
                line,
                record=record,
                snapshot=document["snapshots"][row["before"]],
                workspace=workspace,
                run_dir=run_dir,
            )
            entry.update(
                dispatch=row["id"],
                event_index=event_index,
                line_index=line_index,
                events_sha256=document["artifacts"][row["events"]]["original_sha256"],
                before_snapshot=row["before"],
                after_snapshot=row["after"],
                scope_sha256=util.digest(
                    {"task_id": row["task_id"], "contract_hash": row["contract_hash"], "context": contexts[row["id"]]}
                ),
            )
            peers = paths.setdefault(_path(output, workspace), [])
            if peers:
                entry["issues"].append("repeated_output_claim")
                for peer in peers:
                    if "repeated_output_claim" not in peer["issues"]:
                        peer["issues"].append("repeated_output_claim")
            peers.append(entry)
            result["dependencies"].append(entry)
    return result
