"""Request conversation boundary pauses while the existing coordinator advances.

The observer never launches or resumes a task. It reads public observations and
submits only the participant's named pause, with a stable inbox-bound identity.
"""

import contextlib
import threading
from pathlib import Path

try:
    from . import autocode_client_actions as actions
    from . import autocode_coordination_conversation as policy
    from . import autocode_coordination_conversation_runtime as runtime
    from . import autocode_coordination_message_inbox as inbox
    from . import autocode_coordination_store as store
    from . import autocode_taskrun as tasks
    from . import autocode_util as util
except ImportError:
    import autocode_client_actions as actions
    import autocode_coordination_conversation as policy
    import autocode_coordination_conversation_runtime as runtime
    import autocode_coordination_message_inbox as inbox
    import autocode_coordination_store as store
    import autocode_taskrun as tasks
    import autocode_util as util


def pause_pending(run_dir, *, command=tasks.AUTOCODE, stopped=None):
    """Handle a newly arriving message without waiting for the advance lock."""
    document = store.read(run_dir)
    if not document or store.cancelled(run_dir) or (stopped is not None and stopped.is_set()):
        return []
    conversation = document.get("conversation") or {}
    # The owner admits a conversation only after all participants settle and
    # re-observes them for each step. This watcher only handles new arrivals
    # while ordinary work is running, not an already admitted assessment.
    if conversation.get("active"):
        return []
    finished = {row["request_id"] for row in conversation.get("results", [])}
    message = next((row for row in inbox.read(run_dir) if row["id"] not in finished), None)
    if not message:
        return []
    starting = (document.get("pending") or {}).get("target")
    rows = [row for row in document["members"] if row.get("started") or row["target"] == starting]
    if document.get("joint"):
        rows.append({**document["joint"], "target": "Joint regression"})
    receipts = []
    for row in rows:
        if stopped is not None and stopped.is_set():
            break
        run = tasks.TaskRun(Path(row["workspace"]), Path(row["run_dir"]), command=command)
        run.timeout = 5
        view = runtime.observation(run)
        offer = view.get("pause") or {}
        if not actions.working(view) or not offer.get("next_command") or offer.get("requested"):
            continue
        identifier = policy.request_id(document, message, row["target"], "pause")
        argv = actions.pause(view, identifier, run.workspace, run.run_dir)
        if stopped is not None and stopped.is_set():
            break
        runtime.submit(run, argv, {"id": identifier, "kind": "pause", "text": ""})
        receipts.append(identifier)
    return receipts


def poll(run_dir, command, stopped):
    """Retain observation failures, then re-observe; never infer worker termination."""
    while not stopped.wait(0.25):
        try:
            pause_pending(run_dir, command=command, stopped=stopped)
        except (OSError, ValueError, RuntimeError) as error:
            with contextlib.suppress(OSError):
                util.atomic_json(
                    store.root(run_dir) / "message-watch-error.json",
                    {
                        "at": util.now(),
                        "error_type": type(error).__name__,
                        "effect": "Public observation or pause receipt unavailable; no worker was launched or resumed",
                    },
                )


@contextlib.contextmanager
def watch(run_dir, *, command=tasks.AUTOCODE):
    """The existing coordinator owns the observer for exactly one advancing action."""
    stopped = threading.Event()
    thread = threading.Thread(target=poll, args=(run_dir, command, stopped), daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=40)
        if thread.is_alive():
            raise store.CoordinationError("The conversation pause observer did not settle; retain the current action")
