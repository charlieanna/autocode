"""A proof command cannot read the live workspace or another proof copy (#4152, phase 3).

Phases 1 and 2 hold what the source observer sees resolve outside the proof copy and give the copy
custody of its installed dependencies. A test can still name the live tree itself (an absolute
path, a fixture link, an ancestor a tool walks up to that the observer does not model), and a copy
of the original code then reads the candidate. This module puts each proof command inside a kernel
read boundary where the host has one:

- **hidden**: every other checkout of the repository, as Git lists its worktrees (``copy_of``): the
  live workspace, the main checkout, other chats' worktrees and the other proof copies;
- **visible again inside them**: the copy itself and its Git directories (a proof copy is a Git
  worktree of the live repository), the directory the command writes its results to, the
  installed dependency directories the copy links to (``.venv``, ``venv``, ``node_modules``) and
  the copy's own dependency views.

The most specific rule wins: a hidden path inside a visible one is hidden again, so a task
worktree (a workspace inside its main checkout's store) stays hidden even where the store's run
directory is visible. Nothing else changes: writes, the network, processes and the environment
are the command's as before.

The backends are the agent sandbox's (``autocode_agent_sandbox``): Linux bubblewrap, the hidden
paths covered by an empty tmpfs and the visible ones bound back from the host; macOS Seatbelt,
denying file reads under each hidden path and allowing them again under each visible one, in the
same order. Where no boundary can be set up (no bubblewrap able to create its namespaces, no
sandbox-exec, another platform), the command runs as before and its receipt records why, owner
decision 2026-11-25: phases 1 and 2 still hold or prevent the escapes they see, and holding every
proof would stop AutoCode on such a host. ``record`` is what the receipt keeps.

Bubblewrap runs in a user namespace that keeps the caller's access to files (``autocode_userns``): for
root, one that maps every id, since bubblewrap's own maps only root and so took away root's access to
files other users own.

Imports only autocode_agent_sandbox, autocode_dependency_dirs, autocode_scratch_location and
autocode_userns, none of which imports AutoCode.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from . import autocode_agent_sandbox as agent_sandbox
    from . import autocode_dependency_dirs as dependency_dirs
    from . import autocode_scratch_location as scratch_location
    from . import autocode_userns as userns
except ImportError:
    import autocode_agent_sandbox as agent_sandbox
    import autocode_dependency_dirs as dependency_dirs
    import autocode_scratch_location as scratch_location
    import autocode_userns as userns

HIDE, SHOW = "hide", "show"
# What follows the user namespace (autocode_userns), which keeps root's access to other users' files.
_BWRAP = ("--die-with-parent", "--bind", "/", "/", "--dev-bind", "/dev", "/dev")
_PROBE_PROFILE = "(version 1)(allow default)"


@functools.cache
def problem(platform=None):
    """Why this host cannot set up the read boundary, or None. Probed once per process."""
    platform = platform or sys.platform
    host = agent_sandbox.backend(platform)
    if host is None:
        return f"no read boundary is available on {platform}"
    if platform != sys.platform:
        return None
    if host == agent_sandbox.BUBBLEWRAP:
        return _bubblewrap_problem()
    if not Path(agent_sandbox.SANDBOX_EXEC).is_file():
        return f"the read boundary needs macOS sandbox-exec, which is missing at {agent_sandbox.SANDBOX_EXEC}"
    try:
        probe = subprocess.run(
            [agent_sandbox.SANDBOX_EXEC, "-p", _PROBE_PROFILE, "/bin/sh", "-c", ":"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"the read boundary could not run {agent_sandbox.SANDBOX_EXEC}: {error}"
    if probe.returncode:
        detail = (probe.stderr.strip().splitlines() or ["no message"])[-1][:300]
        # A process already inside a Seatbelt sandbox (a Codex shell) cannot start another.
        return f"{agent_sandbox.SANDBOX_EXEC} cannot start a sandbox here (exit {probe.returncode}: {detail})"
    return None


def _bubblewrap_problem():
    """Why bubblewrap cannot start the boundary's namespace here, probed with the command line a proof uses."""
    if not Path(agent_sandbox.BWRAP).is_file():
        return f"the read boundary needs Linux bubblewrap at {agent_sandbox.BWRAP} (install the bubblewrap package)"
    try:
        probe = subprocess.run(
            [*bubblewrap_arguments([]), "/bin/sh", "-c", ":"],
            capture_output=True,
            text=True,
            timeout=15,
            env={"PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"the read boundary could not run {agent_sandbox.BWRAP}: {error}"
    if probe.returncode:
        detail = (probe.stderr.strip().splitlines() or ["no message"])[-1][:300]
        return (
            f"{agent_sandbox.BWRAP} cannot create the namespace the read boundary needs (exit {probe.returncode}: "
            f"{detail}); this host may deny user namespaces"
        )
    return None


def _real(path) -> Path:
    return Path(os.path.realpath(path))


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _dependency_links(tree: Path):
    """The copy's installed-dependency entries, relative to it, and where each link leads."""
    found = []
    for directory, dirs, files in os.walk(tree, followlinks=False):
        for name in (*dirs, *files):
            if name in dependency_dirs.NAMES:
                path = Path(directory) / name
                found.append((path.relative_to(tree), path))
        dirs[:] = [name for name in dirs if name not in (*dependency_dirs.NAMES, ".git", ".autocode")]
    return found


def plan(tree, *, hidden, outputs=()):
    """The ordered (rule, path) list for a command run in the copy ``tree``, or None when nothing is hidden.

    ``hidden`` are the live checkouts and other copies, ``outputs`` the directories the command writes
    its results to. Paths are resolved; a deeper rule comes later, so it overrides a shallower one.
    """
    tree = _real(tree)
    hidden = {_real(path) for path in hidden if Path(path).is_dir()}
    hidden = {path for path in hidden if not _inside(path, tree)}
    home = _real(Path.home())
    if not hidden or any(_inside(home, path) for path in hidden):
        # A checkout that holds the home directory (or is /) would hide the interpreter and its caches.
        return None
    shown = {tree, *(_real(path) for path in agent_sandbox._git_dirs(tree))}
    shown |= {_real(path) for path in outputs if Path(path).is_dir()}
    for relative, path in _dependency_links(tree):
        if path.is_symlink():
            shown.add(_real(path))  # the copy's own view of the directory
        for root in hidden:
            if (root / relative).is_dir():
                shown.add(_real(root / relative))  # what the view links to
    shown = {path for path in shown - hidden if any(_inside(path, root) for root in hidden)}
    rules = [(HIDE, path) for path in hidden] + [(SHOW, path) for path in shown]
    return sorted(rules, key=lambda rule: (len(rule[1].parts), rule[0] == SHOW, str(rule[1])))


def _git(cwd, *arguments):
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *arguments], capture_output=True, text=True, timeout=30, env=_GIT_ENV
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


_GIT_ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"}


def copy_of(cwd):
    """(the proof copy ``cwd`` is in, every other checkout of its repository), or None.

    A proof copy is a Git worktree of the live repository kept under a scratch mirror
    (autocode_scratch_location), so Git itself lists what it must not read: the live workspace, the
    main checkout, other chats' worktrees and the other proof copies. A directory that is not such a
    copy (the live workspace itself, a test fixture's tree) gets no boundary.
    """
    top = _git(cwd, "rev-parse", "--show-toplevel")
    if not top or not any(part.endswith(scratch_location.SUFFIX) for part in Path(top.strip()).parts):
        return None
    tree = _real(top.strip())
    listing = _git(tree, "worktree", "list", "--porcelain", "-z")
    if listing is None:
        return None
    others = [
        Path(entry[len("worktree ") :])
        for entry in listing.split("\0")
        if entry.startswith("worktree ") and _real(entry[len("worktree ") :]) != tree
    ]
    return tree, others


def for_command(cwd, *, outputs=(), platform=None):
    """The argv prefix that runs a proof command started in ``cwd`` inside the boundary, and its record.

    The prefix is empty, and the record says why, where there is no boundary to set up.
    """
    found = copy_of(cwd)
    if found is None:
        return [], {"backend": None, "reason": "the command does not run in a proof copy"}
    tree, others = found
    return wrap([], tree, hidden=others, outputs=outputs, platform=platform)


def bubblewrap_arguments(rules):
    arguments = [*userns.bubblewrap(agent_sandbox.BWRAP), *_BWRAP]
    for rule, path in rules:
        arguments += ["--tmpfs", str(path)] if rule == HIDE else ["--bind", str(path), str(path)]
    return [*arguments, "--"]


def seatbelt_profile(rules):
    """Reads denied under each hidden path and allowed again under each visible one, in rule order:
    Seatbelt applies the last rule that matches, so the deeper rule wins as it does with bubblewrap.
    Only file contents and listings are denied (``file-read-data``): a path through a hidden
    directory still resolves, so the copy's links into installed dependencies keep working."""
    lines = ["(version 1)", "(allow default)"]
    for rule, path in rules:
        verb = "deny" if rule == HIDE else "allow"
        lines.append(f"({verb} file-read-data (subpath {json.dumps(str(path), ensure_ascii=False)}))")
    return "\n".join([*lines, ""])


def wrap(argv, tree, *, hidden, outputs=(), platform=None):
    """``argv`` inside the read boundary and the record its receipt keeps.

    Without a boundary on this host, or with nothing to hide, ``argv`` is returned unchanged and the
    record says why. Never raises for a host that cannot sandbox: the command still runs.
    """
    platform = platform or sys.platform
    trouble = problem(platform)
    if trouble:
        return list(argv), {"backend": None, "reason": trouble}
    rules = plan(tree, hidden=hidden, outputs=outputs)
    if rules is None:
        return list(argv), {"backend": None, "reason": "no other checkout can be hidden from this copy"}
    record = {
        "backend": agent_sandbox.backend(platform),
        "hidden": [str(path) for rule, path in rules if rule == HIDE],
        "shown": [str(path) for rule, path in rules if rule == SHOW],
    }
    if record["backend"] == agent_sandbox.BUBBLEWRAP:
        return [*bubblewrap_arguments(rules), *argv], record
    return [agent_sandbox.SANDBOX_EXEC, "-p", seatbelt_profile(rules), *argv], record
