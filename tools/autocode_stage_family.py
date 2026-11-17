"""Bind a supervised provider attempt to an enclosing capture's family proof."""

from __future__ import annotations

import os
import stat
import sys
import uuid
from pathlib import Path

try:
    from . import autocode_capture_family as family
except ImportError:
    import autocode_capture_family as family


def _boot():
    value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    if str(uuid.UUID(value)) != value:
        raise ValueError("Invalid boot identity")
    return value


def _root(*, create):
    import pwd

    home = Path(pwd.getpwuid(os.geteuid()).pw_dir).resolve()
    root = home / ".local" / "state" / "autocode" / "stage-family"
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        info = root.lstat()
    except FileNotFoundError:
        if not create:
            return root.resolve()
        raise
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("Stage-family evidence store is not private")
    return root.resolve()


def enroll(receipt_path, nonce, *, cwd=None):
    """Enroll before provider release; an absent capture adds no recovery authority."""
    if sys.platform != "linux" or not os.environ.get(family.ENVIRONMENT_KEY) or cwd is None:
        return None, None
    boot = _boot()
    try:
        root = _root(create=False)
    except (OSError, KeyError, ValueError):
        return None, None  # The ordinary keeper remains in charge when no private store is available.
    try:
        workspace = Path(cwd).resolve()
    except (OSError, TypeError, ValueError):
        return None, None
    if root.is_relative_to(workspace):
        return None, None
    for parent in Path(receipt_path).resolve().parents:
        if parent.name == ".autocode":
            if root.is_relative_to(parent.parent):
                return None, None
            break
    try:
        if _root(create=True) != root:
            return None, None
    except (OSError, KeyError, ValueError):
        return None, None
    registration = family.register_backup(root, nonce, None, boot_id=boot)
    if not registration.enrolled:
        return None, registration
    return {"boot_id": boot, "directory": str(root)}, registration


def clean(metadata):
    """Read only a same-boot family certificate bound to this attempt's nonce."""
    if sys.platform != "linux" or not isinstance(metadata, dict):
        return False
    binding = metadata.get("family_backup")
    if not isinstance(binding, dict) or set(binding) != {"boot_id", "directory"}:
        return False
    try:
        boot = _boot()
        root = _root(create=False)
        if (
            binding["boot_id"] != boot
            or binding["directory"] != str(root)
            or not isinstance(metadata.get("receipt"), str)
        ):
            return False
        return family.backup_clean(root, metadata.get("nonce"), boot)
    except (OSError, KeyError, ValueError, TypeError, family.FamilyError):
        return False
