"""Conservative legacy-worker checks scoped to a run path.

The owned process marker remains authoritative. Legacy ps command text is only
fallback evidence; path boundaries keep a relative suffix in another absolute
workspace path from being mistaken for this run. Ambiguous bare relative paths
remain blocking rather than risking a duplicate worker.
"""

import contextlib
import os
import re
import subprocess
from pathlib import Path

try:
    from .autocode_util import Paused, read, redact
except ImportError:
    from autocode_util import Paused, read, redact

try:
    import psutil
except ImportError:  # only the exact-argv refinements below need it; the ps scan stands alone
    psutil = None

# The scripts a stage-running AutoCode process executes: the source runner, the
# installed console script, the parallel Builder worker and the controller
# scripts the dashboard and the macOS app start.
RUNNER_SCRIPTS = ("autocode.py", "autocode", "autocode_builder_worker.py", "autopilot.py", "autocode_orchestrator.py")
# Interpreter switches that take no value, so the next argument is still the script.
PYTHON_FLAGS = re.compile(r"-[BbdEIiOPqRSsuv]+")


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
    if args[:2] == ["coordinate", "status"]:
        reader, index = "coordinate-status", 2
    elif args[:2] == ["intervention", "inspect"]:
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
            if reader not in ("inspect", "coordinate-status") or as_json:
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
    if reader == "coordinate-status":
        return set(paths) == {"--run-dir"} and as_json
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


def not_a_runner(argv):
    """True when a process's exact argument vector shows it runs no stage itself.

    Two cases, both settled from argument boundaries that ps text loses:
    a Python program other than AutoCode whose arguments merely carry a runner
    command line (a launcher, a terminal holder, a test harness), and the
    `autocode chat` client, which starts its own `autocode` child for every
    action and so is a viewer of the run. Anything else, including `python -c`,
    `python -m` and an unknown interpreter switch, is left to the ps verdict.
    """
    if not argv:
        return False
    name = os.path.basename(argv[0])
    index = 1
    if name.startswith("python"):
        while index < len(argv) and PYTHON_FLAGS.fullmatch(argv[index]):
            index += 1
        if index >= len(argv) or argv[index].startswith("-"):
            return False
        script = os.path.basename(argv[index])
        if script not in RUNNER_SCRIPTS:
            return True
        index += 1
    elif name == "autocode":
        script = name
    else:
        return False
    return script in ("autocode.py", "autocode") and argv[index : index + 1] == ["chat"]


def _exact_argv(pid, command):
    """The argument vector of ``pid``, only while it is still the process ps listed as ``command``.

    None when it cannot be read or no longer matches (gone, reused, access denied,
    or text ps rendered differently), so the conservative ps verdict stands.
    """
    if psutil is None:
        return None
    try:
        argv = psutil.Process(pid).cmdline()
    except (psutil.Error, OSError):
        return None
    return argv if argv and " ".join(argv) == command else None


def own_lineage():
    """This process and every process it descends from.

    None of them is a competing runner: the shell, launcher or chat that started
    this command is waiting on it, and a writer elsewhere still meets the
    per-workspace lock. Without psutil only the parent is known.
    """
    pids = {os.getpid(), os.getppid()}
    if psutil is not None:
        with contextlib.suppress(psutil.Error, OSError):
            pids.update(parent.pid for parent in psutil.Process(os.getpid()).parents())
    return pids


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
    # Skip the runner and everything that launched it: a shell running our own
    # command text (e.g. zsh -lc '... autocode.py ...'), a Python launcher or the
    # chat that started this command is not a duplicate runner.
    lineage = own_lineage()
    for pid, command in _process_commands(result.stdout):
        if pid in lineage:
            continue
        # Also catches an orphaned Codex child with an output path in this run.
        if references_run(command, marker, relative) and duplicate_runner_command(command):
            argv = _exact_argv(pid, command)
            if argv is not None and not_a_runner(argv):
                continue
            raise Paused("PAUSED_WORKSPACE_BUSY", f"Existing run process {pid} is active; leave it untouched")
