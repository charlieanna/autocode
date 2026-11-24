"""Ownership admission for runner command evidence, independent of process runtime.

Only a collected command exit and pinned normal cleanup can supply proof. A
stopped interruption can permit a fresh attempt, but cannot establish a pass.
Legacy command receipts without supervision retain their existing behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_liveness as liveness
    from . import autocode_util as util
except ImportError:
    import autocode_liveness as liveness
    import autocode_util as util

# Indented identity rows for liveness.PROCESS_INVENTORY_LIMIT processes are about 3.6 MiB.
# The keeper and both readers share this ceiling so a receipt that is written can be loaded.
# AutoCode's own suite records roughly 8,000 processes and exceeds the old 1 MiB ceiling.
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
OWNERSHIP_FIELDS = ("supervision", "supervision_sha256", "supervision_errors")
NORMAL_CAUSES = frozenset({"provider_stopped", "controller_finished"})


class OwnershipUncertain(util.Paused):
    """Owned command cleanup is unverified; a new launch must remain blocked."""

    def __init__(self, reason):
        super().__init__("PAUSED_VERIFICATION_UNCERTAIN", reason)


def owned_relative(path, root) -> tuple[Path, Path] | None:
    """Return ``(canonical_root, relative)`` when ``path`` sits physically inside ``root``.

    Platform or caller ancestor aliases above ``root`` (such as macOS's ``/var`` -> ``/private/var``)
    may differ in spelling between ``path`` and ``root``, provided ``root`` itself is an absolute
    non-symlink directory and neither ``path`` nor any directory between ``path`` and ``root``
    traverses a symlink or ``..`` segment.
    """
    target, boundary = Path(path), Path(root)
    if (
        not target.is_absolute()
        or not boundary.is_absolute()
        or ".." in (*target.parts, *boundary.parts)
        or target.is_symlink()
        or boundary.is_symlink()
    ):
        return None
    canonical = boundary.resolve()
    prefix = next(
        (parent for parent in target.parents if not parent.is_symlink() and parent.resolve() == canonical),
        None,
    )
    if prefix is None or any(
        parent.is_symlink() for parent in target.parents if parent != prefix and parent.is_relative_to(prefix)
    ):
        return None
    return canonical, target.relative_to(prefix)


def pinned_bytes(path, digest, root, *, limit):
    """Read bounded immutable capture bytes through no-follow directory FDs."""
    if not isinstance(path, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("command artifact has no exact pin")
    if (
        not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))
        or os.open not in os.supports_dir_fd
    ):
        raise ValueError("descriptor-anchored command reads are unavailable on this host")
    owned = owned_relative(path, root)
    if owned is None:
        raise ValueError("command artifact path is not an ordinary owned path")
    canonical_root, relative = owned
    descriptor = os.open(canonical_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        leaf = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError("command artifact is not a bounded regular file")
            with os.fdopen(leaf, "rb", closefd=False) as stream:
                data = stream.read(limit + 1)
            after = os.fstat(leaf)
            if len(data) > limit or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("command artifact changed while being captured")
        finally:
            os.close(leaf)
    finally:
        os.close(descriptor)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("command artifact changed")
    return data


def load(metadata, *, root=None, pin=None):
    """Load one bounded, birth-bound receipt, optionally within a pinned root.

    The public liveness policy validates the same schema and process inventory
    used by status. No process is inspected and no saved data is changed here.
    Hash verification uses the very bytes that are parsed, not a second read.
    """
    try:
        if not isinstance(metadata, dict) or not isinstance(metadata.get("receipt"), str):
            return None
        path = Path(metadata["receipt"])
        if not path.is_absolute() or path.is_symlink():
            return None
        if root is not None:
            root = Path(root)
            if root.is_symlink():
                return None
            root = root.resolve()
            if not path.resolve().is_relative_to(root):
                return None
        if any(parent.is_symlink() for parent in path.parents):
            return None
        if pin is not None and (not isinstance(pin, str) or re.fullmatch("[0-9a-f]{64}", pin) is None):
            return None
        with path.open("rb") as stream:
            data = stream.read(MAX_RECEIPT_BYTES + 1)
        if len(data) > MAX_RECEIPT_BYTES or (pin is not None and hashlib.sha256(data).hexdigest() != pin):
            return None
        return liveness.classify(metadata, {"receipt": json.loads(data)})["receipt"]
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return None


def completed(receipt, *, root=None):
    """Whether an executed command can contribute evidence, including failure.

    A normal nonzero exit is valid base-failure evidence. Timeout, interruption,
    missing collection and keeper failure never authorize a proof or its reuse.
    An explicitly present invalid supervision value is never a legacy receipt.
    """
    if not isinstance(receipt, dict):
        return False
    present = set(OWNERSHIP_FIELDS) & set(receipt)
    if not present:
        return True
    if present != set(OWNERSHIP_FIELDS):
        return False
    pin = receipt.get("supervision_sha256")
    if not isinstance(pin, str) or re.fullmatch("[0-9a-f]{64}", pin) is None:
        return False
    errors = receipt.get("supervision_errors")
    if not isinstance(errors, list) or errors:
        return False
    if (
        type(receipt.get("exit_code")) is not int
        or receipt.get("timed_out")
        or receipt.get("interrupted")
        or receipt.get("error")
    ):
        return False
    value = load(receipt["supervision"], root=root, pin=pin)
    return bool(
        value and value["phase"] == "stopped" and not value["cleanup_error"] and value["cause"] in NORMAL_CAUSES
    )


def encoded_receipt(value):
    """The bytes ``atomic_json`` writes. The keeper refuses to publish anything larger."""
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def within_reader_limit(value):
    processes = value.get("processes") if isinstance(value, dict) else None
    if isinstance(processes, list) and len(processes) > liveness.PROCESS_INVENTORY_LIMIT:
        return False
    return len(encoded_receipt(value)) <= MAX_RECEIPT_BYTES


def cleanup_complete(metadata):
    """A terminal cleanup permits retry even when its result is interrupted.

    This is an ownership fact, never an exit-code or test-success assertion.
    Runtime callers must separately native-check recorded processes and keeper.
    """
    value = load(metadata)
    return bool(value and value["phase"] == "stopped" and not value["cleanup_error"])


def project(receipt):
    """Retain the optional ownership extension, including invalid partial input."""
    return (
        {name: deepcopy(receipt[name]) for name in OWNERSHIP_FIELDS if name in receipt}
        if isinstance(receipt, dict)
        else {}
    )


def pins(receipt):
    """Keep the recorded ownership pin even when its file has since disappeared."""
    if not isinstance(receipt, dict):
        return {}
    metadata = receipt.get("supervision")
    pin = receipt.get("supervision_sha256")
    if (
        not isinstance(metadata, dict)
        or not isinstance(metadata.get("receipt"), str)
        or not metadata["receipt"]
        or not isinstance(pin, str)
        or re.fullmatch("[0-9a-f]{64}", pin) is None
    ):
        return {}
    return {metadata["receipt"]: pin}


def result_pins(result):
    """The completion-evidence pins of an acceptance result (original brief or risk): each check's output
    and supervision receipt, and the result's summary. One reading both acceptance gates share."""
    result = result or {}
    checks = result.get("checks", [])
    found = {row["output"]: row["output_sha256"] for row in checks if row.get("output") and row.get("output_sha256")}
    for row in checks:
        found.update(pins(row))
    if result.get("summary") and result.get("summary_sha256"):
        found[result["summary"]] = result["summary_sha256"]
    return found
