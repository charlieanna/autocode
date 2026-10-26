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
- ``list``: every change set in the registry's directory;
- ``approve --run-dir RUN --set-token TOKEN``: approve every run's plan at once (#2066, step 5).
  ``status`` offers a ``set_token`` only while every run of the set waits for plan approval with a
  complete plan it shows (autocode_client_actions.plan_for_approval). The set token is a digest of
  those runs' own plan tokens, so a plan revision in any run changes it. ``approve`` reads every
  run's view again, refuses a token that no longer matches, and then approves each run with its own
  plan token, the source run first. An approval therefore still binds to plans the person was shown.

No run builds before its own plan is approved, so approving through the set keeps every run out of
the Builder until all of them are approved. Approving one run alone (its own ``--approve-goal``)
stays an explicit person action that releases that run by itself.

Linking changes nothing in the source run: it still asks its scope question, and its plan still
records the linked target as not covered there.

Checking the runs together and delivering them (``check-command``, ``verify``, ``waive``,
``reopen``, ``order``, ``pr-body``; #2066 step 6) is autocode_changeset_delivery; ``status``
carries its ``verification``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

try:
    from . import autocode_changeset_delivery as delivery
    from . import autocode_changeset_store as store
    from . import autocode_client_actions as actions
    from . import autocode_registry as registry
    from . import autocode_taskrun as taskrun
except ImportError:
    import autocode_changeset_delivery as delivery
    import autocode_changeset_store as store
    import autocode_client_actions as actions
    import autocode_registry as registry
    import autocode_taskrun as taskrun

SET_TOKEN_ENV = "AUTOCODE_CHANGESET_TOKEN"  # read when --set-token is "-", to keep the token out of argv


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
    empty = {"needs": None, "next_command": None, "plan_token": None, "plan": None}
    if not run_dir:
        return {"status": "STARTING", **empty}
    try:
        view = taskrun.TaskRun(Path(workspace), Path(run_dir), command=tuple(command)).status()
    except taskrun.TaskRunError as error:
        return {"status": "UNREADABLE", **empty, "error": str(error)}
    need = view.get("needs") or {}
    plan = actions.plan_for_approval(view) if need.get("kind") == "approve_plan" else None
    return {
        "status": view.get("status"),
        "needs": need.get("kind"),
        "next_command": view.get("next_command"),
        # Only a complete plan the view shows can be approved through the set.
        "plan_token": plan["token"] if plan else None,
        "plan": plan,
    }


def set_token(runs: list[dict]) -> str | None:
    """The token that approves every run's shown plan at once; None unless each run waits for one."""
    if not runs or not all(row.get("plan_token") for row in runs):
        return None
    lines = sorted(f"{row['run_dir']}={row['plan_token']}" for row in runs)
    return "set-" + hashlib.sha256("\n".join(lines).encode()).hexdigest()[:32]


def _approval(summary: dict) -> dict:
    token = set_token(summary["runs"])
    waiting = [row["target"] for row in summary["runs"] if not row.get("plan_token")]
    source = summary["runs"][0]["run_dir"]
    return {
        "set_token": token,
        # The runs whose plan is not yet waiting for approval; the set cannot be approved before them.
        "waiting_on": waiting,
        "next_command": ["autocode", "changeset", "approve", "--run-dir", source, "--set-token", token]
        if token
        else None,
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
    summary = {"changeset": record["id"], "created_at": record.get("created_at"), "runs": runs}
    summary["approval"] = _approval(summary)
    summary["verification"] = delivery.view(summary, record)
    return summary


def uncovered(summary: dict, *, command=taskrun.AUTOCODE) -> list[str]:
    """The source run's external parts no run of the set covers, as ``TARGET: WORK``."""
    _, view = source_view(Path(summary["runs"][0]["run_dir"]), command=command)
    linked = {row["target"] for row in summary["runs"]}
    return [
        f"{part.get('target')}: {part.get('work', '')}".rstrip(": ")
        for part in (view.get("scope") or {}).get("external_parts") or []
        if part.get("target") not in linked
    ]


def deliver(name: str, args) -> dict:
    """Run one verification or delivery action against the set's fresh status."""
    summary = status(args.run_dir)
    record = store.find(args.run_dir) or {}
    if name == "check-command":
        options = args.check[1:] if args.check[:1] == ["--"] else args.check
        delivery.set_check(summary, record, args.within, options)
    elif name == "verify":
        delivery.verify(summary, record)
    elif name == "waive":
        delivery.waive(summary, record, args.reason)
    elif name == "reopen":
        delivery.reopen(summary, record, args.target, args.message)
    elif name == "order":
        delivery.set_order(summary, record, args.targets)
    elif name == "pr-body":
        return {"body": delivery.pr_body(summary, record, args.target, uncovered(summary))}
    return status(args.run_dir)


def approve(run_dir, token: str, *, command=taskrun.AUTOCODE) -> dict:
    """Approve every run's plan with its own token, once ``token`` still matches every shown plan."""
    fresh = status(run_dir, command=command)
    current = fresh["approval"]["set_token"]
    if current is None:
        waiting = ", ".join(fresh["approval"]["waiting_on"])
        raise LinkRefused(f"the change set cannot be approved yet: waiting on {waiting}")
    if token != current:
        raise LinkRefused("a plan in the change set changed since it was shown; read `changeset status` again")
    approved: list[str] = []
    for row in fresh["runs"]:
        run = taskrun.TaskRun(Path(row["workspace"]), Path(row["run_dir"]), command=tuple(command))
        try:
            run.approve_plan(row["plan_token"])
        except taskrun.TaskRunError as error:
            done = ", ".join(approved) or "none"
            raise LinkRefused(f"approving {row['target']} failed after approving {done}: {error}") from error
        approved.append(row["target"])
    return status(run_dir, command=command)


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
    approving = commands.add_parser("approve", help="approve every run's shown plan at once")
    approving.add_argument("--run-dir", type=Path, required=True, help="any run of the change set")
    approving.add_argument(
        "--set-token", required=True, help=f"the set_token `changeset status` showed; - reads {SET_TOKEN_ENV}"
    )
    approving.add_argument("--json", action="store_true", help="print the change set as JSON")
    checking = commands.add_parser("check-command", help="name the integration command run once all runs complete")
    checking.add_argument("--run-dir", type=Path, required=True, help="any run of the change set")
    checking.add_argument("--in", dest="within", required=True, help="the run (repository) whose workspace it runs in")
    checking.add_argument("--json", action="store_true", help="print the change set as JSON")
    checking.add_argument("check", nargs=argparse.REMAINDER, help="after --: the command, as an argument array")
    verifying = commands.add_parser("verify", help="run the integration command against every run's code")
    verifying.add_argument("--run-dir", type=Path, required=True, help="any run of the change set")
    verifying.add_argument("--json", action="store_true", help="print the change set as JSON")
    waiving = commands.add_parser("waive", help="accept the set without a passing integration check")
    waiving.add_argument("--run-dir", type=Path, required=True, help="any run of the change set")
    waiving.add_argument("--reason", required=True, help="why the set is accepted without a passing check")
    waiving.add_argument("--json", action="store_true", help="print the change set as JSON")
    reopening = commands.add_parser("reopen", help="send the run that diverged a follow-up")
    reopening.add_argument("--run-dir", type=Path, required=True, help="any run of the change set")
    reopening.add_argument("--target", required=True, help="the run (repository) to reopen")
    reopening.add_argument("--message", help="the follow-up; default: the failed check's output")
    reopening.add_argument("--json", action="store_true", help="print the change set as JSON")
    ordering = commands.add_parser("order", help="set the order the pull requests merge in")
    ordering.add_argument("--run-dir", type=Path, required=True, help="any run of the change set")
    ordering.add_argument("targets", nargs="+", help="every run (repository) of the set, first to merge first")
    ordering.add_argument("--json", action="store_true", help="print the change set as JSON")
    body = commands.add_parser("pr-body", help="the section a run's pull request carries")
    body.add_argument("--run-dir", type=Path, required=True, help="any run of the change set")
    body.add_argument("--target", required=True, help="the run (repository) whose pull request it is")
    body.add_argument("--json", action="store_true", help="print the section as JSON")
    listing = commands.add_parser("list", help="every change set")
    listing.add_argument("--json", action="store_true", help="print the change sets as JSON")
    return result


def _render(summary: dict) -> str:
    lines = [f"Change set {summary['changeset']}:"]
    for row in summary["runs"]:
        needs = f", needs {row['needs']}" if row.get("needs") else ""
        lines.append(f"  {row['target']} ({row['role']}): {row['status']}{needs}  {row.get('run_dir') or ''}")
        for criterion in (row.get("plan") or {}).get("acceptance_criteria") or []:
            lines.append(f"    {criterion['id']}: {criterion['criterion']}")
    approval = summary.get("approval") or {}
    if approval.get("next_command"):
        lines.append("Approve every plan above at once: " + " ".join(approval["next_command"]))
    elif approval.get("waiting_on"):
        lines.append("Set approval waits for a plan from: " + ", ".join(approval["waiting_on"]))
    verification = summary.get("verification") or {}
    if verification.get("done"):
        lines.append("Checked together: done. Merge order: " + ", then ".join(verification["merge_order"]))
    elif verification.get("next_command"):
        lines.append("Check the runs together: " + " ".join(verification["next_command"]))
    elif (verification.get("result") or {}).get("current"):
        lines.append("The integration check failed: reopen the run that diverged, or waive it with a reason.")
    return "\n".join(lines)


def cli(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "list":
            result: dict = {"changesets": store.records()}
        elif args.command == "status":
            result = status(args.run_dir)
        elif args.command in ("check-command", "verify", "waive", "reopen", "order", "pr-body"):
            result = deliver(args.command, args)
        elif args.command == "approve":
            token = os.environ.get(SET_TOKEN_ENV, "") if args.set_token == "-" else args.set_token
            result = approve(args.run_dir, token)
        else:
            options = args.options[1:] if args.options[:1] == ["--"] else args.options
            result = link(args.run_dir, args.target, args.workspace, options=options)
    except (
        LinkRefused,
        delivery.DeliveryRefused,
        store.ChangesetError,
        registry.RegistryError,
        taskrun.TaskRunError,
    ) as error:
        message = str(error)
        if args.json:
            print(json.dumps({"error": message}))
        else:
            print(f"autocode changeset {args.command}: {message}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.command == "pr-body":
        print(result["body"], end="")
    elif args.command == "list":
        print(
            "\n".join(f"{row['id']}: {row['source']['run_dir']}" for row in result["changesets"]) or "No change sets."
        )
    else:
        print(_render(result))
    return 0
