"""Conservative legacy-worker checks scoped to a run path.

The owned process marker remains authoritative. Legacy ps command text is only
fallback evidence; path boundaries keep a relative suffix in another absolute
workspace path from being mistaken for this run. Ambiguous bare relative paths
remain blocking rather than risking a duplicate worker.
"""
import os
from pathlib import Path
import re
import subprocess

try:
    from .autocode_util import Paused, read
except ImportError:
    from autocode_util import Paused, read


def references_run(command, absolute, relative):
    return any(re.search(r"(?:^|[\s='\"(])" + re.escape(path) +
                         r"(?=$|[/\s'\")])", command) for path in (absolute, relative))


def duplicate_runner_command(command):
    """True only for processes that are themselves the runner or a codex exec call.
    Wrappers (zsh -lc '... autocode.py ...') and helper apps whose argv embeds
    runner prompt text are not duplicate runners."""
    parts = command.split(None, 2)
    if len(parts) < 2:
        return False
    name = os.path.basename(parts[0])
    if name == "codex":
        return parts[1] == "exec"
    if name in ("opencode", "opencode.exe"):
        return parts[1] == "run"
    return (name.startswith("python") or name == "autocode") and any(
        script in command for script in ("autocode.py", "autocode_builder_worker.py"))


def assert_no_legacy_process(run_dir, workspace):
    """Read process metadata internally; never print unrelated command arguments."""
    marker_path = Path(run_dir) / "active-processes.json"
    if marker_path.exists():
        try:
            from . import autocode_process as processes
        except ImportError:
            import autocode_process as processes
        marker = read(marker_path)
        owned = marker.get("processes", [])
        if not owned or processes.live_processes(owned):
            raise Paused("PAUSED_WORKSPACE_BUSY", "Provider commands from an earlier stage may still be alive; inspect its checkpoint")
    try:
        result = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        if isinstance(error, OSError) and "operation not permitted" in str(error).lower():
            # The per-workspace flock still serializes writers when process listing is blocked.
            return
        raise Paused("PAUSED_PROCESS_CHECK", "Cannot inspect legacy workers; refuse possible duplicate launch") from error
    if result.returncode and "operation not permitted" in (result.stderr or "").lower():
        # The per-workspace flock above still serializes writers for this
        # workspace. Sandboxed hosts may deny a machine-wide process listing,
        # which must not prevent an independent workspace from running.
        return
    if result.returncode:
        raise Paused("PAUSED_PROCESS_CHECK", "Cannot inspect legacy workers; refuse possible duplicate launch")
    marker = str(Path(run_dir).resolve())
    relative = os.path.relpath(marker, Path(workspace).resolve())
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid, command = parts
        # Skip the runner and the wrapper that launched it: a shell running our own
        # command text (e.g. zsh -lc '... autocode.py ...') is not a duplicate runner.
        if int(pid) in (os.getpid(), os.getppid()):
            continue
        # Also catches an orphaned Codex child with an output path in this run.
        if references_run(command, marker, relative) and duplicate_runner_command(command):
            raise Paused("PAUSED_WORKSPACE_BUSY", f"Existing run process {pid} is active; leave it untouched")
