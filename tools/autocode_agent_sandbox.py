"""An OS sandbox around the whole agent process of a stage: ``--agent-sandbox`` (opt-in).

Built-in OpenCode stages already run their shell inside the kernel tool boundary
(``autocode_tool_containment``). Claude Code, Codex and configured command providers do not:
their own permission checks decide what the model's shell may read, so a model could read
``~/.aws/credentials`` or ``~/.ssh/id_ed25519`` and paste it into a report or a test. With
``agent_sandbox`` saved in a run's settings, ``wrap()`` runs the provider's whole command (the
client, every tool it starts and every test those tools run) inside one kernel sandbox:

- **reads**: everything the user can read, except the credential stores in ``CREDENTIALS``
  (home-relative) and the login files of the other engines (``ENGINE_LOGINS``);
- **writes**: only the stage's workspace (and its Git directory), the run directory, the
  temporary directories, the launched engine's own state (``ENGINE_STATE``) and the package
  caches (``CACHES``). Every other path is read-only.

The two backends are the ones the tool boundary uses: Linux bubblewrap (``/usr/bin/bwrap``: the
root mounted read-only, writable paths bound back, credential directories covered by an empty
read-only tmpfs and credential files by ``/dev/null``) and macOS Seatbelt (``/usr/bin/sandbox-exec``
with a profile that denies those reads and every write outside the writable paths). No network,
PID or user-visible session namespace is added, so the provider still reaches its model and the
runner's process supervision is unchanged.

Fail-safe: the sandbox is off unless the person asked for it, so existing runs launch exactly as
before; once asked for, a stage that cannot be sandboxed never launches unsandboxed
(``Unavailable``, which the launcher turns into ``PAUSED_TOOL_CONTAINMENT``), and run setup refuses
the flag on a host that cannot provide it (``problem()``).

The agent still reads its own engine's login (it needs it to call its model) and the
environment AutoCode hands it, from which ``autocode_agent_env`` withholds credential variables.

Imports nothing from AutoCode (its RunState annotation only while type checking).
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

FLAG = "--agent-sandbox"
SETTING = "agent_sandbox"
EVENT = "agent_sandbox_enabled"
BWRAP = "/usr/bin/bwrap"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
SEATBELT, BUBBLEWRAP = "seatbelt", "bubblewrap"

# Home-relative credential stores: cloud and cluster logins, SSH and GPG keys, package-registry and
# Git tokens, password managers, browser profiles (cookies) and shell histories (pasted secrets).
CREDENTIALS = (
    ".aws",
    ".azure",
    ".boto",
    ".config/gcloud",
    ".oci",
    ".kube",
    ".docker/config.json",
    ".ssh",
    ".gnupg",
    ".netrc",
    ".git-credentials",
    ".config/git/credentials",
    ".config/gh",
    ".config/hub",
    ".npmrc",
    ".pypirc",
    ".gem/credentials",
    ".cargo/credentials",
    ".cargo/credentials.toml",
    ".m2/settings.xml",
    ".m2/settings-security.xml",
    ".gradle/gradle.properties",
    ".terraform.d/credentials.tfrc.json",
    ".vault-token",
    ".config/doctl",
    ".config/op",
    ".password-store",
    ".local/share/keyrings",
    ".pgpass",
    ".my.cnf",
    ".s3cfg",
    ".databrickscfg",
    ".config/rclone",
    ".mozilla",
    ".config/google-chrome",
    ".config/chromium",
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/Firefox",
    "Library/Cookies",
    ".bash_history",
    ".zsh_history",
    ".python_history",
)
# Each engine's own state, written by its client (sessions, settings, refreshed logins).
ENGINE_STATE = {
    "claude": (".claude", ".claude.json", "Library/Keychains"),
    "codex": (".codex",),
    "opencode": (".config/opencode", ".local/share/opencode", ".local/state/opencode"),
    "kilocode": (".config/kilo", ".local/share/kilo", ".local/state/kilo", ".kilocode"),
}
# The login file of each engine, unreadable to a stage that another engine runs.
ENGINE_LOGINS = {
    "claude": (".claude/.credentials.json",),
    "codex": (".codex/auth.json",),
    "opencode": (".local/share/opencode/auth.json",),
    "kilocode": (".local/share/kilo/auth.json",),
}
# Package caches the project's own build and test commands write.
CACHES = (
    ".cache",
    "Library/Caches",
    ".npm",
    ".yarn",
    ".pnpm-store",
    ".bun/install/cache",
    "go/pkg/mod",
    ".cargo/registry",
    ".cargo/git",
    ".gradle",
    ".m2/repository",
)
LINUX_TEMP = ("/tmp", "/var/tmp", "/dev/shm")
# An explicit user namespace: the CLI drops CAP_SYS_ADMIN from a root runner, and bwrap run by root
# without it creates no namespace of its own. Ordinary users get one anyway.
_BWRAP_BASE = (BWRAP, "--unshare-user", "--die-with-parent", "--ro-bind", "/", "/", "--dev-bind", "/dev", "/dev")
MACOS_TEMP = ("/private/tmp", "/private/var/tmp", "/private/var/folders", "/dev")


class Unavailable(RuntimeError):
    """The stage was asked to run sandboxed and cannot be; nothing was launched."""


def enabled(settings) -> bool:
    return (settings or {}).get(SETTING) is True


def backend(platform=None):
    return {"darwin": SEATBELT, "linux": BUBBLEWRAP}.get(platform or sys.platform)


@functools.lru_cache(maxsize=1)
def _bubblewrap_problem():
    if not Path(BWRAP).is_file():
        return f"the agent sandbox needs Linux bubblewrap at {BWRAP} (install the bubblewrap package)"
    try:
        probe = subprocess.run(
            [*_BWRAP_BASE, "/bin/sh", "-c", ":"],
            capture_output=True,
            text=True,
            timeout=15,
            env={"PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"the agent sandbox could not run {BWRAP}: {error}"
    if probe.returncode:
        detail = (probe.stderr.strip().splitlines() or ["no message"])[-1][:300]
        return (
            f"{BWRAP} cannot create the mount namespace the agent sandbox needs (exit {probe.returncode}: "
            f"{detail}); this host may deny unprivileged user namespaces"
        )
    return None


def problem(settings=None, *, platform=None, codex=None):
    """Why this host cannot sandbox the run's agents, or None.

    ``codex`` says whether a stage runs on the Codex engine (read from ``settings`` when None): on
    macOS Codex applies its own Seatbelt sandbox to every command, and macOS refuses a second
    Seatbelt sandbox inside the first, so a Codex stage cannot run inside this one there.
    """
    platform = platform or sys.platform
    host = backend(platform)
    if host is None:
        return f"the agent sandbox needs macOS sandbox-exec or Linux bubblewrap; this machine is {platform}"
    if host == SEATBELT:
        if not Path(SANDBOX_EXEC).is_file():
            return f"the agent sandbox needs macOS sandbox-exec, which is missing at {SANDBOX_EXEC}"
        settings = settings or {}
        if codex is None:
            # An unset engine is Codex (autocode_quota_route.engine); a role may name its own.
            engines = {settings.get("engine", "codex")}
            engines.update(route.get("engine") for route in (settings.get("roles") or {}).values() if route)
            codex = "codex" in engines
        if codex:
            return (
                "the agent sandbox cannot contain Codex on macOS, which applies its own Seatbelt sandbox to each "
                "command and cannot start one inside another; use Claude Code or OpenCode, or Linux"
            )
        return None
    return _bubblewrap_problem() if platform == sys.platform else None


def _real(path) -> Path:
    return Path(os.path.realpath(path))


def _git_dirs(workspace):
    """A worktree's own Git directory and the common one it shares, where `git status` writes."""
    dot_git = Path(workspace) / ".git"
    if not dot_git.is_file():
        return []
    match = re.match(r"gitdir:\s*(.+)", dot_git.read_text(errors="replace").strip())
    if not match:
        return []
    own = (Path(workspace) / match.group(1).strip()).resolve()
    found = [own]
    common = own / "commondir"
    if common.is_file():
        found.append((own / common.read_text(errors="replace").strip()).resolve())
    return found


def policy(*, workspace, run_dir, provider, home=None, environment=None, platform=None):
    """The paths a sandboxed stage may write and may not read, all resolved: (writable, denied)."""
    platform = platform or sys.platform
    home = Path(home or Path.home())
    environment = environment if environment is not None else os.environ
    engine = provider if provider in ENGINE_STATE else "opencode" if provider in (None, "opencode") else None
    temp = MACOS_TEMP if platform == "darwin" else LINUX_TEMP
    writable = [Path(workspace), Path(run_dir), *_git_dirs(workspace), *(Path(p) for p in temp)]
    if environment.get("TMPDIR"):
        writable.append(Path(environment["TMPDIR"]))
    own = ENGINE_STATE.get(engine, ()) if engine else tuple(path for paths in ENGINE_STATE.values() for path in paths)
    writable.extend(home / name for name in (*own, *CACHES))
    # A configured provider AutoCode does not know may sign in through any engine's login.
    logins = tuple(path for name, paths in ENGINE_LOGINS.items() if engine and name != engine for path in paths)
    denied = [home / name for name in (*CREDENTIALS, *logins)]
    # A symlinked store is denied where it points, too.
    writable = sorted({_real(path) for path in writable}, key=lambda path: (len(path.parts), str(path)))
    denied = sorted({variant for path in denied for variant in (path, _real(path))}, key=str)
    return writable, denied


def bubblewrap_arguments(writable, denied):
    """bwrap's arguments: the root read-only, each existing writable path bound back, then each existing
    credential store covered, last, so a writable cache never uncovers a credential file inside it."""
    arguments = list(_BWRAP_BASE)
    for path in writable:
        if path.exists() and str(path) != "/dev":
            arguments += ["--bind", str(path), str(path)]
    for path in denied:
        if path.is_symlink() or not path.exists():
            continue
        if path.is_dir():
            arguments += ["--tmpfs", str(path), "--remount-ro", str(path)]
        else:
            arguments += ["--ro-bind", "/dev/null", str(path)]
    return [*arguments, "--"]


def _regex_literal(text):
    """``text`` as a Seatbelt (POSIX extended) regex literal: only its metacharacters escaped."""
    return re.sub(r"([.^$*+?()\[\]{}|\\])", r"\\\1", text)


def seatbelt_profile(writable, denied, *, home):
    quoted = lambda path: json.dumps(str(path), ensure_ascii=False)  # noqa: E731
    allowed = " ".join("(subpath " + quoted(path) + ")" for path in writable)
    # The engine's own home files are replaced atomically through siblings (~/.claude.json.tmp.*, .lock).
    siblings = " ".join(
        "(regex " + quoted("^" + _regex_literal(str(_real(home) / name)) + "(\\..*)?$") + ")"
        for name in (".claude.json",)
        if any(path == _real(home) / name for path in writable)
    )
    return "\n".join(
        [
            "(version 1)",
            "(allow default)",
            f'(deny file-write* (require-not (require-any {allowed} {siblings} (literal "/dev/null"))))',
            "(deny file-read* file-write* " + " ".join("(subpath " + quoted(path) + ")" for path in denied) + ")",
            "",
        ]
    )


def wrap(command, *, workspace, run_dir, provider, home=None, environment=None, platform=None):
    """The stage's command inside the agent sandbox, and the record the stage keeps of it.

    Raises Unavailable, having launched nothing, when this host cannot sandbox it.
    """
    platform = platform or sys.platform
    trouble = problem(platform=platform, codex=provider == "codex")
    if trouble:
        raise Unavailable(trouble)
    home = Path(home or Path.home())
    writable, denied = policy(
        workspace=workspace, run_dir=run_dir, provider=provider, home=home, environment=environment, platform=platform
    )
    record = {
        "backend": backend(platform),
        "writable": [str(path) for path in writable if path.exists()],
        "denied_reads": [str(path) for path in denied if path.exists() or path.is_symlink()],
    }
    if backend(platform) == BUBBLEWRAP:
        return [*bubblewrap_arguments(writable, denied), *command], record
    profile = seatbelt_profile(writable, denied, home=home)
    directory = Path(run_dir) / "agent-sandbox"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(profile.encode()).hexdigest()[:16] + ".sb")
    path.write_text(profile)
    record["profile"] = str(path)
    return [SANDBOX_EXEC, "-f", str(path), *command], record


def configure(state: RunState, settings, *, enable, now) -> None:
    """At run setup: save ``--agent-sandbox`` (it stays on for every resume) and refuse it where the host
    cannot sandbox the run's agents, before any stage. Raises ValueError, leaving the run unchanged."""
    if not enable and not enabled(settings):
        return
    trouble = problem(settings)
    if trouble:
        raise ValueError(f"Refused before any stage launched: {FLAG} was asked for, but {trouble}.")
    if enabled(settings):
        return
    settings[SETTING] = True
    state.setdefault("user_events", []).append({"kind": EVENT, "actor": "user_cli", "at": now(), "flag": FLAG})
