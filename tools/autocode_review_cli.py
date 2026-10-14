"""Inspect and decide pending human reviews through the public task-run interface."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

try:
    from . import autocode_review_diff as review_diff
    from . import autocode_review_presentation as presentation
    from . import autocode_util as util
    from .autocode_taskrun import TaskRun, TaskRunError
except ImportError:
    import autocode_review_diff as review_diff
    import autocode_review_presentation as presentation
    import autocode_util as util
    from autocode_taskrun import TaskRun, TaskRunError


def parser():
    result = argparse.ArgumentParser(prog="autocode review", description=__doc__)
    result.add_argument(
        "--workspace", type=Path, default=Path.cwd(), help="task workspace (default: current directory)"
    )
    result.add_argument("--run-dir", type=Path, help="saved run; otherwise attach to the workspace's only run")
    display = result.add_mutually_exclusive_group()
    display.add_argument(
        "--json", action="store_true", help="print the review evidence as JSON without asking for decisions"
    )
    display.add_argument("--show", action="store_true", help="print the review evidence without asking for decisions")
    return result


def _read(prompt):
    print(prompt, end="", flush=True)
    line = sys.stdin.readline()
    return None if not line else line.strip()


def _next_action(view, run):
    need = view.get("needs") or {}
    command = shlex.join(["autocode", "--workspace", str(run.workspace), "--run-dir", str(run.run_dir)])
    if need.get("kind") == "continue":
        return "Continue with: " + command
    if need.get("kind") == "review":
        return "Pending human reviews: " + ", ".join(need.get("criteria") or [])
    if need:
        reason = str(need.get("reason") or need.get("question") or need.get("kind"))
        return f"Next: {reason}. Inspect the current action with: {command} --explain"
    return "Run status: " + str(view.get("status") or "unknown")


def review(run, *, show=False, as_json=False):
    """Display a fresh snapshot; submit only decisions the reader explicitly enters."""
    view = run.status(inspect_evidence=True)
    data = presentation.review_data(view)
    data["diff"] = review_diff.collect(run.workspace, view)
    if as_json:
        print(json.dumps(data, indent=2))
        return 0
    print("Human review: " + presentation.display_text(data["status"]))
    print("Workspace: " + presentation.display_text(str(run.workspace)))
    print("Run: " + presentation.display_text(str(run.run_dir)))
    pending = [row for row in data["criteria"] if row["pending"]]
    if not pending:
        print("No criteria are awaiting human review.")
        print(presentation.display_text(_next_action(view, run)))
        return 0
    if data.get("question"):
        print(presentation.display_text(data["question"]))
    print("Evidence freshness: " + presentation.display_text(data.get("freshness") or "unknown"))
    if data.get("reasons"):
        print("Evidence: " + presentation.display_text("; ".join(map(str, data["reasons"]))))
    for row in pending:
        print("\n" + presentation.render_review({**row, "diff_hunks": data["diff"]}))
        if show:
            continue
        token = data.get("token")
        if not token:
            raise TaskRunError("No current human-review token is published; inspect autocode status")
        while True:
            answer = _read("Approve, reject, or skip? [a/r/s] ")
            if answer is None:
                print("\nEnd of input; remaining reviews are unchanged.")
                return 0
            choice = answer.casefold()
            if choice in {"skip", "s", ""}:
                break
            if choice in {"approve", "a"}:
                view = run.approve_review(row["id"], token)
                print("Approved " + presentation.display_text(row["id"]) + ".")
                break
            if choice in {"reject", "r"}:
                note = _read("Rejection note: ")
                if note is None:
                    print("\nEnd of input; no rejection was submitted.")
                    return 0
                if not note:
                    print("A rejection needs a nonempty note.")
                    continue
                view = run.reject_review(row["id"], token, note)
                print("Rejected " + presentation.display_text(row["id"]) + "; your note is saved for revision.")
                print(presentation.display_text(_next_action(view, run)))
                return 0
            print("Enter approve, reject, or skip.")
    if not show:
        print(presentation.display_text(_next_action(view, run)))
    return 0


def cli(argv=None):
    args = parser().parse_args(argv)
    try:
        workspace = args.workspace.resolve()
        run = TaskRun(workspace, args.run_dir.resolve()) if args.run_dir is not None else TaskRun.attach(workspace)
        if run is None:
            raise TaskRunError(f"No saved run in {workspace}")
        return review(run, show=args.show, as_json=args.json)
    except (TaskRunError, OSError, ValueError) as error:
        print("autocode review: " + presentation.display_text(util.redact(str(error))), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nReview interrupted; unsubmitted decisions are unchanged.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(cli())
