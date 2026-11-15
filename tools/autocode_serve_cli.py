"""`autocode serve`: run the warm local CLI host (#3618) or a saved run's development server (#730).

With no subcommand (or ``autocode serve host``), serves warm workspace snapshots and read-only CLI
queries over this user's private Unix socket until ``--idle-seconds`` elapses or ``SIGINT``/``SIGTERM``
arrives; ``--status`` inspects it and ``--stop`` ends it.

With ``start``, ``stop`` or ``status``, manages the project's ``serve`` development server saved with a
run (autocode_dev_server_config; autocode_dev_server). Nothing here starts, resumes or changes a run.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_dev_server as dev_server
    from . import autocode_dev_server_config as config
    from . import autocode_entry as entry
    from . import autocode_source_host as source_host
    from . import autocode_util as util
    from .autocode_taskrun import TaskRun, TaskRunError
except ImportError:
    import autocode_dev_server as dev_server
    import autocode_dev_server_config as config
    import autocode_entry as entry
    import autocode_source_host as source_host
    import autocode_util as util
    from autocode_taskrun import TaskRun, TaskRunError

COMMANDS = {
    "start": "Run the run's development server until Ctrl-C or autocode serve stop.",
    "stop": "Stop the run's development server and wait for it to end.",
    "status": "Show the run's development server: whether it is configured, running and where it answers.",
}
HOST_HELP = "Run, inspect or stop the warm local CLI and snapshot host."
TAIL_LINES = 20


def _seconds(text):
    value = float(text)
    if not 0 < value <= 3600:
        raise argparse.ArgumentTypeError("must be more than 0 and at most 3600 seconds")
    return value


def _idle_seconds(text):
    value = float(text)
    if not 0 < value <= 86400:
        raise argparse.ArgumentTypeError("must be more than 0 and at most 86400 seconds")
    return value


def _add_host_options(target: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    flag_default = argparse.SUPPRESS if suppress_defaults else False
    idle_default = argparse.SUPPRESS if suppress_defaults else float(source_host.IDLE_SECONDS)
    target.add_argument(
        "--idle-seconds",
        type=_idle_seconds,
        default=idle_default,
        help=f"seconds of inactivity before the warm CLI host exits (default {source_host.IDLE_SECONDS})",
    )
    target.add_argument(
        "--status",
        action="store_true",
        default=flag_default,
        help="show whether the warm local CLI host is running",
    )
    target.add_argument(
        "--stop",
        action="store_true",
        default=flag_default,
        help="stop the warm local CLI host if one is running",
    )
    target.add_argument(
        "--json",
        action="store_true",
        default=flag_default,
        help="print the status or stop result as one JSON object",
    )


def parser():
    result = argparse.ArgumentParser(prog="autocode serve", description=__doc__.splitlines()[0])
    _add_host_options(result, suppress_defaults=False)
    commands = result.add_subparsers(dest="command", required=False, metavar="{start,stop,status,host}")
    for name, text in COMMANDS.items():
        command = commands.add_parser(name, help=text, description=text)
        command.add_argument(
            "--workspace", type=Path, default=Path.cwd(), help="task workspace (default: current directory)"
        )
        command.add_argument("--run-dir", type=Path, help="saved run; otherwise the workspace's only run")
        if name == "stop":
            command.add_argument(
                "--timeout",
                type=_seconds,
                default=dev_server.STOP_WAIT_SECONDS,
                help=f"seconds to wait for the server to end (default {dev_server.STOP_WAIT_SECONDS})",
            )
        if name == "status":
            command.add_argument(
                "--json", action="store_true", default=argparse.SUPPRESS, help="print the status as one JSON object"
            )
    host_command = commands.add_parser("host", help=HOST_HELP, description=HOST_HELP)
    _add_host_options(host_command, suppress_defaults=True)
    return result


def _run(args):
    """The run's workspace, directory and saved state, read-only; the run must live in that workspace."""
    if not args.workspace.is_dir():  # named, not an errno from a strict resolve
        raise ValueError(f"No such workspace directory: {args.workspace}")
    workspace = args.workspace.resolve(strict=True)
    if args.run_dir is None:
        run = TaskRun.attach(workspace)
        if run is None:
            raise ValueError(f"No saved run in {workspace}")
        run_dir = Path(run.run_dir).resolve(strict=True)
    else:
        if not args.run_dir.is_dir():
            raise ValueError(f"No such run directory: {args.run_dir}")
        run_dir = args.run_dir.resolve(strict=True)
    if not run_dir.is_relative_to(workspace / ".autocode" / "runs"):
        raise ValueError(f"{run_dir} is not a run of {workspace}; pass the run's own --workspace")
    state = util.read(run_dir / "state.json")
    if not isinstance(state, dict) or Path(str(state.get("workspace", ""))).resolve() != workspace:
        raise ValueError(f"{run_dir} belongs to another workspace")
    return workspace, run_dir, state


def _start(_args, workspace, run_dir, state: RunState) -> int:
    saved = (state.get("settings") or {}).get(config.SETTING)
    if not isinstance(saved, dict):
        raise ValueError(
            "This run has no serve command. Name one as serve in .autocode/config.toml "
            "(docs/cli.md#project-settings); a new run saves it when it starts."
        )
    stop = threading.Event()
    signals = [signal.SIGINT, signal.SIGTERM, *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])]
    previous = {number: signal.signal(number, lambda *_: stop.set()) for number in signals}
    try:
        record = dev_server.serve(
            run_dir, workspace, saved, should_stop=stop.is_set, on_ready=_announce, on_wait=_waiting
        )
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    if record["status"] == "stopped":
        print(f"Development server stopped ({record['reason']}).")
        return 0
    print(f"Development server {record['status']}: {record['reason']}.", file=sys.stderr)
    _print_tail(record)
    return 1


def _announce(record) -> None:
    if record.get("restarts"):
        print(f"Serving {record['url']} again after a code change ({record['code_checkpoint']}).", flush=True)
        return
    print(f"Serving {record['url']} ({record['command']}).", flush=True)
    print("Stop it with Ctrl-C or: autocode serve stop", flush=True)


def _waiting(record) -> None:
    print(f"Development server {record['status']}: {record['reason']}.", file=sys.stderr)
    _print_tail(record)
    print(
        "Waiting for the run's next code change; stop it with Ctrl-C or: autocode serve stop",
        file=sys.stderr,
        flush=True,
    )


def _stop(args, _workspace, run_dir, _state) -> int:
    record = dev_server.request_stop(run_dir, timeout=args.timeout)
    if record is None:
        print("No development server is running for this run.")
        return 0
    print(f"Development server {record['status']} ({record.get('reason') or 'no reason recorded'}).")
    return 0


def _status(args, _workspace, run_dir, state: RunState) -> int:
    shown = dev_server.inspect(run_dir, state.get("settings"))
    if args.json:
        print(json.dumps(shown, indent=2))
        return 0
    if not shown["configured"] and shown["status"] == "off":
        print("No serve command: name one as serve in .autocode/config.toml; a new run saves it.")
        return 0
    print(f"Development server: {shown['status']}" + (f" at {shown['url']}" if shown["url"] else ""))
    print(f"Command: {shown['command']}")
    for label, key in (("Reason", "reason"), ("Exit code", "exit_code"), ("Output", "log")):
        if shown[key] is not None:
            print(f"{label}: {shown[key]}")
    return 0


def _host(args) -> int:
    if args.status and args.stop:
        raise ValueError("Choose either --status or --stop, not both")
    if os.name == "nt":
        if args.status:
            shown = {"running": False, "pid": None, "socket": None, "supported": False}
            if args.json:
                print(json.dumps(shown, indent=2))
            else:
                print("Warm CLI host: unsupported on native Windows.")
            return 0
        raise ValueError("Warm local CLI host requires POSIX Unix-domain sockets")
    sock = source_host.socket_path()
    if args.status:
        shown = source_host.host_status(sock)
        if args.json:
            print(json.dumps(shown, indent=2))
        elif shown["running"]:
            print(f"Warm CLI host: running (pid {shown['pid']}, socket {shown['socket']})")
        else:
            print(f"Warm CLI host: not running ({shown['socket']})")
        return 0
    if args.stop:
        stopped = source_host.stop_host(sock)
        if args.json:
            print(json.dumps(stopped, indent=2))
        elif stopped["stopped"]:
            print(f"Warm CLI host stopped (pid {stopped['pid']}).")
        else:
            print("No warm CLI host is running.")
        return 0
    source_host.serve(sock, cli_runner=entry.run_in_host, warmup=entry._autocode_module, idle_seconds=args.idle_seconds)
    return 0


def _print_tail(record) -> None:
    lines = (record.get("tail") or "").splitlines()[-TAIL_LINES:]
    if lines:
        print("Last output:", file=sys.stderr)
        for line in lines:
            print("  " + util.redact(line), file=sys.stderr)


def cli(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command in (None, "host"):
            return _host(args)
        workspace, run_dir, state = _run(args)
        return {"start": _start, "stop": _stop, "status": _status}[args.command](args, workspace, run_dir, state)
    except (*dev_server.ERRORS, TaskRunError, OSError, ValueError, RuntimeError) as error:
        print("autocode serve: " + util.redact(str(error)), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nautocode serve: interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(cli())
