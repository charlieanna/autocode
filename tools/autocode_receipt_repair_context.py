"""Bounded receipt authority for correcting a report citation, never accepting it.

The evidence verifier supplies authority only after its existing containment,
output-hash and attempt/event authentication checks pass. This pure module does
not read files, grant retries, parse provider prose or relax check matching.
It writes only the existing pending report-repair record's nested context/pins.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shlex

CATEGORY = "COMMAND_RECEIPT_MISMATCH"
MAX_CONTEXT_BYTES = 24 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
MAX_ARGUMENTS = 128
_ATTRIBUTE = "command_receipt_repair_context"
_AUTHORITIES = {"attempt_bound_capture", "executed_capture_event"}
INSTRUCTION = (
    "If command_receipt_repair_context contains an authenticated_receipt, use its exact command, "
    "argv, integer exit_code and evidence_ref to correct that citation only. command is display text "
    "for the recorded argv; JSON escaping is not another shell decoding step. Never substitute "
    "placeholders, change arguments or turn a nonzero exit into zero. full_output is a pinned "
    "path/hash/byte-count pointer, not a new execution or proof of PASS. Missing/omitted context "
    "confers no receipt authority; consult the original retained evidence. All existing check "
    "verification and read-only restrictions still apply.\n"
)


def receipt_snapshot(data):
    """Parse and hash exactly one bounded receipt read; no file access here.

    The caller reads at most MAX_RECEIPT_BYTES + 1 from one open handle. The
    extra byte detects actual oversize even if stat information is stale.
    """
    if not isinstance(data, bytes) or len(data) > MAX_RECEIPT_BYTES:
        raise ValueError("Receipt repair context exceeds its byte bound")
    receipt = json.loads(data)
    if not isinstance(receipt, dict):
        raise ValueError("Receipt repair context must be one JSON object")
    return receipt, hashlib.sha256(data).hexdigest()


def diagnostic(check, receipt, *, omitted=None):
    """Generic, bounded mismatch fields; never infer which one caused a live run."""
    fields = []
    argv = receipt.get("command")
    if not isinstance(argv, list) or not all(isinstance(part, str) for part in argv):
        fields.append("receipt_command_shape")
    else:
        try:
            if shlex.split(check.get("command", "")) != argv:
                fields.append("command_arguments")
        except ValueError:
            fields.append("command_parse")
    if type(receipt.get("exit_code")) is not int:
        fields.append("receipt_exit_shape")
    elif check.get("exit_code") is not None and check["exit_code"] != receipt["exit_code"]:
        fields.append("exit_code")
    value = {"category": CATEGORY, "mismatch_fields": fields, "authenticated_receipt": None}
    if omitted:
        value["omitted"] = omitted
    return value


def authenticated(check, receipt, *, authority, receipt_path, receipt_sha256, output_path, output_bytes):
    """Project already-authenticated evidence exactly, or omit it as a whole.

    ``command`` is shell-quoted display text; ``argv`` is the authority. JSON
    escaping is decoded exactly once by ordinary JSON serialization. Output is
    a hash/size/path pointer, not a truncation or summary used as evidence.
    """
    value = diagnostic(check, receipt)
    argv = receipt.get("command")
    if (
        not isinstance(authority, str)
        or authority not in _AUTHORITIES
        or not isinstance(argv, list)
        or not all(isinstance(part, str) for part in argv)
        or type(receipt.get("exit_code")) is not int
        or not isinstance(receipt_path, str)
        or not isinstance(output_path, str)
        or type(output_bytes) is not int
        or output_bytes < 0
        or not isinstance(receipt_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", receipt_sha256)
        or not isinstance(receipt.get("full_output_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", receipt["full_output_sha256"])
    ):
        return diagnostic(check, receipt, omitted="invalid_authenticated_metadata")
    if len(argv) > MAX_ARGUMENTS:
        return diagnostic(check, receipt, omitted="argument_count_limit")
    value["authenticated_receipt"] = {
        "authority": authority,
        "evidence_ref": check["evidence_ref"],
        "receipt_path": receipt_path,
        "receipt_sha256": receipt_sha256,
        "command": shlex.join(argv),
        "argv": list(argv),
        "exit_code": receipt["exit_code"],
        "full_output": {
            "path": output_path,
            "sha256": receipt["full_output_sha256"],
            "bytes": output_bytes,
        },
    }
    if len(json.dumps(value, ensure_ascii=True).encode("utf-8")) > MAX_CONTEXT_BYTES:
        return diagnostic(check, receipt, omitted="context_size_limit")
    return value


def attach(error, value):
    """Keep the existing plain ValueError type/text and failure-ledger identity."""
    setattr(error, _ATTRIBUTE, copy.deepcopy(value))
    return error


def _bounded(value):
    if not isinstance(value, dict) or value.get("category") != CATEGORY:
        return None
    if (
        not {"category", "mismatch_fields", "authenticated_receipt"}.issubset(value)
        or set(value) - {"category", "mismatch_fields", "authenticated_receipt", "omitted"}
        or ("omitted" in value and not isinstance(value["omitted"], str))
    ):
        return None
    try:
        data = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return None
    if len(data) > MAX_CONTEXT_BYTES:
        return None
    result = json.loads(data)
    fields = result.get("mismatch_fields")
    if (
        not isinstance(fields, list)
        or not all(
            isinstance(field, str)
            and field
            in {"receipt_command_shape", "command_arguments", "command_parse", "receipt_exit_shape", "exit_code"}
            for field in fields
        )
        or len(fields) > 2
        or len(set(fields)) != len(fields)
    ):
        return None
    receipt = result.get("authenticated_receipt")
    if receipt is not None:
        if not isinstance(receipt, dict):
            return None
        argv = receipt.get("argv")
        output = receipt.get("full_output")
        if (
            set(receipt)
            != {
                "authority",
                "evidence_ref",
                "receipt_path",
                "receipt_sha256",
                "command",
                "argv",
                "exit_code",
                "full_output",
            }
            or not isinstance(receipt.get("authority"), str)
            or receipt["authority"] not in _AUTHORITIES
            or not isinstance(argv, list)
            or len(argv) > MAX_ARGUMENTS
            or not all(isinstance(part, str) for part in argv)
            or receipt.get("command") != shlex.join(argv)
            or type(receipt.get("exit_code")) is not int
            or not isinstance(receipt.get("evidence_ref"), str)
            or not isinstance(output, dict)
            or set(output) != {"path", "sha256", "bytes"}
            or type(output.get("bytes")) is not int
            or output["bytes"] < 0
        ):
            return None
    return result


def _pins(value):
    receipt = value.get("authenticated_receipt")
    if not isinstance(receipt, dict):
        return {}
    output = receipt.get("full_output")
    if not isinstance(output, dict):
        return None
    pins = {}
    for path, sha256 in (
        (receipt.get("receipt_path"), receipt.get("receipt_sha256")),
        (output.get("path"), output.get("sha256")),
    ):
        if not isinstance(path, str) or not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            return None
        if path in pins and pins[path] != sha256:
            return None
        pins[path] = sha256
    return pins


def retain(pending, error):
    """Pair the latest rejected error with its own context; never reuse old context.

    The existing repair source/pin guard checks the added receipt/output pins
    before another repair can launch. This grants no attempt or acceptance.
    """
    pending.pop("receipt_repair_context", None)
    value = _bounded(getattr(error, _ATTRIBUTE, None))
    if value is None:
        return
    pins = _pins(value)
    if pins is None:
        return
    retained = pending.setdefault("pins", {})
    if any(path in retained and retained[path] != sha256 for path, sha256 in pins.items()):
        value = {
            "category": CATEGORY,
            "mismatch_fields": value.get("mismatch_fields", []),
            "authenticated_receipt": None,
            "omitted": "saved_pin_conflict",
        }
    else:
        retained.update(pins)
    pending["receipt_repair_context"] = value


def handoff(pending):
    """Read the nested context only when all of its authority pins are retained.

    The caller must run its existing on-disk pin/source checks first. Older
    checkpoints have no context and keep their existing report-repair behavior.
    """
    value = _bounded(pending.get("receipt_repair_context"))
    if value is None:
        return None
    pins = _pins(value)
    if pins is None or any(pending.get("pins", {}).get(path) != sha256 for path, sha256 in pins.items()):
        return None
    return value


def prompt_parts(pending):
    """Add receipt guidance only with current pinned authenticated authority.

    Older checkpoints and diagnostic-only omissions retain their exact legacy
    prompt and handoff bytes. The existing disk/source guards precede this call.
    """
    value = handoff(pending)
    if value is None or value["authenticated_receipt"] is None:
        return "", {}
    return INSTRUCTION, {_ATTRIBUTE: value}
