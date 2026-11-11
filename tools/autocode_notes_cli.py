"""See and correct what AutoCode has learned about a repository (`autocode notes`, #2943).

``list`` shows the project's notes, each with the receipt of the observation that produced it.
``retract NOTE`` says a note is wrong: it stays recorded as retracted, so the same fact is not
learned again over the correction. ``correct NOTE --text TEXT`` replaces a note's text with the
person's own. A fresh task reads the active notes; a task already running keeps the notes it
started with. Nothing here launches a model or changes a saved run.

``list --json`` is the view clients read: each note carries ``actions.retract`` (an argument
array a client takes as it is) and ``actions.correct`` (one a client completes with the text the
person typed, as its final element).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

try:
    from . import autocode_project_memory_interface as memory_interface
    from . import autocode_project_notes as store
except ImportError:
    import autocode_project_memory_interface as memory_interface
    import autocode_project_notes as store


def parser() -> argparse.ArgumentParser:
    """The arguments `autocode notes` reads; `autocode completion` reads them too."""
    result = argparse.ArgumentParser(prog="autocode notes", description=__doc__.split("\n\n")[0])
    result.add_argument(
        "action", nargs="?", choices=("list", "retract", "correct"), default="list", help="What to do (default: list)"
    )
    result.add_argument("note", nargs="?", help="retract, correct: the note's ID, as list shows it")
    result.add_argument("--text", help="correct: the note's corrected text, one plain sentence or command")
    result.add_argument("--reason", help="retract: why the note is wrong (optional)")
    result.add_argument(
        "--all", action="store_true", help="list: include retracted notes, with who retracted them and why"
    )
    result.add_argument("--workspace", type=Path, default=Path.cwd(), help="project or task worktree (default: cwd)")
    result.add_argument("--json", action="store_true", help="print the result as JSON")
    return result


def _actions(project: Path, note: dict) -> dict:
    if note["retraction"] is not None:
        return {}
    where = ["--workspace", str(project)]
    return {
        "retract": ["notes", "retract", note["id"], *where, "--json"],
        "correct": ["notes", "correct", note["id"], *where, "--json", "--text"],
    }


def listing(project: Path, *, include_retracted=False) -> dict:
    """The `notes list --json` view of a project's notes."""
    loaded = store.load(project)
    rows = loaded["notes"] if include_retracted else store.active(loaded["notes"])
    return {
        "workspace": str(project),
        "path": store.PATH,
        "status": loaded["status"],
        "note": loaded["note"],
        "notes": [{**row, "actions": _actions(project, row)} for row in rows],
    }


def _print_list(view: dict) -> None:
    if view["status"] == "invalid":
        print(view["note"], file=sys.stderr)
    if not view["notes"]:
        print(f"No project notes for {view['workspace']}.")
        return
    for note in view["notes"]:
        receipt = note["receipt"]
        where = f"run {receipt['run']}" if receipt["run"] else "a person's correction"
        state = "" if note["retraction"] is None else " (retracted)"
        print(
            f"{note['id']}  {note['kind']}{state}\n  {note['text']}\n  Learned from {where}: {receipt['observation']}"
        )


def _fail(args, message: str) -> int:
    if args.json:
        print(json.dumps({"error": message}))
    else:
        print(message, file=sys.stderr)
    return 1


def cli(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        project = memory_interface.selected(args.workspace.resolve())
    except (OSError, ValueError) as error:
        return _fail(args, "Cannot read project notes: " + str(error))
    if args.action == "list":
        view = listing(project, include_retracted=args.all)
        if args.json:
            print(json.dumps(view))
        else:
            _print_list(view)
        return 0 if view["status"] != "invalid" else 1
    if not args.note:
        return _fail(args, f"`autocode notes {args.action}` needs the note's ID; `autocode notes list` shows them.")
    if args.action == "correct" and not (args.text or "").strip():
        return _fail(args, "`autocode notes correct` needs the corrected text: --text TEXT.")
    at = dt.datetime.now(dt.UTC).isoformat()
    try:
        if args.action == "retract":
            note = store.retract(project, args.note, reason=(args.reason or "").strip() or None, at=at)
        else:
            note = store.correct(project, args.note, args.text, at=at)
    except KeyError as error:
        return _fail(args, str(error.args[0]))
    except (OSError, ValueError) as error:
        return _fail(args, f"Cannot {args.action} project note {args.note}: {error}")
    if args.json:
        print(json.dumps({"workspace": str(project), "path": store.PATH, "action": args.action, "note": note}))
    elif args.action == "retract":
        print(f"Retracted {note['id']}; a new task will not be given it.")
    else:
        print(f"Replaced {args.note} with {note['id']}: {note['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
