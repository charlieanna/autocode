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
OBSERVER_SCRIPTS = ("autocode.py", "autocode_entry.py", "autocode")
RUNNER_SCRIPTS = (*OBSERVER_SCRIPTS, "autocode_builder_worker.py", "autopilot.py", "autocode_orchestrator.py")
RUNNER_MODULES = ("autocode", "autocode_entry", "autocode_cli.autocode", "autocode_cli.autocode_entry")
RUNNER_MODULE_MARKER = re.compile(r"(?:^|\s)-m\s+(?:" + "|".join(map(re.escape, RUNNER_MODULES)) + r")(?:\s|$)")
PROVIDER_VERBS = {"codex": "exec", "opencode": "run", "opencode.exe": "run"}
PROCESS_GONE = object()
# Interpreter switches that take no value, so the next argument is still the script.
PYTHON_FLAGS = re.compile(r"-[BbdEIiOPqRSsuv]+")


def references_run(command, absolute, relative):
    return any(
        re.search(r"(?:^|[\s='\"(])" + re.escape(path) + r"(?=$|[/\s'\")])", command) for path in (absolute, relative)
    )


def _python_interpreter(path):
    return os.path.basename(path).lower().startswith("python")


def _entry_arguments(argv, *, strict=False):
    """The common argument tail for supported source, module and console entries."""
    if not argv:
        return None
    index = 1
    if _python_interpreter(argv[0]):
        if any(character.isspace() for character in os.path.basename(argv[0])):
            return None
        while index < len(argv) and PYTHON_FLAGS.fullmatch(argv[index]):
            if strict and argv[index] not in ("-B", "-u"):
                return None
            index += 1
        if argv[index : index + 1] == ["-m"]:
            if index + 1 >= len(argv) or argv[index + 1] not in RUNNER_MODULES:
                return None
            index += 2
        elif index < len(argv) and os.path.basename(argv[index]) in OBSERVER_SCRIPTS:
            index += 1
        else:
            return None
    elif os.path.basename(argv[0]) != "autocode":
        return None
    return argv[index:]


def _readonly_runner_argv(argv):
    """Accept only a complete, non-mutating public observer argument vector."""
    args = _entry_arguments(argv, strict=True)
    if args is None:
        return False
    if args[:2] == ["coordinate", "status"]:
        reader, index = "coordinate-status", 2
    elif args[:2] == ["intervention", "inspect"]:
        reader, index = "inspect", 2
    else:
        reader, index = None, 0
    paths = {}
    inspected = False
    as_json = False
    while index < len(args):
        option, separator, value = args[index].partition("=")
        if option in ("--status", "--dry-run") and not separator:
            if reader is not None:
                return False
            reader = option
            index += 1
            continue
        if option == "--json" and not separator:
            if as_json:
                return False
            as_json = True
            index += 1
            continue
        if option == "--inspect-evidence" and not separator:
            if inspected:
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
        if not value or "\n" in value or "\r" in value or not Path(value).is_absolute():
            return False
        paths[option] = value
        index += 1
    if reader == "coordinate-status":
        return set(paths) == {"--run-dir"} and as_json and not inspected
    if reader not in ("--status", "--dry-run", "inspect"):
        return False
    return (
        set(paths) == {"--workspace", "--run-dir"}
        and (reader != "inspect" or as_json)
        and (reader == "--status" or not inspected)
        and (reader != "--dry-run" or not as_json)
    )


def _runner_marker(command):
    return bool(
        any(script in command for script in RUNNER_SCRIPTS if script != "autocode")
        or re.search(r"(?:^|\s)(?:\S*/)?autocode(?:\s|$)", command)
        or RUNNER_MODULE_MARKER.search(command)
    )


def _provider_marker(command):
    return any(
        re.search(r"(?:^|\s)(?:\S*/)?" + re.escape(name) + r"\s+" + re.escape(verb) + r"(?:\s|$)", command)
        for name, verb in PROVIDER_VERBS.items()
    )


def duplicate_runner_command(command):
    """Detect a possible runner from ps text, without granting reader authority.

    This coarse scan includes status readers. Only authenticated exact argv may
    exempt them; wrappers and helper apps are refined after the scan.
    """
    parts = command.split(None, 2)
    if len(parts) < 2:
        return False
    name = os.path.basename(parts[0])
    if name in PROVIDER_VERBS:
        return parts[1] == PROVIDER_VERBS[name]
    return (_python_interpreter(name) or name == "autocode") and (name == "autocode" or _runner_marker(command))


def _possible_runner_argv(argv, command):
    """Classify a possible runner using actual executable argument boundaries."""
    name = os.path.basename(argv[0])
    if name in PROVIDER_VERBS:
        return argv[1:2] == [PROVIDER_VERBS[name]]
    if name == "autocode":
        return True
    if not _python_interpreter(name):
        return False
    index = 1
    while index < len(argv) and PYTHON_FLAGS.fullmatch(argv[index]):
        index += 1
    if argv[index : index + 1] == ["-m"]:
        return (index + 1 < len(argv) and argv[index + 1] in RUNNER_MODULES) or _runner_marker(command)
    if index < len(argv) and any(runner in os.path.basename(argv[index]) for runner in RUNNER_SCRIPTS):
        return True
    return _runner_marker(command)


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
    args = _entry_arguments(argv)
    if args is not None:
        return args[:1] == ["chat"] or _readonly_runner_argv(argv)
    name = os.path.basename(argv[0])
    index = 1
    if _python_interpreter(name):
        if any(character.isspace() for character in name):
            return False
        while index < len(argv) and PYTHON_FLAGS.fullmatch(argv[index]):
            index += 1
        if index >= len(argv) or argv[index].startswith("-"):
            return False
        script = os.path.basename(argv[index])
        if script not in RUNNER_SCRIPTS:
            return not any(runner in script for runner in RUNNER_SCRIPTS)
    return False


def _exact_argv(pid, command):
    """The argument vector of ``pid``, only while it is still the process ps listed as ``command``.

    PROCESS_GONE only when the listed process has already exited; None when it
    cannot be read or matched, so the conservative ps verdict stands.
    """
    if psutil is None:
        return None
    try:
        argv = psutil.Process(pid).cmdline()
    except psutil.ZombieProcess:
        return None
    except psutil.NoSuchProcess:
        return PROCESS_GONE
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
        if not references_run(command, marker, relative):
            continue
        argv = _exact_argv(pid, command)
        if argv is PROCESS_GONE:
            continue
        if argv is not None:
            possible = _possible_runner_argv(argv, command) and not not_a_runner(argv)
        else:
            # Text can identify a possible runner, but cannot exempt an observer.
            possible = duplicate_runner_command(command) or _runner_marker(command) or _provider_marker(command)
        if possible:
            raise Paused("PAUSED_WORKSPACE_BUSY", f"Existing run process {pid} is active; leave it untouched")
