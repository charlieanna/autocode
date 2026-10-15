"""`autocode runs`: which of this project's runs need you, and the command that answers.

One read-only table over several task runs. Each row shows what that run's own
status view says about it: its status, what it needs, and the exact command the
run accepts next. Nothing here starts, resumes or changes a run, and a run whose
checkpoint cannot be read is shown with the reason rather than dropped.

Discovery has two scopes. By default the current project's
``<workspace>/.autocode/runs`` is listed, which covers a run before it is
registered and a crashed run whose pointer is stale. ``--all`` walks the per-user
registry instead, so every project the person has run appears.

A row reports what a run last saved. Unlike that run's own ``--status``, nothing
here inspects a supervised process, so a stage hung mid-flight still reads as
working: use ``--status`` on one run when the question is whether it is alive.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
from pathlib import Path

try:
    from . import autocode_registry as registry
    from . import autocode_run_view as run_view
except ImportError:
    import autocode_registry as registry
    import autocode_run_view as run_view

SCHEMA = 1
# Buckets in display order; what a run needs decides which one it is in.
NEEDS = "needs you"
WORKING = "working"
SETTLED = "settled"
UNREADABLE = "unreadable"
BUCKETS = (NEEDS, WORKING, SETTLED, UNREADABLE)
# A controller is doing work in these statuses; they need no person yet.
WORKING_STATUSES = ("RUNNING", "DISCOVERING", "WAITING_FOR_USER")
NAME_WIDTH = 52
STAMP = "%Y-%m-%dT%H:%M:%S"


def _stamp(seconds: float) -> str:
    return time.strftime(STAMP, time.localtime(seconds))


def _age(stamped: str | None, now: float) -> str:
    """How long since the checkpoint was written, as the person reads it."""
    if not stamped:
        return ""
    try:
        written = time.mktime(time.strptime(stamped, STAMP))
    except ValueError:
        return ""
    seconds = max(0.0, now - written)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds)}s"


def _bucket(need: str | None, status: str | None) -> str:
    if need and need != "continue":
        return NEEDS
    return WORKING if status in WORKING_STATUSES else SETTLED


def unreadable(run_dir: Path, workspace: Path, reason: str) -> dict:
    return {
        "run_dir": str(run_dir),
        "name": run_dir.name,
        "workspace": str(workspace),
        "bucket": UNREADABLE,
        "availability": "unreadable",
        "reason": reason,
    }


def row(workspace: Path, run_dir: Path, now: float) -> dict:
    """Project one run directory onto a display row, reading only its checkpoint."""
    state_path = run_dir / "state.json"
    try:
        updated_at = _stamp(state_path.stat().st_mtime)
    except OSError:
        updated_at = None
    availability, payload = registry.read_checkpoint(state_path, workspace)
    if availability != "available":
        return {
            **unreadable(run_dir, workspace, payload.get("message") or availability),
            "availability": availability,
            "updated_at": updated_at,
        }
    state = payload["state"]
    try:
        shown = run_view.action(state, run_dir=run_dir, workspace=workspace)
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        # A checkpoint another version wrote: the row still has to be shown.
        return {
            **unreadable(run_dir, workspace, f"status view could not read it: {error}"),
            "availability": availability,
            "status": state.get("status"),
            "updated_at": updated_at,
        }
    need = shown["needs"] or {}
    command = shown["next_command"]
    reason = None
    if need.get("abandon_stage"):
        # Only the run itself can tell an uncertain attempt from one whose retained
        # report just needs repair, and only the first may be set aside. Point the
        # row at its own status rather than offering a command that can discard work.
        command = f"autocode --run-dir {shlex.quote(str(run_dir))} --workspace {shlex.quote(str(workspace))} --status"
        reason = f"uncertain attempt {need['abandon_stage']}; its own status names the recovery"
    return {
        "run_dir": str(run_dir),
        "name": run_dir.name,
        "workspace": str(workspace),
        "bucket": _bucket(need.get("kind"), state.get("status")),
        "availability": availability,
        "status": state.get("status"),
        "needs": need.get("kind"),
        "needs_reason": need.get("reason"),
        "pause_category": shown["pause_category"],
        "pause_category_label": shown["pause_category_label"],
        "phase": state.get("phase"),
        "next_stage": state.get("next_stage"),
        "iteration": state.get("iteration"),
        "next_command": command,
        "reason": reason,
        "updated_at": updated_at,
    }


def _pairs(workspace: Path, all_workspaces: bool) -> list[tuple[Path, Path]]:
    """(run directory, its workspace) for one project, or for every registered project."""
    if not all_workspaces:
        runs = Path(workspace) / ".autocode" / "runs"
        if not runs.is_dir():
            return []
        return [(run_dir, Path(workspace)) for run_dir in sorted(p for p in runs.iterdir() if p.is_dir())]
    records = registry.listing()["runs"]
    return [
        (Path(record["run_dir"]), Path(record["workspace"]))
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("run_dir"), str)
        and isinstance(record.get("workspace"), str)
    ]


def rows(workspace: Path, *, all_workspaces: bool = False, now: float | None = None) -> list[dict]:
    """Every run in scope: needing-runs first, then working, settled and unreadable, each by start time."""
    clock = time.time() if now is None else now
    return sorted(
        (row(one_workspace, run_dir, clock) for run_dir, one_workspace in _pairs(Path(workspace), all_workspaces)),
        key=lambda item: (BUCKETS.index(item["bucket"]), item.get("run_dir") or ""),
    )


def counts(entries: list[dict]) -> dict:
    """How many runs sit in each bucket, empty buckets included."""
    return {
        "total": len(entries),
        **{bucket: sum(1 for item in entries if item["bucket"] == bucket) for bucket in BUCKETS},
    }


def _detail(item: dict) -> str:
    if item.get("reason"):
        return str(item["reason"])
    stage = item.get("phase") or item.get("next_stage")
    if item["bucket"] != WORKING:
        stage = None
    iteration = f"iteration {item['iteration']}" if item.get("iteration") else None
    return " · ".join(str(value) for value in (stage, iteration) if value)


def render(entries: list[dict], scope: str, *, now: float | None = None) -> str:
    """Group rows by bucket, each needing run followed by the command that answers it."""
    clock = time.time() if now is None else now
    summary = counts(entries)
    lines = [f"{scope}: {summary['total']} run(s), {summary[NEEDS]} need you"]
    for bucket in BUCKETS:
        group = [item for item in entries if item["bucket"] == bucket]
        if not group:
            continue
        lines += ["", f"  {bucket} ({len(group)})"]
        for item in group:
            label = item.get("pause_category_label") or item.get("pause_category") or item.get("status") or "unreadable"
            head = (
                f"    {_age(item.get('updated_at'), clock):>5}  {item['name'][:NAME_WIDTH]:<{NAME_WIDTH}} {label:<26}"
            )
            lines.append(f"{head} {_detail(item)}".rstrip())
            if item.get("next_command"):
                lines.append(f"{'':11}-> {item['next_command']}")
    return "\n".join(lines)


def parser() -> argparse.ArgumentParser:
    """The arguments `autocode runs` reads; `autocode completion` reads them too."""
    result = argparse.ArgumentParser(
        prog="autocode runs", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    result.add_argument("--workspace", default=".", help="project whose runs to list; refuse it with --all")
    result.add_argument("--all", action="store_true", help="list every registered run, not just this project's")
    result.add_argument("--needs", action="store_true", help="show only the runs waiting on a person")
    result.add_argument("--json", action="store_true", help="print the rows as JSON")
    return result


def cli(argv: list[str] | None = None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    workspace = Path(args.workspace).expanduser().resolve()
    if args.all and str(workspace) != str(Path.cwd()):
        arguments.error("--all already covers every registered project; drop --workspace")
    entries = rows(workspace, all_workspaces=args.all)
    scope = "all registered projects" if args.all else str(workspace)
    if not entries:
        print(f"No runs found for {scope}.", file=sys.stderr)
        return 2
    selected = [item for item in entries if item["bucket"] == NEEDS] if args.needs else entries
    if args.json:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "scope": "registry" if args.all else "workspace",
                    "workspace": None if args.all else str(workspace),
                    "counts": counts(entries),
                    "runs": selected,
                },
                indent=2,
            )
        )
        return 0
    if not selected:
        print(f"{scope}: no run needs you ({len(entries)} run(s) listed).")
        return 0
    print(render(selected, scope))
    if not args.needs:
        print("\nRead-only: nothing here starts, resumes or changes a run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
