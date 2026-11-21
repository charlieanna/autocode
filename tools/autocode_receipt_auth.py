"""Authenticate a captured check against a runner-owned attempt or executed event."""

import contextlib
import json
import os
import shlex
from pathlib import Path

try:
    from . import autocode_receipts as receipts
    from .autocode_util import file_hash
except ImportError:
    import autocode_receipts as receipts
    from autocode_util import file_hash


def authenticate(receipt, path, workspace, tool_outputs, *, receipt_only, capture_context):
    """The existing receipt checks, shared by acceptance and optional repair context."""
    raw = Path(receipt["full_output"])
    if (
        not raw.resolve().is_relative_to(Path(workspace).resolve() / ".autocode")
        or file_hash(raw) != receipt["full_output_sha256"]
    ):
        raise ValueError("Full check output missing or changed")
    if receipt_only:
        if not capture_context or receipt.get("capture_context") != capture_context:
            raise ValueError("Capture receipt does not belong to this validation attempt")
        return "attempt_bound_capture"
    # capture prints one JSON object, then its attestation of the receipt's bytes on stderr
    # (autocode_receipts.attestation_line). Match structurally even if event text adds shell
    # notices or a display filter dropped the receipt line. No word-search for PASS/COMPLETE.
    try:
        receipt_bytes = path.read_bytes()
        attested = receipt_bytes if json.loads(receipt_bytes) == receipt else None
    except (OSError, ValueError):
        attested = None
    matched = False
    for item in tool_outputs:
        if item["type"] == "tool_output":
            try:
                invocation = shlex.split(item.get("command", ""))
                marker = invocation.index("capture")
                output_flag = invocation.index("--output", marker + 1)
                cited = Path(invocation[output_flag + 1])
                cited = cited if cited.is_absolute() else Path(workspace) / cited
                if cited.resolve() != path.resolve():
                    continue
            except (ValueError, IndexError):
                continue
        for line in item.get("aggregated_output", "").splitlines():
            with contextlib.suppress(ValueError):
                value = json.loads(line)
                matched |= value == receipt or (attested is not None and receipts.attests(value, attested))
    if not matched:
        names = {str(path), os.path.relpath(path.resolve(), Path(workspace).resolve())}
        shortened = receipts.display_filter([item.get("command") for item in tool_outputs], names)
        raise ValueError(
            "No independently executed Validator tool event matches receipt"
            + (
                f": its capture command sent the output through `{shortened}`, which dropped both the receipt"
                " line and capture's short exit_code line. Run capture again without filtering its output"
                " and cite that new receipt."
                if shortened
                else ""
            )
        )
    return "executed_capture_event"
