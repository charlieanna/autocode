"""`autocode changeset`: link a run in another registered repository to a run whose request spans it (#2066).

A layer above the task-run interface (AGENTS.md rule 5): it reads runs only through
``autocode_taskrun.TaskRun`` and their status views, never ``state.json`` or ``autocode.py``.

- ``link --run-dir RUN --target TARGET --workspace PROJECT``: ``TARGET`` must be one of the source
  run's ``view.scope.external_parts``, and ``PROJECT`` an available project in the AutoCode registry
  (its path or registry id), never a path the model or the task text supplied. The linked run starts
  with the ready brief the source run's ``view.scope.briefs`` gives for that target, advances to its
  first stop like any other ``TaskRun.start``, and joins the source run's change set
  (autocode_changeset_store). Arguments after ``--`` are the run options the linked run starts with;
- ``status --run-dir RUN``: the run's change set with each member's status, needs and next command,
  read from that member's own status view;
- ``list``: every change set in the registry's directory.

Linking changes nothing in the source run: it still asks its scope question, and its plan still
records the linked target as not covered there. Joint approval and joint verification are later
steps of #2066.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from . import autocode_changeset_store as store
    from . import autocode_registry as registry
    from . import autocode_taskrun as taskrun
except ImportError:
    import autocode_changeset_store as store
    import autocode_registry as registry
    import autocode_taskrun as taskrun


class LinkRefused(Exception):
    """A link the source run's view or the registry does not allow."""


def _workspace_of(run_dir: Path) -> Path:
    """A run directory is ``<workspace>/.autocode/runs/<run>``; anything else is not a run."""
    run_dir = run_dir.resolve()
    if run_dir.parent.name != "runs" or run_dir.parent.parent.name != ".autocode":
        raise LinkRefused(f"{run_dir} is not a run directory (<workspace>/.autocode/runs/<run>)")
    return run_dir.parents[2]


def registered_project(choice: str) -> Path:
    """The available registered project ``choice`` names by path or registry id."""
    candidate = str(Path(choice).expanduser().resolve())
    rows = registry.listing()["workspaces"]
    for row in rows:
        if choice == row.get("id") or candidate == row.get("workspace"):
            if row.get("availability") != "available":
                raise LinkRefused(f"registered project {row.get('workspace')} is {row.get('availability')}")
            return Path(row["workspace"])
    known = ", ".join(sorted(str(row.get("workspace")) for row in rows if row.get("workspace"))) or "none"
    raise LinkRefused(f"{choice} is not a registered project (registered: {known})")


def source_view(run_dir: Path, *, command=taskrun.AUTOCODE) -> tuple[Path, dict]:
    workspace = _workspace_of(run_dir)
    return workspace, taskrun.TaskRun(workspace, run_dir.resolve(), command=tuple(command)).status()


def link(run_dir, target: str, project: str, *, options=(), command=taskrun.AUTOCODE, timeout=None) -> dict:
    """Start the linked run for ``target`` in ``project`` and record it; return the change set's summary."""
    run_dir = Path(run_dir).resolve()
    workspace, view = source_view(run_dir, command=command)
    scope = view.get("scope") or {}
    targets = [part.get("target") for part in scope.get("external_parts") or []]
    if target not in targets:
        raise LinkRefused(
            f"{target!r} is not work this run found in another repository (scope.external_parts: {targets or 'none'})"
        )
    brief = next((row["brief"] for row in scope.get("briefs") or [] if row.get("repository") == target), None)
    if not brief:
        raise LinkRefused(f"the run's status gives no brief for {target}")
    member_workspace = registered_project(project)
    if member_workspace == workspace:
        raise LinkRefused("the linked run must be in another repository than the source run")
    record = store.reserve(
        source_workspace=workspace, source_run_dir=run_dir, target=target, workspace=member_workspace, brief=brief
    )
    try:
        member = taskrun.TaskRun.start(
            member_workspace, brief, options=tuple(options), command=tuple(command), timeout=timeout
        )
    except taskrun.TaskRunError as error:
        if error.run_dir is None:
            store.release(record["id"], target)
        else:
            store.bind(record["id"], target, error.run_dir)
        raise
    store.bind(record["id"], target, member.run_dir)
    return status(run_dir, command=command)


def _member_status(workspace, run_dir, command) -> dict:
    if not run_dir:
        return {"status": "STARTING", "needs": None, "next_command": None}
    try:
        view = taskrun.TaskRun(Path(workspace), Path(run_dir), command=tuple(command)).status()
    except taskrun.TaskRunError as error:
        return {"status": "UNREADABLE", "needs": None, "next_command": None, "error": str(error)}
    return {
        "status": view.get("status"),
        "needs": (view.get("needs") or {}).get("kind"),
        "next_command": view.get("next_command"),
    }


def status(run_dir, *, command=taskrun.AUTOCODE) -> dict:
    """The change set ``run_dir`` belongs to, each run with what its own status view says now."""
    record = store.find(run_dir)
    if record is None:
        raise LinkRefused(f"{Path(run_dir).resolve()} is in no change set")
    source = record["source"]
    runs = [{"role": "source", "target": Path(source["workspace"]).name, **source}]
    runs += [
        {"role": "member", "target": row["target"], "workspace": row["workspace"], "run_dir": row.get("run_dir")}
        for row in record["members"]
    ]
    for row in runs:
        row.update(_member_status(row["workspace"], row["run_dir"], command))
    return {"changeset": record["id"], "created_at": record.get("created_at"), "runs": runs}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="autocode changeset", description="Link runs in different registered repositories for one request."
    )
    commands = result.add_subparsers(dest="command", required=True)
    linking = commands.add_parser("link", help="start a linked run in another registered project")
    linking.add_argument("--run-dir", type=Path, required=True, help="the run whose request spans another repository")
    linking.add_argument("--target", required=True, help="the repository as the run's scope.external_parts names it")
    linking.add_argument("--workspace", required=True, help="the registered project to start the linked run in")
    linking.add_argument("--json", action="store_true", help="print the change set as JSON")
    linking.add_argument("options", nargs=argparse.REMAINDER, help="after --: run options for the linked run")
    showing = commands.add_parser("status", help="the change set a run belongs to, with each run's status")
    showing.add_argument("--run-dir", type=Path, required=True, help="any run of the change set")
    showing.add_argument("--json", action="store_true", help="print the change set as JSON")
    listing = commands.add_parser("list", help="every change set")
    listing.add_argument("--json", action="store_true", help="print the change sets as JSON")
    return result


def _render(summary: dict) -> str:
    lines = [f"Change set {summary['changeset']}:"]
    for row in summary["runs"]:
        needs = f", needs {row['needs']}" if row.get("needs") else ""
        lines.append(f"  {row['target']} ({row['role']}): {row['status']}{needs}  {row.get('run_dir') or ''}")
    return "\n".join(lines)


def cli(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "list":
            result: dict = {"changesets": store.records()}
        elif args.command == "status":
            result = status(args.run_dir)
        else:
            options = args.options[1:] if args.options[:1] == ["--"] else args.options
            result = link(args.run_dir, args.target, args.workspace, options=options)
    except (LinkRefused, store.ChangesetError, registry.RegistryError, taskrun.TaskRunError) as error:
        message = str(error)
        if args.json:
            print(json.dumps({"error": message}))
        else:
            print(f"autocode changeset {args.command}: {message}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.command == "list":
        print(
            "\n".join(f"{row['id']}: {row['source']['run_dir']}" for row in result["changesets"]) or "No change sets."
        )
    else:
        print(_render(result))
    return 0
