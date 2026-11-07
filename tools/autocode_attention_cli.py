"""Acknowledge one inspected alert without answering or advancing its task."""

import argparse
import json
from pathlib import Path

try:
    from . import autocode_attention_store as store
    from .autocode_taskrun import TaskRun, TaskRunError
except ImportError:
    import autocode_attention_store as store
    from autocode_taskrun import TaskRun, TaskRunError


def parser():
    result = argparse.ArgumentParser(prog="autocode attention", description=__doc__)
    commands = result.add_subparsers(dest="action", required=True)
    ack = commands.add_parser("ack", help="Record this local user's read acknowledgement; launches no model")
    ack.add_argument("--workspace", required=True, help="Project directory")
    ack.add_argument("--run-dir", required=True, help="The saved run")
    ack.add_argument("--item-id", required=True, help="The alert ID the status view or run inventory names")
    return result


def cli(argv):
    args = parser().parse_args(argv)
    try:
        prior = store.inspect(args.item_id, args.workspace, args.run_dir)
        if prior.get("state") == "acknowledged":
            print(json.dumps({"version": 1, "acknowledged": True, "receipt": prior["receipt"], "reconciled": True}))
            return 0
        run = TaskRun(Path(args.workspace), Path(args.run_dir), timeout=5)
        view = run.status()
        current = view.get("attention") or {}
        item = current.get("item") or {}
        if item.get("id") != args.item_id:
            print(
                json.dumps(
                    {
                        "version": 1,
                        "acknowledged": False,
                        "reason": "Alert is resolved or superseded; inspect current status",
                        "current": current,
                    }
                )
            )
            return 2
        receipt = store.acknowledge(args.item_id, item["workspace"], item["run_dir"])
        print(json.dumps({"version": 1, "acknowledged": True, "receipt": receipt, "reconciled": False}))
        return 0
    except (OSError, ValueError, TaskRunError):
        print(
            json.dumps(
                {
                    "version": 1,
                    "acknowledged": False,
                    "reason": "Acknowledgement could not be confirmed; inspect this same alert again",
                }
            )
        )
        return 2
