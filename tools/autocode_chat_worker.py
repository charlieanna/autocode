"""Terminal viewer use of the core's named durable-worker transport.

No task state access or worker signalling. Losing this viewer at any point
leaves the retained core launch for inspection, never a paid replay.
"""

import contextlib
import json
import time
from pathlib import Path

try:
    from . import autocode_client_actions as actions
except ImportError:
    import autocode_client_actions as actions


def advance(client, argv, env=None):
    literal = list(argv)
    fresh = "--new-run-dir" in literal
    for flag, expected in (
        ("--workspace", client.workspace),
        ("--run-dir", client.run_dir),
        ("--new-run-dir", client.run_dir),
    ):
        if flag in literal:
            index = literal.index(flag)
            if literal.count(flag) != 1 or literal[index + 1] != str(expected):
                raise actions.ClientGap("Worker action names another task destination")
            del literal[index : index + 2]
    execution = client._execution_offer if fresh else (client._status_view or {}).get("execution") or {}
    command = actions.worker_transport(execution, literal, client.workspace, client.run_dir, fresh=fresh)
    reply = client.cli(command, env=env)
    if reply.returncode:
        raise actions.ClientGap("Durable launch was refused: " + (reply.stderr or reply.stdout)[-2000:])
    try:
        receipt = json.loads(reply.stdout)
        launch_id = receipt["launch_id"]
        inspection = receipt["inspect_command"]
        if inspection != ["autocode", "worker", "inspect", launch_id]:
            raise ValueError("unsupported inspection action")
    except (ValueError, KeyError, TypeError):
        raise actions.ClientGap("Worker launch receipt is unreadable; inspect before retrying") from None
    client.say("Durable worker launched. /quit or Ctrl-D detaches this viewer; Ctrl-C requests a boundary pause.")
    if client._launched_worker is not None:
        client._launched_worker(receipt)
    deadline = time.monotonic() + 30
    client._attached = True
    try:
        while not receipt.get("ready"):
            if time.monotonic() >= deadline:
                raise actions.ClientGap("Core launch binding is pending. Inspect with: " + " ".join(inspection))
            if (receipt.get("service") or {}).get("active_state") in ("failed", "inactive"):
                raise actions.ClientGap(
                    "Worker ended before binding. Inspect its retained receipt and log: " + " ".join(inspection)
                )
            try:
                client._wait(0.1)
            except KeyboardInterrupt:
                client.say("Core binding is pending; no pause is offered yet. Detaching leaves the launch unchanged.")
            checked = client.cli(inspection[1:])
            if checked.returncode:
                raise actions.ClientGap("Launch inspection unavailable; do not replay the request")
            receipt = json.loads(checked.stdout)
        bound = receipt["binding"]
        client.workspace, client.run_dir = Path(bound["workspace"]), Path(bound["run_dir"])
        with contextlib.suppress(actions.ClientGap):
            client.show(client.status())
        client.watch()
    finally:
        client._attached = False
