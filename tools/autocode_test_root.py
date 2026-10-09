"""Caller-selected test-suite roots, never a generic write boundary.

Saved once in settings.regression.test_root at launch. Regression detection and
proof use the root; component admission also corroborates it against the original
generated task. A rooted suite cannot prove changes outside that directory.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
from contextlib import ExitStack
from pathlib import Path

_SEGMENT = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.@+-]*")
POLICY_MAX_BYTES = 1024 * 1024


def normalize(value) -> str:
    """A plain relative POSIX directory, without a trailing slash or pathspec magic."""
    text = str(value)
    parts = text[:-1].split("/") if text.endswith("/") else text.split("/")
    if not all(_SEGMENT.fullmatch(part) and part not in (".", "..") for part in parts):
        raise ValueError(f"--test-root must be a directory inside the workspace, such as components/api/, "
                         f"named by plain segments of letters, digits, '.', '_', '-', '+' and '@': {text!r}")
    return "/".join(parts)


def outside(root: str, paths) -> list[str]:
    """Paths not strictly inside root; a file or link at root itself is outside."""
    return sorted(path for path in paths if not path.startswith(root + "/"))


def inside(root: str, paths) -> list[str]:
    """Paths strictly inside root, relative to it."""
    return [path[len(root) + 1:] for path in paths if path.startswith(root + "/")]


def unittest_start(root: str, directory: Path, tests) -> str:
    """Apply the project-root discovery rule within the caller's directory."""
    if (any("/" not in path for path in tests) or (directory / "tests" / "__init__.py").is_file()
            or (directory / "test" / "__init__.py").is_file()):
        return root
    return next((f"{root}/{name}" for name in ("tests", "test") if (directory / name).is_dir()), root)


def read_policy(workspace: Path, path: Path) -> str:
    """Read current policy through bounded relative links without following host paths.

    Missing ordinary policy is empty; an unresolved link or unreadable existing
    policy is unknown. Directory descriptors prevent a replaced path from escaping
    the workspace between link validation and opening its bytes. Policies over
    one MiB are unknown; the read itself is bounded even if the file grows.
    """
    pending, resolved, links, seen = list(path.relative_to(workspace).parts), [], 0, set()
    with ExitStack() as stack:
        root = os.open(workspace.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, root)
        directories = [root]
        while pending:
            part = pending.pop(0)
            if part == ".":
                continue
            if part == "..":
                if not resolved:
                    raise ValueError("Current policy escapes the repository")
                resolved.pop()
                directories.pop()
                continue
            try:
                entry = os.stat(part, dir_fd=directories[-1], follow_symlinks=False)
            except FileNotFoundError:
                if links:
                    raise ValueError("Current policy link target is absent")
                return ""
            if stat.S_ISLNK(entry.st_mode):
                step = (tuple(resolved), part, tuple(pending))
                if step in seen:
                    raise ValueError("Current policy link contains a loop")
                seen.add(step)
                links += 1
                target = os.readlink(part, dir_fd=directories[-1])
                if links > 32 or not target or target.startswith("/") or "\0" in target:
                    raise ValueError("Current policy link is external or exceeds the resolution limit")
                pending = [piece for piece in target.split("/") if piece] + (["."] if target.endswith("/") else []) + pending
            elif stat.S_ISDIR(entry.st_mode) and pending:
                directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directories[-1])
                stack.callback(os.close, directory)
                directories.append(directory)
                resolved.append(part)
            elif stat.S_ISREG(entry.st_mode) and not pending:
                descriptor = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=directories[-1])
                with os.fdopen(descriptor, "rb") as policy:
                    current = os.fstat(policy.fileno())
                    if not stat.S_ISREG(current.st_mode):
                        raise ValueError("Current policy changed to a nonregular file")
                    if current.st_size > POLICY_MAX_BYTES:
                        raise ValueError("Current policy exceeds the one MiB read limit")
                    content = policy.read(POLICY_MAX_BYTES + 1)
                    if len(content) > POLICY_MAX_BYTES:
                        raise ValueError("Current policy exceeds the one MiB read limit")
                    return content.decode("utf-8", errors="replace")
            else:
                raise ValueError("Current policy is not a regular file")
    raise ValueError("Current policy resolves to a directory")


def empty_on_base(workspace, base: str, root: str) -> bool:
    """True only when the pinned base holds nothing under root; Git errors are False."""
    try:
        listing = subprocess.run(["git", "--literal-pathspecs", "ls-tree", "-r", "-z", "--name-only", base, "--",
                                  root + "/"], cwd=workspace, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return listing.returncode == 0 and not listing.stdout.strip(b"\0")


def first_suite(base_receipt: dict, candidate_receipt: dict) -> bool:
    """Execution evidence for an empty pinned root, not a passing baseline.

    The caller also requires a detected rooted suite and fully attributed results.
    """
    base = base_receipt.get("results")
    candidate = candidate_receipt.get("results") or {}
    ran_nothing = base is None or (not base.get("total") and not any(
        base.get(key) for key in ("passed", "failed", "skipped", "collection_errors", "uncollected")))
    return bool(ran_nothing
                and base_receipt.get("timed_out") is False
                and candidate_receipt.get("timed_out") is False
                and candidate_receipt.get("exit_code") == 0
                and candidate.get("complete") is True
                and candidate.get("passed")
                and not candidate.get("failed") and not candidate.get("skipped")
                and not candidate.get("collection_errors"))
