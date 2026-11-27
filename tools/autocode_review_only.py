"""A review-only run (`--review-only`, the "Review only" run mode): judge a change and change nothing else.

Owner decision 2026-11-27: alongside the slow lane, the fast lane and Plan only, the person may start a chat
that reviews a pull request, a branch, a patch or an uncommitted change. It takes the same path as every task
(#3010): Requirements, the Planner, the Plan Reviewer, the exact-plan approval, the Builder, the Tester and the
Completion Reviewer. No retired stage comes back. What the mode adds:

- the job is the review job (``--workflow review``), so Requirements does not have to read it from the words;
- every stage is told the one deliverable, ``REPORT`` in a fixed shape, and how to find the change under
  review (``instruction``), so the person does not have to spell out a findings format or "change nothing";
- the Builder may write only under ``WRITES``. Before its first attempt the runner saves the whole workspace as a
  Git object (``capture_start``), uncommitted changes included, since the change under review is often an
  uncommitted one that Git alone cannot put back. Any other file a Builder changes is put back from it and the
  Builder goes again at once (``check_writes``), as a spec-first Builder that touches the Tester's tests does
  (autocode_acceptance_tests.check_untouched); after ``DIRECT_RETRIES`` such attempts the attempt is rejected
  through the ordinary invalid-output path (StrayWrites);
- completion is refused while the turn changed anything outside ``WRITES`` (``ready``), the backstop for a
  write the Builder guard did not see (a parallel batch);
- the chat's result is the review's verdict and its blocking findings (``summary``), recorded when the Builder's
  report is applied, since the chat history reads no report files.

Chosen when a new run starts and saved as ``settings.review_only``; a saved run keeps its choice, and a
follow-up turn in the same chat is review-only too. "Fix them" belongs in a new chat on a lane that builds.

State key written here: ``review_only`` = {"start": the saved workspace's Git commit, "builder_edits":
[{"contract_hash", "paths", "resolved"?}], "findings": the latest Builder's summary of ``REPORT`` or None}. Read here, by autocode_chat_history and by
the completion gate. Imports only lower-level modules.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_code_checkpoints as code_checkpoints
    from . import autocode_prompts as prompts
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_stray_writes as stray_writes
    from . import autocode_util as util
except ImportError:
    import autocode_code_checkpoints as code_checkpoints
    import autocode_prompts as prompts
    import autocode_source_snapshot as source_snapshot
    import autocode_stray_writes as stray_writes
    import autocode_util as util

SETTING = "review_only"
FLAG = "--review-only"
WORKFLOW = "review"
KEY = "review_only"
WRITES = ("review/",)
REPORT = "review/findings.json"
# Builder attempts that changed a file outside WRITES and were sent straight back, per contract, before the
# runner rejects one through its ordinary invalid-output path instead (the same bound as a spec-first Builder).
DIRECT_RETRIES = 2
VERDICTS = {"approve": "approve", "request_changes": "request changes"}
SHOWN_FINDINGS = 10


def add_argument(parser) -> None:
    parser.add_argument(
        FLAG,
        action="store_true",
        default=None,
        help="New run: review a pull request, branch, patch or uncommitted change and change nothing else. "
        f"Runs as --workflow {WORKFLOW}; the only file written is {REPORT}. Needs joint planning",
    )


def new_run_setting(args, joint: bool) -> bool:
    """Off unless asked for; refused where it cannot hold (no Plan Reviewer, another job, plan only, no build)."""
    if not getattr(args, "review_only", None):
        return False
    if not joint:
        raise ValueError(f"{FLAG} needs joint planning: the Plan Reviewer checks the review plan before you approve it")
    if getattr(args, "workflow", None) not in (None, WORKFLOW):
        raise ValueError(f"{FLAG} runs the {WORKFLOW} job; it cannot be combined with --workflow {args.workflow}")
    if getattr(args, "plan_only", None):
        raise ValueError(f"{FLAG} cannot be combined with --plan-only: a plan-only run never writes the review")
    if getattr(args, "skip_to_build", False):
        raise ValueError(f"{FLAG} cannot be combined with --skip-to-build: the review plan still needs approval")
    return True


def resume_refused(saved_settings: dict, args) -> bool:
    """A saved run keeps its choice; repeating it is harmless."""
    requested = getattr(args, "review_only", None)
    return requested is not None and bool(requested) != bool(saved_settings.get(SETTING))


def enabled(state: RunState) -> bool:
    return bool((state.get("settings") or {}).get(SETTING))


def workflow(args, state: RunState) -> str | None:
    """The job a new run pins at start: ``--workflow`` when given, else the review job on a review-only run."""
    named = getattr(args, "workflow", None)
    if named or getattr(args, "run_dir", None):
        return named
    return WORKFLOW if getattr(args, "review_only", None) or enabled(state) else None


def outside(paths) -> list[str]:
    """The changed paths a review-only run may not change, sorted. Runner-owned .autocode/ files are never counted."""
    return sorted(
        str(path) for path in paths or () if not str(path).startswith((*WRITES, ".autocode/")) and str(path).strip()
    )


def instruction(state: RunState) -> str:
    """The rule every stage of a review-only run reads; "" on any other run."""
    return (
        prompts.get("fragments/review-only/rule.md").format(report=REPORT, writes=WRITES[0]) if enabled(state) else ""
    )


def builder_note(state: RunState) -> str:
    """The Builder's line naming the files it changed outside WRITES last time and the runner put back."""
    if not enabled(state):
        return ""
    last = _unresolved(state)
    restored = (
        "\nYour previous attempt changed " + ", ".join(last) + ", outside " + WRITES[0] + "; the runner put "
        "them back. This is a review: write your findings in " + REPORT + " and change no other file.\n"
        if last
        else ""
    )
    return restored + f"\nThis run is Review only: the only file you may create or change is {REPORT}.\n"


def capture_start(state: RunState, workspace, directory=None) -> None:
    """Save the workspace as the review found it, once, before the first Builder attempt of a review-only run.

    Every file outside WRITES must stay exactly so; ``restore_from_start`` puts one back from this object. A
    workspace Git cannot capture (a nested repository) keeps the attempt-snapshot restore of stray_writes."""
    if not enabled(state) or (state.get(KEY) or {}).get("start"):
        return
    entry = state.setdefault(KEY, {"builder_edits": [], "findings": None})
    try:
        entry["start"] = code_checkpoints.capture_tree(workspace, source_snapshot.snapshot(workspace), directory)
    except (OSError, ValueError, KeyError) as problem:
        entry["start_error"] = str(problem)[:500]


def restore_from_start(state: RunState, workspace, paths) -> tuple[list[str], list[str]]:
    """Put ``paths`` back as the saved start had them (absent there: removed); return (restored, kept)."""
    commit = (state.get(KEY) or {}).get("start")
    root, restored, kept = Path(workspace), [], []
    for path in paths:
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-z", str(commit), "--", path], capture_output=True, check=True
        ).stdout.split(b"\0")[0]
        target = root / path
        try:
            if not listed:
                if target.is_symlink() or target.is_file():
                    target.unlink()
                restored.append(path)
                continue
            mode, kind, oid = listed.split(b"\t", 1)[0].decode().split()
            if kind != "blob" or mode not in ("100644", "100755", "120000"):
                kept.append(path)
                continue
            content = subprocess.run(
                ["git", "-C", str(root), "cat-file", "blob", oid], capture_output=True, check=True
            ).stdout
            if target.is_symlink() or target.is_file():
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == "120000":
                target.symlink_to(content.decode())
            else:
                target.write_bytes(content)
                target.chmod(0o755 if mode == "100755" else 0o644)
            restored.append(path)
        except (OSError, ValueError) as problem:
            kept.append(f"{path} (restore failed: {problem})")
    return restored, kept


def check_writes(state: RunState, record: dict, workspace) -> bool:
    """Put back any file outside WRITES a Builder attempt changed; True when the Builder goes again at once.

    After DIRECT_RETRIES such attempts under one contract the attempt is rejected (StrayWrites, with nothing
    left for the runner's own restore). False when the attempt kept to WRITES, or on any other run."""
    if not enabled(state):
        return False
    stray = outside(record.get("changed_files"))
    entry = state.setdefault(KEY, {"builder_edits": [], "findings": None})
    if not stray:
        for row in entry.get("builder_edits") or []:
            row["resolved"] = True  # a clean attempt: the Builder's prompt stops naming the old edit
        return False
    try:
        if entry.get("start"):
            restored, kept = restore_from_start(state, workspace, stray)
        else:
            restored, kept = stray_writes.restore(workspace, record, stray)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as problem:
        restored, kept = [], [f"{path} (restore failed: {problem})" for path in stray]
    current = (state.get("goal_contract") or {}).get("hash")
    retries = [row for row in entry.get("builder_edits") or [] if row.get("contract_hash") == current]
    if len(retries) < DIRECT_RETRIES and not kept:
        entry.setdefault("builder_edits", []).append({"contract_hash": current, "paths": restored})
        state["next_stage"] = "builder"
        return True
    note = ("; left as they are (changed since, or not clean before the attempt): " + ", ".join(kept)) if kept else ""
    raise stray_writes.StrayWrites(
        f"A Review only run changes nothing but {REPORT}; this attempt changed "
        + ", ".join(stray)
        + (" (restored: " + ", ".join(restored) + ")" if restored else "")
        + note
        + f". Write the findings in {REPORT} and change no other file.",
        [],
    )


def record_findings(state: RunState, workspace) -> None:
    """Save the summary of REPORT as the Builder left it, for the chat's result; None when it is unreadable."""
    if not enabled(state):
        return
    state.setdefault(KEY, {"builder_edits": [], "findings": None})["findings"] = summary(Path(workspace) / REPORT)


def summary(path: Path) -> dict | None:
    """The verdict and findings of a findings file, bounded, or None when it is missing or not the shape."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict) or report.get("verdict") not in VERDICTS:
        return None
    rows = [row for row in report.get("findings") or [] if isinstance(row, dict)]

    def shown(row: dict) -> dict:
        lines = row.get("lines")
        span = lines if isinstance(lines, list) and all(type(n) is int for n in lines) else []
        return {
            "id": str(row.get("id") or ""),
            "file": str(row.get("file") or ""),
            "lines": span[:2],
            "summary": str(row.get("summary") or "")[:600],
        }

    blocking = [row for row in rows if row.get("severity") == "blocking"]
    return {
        "verdict": report["verdict"],
        "change_under_review": str(report.get("change_under_review") or "")[:300],
        "blocking": [shown(row) for row in blocking[:SHOWN_FINDINGS]],
        "blocking_count": len(blocking),
        "advisory_count": sum(1 for row in rows if row.get("severity") == "advisory"),
    }


def response(state: RunState) -> str | None:
    """The finished review as the chat shows it: verdict, counts and the blocking findings; None off this mode."""
    if not enabled(state) or state.get("status") != "TASK_COMPLETE":
        return None
    found = (state.get(KEY) or {}).get("findings")
    if not isinstance(found, dict):
        return f"Review complete. The findings are in {REPORT}; nothing else was changed."
    reviewed = f" of {found['change_under_review']}" if found.get("change_under_review") else ""
    lines = [
        f"Review{reviewed} complete: {VERDICTS.get(found.get('verdict'), found.get('verdict'))}. "
        f"{found.get('blocking_count', 0)} blocking, {found.get('advisory_count', 0)} advisory. "
        "Nothing in the project was changed.",
    ]
    for row in found.get("blocking") or []:
        span = row.get("lines") or []
        where = row["file"] + (
            f":{span[0]}" + (f"-{span[1]}" if len(span) > 1 and span[1] != span[0] else "") if span else ""
        )
        lines.append(f"- {row['id']} ({where}): {row['summary']}")
    more = int(found.get("blocking_count") or 0) - len(found.get("blocking") or [])
    if more > 0:
        lines.append(f"- and {more} more blocking findings")
    lines.append(f"\nEvery finding, with its evidence, is in {REPORT}.")
    return "\n".join(lines)


def ready(state: RunState, current) -> bool:
    """Whether the turn changed nothing outside WRITES: True on any other run, or when the start is unknown."""
    return not changed_outside(state, current)


def changed_outside(state: RunState, current) -> list[str]:
    """The files this turn changed outside WRITES, from its first stage's before-snapshot to ``current``."""
    if not enabled(state) or not isinstance(current, dict) or not isinstance(current.get("files"), dict):
        return []
    turns = state.get("turns") or []
    start = turns[-1].get("stage_index") if turns else 0
    if not isinstance(start, int):
        return []
    first = next(
        (
            row["before_ref"]
            for row in (state.get("stages") or [])[start:]
            if isinstance(row, dict) and row.get("before_ref")
        ),
        None,
    )
    try:
        before = util.read(first) if first else None
    except (OSError, ValueError):
        return []
    if not isinstance(before, dict) or not isinstance(before.get("files"), dict):
        return []
    return outside(util.changed_paths(before, current))


def rejection(state: RunState, current) -> str:
    """Why completion was refused for a write outside WRITES, or "" when nothing outside it changed."""
    changed = changed_outside(state, current)
    if not changed:
        return ""
    return (
        f"Completion rejected: a Review only run changes nothing but {REPORT}, and this one changed "
        + ", ".join(changed[:20])
        + (f" and {len(changed) - 20} more" if len(changed) > 20 else "")
        + ". Request REWORK with a next_task that puts those files back as they were."
    )


def _unresolved(state: RunState) -> list[str]:
    edits = (state.get(KEY) or {}).get("builder_edits") or []
    current = (state.get("goal_contract") or {}).get("hash")
    last = edits[-1] if edits else {}
    return list(last.get("paths") or []) if last.get("contract_hash") == current and not last.get("resolved") else []
