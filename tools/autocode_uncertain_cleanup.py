"""Recheck an uncertain verification cleanup without accepting its result."""

from __future__ import annotations

import os

try:
    from . import autocode_command_receipt as receipts
    from . import autocode_process as processes
    from . import autocode_util as util
except ImportError:
    import autocode_command_receipt as receipts
    import autocode_process as processes
    import autocode_util as util


def settle(bound):
    """Retire only this controller's exact, terminal scan-churn uncertainty.

    A fresh native scan must prove every recorded process and admitted group
    and session absent. This grants a fresh check, never a successful result.
    """
    check = bound["check"]
    metadata = bound["metadata"]
    path = metadata["receipt"]
    pin = bound["pins"][path]
    receipt = receipts.load(metadata, pin=pin)
    if (
        not receipt
        or receipt.get("phase") != "uncertain"
        or receipt.get("cause") != "controller_finished"
        or not str(receipt.get("cleanup_error") or "").startswith("SessionScanIncomplete:")
    ):
        return None
    owner, keeper, provider = (metadata[key] for key in ("owner", "keeper", "provider"))
    pid = os.getpid()
    if owner.get("pid") != pid or not processes.matches(owner, processes.process_table({pid}).get(pid)):
        return None
    if provider.get("group") != provider.get("pid") or provider.get("session") != provider.get("pid"):
        return None
    admissions = receipt.get("session_admissions", [])
    if not isinstance(admissions, list):
        return None
    rows = [provider, *receipt["processes"], *check["processes"]]
    rows = [row for row in rows if not (row.get("pid") == pid and processes.matches(owner, row))]
    if processes.live_processes([keeper, *rows]):
        return None
    groups = [row for row in rows if row.get("group") == row.get("pid")]
    tree = processes.ProcessTree(
        provider["pid"], lambda _rows: None, groups=groups, sessions=[provider], session_admissions=admissions
    )
    tree.known.update({row["pid"]: dict(row) for row in rows})
    if processes.settled_sample(tree, notify=False) or tree.groups or tree.sessions or tree.session_admissions:
        return None
    if receipts.load(metadata, pin=pin) != receipt or any(
        util.file_hash(name) != digest for name, digest in bound["pins"].items()
    ):
        return None
    return {"receipt": path, "receipt_sha256": pin, "cleanup_error": receipt["cleanup_error"]}


def settle_with_retry(bound):
    """Give a changing PID frontier a bounded chance to settle."""
    for attempt in range(3):
        try:
            return settle(bound)
        except processes.SessionScanIncomplete:
            if attempt == 2:
                raise
            util.sleep(0.2)
    return None
