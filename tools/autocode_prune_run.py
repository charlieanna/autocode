"""`autocode prune-run RUN [--yes]`: delete settled evidence bytes, keep every receipt and hash.

A run's disk grows with every attempt: each launch retains a full verification
copy (tree plus its standalone Git objects) and each admitted job keeps a
byte-for-byte source store for later restoration (issue #971). Once an attempt
is settled, those bytes are evidence in name only — the manifests, hashes and
receipts that prove what was there survive in the run records — while the live
re-read paths are gone: `autocode capture` re-validates only a live stage's
copy, and job-source restoration runs only for a still-pending failure.

This command lists what is deletable and why, and deletes only with --yes
(the clean-worktrees pattern). It is deliberately fail-safe: a directory whose
bytes any state record still names at file level, that belongs to a live stage
or a pending failure recovery, or that is the newest of its kind is kept, with
the referencing strings shown. Run state is never modified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import argparse
import json
import shutil
import sys
from pathlib import Path

try:
    from . import autocode_stop as stop
    from . import autocode_util as util
except ImportError:
    import autocode_stop as stop
    import autocode_util as util

KINDS = ("verification-copy", "source-store")
PENDING_FIELDS = ("active_stage", "job_failure", "recovery_context", "stuck_investigation", "pending_report_repair")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _bytes(path):
    total = 0
    for child in Path(path).rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _blocking_references(state: RunState, directory):
    """State strings naming files inside the directory: someone may still read those bytes.

    The manifest (verification-copy.json) and the tree root (a captured cwd)
    are provenance, not re-reads: they name what was there and stay accurate
    after the bytes are gone.
    """
    prefix = str(directory) + "/"
    allowed = {"verification-copy.json", "scratch/verification"}
    found = []
    for text in _strings(state):
        if not text.startswith(prefix):
            continue
        relative = text[len(prefix) :]
        if relative not in allowed:
            found.append(text)
    return found


def _finished(state: RunState):
    """A finished run (TASK_COMPLETE, or a durable stop applied) never restores or captures again."""
    return state.get("status") == "TASK_COMPLETE" or stop.applied_stop(state) is not None


def _pending_for(state: RunState, stem):
    """Whether any live/pending state still names this attempt (its restore or capture may run)."""
    for field in PENDING_FIELDS:
        for text in _strings(state.get(field)):
            if stem and stem in text:
                return f"state[{field!r}] still names {stem}"
    return ""


def candidates(state: RunState, run_dir):
    """Classify every retained evidence directory: what is deletable, what is kept, and why."""
    run = Path(run_dir)
    rows: dict = {"verification-copy": [], "source-store": []}
    for directory in sorted(run.glob("tool-containment-*")):
        if directory.is_dir() and not directory.is_symlink():
            rows["verification-copy"].append(directory)
    for directory in sorted(run.glob("iterations/*/*.source")):
        if directory.is_dir() and not directory.is_symlink():
            rows["source-store"].append(directory)
    plan: dict = {"delete": [], "keep": []}
    for kind in KINDS:
        newest = max(rows[kind], key=lambda path: path.stat().st_mtime, default=None)
        for directory in rows[kind]:
            item = {"path": str(directory), "kind": kind, "bytes": _bytes(directory)}
            reason = ""
            if not _finished(state):
                stem = (
                    directory.name
                    if directory.name.startswith("tool-containment-")
                    else directory.name[: -len(".source")]
                )
                reason = _pending_for(state, stem)
            if not reason and directory == newest:
                reason = "the newest " + kind + " of the run"
            if not reason:
                hits = _blocking_references(state, directory)
                if hits:
                    reason = "still named by state: " + "; ".join(hits[:2]) + (" ..." if len(hits) > 2 else "")
            if reason:
                item["reason"] = reason
                plan["keep"].append(item)
            else:
                plan["delete"].append(item)
    plan["freed_bytes"] = sum(item["bytes"] for item in plan["delete"])
    return plan


def prune(plan):
    """Delete the plan's bytes, keeping each verification copy's manifest beside its tree."""
    removed = 0
    for item in plan["delete"]:
        path = Path(item["path"])
        if item["kind"] == "verification-copy":
            scratch = path / "scratch"
            if scratch.is_dir() and not scratch.is_symlink():
                shutil.rmtree(scratch)
        elif path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        removed += item["bytes"]
    return removed


def parser() -> argparse.ArgumentParser:
    """The arguments `autocode prune-run` reads; `autocode completion` reads them too."""
    result = argparse.ArgumentParser(prog="autocode prune-run", description=__doc__)
    result.add_argument("run", nargs="?", help="run directory whose settled evidence bytes to prune")
    # Every other command names the run with --run-dir; accept it here too (#2385).
    result.add_argument("--run-dir", dest="run_dir", help="the same run, named as the other commands name it")
    result.add_argument("--yes", action="store_true", help="delete the listed set (without it, only list)")
    result.add_argument("--json", action="store_true", help="print the result as JSON")
    return result


def cli(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    if bool(args.run) == bool(args.run_dir):
        arguments.error("name the run once: RUN or --run-dir RUN")
    run = Path(args.run or args.run_dir)
    state_path = run / "state.json"
    if not state_path.is_file():
        arguments.error(f"{run} is not an autocode run directory (no state.json)")
    state = util.read_object(state_path)
    if state.get("active_stage"):
        stage = (
            (state.get("active_stage") or {}).get("stage", "unknown")
            if isinstance(state.get("active_stage"), dict)
            else "unknown"
        )
        msg = f"Refused: stage {stage} is active; its capture still validates its copy"
        if args.json:
            print(json.dumps({"error": {"code": "stage_active", "message": msg}}), file=sys.stderr)
        else:
            print(msg)
        return 2
    plan = candidates(state, run)
    if args.json:
        if not args.yes:
            payload = {
                "run": str(run),
                "keep": plan["keep"],
                "delete": plan["delete"],
                "freed_bytes": plan["freed_bytes"],
                "pruned": False,
            }
            print(json.dumps(payload, indent=2))
            return 0
        freed = prune(plan)
        payload = {
            "run": str(run),
            "keep": plan["keep"],
            "delete": plan["delete"],
            "freed_bytes": freed,
            "pruned": True,
        }
        print(json.dumps(payload, indent=2))
        return 0
    for item in plan["keep"]:
        print(f"keep    {item['bytes']:>12} B  {item['path']}  ({item['reason']})")
    for item in plan["delete"]:
        print(f"delete  {item['bytes']:>12} B  {item['path']}  ({item['kind']}; manifest and hashes retained)")
    if not plan["delete"]:
        print("Nothing to prune: every retained directory is still named by the run's state.")
        return 0
    if not args.yes:
        print(
            f"{len(plan['delete'])} director{'y' if len(plan['delete']) == 1 else 'ies'} ({plan['freed_bytes']} B) would be deleted; re-run with --yes to prune."
        )
        return 0
    freed = prune(plan)
    print(
        f"Pruned {len(plan['delete'])} director{'y' if len(plan['delete']) == 1 else 'ies'}: {freed} B freed; manifests, receipts and hashes retained."
    )
    return 0
