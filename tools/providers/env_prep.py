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
nothing from AutoCode, so it cannot join the import cycle listed in
tests/test_architecture.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping


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
    return subprocess.run(command, env=dict(env), **kwargs)


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
