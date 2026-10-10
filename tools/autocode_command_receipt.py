"""Ownership admission for runner command evidence, independent of process runtime.

Only a collected command exit and pinned normal cleanup can supply proof. A
stopped interruption can permit a fresh attempt, but cannot establish a pass.
Legacy receipts remain supported when they contain a normal integer exit; an
empty, timed-out, interrupted or errored receipt cannot establish a pass.
"""
from __future__ import annotations

import hashlib
import json
import re
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
OWNERSHIP_FIELDS = ('supervision', 'supervision_sha256', 'supervision_errors')
NORMAL_CAUSES = frozenset({'provider_stopped', 'controller_finished'})


class OwnershipUncertain(util.Paused):
    """Owned command cleanup is unverified; a new launch must remain blocked."""

    def __init__(self, reason):
        super().__init__('PAUSED_VERIFICATION_UNCERTAIN', reason)


def load(metadata, *, root=None, pin=None):
    """Load one bounded, birth-bound receipt, optionally within a pinned root.

    The public liveness policy validates the same schema and process inventory
    used by status. No process is inspected and no saved data is changed here.
    Hash verification uses the very bytes that are parsed, not a second read.
    """
    try:
        if not isinstance(metadata, dict) or not isinstance(metadata.get('receipt'), str):
            return None
        path = Path(metadata['receipt'])
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
        if pin is not None and (not isinstance(pin, str) or re.fullmatch('[0-9a-f]{64}', pin) is None):
            return None
        with path.open('rb') as stream:
            data = stream.read(MAX_RECEIPT_BYTES + 1)
        if len(data) > MAX_RECEIPT_BYTES or (pin is not None and hashlib.sha256(data).hexdigest() != pin):
            return None
        return liveness.classify(metadata, {'receipt': json.loads(data)})['receipt']
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
    if (type(receipt.get('exit_code')) is not int or receipt.get('timed_out')
            or receipt.get('interrupted') or receipt.get('error')):
        return False
    present = set(OWNERSHIP_FIELDS) & set(receipt)
    if not present:
        return True
    if present != set(OWNERSHIP_FIELDS):
        return False
    pin = receipt.get('supervision_sha256')
    if not isinstance(pin, str) or re.fullmatch('[0-9a-f]{64}', pin) is None:
        return False
    errors = receipt.get('supervision_errors')
    if not isinstance(errors, list) or errors:
        return False
    value = load(receipt['supervision'], root=root, pin=pin)
    return bool(value and value['phase'] == 'stopped' and not value['cleanup_error']
                and value['cause'] in NORMAL_CAUSES)


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
    return bool(value and value['phase'] == 'stopped' and not value['cleanup_error'])


def project(receipt):
    """Retain the optional ownership extension, including invalid partial input."""
    return ({name: deepcopy(receipt[name]) for name in OWNERSHIP_FIELDS if name in receipt}
            if isinstance(receipt, dict) else {})


def pins(receipt):
    """Keep the recorded ownership pin even when its file has since disappeared."""
    if not isinstance(receipt, dict):
        return {}
    metadata = receipt.get('supervision')
    pin = receipt.get('supervision_sha256')
    if (not isinstance(metadata, dict) or not isinstance(metadata.get('receipt'), str)
            or not metadata['receipt'] or not isinstance(pin, str)
            or re.fullmatch('[0-9a-f]{64}', pin) is None):
        return {}
    return {metadata['receipt']: pin}
