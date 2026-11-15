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

Environment mappings, preflight ownership and reuse live here. Executions use
the shared captured-process supervisor; provider checks never run a model.

Within a run (``reuse_checks``), a preflight that succeeded is answered again for
``REUSE_SECONDS``. A chat message relaunches the controller, and the same
`kilo --version`, `kilo models` and `kilo auth list` cost 6.4 s of a 28 s
follow-up turn on 2026-10-20. Reuse is keyed by the exact command, working
directory, the environment, the effective output-decoding policy, the executable's
resolved file identity and the tool's own login and settings files, so a new tool install, a changed setting, a
new login or another folder runs the check again; a failed check is never kept.
A caller that can tell a whole answer from a cut-off one passes ``complete``: an
answer it cannot read to the end is not a successful check, so it is neither kept
nor answered again (#3078), and ``fresh`` runs the check even when an answer is kept.

Since 2026-11-07 (Ankur, after a new chat's "hi" spent seconds repeating them) the
answers are kept for the project, ``PROJECT/.autocode/preflight-reuse``, so a new
chat in the same project reuses them too; a run directory outside a project's
``.autocode/runs`` keeps its own. The variables a terminal sets for its own
session (``SESSION_VARIABLES``) are left out of the key: a chat picked up from
another terminal window asks the same tool the same question.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path

try:
    from .. import autocode_captured_process as captured
    from .. import autocode_output_decoding as output_decoding
    from ..autocode_invocation_environment import without_call_markers
except ImportError:
    import autocode_output_decoding as output_decoding
    import autocode_captured_process as captured
    from autocode_invocation_environment import without_call_markers

CONNECTION_TIMEOUT_SECONDS = 45
OWNED_CHECK_SCHEMA = 1  # Older kept answers predate owned descendant cleanup.
REUSE_SECONDS = 3600  # owner decision 2026-10-20: an hour
REUSE_FOLDER = "preflight-reuse"
LISTING_RETRY_FACTOR = 2  # a timed-out listing's second attempt gets twice its bound (#3313)
# Set by the shell or terminal for its own session; a tool's answer never depends on them.
SESSION_VARIABLES = frozenset(
    {
        "_",
        "OLDPWD",
        "SHLVL",
        "TERM_SESSION_ID",
        "ITERM_SESSION_ID",
        "SECURITYSESSIONID",
        "TMUX_PANE",
        "WINDOWID",
        "KITTY_WINDOW_ID",
        "WEZTERM_PANE",
    }
)
_reuse: Path | None = None
_timed_out: set[str] = set()  # execution keys whose warm-up attempt overran its bound, in this run
_done_in_run: set = set()  # the steps once_in_run has run, in this run
_once_lock = threading.Lock()
_STDLIB_RUN = subprocess.run


class KeptCheck(subprocess.CompletedProcess):
    """A preflight answered from a kept check rather than a new run of the command."""


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


def _decoding(kwargs) -> output_decoding.Decoding | None:
    output_decoding.validate_text_aliases(kwargs.get("text"), kwargs.get("universal_newlines"))
    if kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("encoding") or kwargs.get("errors"):
        return output_decoding.resolve(kwargs.get("encoding"), kwargs.get("errors"))
    return None


def _read_captured(handle, decoding: output_decoding.Decoding | None, *, lossy: bool = False):
    handle.seek(0)
    raw = handle.read()
    return raw if decoding is None else decoding.decode(raw, lossy=lossy)


def _run_captured(command, env: Mapping[str, str], kwargs: dict, decoding: output_decoding.Decoding | None):
    # POSIX pipes truncate around ~81 KB when Bun/Node CLIs set O_NONBLOCK on fd 1 (#3078);
    # file-backed stdio avoids pipe backpressure while leaving patched subprocess.run untouched.
    run_kwargs = dict(kwargs)
    run_kwargs.pop("capture_output", None)
    with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
        run_kwargs["stdout"] = out_file
        run_kwargs["stderr"] = err_file
        try:
            completed = _STDLIB_RUN(command, env=dict(env), **run_kwargs)
        except subprocess.TimeoutExpired as error:
            error.stdout = _read_captured(out_file, decoding, lossy=True)
            error.stderr = _read_captured(err_file, decoding, lossy=True)
            raise
        except subprocess.CalledProcessError as error:
            error.output = _read_captured(out_file, decoding)
            error.stderr = _read_captured(err_file, decoding)
            raise
        return subprocess.CompletedProcess(
            completed.args,
            completed.returncode,
            _read_captured(out_file, decoding),
            _read_captured(err_file, decoding),
        )


def preflight_run(
    command, env: Mapping[str, str], *, require_executable=False, complete=None, fresh=False, warming=False, **kwargs
):
    """Run a preflight subprocess (version, roster, auth summary) with the explicit environment.

    Explicit-environment callers require executable admission before spawn;
    this avoids execvp's default system PATH when the mapping omits PATH. The
    ambient API retains its existing behavior. The caller owns timeouts and
    failure interpretation; no retry is added here. ``complete(stdout)``, when given,
    decides whether an answer is whole enough to keep or to answer again; ``fresh``
    skips a kept answer (and keeps the new one as usual).
    """
    if require_executable and not resolve_executable(command[0], env, cwd=kwargs.get("cwd")):
        raise FileNotFoundError(f"{command[0]!r} cannot be found in the explicit environment")
    decoding = _decoding(kwargs)
    execution_key = _execution_key(command, env, kwargs)
    key = _reuse_key(execution_key, decoding)
    kept = None if fresh else _recall(key, command, complete)
    if kept is not None:
        return kept
    try:
        result = captured.run(command, env=dict(env), **kwargs)
    except subprocess.TimeoutExpired:
        if warming and execution_key:
            # A warm-up that overran its bound was the check's first attempt (preflight_listing).
            _timed_out.add(execution_key)
        raise
    if key and result.returncode == 0 and (complete is None or complete(result.stdout)):
        _keep(key, result)
    return result


def preflight_listing(command, env: Mapping[str, str], *, timeout, **kwargs):
    """A model-free listing (a login summary or a model roster), asked once more when it times out (#3313).

    Under load these reads overran their bound by a fraction of a second (`kilo auth list` exited 0
    after 15.3 s against 15 s; `kilo models --verbose` took 21.5 s against 30 s), and the single cut
    refused a person's `autocode stages set` or paused a run to ask about machinery a second read
    settles. The second attempt gets twice the bound, after owned cleanup of the first.
    Only a timeout is retried: a nonzero exit or an OS error is the answer. The caller passes only a
    listing command, never one that runs a model; the final ``TimeoutExpired`` names the last bound.
    """
    decoding = _decoding(kwargs)
    if decoding is not None:
        # One listing keeps its parent's policy through warm-up accounting and
        # both existing attempts, even if the process locale changes meanwhile.
        kwargs = {**kwargs, "encoding": decoding.encoding}
    execution_key = _execution_key(command, env, kwargs)
    if execution_key is None or execution_key not in _timed_out:
        try:
            return preflight_run(command, env, timeout=timeout, **kwargs)
        except subprocess.TimeoutExpired:
            pass
    # The first attempt overran its bound here or in the warm-up (CommandProvider.warm_checks).
    _timed_out.discard(execution_key)
    return preflight_run(command, env, timeout=timeout * LISTING_RETRY_FACTOR, **kwargs)


def listing_bounds(timeout) -> str:
    """How long both attempts of :func:`preflight_listing` waited, for its caller's message."""
    return f"{timeout:g} seconds and again after {timeout * LISTING_RETRY_FACTOR:g} seconds"


@contextlib.contextmanager
def reuse_checks(run_dir):
    """While one run is driven, reuse its successful preflights (see the module docstring)."""
    global _reuse
    previous, _reuse = _reuse, reuse_folder(run_dir)
    try:
        yield
    finally:
        _reuse = previous
        _timed_out.clear()
        _done_in_run.clear()


def once_in_run(token, step) -> bool:
    """Run ``step()`` the first time ``token`` is named while checks are kept (``reuse_checks``).

    Every caller waits while that first step runs, so checks asked from several threads at once (route
    discovery reads three together) find its kept answers instead of starting the same commands beside
    it. Returns whether this call ran the step."""
    if _reuse is None:
        return False
    with _once_lock:
        if token in _done_in_run:
            return False
        _done_in_run.add(token)
        step()
        return True


@contextlib.contextmanager
def reuse_workspace_checks(workspace=".", run_dir=None):
    """Reuse kept preflight checks for read-only workspace/run CLIs without creating ``.autocode``."""
    if run_dir:
        with reuse_checks(run_dir):
            yield
        return
    if _reuse is None and workspace is not None and (Path(workspace) / ".autocode").is_dir():
        with reuse_checks(Path(workspace) / ".autocode" / "runs" / "_cli"):
            yield
        return
    yield


def reuse_folder(run_dir) -> Path:
    """The project's folder for kept checks, or the run's own when it is not in a project's run store."""
    run_dir = Path(run_dir)
    store = run_dir.parent
    if store.name == "runs" and store.parent.name == ".autocode":
        return store.parent / REUSE_FOLDER
    return run_dir / REUSE_FOLDER


# The settings an OpenCode-family tool reads that can change a listing or an agent's permissions: JSON
# settings files, and the agent and mode definitions in their folders. Lock files and installed plugins are
# left out, since the tool may rewrite them as it starts. The project's own copies, in the folder the tool
# runs in, count as well as the tool's config folder.
SETTINGS_SUFFIXES = (".json", ".jsonc")
DEFINITION_FOLDERS = ("agent", "agents", "mode", "modes")
PROJECT_SETTINGS = (".kilo", ".kilocode", ".opencode")
PROJECT_FILES = ("kilo.json", "kilo.jsonc", "opencode.json", "opencode.jsonc")
SETTINGS_FILE_LIMIT = 200


def _settings_in(folder: Path) -> list[Path]:
    """A settings folder's JSON files and every agent or mode definition below it, bounded."""
    found: list[Path] = []
    with contextlib.suppress(OSError):
        found = sorted(path for path in folder.iterdir() if path.suffix in SETTINGS_SUFFIXES and path.is_file())
    for name in DEFINITION_FOLDERS:
        with contextlib.suppress(OSError):
            found.extend(sorted(path for path in (folder / name).rglob("*") if path.is_file()))
    return found[:SETTINGS_FILE_LIMIT]


def _tool_files(executable: str, env: Mapping[str, str], cwd=None) -> list:
    """The tool's own login and settings files, by identity: its XDG data ``auth.json``, its config folder's
    settings, and the project settings in the folder it runs in."""
    name = Path(executable).name
    home = env.get("HOME") or os.path.expanduser("~")
    data = Path(env.get("XDG_DATA_HOME") or Path(home, ".local", "share")) / name
    config = Path(env.get("XDG_CONFIG_HOME") or Path(home, ".config")) / name
    settings = _settings_in(config)
    if cwd is not None:
        settings.extend(Path(cwd, entry) for entry in PROJECT_FILES)
        for entry in PROJECT_SETTINGS:
            settings.extend(_settings_in(Path(cwd, entry)))
    found = []
    for path in [data / "auth.json", *settings]:
        try:
            info = path.stat()
        except OSError:
            continue
        found.append([str(path), info.st_mtime_ns, info.st_size])
    return found


def _execution_key(command, env: Mapping[str, str], kwargs) -> str | None:
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
    variables = sorted(
        (name, value) for name, value in without_call_markers(env).items() if name not in SESSION_VARIABLES
    )
    identity = [
        list(command),
        str(kwargs.get("cwd")),
        variables,
        real,
        info.st_mtime_ns,
        info.st_size,
        _tool_files(command[0], env, kwargs.get("cwd")),
    ]
    return hashlib.sha256(json.dumps(identity, default=str).encode()).hexdigest()


def _reuse_key(execution_key: str | None, decoding: output_decoding.Decoding | None) -> str | None:
    if execution_key is None or decoding is None:
        return None
    # A completed timeout counts the execution regardless of its output decoder;
    # only a reusable decoded answer additionally depends on the output policy.
    identity = [execution_key, decoding.identity()]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _recall(key, command, complete=None):
    if key is None or _reuse is None:
        return None
    try:
        kept = json.loads((_reuse / f"{key}.json").read_text(encoding="utf-8"))
        # An answer kept before its caller checked for a cut-off one (#3078) is not answered again.
        if (
            kept.get("ownership_schema") == OWNED_CHECK_SCHEMA
            and 0 <= time.time() - kept["at"] < REUSE_SECONDS
            and (complete is None or complete(kept["stdout"]))
        ):
            return KeptCheck(command, 0, kept["stdout"], kept["stderr"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _keep(key, result) -> None:
    if _reuse is None:
        return
    with contextlib.suppress(OSError):
        _reuse.mkdir(mode=0o700, exist_ok=True)
        temporary = _reuse / f".{key}.{os.getpid()}.{threading.get_ident()}"
        temporary.write_text(
            json.dumps(
                {
                    "ownership_schema": OWNED_CHECK_SCHEMA,
                    "at": time.time(),
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            ),
            encoding="utf-8",
        )
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
