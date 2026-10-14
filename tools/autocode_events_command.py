"""`autocode events`: one saved run's recorded events as a JSON-lines stream.

The streaming form of the status view contract: `--status` reports where a run
stands now, and this prints how it got there, one event per line, exactly as the
run recorded it in its own `user_events` list — its approvals, answers, contract
and configuration changes, routing decisions and the recoveries it took on its
own. A provider's transcript is not part of it. Nothing here starts, resumes or
changes a run.

`--follow` keeps printing new events until the run itself says the stream is
over, which is the contract's own answer: a completed run ends it with exit 0,
and a run that needs a person ends it with exit 2 and that person's command on
stderr. Like `autocode runs`, this reads saved checkpoints and inspects no
supervised process, so a stage hung mid-flight keeps the stream open: bound it
with `--timeout`, or ask `autocode --run-dir RUN --status` whether it is alive.

Each line is the run's own record, so `kind` is always present and `at` and
`actor` are present on all but a few records; the remaining fields belong to
that kind alone. The envelope is `docs/schemas/run-event.schema.json`. The
vocabulary of kinds grows with the runner, so a consumer must accept a kind and
a field it has not seen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import argparse
import json
import sys
from pathlib import Path

try:
    from . import autocode_registry as registry
    from . import autocode_run_view as run_view
    from . import autocode_util as util
except ImportError:
    import autocode_registry as registry
    import autocode_run_view as run_view
    import autocode_util as util

POLL_SECONDS = 1.0


def saved_runs(workspace: Path) -> list[Path]:
    """The runs saved in this project, whether or not the registry points at them."""
    root = Path(workspace) / ".autocode" / "runs"
    return sorted(path.parent for path in root.glob("*/state.json")) if root.is_dir() else []


def saved_workspace(run_dir: Path) -> Path | None:
    """The checkout a run says it was saved against, which `--run-dir` alone acts from."""
    try:
        state = json.loads((run_dir / "state.json").read_text())
    except (OSError, ValueError):
        return None
    workspace = state.get("workspace") if isinstance(state, dict) else None
    return Path(workspace) if isinstance(workspace, str) and workspace else None


def resolve(workspace: Path | None, run_dir: Path | None) -> tuple[Path, Path]:
    """(run directory, the workspace its checkpoint was written against)."""
    if run_dir is not None:
        run_dir = run_dir.expanduser().resolve()
        if workspace is not None:
            return run_dir, workspace.expanduser().resolve()
        return run_dir, saved_workspace(run_dir) or Path.cwd()
    workspace = (workspace or Path.cwd()).expanduser().resolve()
    runs = saved_runs(workspace)
    if not runs:
        raise ValueError(f"no saved run in {workspace}")
    if len(runs) > 1:
        named = ", ".join(str(run) for run in runs)
        raise ValueError(f"{len(runs)} runs are saved in {workspace}; name one with --run-dir: {named}")
    return runs[0], workspace


def events_of(state: RunState) -> list[dict]:
    """The run's recorded events, in the order it appended them."""
    return [event for event in state.get("user_events") or [] if isinstance(event, dict)]


def emit(event: dict) -> None:
    print(json.dumps(event), flush=True)


def settled(state: RunState, run_dir: Path, workspace: Path) -> tuple[int, str] | None:
    """Why the stream ends, or None while the run keeps working on its own."""
    status = state.get("status")
    if status in run_view.COMPLETE:
        return 0, f"run {status} after {len(events_of(state))} event(s)"
    try:
        shown = run_view.action(state, run_dir=run_dir, workspace=workspace)
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        return 2, f"status view could not read the checkpoint: {error}"
    need = shown.get("needs") or {}
    kind = need.get("kind")
    if kind and kind != "continue":
        label = shown.get("pause_category_label") or shown.get("pause_category") or status
        note = f"run needs you ({label})"
        return 2, f"{note}; next: {shown['next_command']}" if shown.get("next_command") else note
    if status not in run_view.CONTINUE:
        return 2, f"run stopped at {status}"
    return None


def _load(run_dir: Path, workspace: Path) -> tuple[RunState | None, str]:
    availability, payload = registry.read_checkpoint(run_dir / "state.json", workspace)
    if availability != "available":
        return None, str(payload.get("message") or availability)
    return cast("RunState", payload["state"]), ""


def stream(run_dir: Path, workspace: Path, *, follow: bool = False, timeout: float | None = None) -> int:
    """Print the run's events; with `follow`, keep printing until the run ends the stream."""
    state, message = _load(run_dir, workspace)
    if state is None:
        print(f"autocode events: {message}", file=sys.stderr)
        return 2
    events = events_of(state)
    for event in events:
        emit(event)
    if not follow:
        return 0
    ended = settled(state, run_dir, workspace)
    if ended is not None:
        print(f"autocode events: {ended[1]}", file=sys.stderr)
        return ended[0]
    cursor = len(events)
    deadline = None if timeout is None else util.monotonic() + timeout
    while True:
        if deadline is not None and util.monotonic() >= deadline:
            print(f"autocode events: --timeout {timeout:g}s reached with the run still working", file=sys.stderr)
            return 2
        util.sleep(POLL_SECONDS)
        state, message = _load(run_dir, workspace)
        if state is None:
            print(f"autocode events: {message}", file=sys.stderr)
            return 2
        events = events_of(state)
        for event in events[cursor:]:
            emit(event)
        cursor = len(events)
        ended = settled(state, run_dir, workspace)
        if ended is not None:
            print(f"autocode events: {ended[1]}", file=sys.stderr)
            return ended[0]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="autocode events", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    result.add_argument("--workspace", type=Path, help="project whose saved run to stream; the run's own by default")
    result.add_argument("--run-dir", type=Path, help="stream this saved run; otherwise the workspace's only run")
    result.add_argument("--follow", action="store_true", help="keep printing new events until the run ends the stream")
    result.add_argument(
        "--timeout", type=float, help="with --follow, stop after this many seconds; no bound by default"
    )
    return result


def cli(argv: list[str] | None = None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.timeout is not None and not args.follow:
        arguments.error("--timeout needs --follow")
    if args.timeout is not None and args.timeout <= 0:
        arguments.error("--timeout must be greater than zero")
    try:
        run_dir, workspace = resolve(args.workspace, args.run_dir)
        return stream(run_dir, workspace, follow=args.follow, timeout=args.timeout)
    except (OSError, ValueError) as error:
        print(f"autocode events: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nautocode events: interrupted; the run is unchanged.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(cli())
