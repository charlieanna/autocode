"""Explicit provider-environment preparation (the issue #226 follow-up).

A pilot launcher once built its child environment after clearing the process
environment, so PATH was lost before provider preflight could run (GitHub
issue #226). The durable contract is the opposite order: accept an explicit
environment mapping -- or take one snapshot of the process environment -- and
construct the complete child environment from it before any preflight or
launch subprocess is spawned.

Nothing here mutates ``os.environ``. Discovery and child construction use an
explicit mapping or :func:`snapshot_environment`. Parallel workers pass their
own mappings; billing admission deliberately checks both the explicit mapping
and the process environment through :func:`combined_environment`.

Pure functions over mappings, standard library only. This module imports
nothing from AutoCode, so it cannot create an import cycle.

Within a run (``reuse_checks``), a preflight that succeeded is answered from the
run directory for ``REUSE_SECONDS``. A chat message relaunches the controller,
and the same `kilo --version`, `kilo models` and `kilo auth list` cost 6.4 s of
a 28 s follow-up turn on 2026-10-20. Reuse is keyed by the exact command, working
directory, the complete environment and the executable's resolved file
identity, so a new tool install, a changed setting or another folder runs the
check again; a failed check is never kept.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path

REUSE_SECONDS = 3600  # owner decision 2026-10-20: an hour
REUSE_FOLDER = "preflight-reuse"
_reuse: Path | None = None


def snapshot_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The effective environment: a copy of ``env``, or one snapshot of ``os.environ``.

    Callers that pass a mapping get an independent copy. Discovery and launch
    cannot observe a later mutation of the mapping handed over; billing guards
    separately inspect the ambient environment as well.
    """
    return dict(env) if env is not None else dict(os.environ)


def child_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """A complete child environment for launch, constructed from the effective environment."""
    return snapshot_environment(env)


def resolve_executable(name: str, env: Mapping[str, str], *, cwd=None, allow_default_path=False) -> str | None:
    """Find ``name`` on ``env``'s PATH, never the process PATH.

    Absolute executables do not require PATH. Bare names without a declared
    PATH fail admission rather than falling back to the process or system PATH.
    """
    path = env.get("PATH")
    if path is None and allow_default_path:
        try:
            path = os.confstr("CS_PATH")
        except (AttributeError, ValueError):
            path = os.defpath
    if os.path.dirname(name):
        target = os.path.abspath(os.path.join(cwd, name)) if cwd is not None else name
        return shutil.which(target, path="")
    if path is None:
        return None
    if cwd is not None:
        path = os.pathsep.join(os.path.abspath(os.path.join(cwd, part)) for part in path.split(os.pathsep))
    # An explicitly empty PATH is one empty component: POSIX exec searches
    # the child's working directory. It is different from an absent PATH.
    return shutil.which(name, path=path or os.curdir)


def preflight_run(command, env: Mapping[str, str], *, require_executable=False, **kwargs):
    """Run a preflight subprocess (version, roster, auth summary) with the explicit environment.

    Explicit-environment callers require executable admission before spawn;
    this avoids execvp's default system PATH when the mapping omits PATH. The
    ambient API retains its existing behavior. The caller owns timeouts and
    failure interpretation; no retry is added here.
    """
    if require_executable and not resolve_executable(command[0], env, cwd=kwargs.get("cwd")):
        raise FileNotFoundError(f"{command[0]!r} cannot be found in the explicit environment")
    key = _reuse_key(command, env, kwargs)
    kept = _recall(key, command)
    if kept is not None:
        return kept
    result = subprocess.run(command, env=dict(env), **kwargs)
    if key and result.returncode == 0:
        _keep(key, result)
    return result


@contextlib.contextmanager
def reuse_checks(run_dir):
    """While one run is driven, reuse its successful preflights (see the module docstring)."""
    global _reuse
    previous, _reuse = _reuse, Path(run_dir) / REUSE_FOLDER
    try:
        yield
    finally:
        _reuse = previous


def _reuse_key(command, env: Mapping[str, str], kwargs) -> str | None:
    if _reuse is None or not kwargs.get("capture_output") or not kwargs.get("text") or "input" in kwargs:
        return None
    executable = resolve_executable(command[0], env, cwd=kwargs.get("cwd"))
    if not executable:
        return None
    try:
        real = os.path.realpath(executable)
        info = os.stat(real)
    except OSError:
        return None
    identity = [list(command), str(kwargs.get("cwd")), sorted(env.items()), real, info.st_mtime_ns, info.st_size]
    return hashlib.sha256(json.dumps(identity, default=str).encode()).hexdigest()


def _recall(key, command):
    if key is None or _reuse is None:
        return None
    try:
        kept = json.loads((_reuse / f"{key}.json").read_text(encoding="utf-8"))
        if 0 <= time.time() - kept["at"] < REUSE_SECONDS:
            return subprocess.CompletedProcess(command, 0, kept["stdout"], kept["stderr"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _keep(key, result) -> None:
    if _reuse is None:
        return
    with contextlib.suppress(OSError):
        _reuse.mkdir(mode=0o700, exist_ok=True)
        temporary = _reuse / f".{key}.{os.getpid()}"
        temporary.write_text(json.dumps({"at": time.time(), "stdout": result.stdout, "stderr": result.stderr}))
        temporary.chmod(0o600)
        os.replace(temporary, _reuse / f"{key}.json")


def combined_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The explicit mapping and the process environment together.

    Admission guards (API-key and billing-route checks) must never examine less
    than they did before explicit environments existed: a forbidden variable
    counts when it is set in either the mapping or the process environment.
    """
    combined = dict(os.environ)
    if env is not None:
        combined.update(env)
    return combined
