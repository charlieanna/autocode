"""Read Git placement facts without turning a failed query into permission to create a repository."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def _query(folder, *args):
    try:
        return subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(folder), *args],
            capture_output=True,
            text=True,
            timeout=30,
            env={**util.git_environment(), "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"Cannot inspect Git in {folder}: Git did not answer within 30 seconds") from error
    except OSError as error:
        raise ValueError(f"Cannot inspect Git in {folder}: {error}") from error


def _failed(folder, result):
    # Keep the actual failure, without Git's following setup commands becoming instructions in a client.
    reason = result.stderr.strip().splitlines()
    detail = reason[0] if reason else f"Git exited with status {result.returncode}"
    return ValueError(f"Cannot inspect Git in {folder}: {detail}")


def _metadata_present(folder):
    for parent in (Path(folder), *Path(folder).parents):
        try:
            (parent / ".git").lstat()
            return True
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ValueError(f"Cannot inspect Git in {folder}: {error}") from error
    return False


def root(folder) -> Path | None:
    """A checkout's root, or None only when Git reports no repository and none has metadata here.

    Git uses exit 128 for both an absent repository and failures such as ownership refusals or
    corrupt metadata. Its C-locale missing-repository diagnostic and the absence of a .git entry
    must agree before a caller may offer initialization. A broken linked worktree is an error too.
    """
    result = _query(folder, "rev-parse", "--show-toplevel")
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    if (
        result.returncode == 128
        and result.stderr.startswith("fatal: not a git repository (or any")
        and not _metadata_present(folder)
    ):
        return None
    raise _failed(folder, result)


def has_ref(folder, name) -> bool:
    result = _query(folder, "rev-parse", "--verify", "--quiet", name)
    if result.returncode in (0, 1):
        return result.returncode == 0
    raise _failed(folder, result)


def read(folder, *args) -> str:
    result = _query(folder, *args)
    if result.returncode:
        raise _failed(folder, result)
    return result.stdout.strip()
