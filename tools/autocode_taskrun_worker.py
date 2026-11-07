"""Public task-run driver for a named worker envelope; no private runtime imports."""

import json

try:
    from . import autocode_client_actions as actions
except ImportError:
    import autocode_client_actions as actions


def dispatch(workspace, run_dir, argv, *, execution, fresh=False, command, env, process):
    arguments = actions.worker_transport(execution, list(argv), workspace, run_dir, fresh=fresh)
    reply = process([*command, *arguments], env=env, advancing=False)
    if reply.returncode:
        raise RuntimeError("Durable action was refused: " + (reply.stderr or reply.stdout)[-2000:])
    try:
        receipt = json.loads(reply.stdout)
        if receipt["inspect_command"] != ["autocode", "worker", "inspect", receipt["launch_id"]]:
            raise ValueError("unsupported receipt")
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("Worker receipt is unreadable; inspect before retrying") from None
    return receipt
