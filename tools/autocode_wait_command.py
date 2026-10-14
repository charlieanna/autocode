"""`autocode wait`: block until one saved run reaches the person, the end, or loses its controller.

`autocode runs` answers "which run needs me now" once, from what each run last saved;
this command is its blocking form, and it closes the gap a saved checkpoint cannot:
every second it also inspects, read-only, whether a controller still owns the run.
`autocode events --follow` streams how a run got where it is; this says only when to
look. Nothing here starts, resumes or changes a run.

The wait ends the first time one of these holds, and the outcome names which:

- ``complete``  — the run reported TASK_COMPLETE/COMPLETE. Exit 0.
- ``needs_you`` — the status view names what a person must do; the command that
  answers it is printed too. Exit 2.
- ``controller_lost`` — the saved status is still RUNNING but no controller or
  provider owns the run, so it will not advance on its own. Exit 2.
- ``stopped``   — the run stopped otherwise (a status that needs nobody and
  continues nothing). Exit 2.
- ``timeout``   — ``--timeout SECONDS`` elapsed with none of the above. Exit 2.

Exit codes stay 0 and 2 (#885); the outcome line carries the distinction. A run whose
checkpoint cannot be read ends the wait immediately, like every read this family does.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from . import autocode_controller_owner as controller_owner
    from . import autocode_events_command as events
    from . import autocode_registry as registry
    from . import autocode_run_view as run_view
    from . import autocode_util as util
except ImportError:
    import autocode_controller_owner as controller_owner
    import autocode_events_command as events
    import autocode_registry as registry
    import autocode_run_view as run_view
    import autocode_util as util

SCHEMA = 1


def _load(run_dir: Path, workspace: Path) -> tuple[dict | None, str]:
    availability, payload = registry.read_checkpoint(run_dir / "state.json", workspace)
    if availability != "available":
        return None, str(payload.get("message") or availability)
    return payload["state"], ""


def outcome(state: dict, run_dir: Path, workspace: Path) -> dict | None:
    """The wait-ending outcome, or None while the run still works on its own.

    A fresh controller inspection is part of the answer: a saved RUNNING status is a
    claim, and `autocode runs` already shows that claim without testing it.
    """
    status = state.get("status")
    if status in run_view.COMPLETE:
        return {"outcome": "complete", "status": status, "reason": f"run reported {status}"}
    try:
        shown = run_view.action(state, run_dir=run_dir, workspace=workspace)
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        return {
            "outcome": "stopped",
            "status": status,
            "reason": f"status view could not read the checkpoint: {error}",
        }
    need = shown.get("needs") or {}
    kind = need.get("kind")
    if kind and kind != "continue":
        return {
            "outcome": "needs_you",
            "status": status,
            "needs": kind,
            "pause_category": shown.get("pause_category"),
            "pause_category_label": shown.get("pause_category_label"),
            "next_command": shown.get("next_command"),
            "reason": need.get("reason"),
        }
    owner = controller_owner.inspect(state, run_dir, workspace)
    if owner.get("kind") == "ended":
        return {
            "outcome": "controller_lost",
            "status": status,
            "controller": owner.get("reason"),
            "next_command": f"autocode --run-dir {run_dir} --workspace {workspace} --status",
            "reason": owner.get("reason"),
        }
    if status not in run_view.CONTINUE:
        return {"outcome": "stopped", "status": status, "reason": f"run stopped at {status}"}
    return None


def _report(result: dict, run_dir: Path, workspace: Path, waited: float, *, as_json: bool) -> None:
    payload = {
        "schema": SCHEMA,
        "run_dir": str(run_dir),
        "workspace": str(workspace),
        "waited_seconds": round(waited, 1),
        **result,
    }
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    head = f"autocode wait: {payload['outcome']} after {payload['waited_seconds']:g}s"
    if payload.get("reason"):
        head += f" — {payload['reason']}"
    print(head)
    if result.get("next_command"):
        print(f"-> {result['next_command']}")


def wait(run_dir: Path, workspace: Path, *, timeout: float | None = None, as_json: bool = False) -> int:
    """Block until the run reaches the person, the end, or loses its controller."""
    started = util.monotonic()
    deadline = None if timeout is None else util.monotonic() + timeout
    while True:
        state, message = _load(run_dir, workspace)
        if state is None:
            _report(
                {"outcome": "unreadable", "status": None, "reason": message},
                run_dir,
                workspace,
                util.monotonic() - started,
                as_json=as_json,
            )
            return 2
        result = outcome(state, run_dir, workspace)
        if result is not None:
            _report(result, run_dir, workspace, util.monotonic() - started, as_json=as_json)
            return 0 if result["outcome"] == "complete" else 2
        if deadline is not None and util.monotonic() >= deadline:
            owner = controller_owner.inspect(state, run_dir, workspace)
            _report(
                {
                    "outcome": "timeout",
                    "status": state.get("status"),
                    "controller": owner.get("kind"),
                    "reason": f"--timeout {timeout:g}s reached; run still {state.get('status')}",
                },
                run_dir,
                workspace,
                util.monotonic() - started,
                as_json=as_json,
            )
            return 2
        util.sleep(events.POLL_SECONDS)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="autocode wait", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    result.add_argument("--workspace", type=Path, help="project whose saved run to wait on; the run's own by default")
    result.add_argument("--run-dir", type=Path, help="wait on this saved run; otherwise the workspace's only run")
    result.add_argument("--timeout", type=float, help="stop waiting after this many seconds; no bound by default")
    result.add_argument("--json", action="store_true", help="print the outcome as one JSON object on stdout")
    return result


def cli(argv: list[str] | None = None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.timeout is not None and args.timeout <= 0:
        arguments.error("--timeout must be greater than zero")
    try:
        run_dir, workspace = events.resolve(args.workspace, args.run_dir)
        return wait(run_dir, workspace, timeout=args.timeout, as_json=args.json)
    except (OSError, ValueError) as error:
        print(f"autocode wait: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nautocode wait: interrupted; the run is unchanged.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(cli())
