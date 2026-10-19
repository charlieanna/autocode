"""Conservative legacy-worker checks scoped to a run path.

The owned process marker remains authoritative. Legacy ps command text is only
fallback evidence; path boundaries keep a relative suffix in another absolute
workspace path from being mistaken for this run. Ambiguous bare relative paths
remain blocking rather than risking a duplicate worker.
"""

import os
import re
import subprocess
from pathlib import Path

try:
    from .autocode_util import Paused, read, redact
except ImportError:
    from autocode_util import Paused, read, redact


def references_run(command, absolute, relative):
    return any(
        re.search(r"(?:^|[\s='\"(])" + re.escape(path) + r"(?=$|[/\s'\")])", command) for path in (absolute, relative)
    )


def _readonly_runner_command(command):
    """Recognize only unambiguous TaskRun and dashboard observer argv.

    ps command text does not preserve argument boundaries for quoted paths or
    task/prompt text. Keep those cases blocking, along with every option outside
    this small reader grammar. In particular, --request-milestone-checkpoints
    mutates the run even when combined with --status, and --show-goal saves it.
    """
    if any(character in command for character in "'\"\\\n\r"):
        return False
    argv = command.split()
    if not argv or not os.path.basename(argv[0]).startswith("python"):
        return False
    script = 1
    while script < len(argv) and argv[script] in ("-B", "-u"):
        script += 1
    if script >= len(argv) or os.path.basename(argv[script]) != "autocode.py":
        return False
    args = argv[script + 1 :]
    if args[:2] == ["intervention", "inspect"]:
        reader, index = "inspect", 2
    elif args and args[0] in ("--status", "--dry-run"):
        reader, index = args[0], 1
    else:
        return False
    paths = {}
    inspected = False
    as_json = False
    while index < len(args):
        option, separator, value = args[index].partition("=")
        if option == "--json" and not separator:
            if reader != "inspect" or as_json:
                return False
            as_json = True
            index += 1
            continue
        if option == "--inspect-evidence" and not separator:
            if reader != "--status" or inspected:
                return False
            inspected = True
            index += 1
            continue
        if option not in ("--workspace", "--run-dir") or option in paths:
            return False
        if not separator:
            index += 1
            if index >= len(args):
                return False
            value = args[index]
        if not value or not Path(value).is_absolute():
            return False
        paths[option] = value
        index += 1
    return set(paths) == {"--workspace", "--run-dir"} and (reader != "inspect" or as_json)


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
    return (
        (name.startswith("python") or name == "autocode")
        and any(script in command for script in ("autocode.py", "autocode_builder_worker.py"))
        and not _readonly_runner_command(command)
    )


def _process_commands(output):
    """Keep argument continuations attached to their numeric ps PID row.

    Dropping an unframed continuation could hide a mutation after a seemingly
    canonical --status command. Preserve its newline so the reader grammar
    refuses that ambiguous entry. Never expose malformed process text.
    """
    entry = None
    for line in output.splitlines():
        parts = line.lstrip().split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[0-9]+", parts[0]):
            if entry is not None:
                yield entry
            entry = (int(parts[0]), parts[1])
        elif entry is not None:
            entry = (entry[0], entry[1] + "\n" + line)
        elif line.strip():
            raise Paused("PAUSED_PROCESS_CHECK", "Cannot parse legacy workers; refuse possible duplicate launch")
    if entry is not None:
        yield entry


INSPECTION_SECONDS = 30


def _cannot_inspect(cause):
    """The refusal when the process listing fails, naming how it failed (#1059): ps could not start, did
    not finish, or exited with an error (its first stderr line, bounded and redacted). The listing
    itself, other tasks' commands and the environment are never shown."""
    return Paused("PAUSED_PROCESS_CHECK", f"Cannot inspect legacy workers ({cause}); refuse possible duplicate launch")


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
            raise Paused(
                "PAUSED_WORKSPACE_BUSY",
                "Provider commands from an earlier stage may still be alive; inspect its checkpoint",
            )
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=INSPECTION_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        if isinstance(error, OSError) and "operation not permitted" in str(error).lower():
            # The per-workspace flock still serializes writers when process listing is blocked.
            return
        raise _cannot_inspect(
            f"ps did not finish within {INSPECTION_SECONDS} s"
            if isinstance(error, subprocess.TimeoutExpired)
            else f"ps could not start: {error.strerror or type(error).__name__}"
        ) from error
    if result.returncode and "operation not permitted" in (result.stderr or "").lower():
        # The per-workspace flock above still serializes writers for this
        # workspace. Sandboxed hosts may deny a machine-wide process listing,
        # which must not prevent an independent workspace from running.
        return
    if result.returncode:
        detail = next((line.strip() for line in (result.stderr or "").splitlines() if line.strip()), "")
        raise _cannot_inspect(f"ps exited {result.returncode}" + (f": {redact(detail)[:200]}" if detail else ""))
    marker = str(Path(run_dir).resolve())
    relative = os.path.relpath(marker, Path(workspace).resolve())
    for pid, command in _process_commands(result.stdout):
        # Skip the runner and the wrapper that launched it: a shell running our own
        # command text (e.g. zsh -lc '... autocode.py ...') is not a duplicate runner.
        if pid in (os.getpid(), os.getppid()):
            continue
        # Also catches an orphaned Codex child with an output path in this run.
        if references_run(command, marker, relative) and duplicate_runner_command(command):
            raise Paused("PAUSED_WORKSPACE_BUSY", f"Existing run process {pid} is active; leave it untouched")
