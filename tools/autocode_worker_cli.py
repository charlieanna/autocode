"""Core-owned opt-in durable launch on an already configured Linux user manager.

`launch` returns a reconciliable receipt, never task completion. Repeating an
identical request inspects the original dispatch; it cannot restart a service.
`inspect` and `capabilities` launch no task or provider. The internal `execute`
entry execs the ordinary CLI, which binds under its ordinary run lock.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

try:
    from . import autocode_util as util
    from . import autocode_worker_binding as binding
    from . import autocode_worker_records as records
    from . import autocode_worker_service as service
    from .autocode_control_tokens import CONTROL_TOKEN_ENV_VARS
    from .autocode_worker_view import PREFIX
except ImportError:
    import autocode_util as util
    import autocode_worker_binding as binding
    import autocode_worker_records as records
    import autocode_worker_service as service
    from autocode_control_tokens import CONTROL_TOKEN_ENV_VARS
    from autocode_worker_view import PREFIX

CORE = (sys.executable, str(Path(__file__).resolve().with_name("autocode.py")))


def capabilities(*, services=None):
    return {
        "version": 1,
        "default": "owned",
        "modes": [
            {"id": "owned", "available": True, "detach_supported": False},
            {"id": "durable_local", **(services or service.UserServices()).capability(), "next_command": list(PREFIX)},
        ],
    }


def inspect(launch_id, *, services=None):
    root = binding.storage()
    record = records.read(root, launch_id)
    bound = record.get("binding")
    ready, reason = False, "Core binding has not been observed; do not replay this request"
    try:
        if record["request"]["boot_id"] != binding.boot_id():
            reason = "The host rebooted; inspect retained attempts through the run status"
        elif record["request"]["source"] != binding.runtime_identity():
            reason = "The controller runtime changed; no automatic adoption or replay is allowed"
        elif bound:
            checkpoint = util.read_object(Path(bound["run_dir"]) / "state.json")
            execution = checkpoint.get("execution") or {}
            ready = (
                checkpoint.get("workspace") == bound["workspace"]
                and execution.get("launch_id") == record["id"]
                and execution.get("controller_epoch") == bound["controller_id"]
            )
            reason = None if ready else "The run no longer carries this launch binding; inspect its current status"
    except (OSError, ValueError, RuntimeError):
        reason = "The bound run cannot be inspected; its outcome is unknown"
    return {
        "version": 1,
        "launch_id": launch_id,
        "phase": record["phase"],
        "dispatch_count": record["dispatch_count"],
        "binding": bound,
        "ready": ready,
        "reason": reason,
        "service": (services or service.UserServices()).inspect(launch_id),
        "log": str(root / (launch_id + ".log")),
        "inspect_command": ["autocode", "worker", "inspect", launch_id],
        "execution": {"version": 1, "mode": "durable_local", "detach_supported": record["dispatch_count"] == 1},
    }


def launch(workspace, run_dir, argv, *, checkpoint=None, request_id=None, services=None):
    workspace, run_dir = Path(workspace).resolve(strict=True), Path(run_dir).resolve()
    binding.outside(workspace)
    argv = list(argv)
    if not argv or "--" in argv or any(word.startswith("--worker-receipt") for word in argv):
        raise ValueError("Durable launch requires one literal core task action; use --brief for dash-prefixed text")
    if argv[0] in ("worker", "chat", "unattended", "conversation", "checkpoint"):
        raise ValueError("This durable adapter launches ordinary core task actions only")
    if any(flag in argv for flag in ("--workspace", "--run-dir", "--new-run-dir", "--chat")):
        raise ValueError("The durable envelope owns task paths and noninteractive execution")
    fresh = checkpoint is None
    arguments = [
        *argv,
        "--no-chat",
        "--workspace",
        str(workspace),
        "--new-run-dir" if fresh else "--run-dir",
        str(run_dir),
    ]
    authority = util.digest({key: os.environ[key] for key in CONTROL_TOKEN_ENV_VARS if key in os.environ})
    wanted = records.request(
        workspace=str(workspace),
        run_dir=str(run_dir),
        argv=arguments,
        request_id=request_id or util.digest({"checkpoint": checkpoint, "argv": arguments, "authority": authority}),
        source=binding.runtime_identity(),
        boot_id=binding.boot_id(),
        expected_checkpoint=checkpoint,
        authority_hash=authority,
    )
    root = binding.storage()
    record, _ = records.prepare(root, wanted)
    if record["dispatch_count"]:
        return inspect(record["id"], services=services)
    if fresh:
        if run_dir.parent != workspace / ".autocode" / "runs" or run_dir.exists():
            raise ValueError("New durable runs require a fresh immediate run destination")
    else:
        state = util.read_object(run_dir / "state.json")
        if state.get("workspace") != str(workspace) or util.digest(state) != checkpoint:
            raise ValueError("Durable action checkpoint or workspace is stale; refresh the view")
        saved = state.get("execution") or {}
        if saved.get("mode") != "durable_local" or saved.get("runtime") != wanted["source"]:
            raise ValueError("Legacy or changed-runtime runs cannot silently acquire durable execution")
    if not (services or service.UserServices()).capability()["available"]:
        raise service.ServiceUnavailable("Durable execution is unavailable; no launch was attempted")
    record, dispatch = records.begin_dispatch(root, record["id"], record["request_hash"])
    if dispatch:
        # The sealed receipt is retained even if manager access disappeared.
        with contextlib.suppress(service.ServiceOutcomeUnknown, service.ServiceUnavailable):
            (services or service.UserServices()).start(
                record["id"],
                [*CORE, "worker", "execute", "--receipt", str(root / (record["id"] + ".json"))],
                workspace=workspace,
                log=root / (record["id"] + ".log"),
            )
    return inspect(record["id"], services=services)


def parser():
    result = argparse.ArgumentParser(prog="autocode worker", description=__doc__)
    commands = result.add_subparsers(dest="action", required=True)
    commands.add_parser("capabilities", help="Read available worker lifetime modes; launches nothing")
    check = commands.add_parser("inspect", help="Inspect a retained launch without replaying it")
    check.add_argument("launch_id", help="The launch receipt's ID")
    listing = commands.add_parser("launches", help="Find retained launches after a lost response; never replays them")
    listing.add_argument("--workspace", required=True, type=Path, help="Project directory")
    listing.add_argument("--run-dir", type=Path, help="Only launches of this run")
    start = commands.add_parser("launch", help="Launch one exact core task action in a persistent user service")
    start.add_argument("--workspace", required=True, type=Path, help="Project directory")
    start.add_argument("--run-dir", required=True, type=Path, help="The exact run destination")
    start.add_argument("--checkpoint", required=True, help="Current observation token, or - for a fresh run")
    start.add_argument("--request-id", help="Stable request ID; defaults to the exact action/checkpoint fingerprint")
    start.add_argument("arguments", nargs=argparse.REMAINDER, help="Literal core arguments after --")
    execute = commands.add_parser("execute", help=argparse.SUPPRESS)
    execute.add_argument("--receipt", required=True, type=Path, help=argparse.SUPPRESS)
    return result


def cli(argv):
    args = parser().parse_args(argv)
    try:
        if args.action == "capabilities":
            answer = capabilities()
        elif args.action == "inspect":
            answer = inspect(args.launch_id)
        elif args.action == "launches":
            root = binding.storage()
            rows = []
            if root.exists():
                for path in sorted(root.glob("*.json")):
                    record = records.read(root, path.stem)
                    candidates = [record["request"], record.get("binding") or {}]
                    if any(
                        row.get("workspace") == str(args.workspace.resolve())
                        and (args.run_dir is None or row.get("run_dir") == str(args.run_dir.resolve()))
                        for row in candidates
                    ):
                        rows.append(inspect(record["id"]))
            answer = {"version": 1, "launches": rows}
        elif args.action == "launch":
            if args.arguments[:1] != ["--"]:
                raise ValueError("Use -- before the literal core task arguments")
            answer = launch(
                args.workspace,
                args.run_dir,
                args.arguments[1:],
                checkpoint=None if args.checkpoint == "-" else args.checkpoint,
                request_id=args.request_id,
            )
        else:
            path = args.receipt.absolute()
            record = records.read(path.parent, path.stem)
            binding.load_entry(path, record["request"]["argv"])
            command = [*CORE, *record["request"]["argv"], "--worker-receipt", str(path)]
            os.execv(command[0], command)
            raise AssertionError("exec returned")
        print(json.dumps(answer))
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(f"autocode worker: {error}", file=sys.stderr)
        return 2
