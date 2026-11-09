"""Terminal viewer use of the core's named durable-worker transport.

No task state access or worker signalling. Losing this viewer at any point
leaves the retained core launch for inspection, never a paid replay.
"""

import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

try:
    from . import autocode_chat_polling as polling
    from . import autocode_client_actions as actions
except ImportError:
    import autocode_chat_polling as polling
    import autocode_client_actions as actions


def launch_command(client, command, env=None):
    """Keep Ctrl-C in the viewer while the short core launcher returns its receipt.

    This child is only the service launcher. The manager-owned controller never
    inherits its terminal or output handle. No timeout/exit here kills a worker.
    """
    with tempfile.TemporaryFile("w+b") as log:
        child = subprocess.Popen(
            [*client.command, *command],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**(client.env or os.environ), **(env or {})},
            cwd=client.workspace,
            start_new_session=True,
        )
        deadline, noticed = time.monotonic() + polling.READ_TIMEOUT, False
        try:
            while child.poll() is None:
                if time.monotonic() >= deadline:
                    raise actions.ClientGap("Launch receipt is pending; inspect retained launches before retrying")
                try:
                    pause = client._wait(0.05) == "pause"
                except KeyboardInterrupt:
                    pause = True
                if pause:
                    client._pause_pending = True
                    if not noticed:
                        client.say(
                            "Launch binding is pending; the boundary pause will be requested when the core offers it."
                        )
                        noticed = True
        finally:
            if child.poll() is None:
                # The launch is retained by design, but its launcher must not linger as a zombie:
                # a daemon thread reaps it whenever it exits (the poller decodes child bytes the
                # same tolerant way, autocode_chat_polling).
                threading.Thread(target=child.wait, name="autocode-chat-launch-reap", daemon=True).start()
        log.seek(0)
        return subprocess.CompletedProcess(command, child.returncode, log.read().decode("utf-8", "replace"), "")


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
    reply = launch_command(client, command, env=env)
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
    client.say("Launch recorded; checking its core binding. /quit or Ctrl-D detaches without replaying it.")
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
                if client._wait(0.1) == "pause":
                    client._pause_pending = True
            except KeyboardInterrupt:
                client._pause_pending = True
                client.say("Core binding is pending; the boundary pause will be requested when available.")
            try:
                checked = client.cli(inspection[1:])
            except KeyboardInterrupt:
                client._pause_pending = True
                continue  # Inspection is read-only; the launch is never repeated.
            if checked.returncode:
                raise actions.ClientGap("Launch inspection unavailable; do not replay the request")
            try:
                receipt = json.loads(checked.stdout)
                if not isinstance(receipt, dict):
                    raise ValueError("not an object")
            except ValueError:
                # The first receipt parse above is guarded the same way: a malformed body is a
                # gap about the retained launch, never an exception that ends the whole chat.
                raise actions.ClientGap("Launch inspection is unreadable; do not replay the request") from None
        try:
            bound = receipt["binding"]
            client.workspace, client.run_dir = Path(bound["workspace"]), Path(bound["run_dir"])
        except (KeyError, TypeError):
            raise actions.ClientGap(
                "The launch recorded no core binding. Inspect the retained launch: " + " ".join(inspection)
            ) from None
        client.say("Durable worker attached. Ctrl-C requests only a boundary pause; /quit or Ctrl-D detaches.")
        if client._pause_pending:
            client._pause_pending = False
            try:
                client.request_pause(again="This viewer can only request a boundary pause.")
            except KeyboardInterrupt:
                client.say("This viewer can only request a boundary pause; no worker signal was sent.")
        client.watch()
    finally:
        client._attached = False
