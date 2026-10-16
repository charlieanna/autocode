"""A change outside a package run's package is a runner finding (#711).

``--package PATH`` (autocode_package_scope) is the Builder's write scope, and its handoff says so
(``context``). Before each Tester and Completion Reviewer dispatch (``sol``, ``astra_checkpoint``,
``astra_review``), autocode_regression.before_review passes the candidate's changes since the run's
base, as the regression proof reads them (autocode_verify.changed_files), to ``reconcile``. It does
so whether or not the run owes a regression proof. A change outside the package (a file added,
modified or deleted there; a rename is both) opens one blocking finding with source ``runner`` in
the findings ledger, naming each path and what happened to it. The ordinary route carries it: the
completion gate and a milestone's acceptance refuse while it is open, every stage sees it in
``open_findings``, and a correction task is assigned it. Only this module closes it, when a later
check finds no change outside the package; no model report can (autocode_findings applies a
reviewer's dispositions to its own source only). A user who closes it with --close-finding accepts
nothing: --accept-completion runs the same check first (autocode_regression.refuse_outside_package)
and refuses while the change is there, and the next check before review opens a new finding.

Outside the package, what changed_files leaves out (AutoCode's run state, bytecode and the runner's
dependency links) is not a change, and neither is anything ``exempt`` names: what the checkout held at
launch that the run's base cannot hold (``at_launch``), the files the runner wrote for a job of this
run, and what an earlier, finished turn of the run left. The regression proof's list of changes outside
the package leaves out the same paths (autocode_regression passes ``exempt`` to autocode_verify.verify).
Anything else counts: a lockfile or workspace file at the repository root is shared by every package,
so a change to it is reported too.

Imports nothing above the findings ledger; the caller passes the changes it just read. State:
``package_outside_at_launch`` ({path: added or modified}, written once by ``at_launch``, read by
``exempt``); and findings_ledger rows with source ``runner`` and ``package`` (the run's package),
``paths`` ({path: added, modified or deleted}) and ``source_revision`` (the source the check last
found them on), fields read only here. ``exempt`` also reads ``investigation.note_path``
(autocode_bug_job), ``design_check.blockers`` (autocode_design_check_job) and
``turns[].previous.wrote`` (autocode_follow_up).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_findings as findings_ledger
    from . import autocode_package_scope as package_scope
    from . import autocode_prompts as prompts
    from . import autocode_util as util
except ImportError:
    import autocode_findings as findings_ledger
    import autocode_package_scope as package_scope
    import autocode_prompts as prompts
    import autocode_util as util

SOURCE = "runner"
# The dispatches the candidate is judged before: the Tester and the Completion Reviewer (the checkpoint
# stage is either, autocode_roles). The Builder's own dispatch is not one; its handoff names the scope.
STAGES = ("sol", "astra_checkpoint", "astra_review")


def context(settings) -> dict | None:
    """The Builder's write scope in a package run, for its handoff; None for a run of the whole repository."""
    package = package_scope.of(settings)
    if package is None:
        return None
    return {
        "package": package + "/",
        "instruction": prompts.get("fragments/package-scope/builder.md").format(package=package),
    }


def at_launch(state: RunState, package, changes) -> None:
    """Record what a new run's checkout already holds outside ``package`` that its base cannot hold.

    ``changes`` is autocode_verify.changed_files against the base just taken. An in-place run's base
    (autocode_verify.commit_worktree) holds the checkout's uncommitted and untracked files, but not an
    untracked Git repository or a submodule's uncommitted work, which changed_files reports against
    any base: they are the person's, not a change the Builder made, and are never reported.
    """
    held = package_scope.changes_outside(package, changes) if package else {}
    if held:
        state["package_outside_at_launch"] = held


def exempt(state: RunState) -> list[str]:
    """Paths outside the package that are not this turn's change, each with everything under it.

    What the checkout held at launch that the base cannot hold (``at_launch``); the files the runner
    wrote for a job of this run: a bug fix's diagnosis note (autocode_bug_job) and a design check's
    blockers (autocode_design_check_job); and what an earlier, finished turn of the run left
    (autocode_follow_up), such as a review's report or a design's documents.
    """
    paths = [
        *(state.get("package_outside_at_launch") or {}),
        (state.get("investigation") or {}).get("note_path"),
        (state.get("design_check") or {}).get("blockers"),
    ]
    for turn in state.get("turns") or []:
        wrote = ((turn.get("previous") or {}) if isinstance(turn, dict) else {}).get("wrote")
        paths += wrote if isinstance(wrote, list) else []
    return sorted({path for path in paths if isinstance(path, str) and path.strip()})


def open_row(state: RunState):
    """The open finding about changes outside the package, or None."""
    return next((row for row in findings_ledger.open_entries(state, SOURCE) if row.get("package")), None)


def _text(package, paths, base):
    listed = ", ".join(f"{path} ({change})" for path, change in paths.items())
    return (
        f"Files outside the package {package}/ changed: {listed}. A --package run may change only files "
        f"inside {package}/: restore each of these to its content at the run's base revision "
        f"{str(base)[:12]} (remove an added file, restore a deleted one)."
    )


def reconcile(state: RunState, changes, revision, base):
    """Open, refresh or close the run's finding about changes outside its package; return the open row or None.

    ``changes`` is autocode_verify.changed_files for the current source (``revision``) against the
    run's ``base``; what ``exempt`` names is left out. A run without a package is left unchanged.
    """
    package = package_scope.of(state.get("settings"))
    if package is None:
        return None
    paths = package_scope.changes_outside(package, changes, exempt(state))
    row, at = open_row(state), util.now()
    checked = f"Runner check of source {str(revision)[:12]} against the run's base {str(base)[:12]}"
    if not paths:
        if row is not None:
            findings_ledger.settle(
                row,
                "resolved",
                resolved_at=at,
                resolved_in=checked,
                resolution_evidence=f"{checked}: no file outside the package {package}/ changed",
            )
        return None
    evidence = f"{checked}: {len(paths)} file{'s' if len(paths) != 1 else ''} outside {package}/ changed"
    if row is None:
        row = {
            "id": findings_ledger.allocate_id(state),
            "source": SOURCE,
            "severity": "high",
            "finding": _text(package, paths, base),
            "evidence": evidence,
            "blocking": True,
            "status": "open",
            "opened_at": at,
            "opened_in": checked,
            # Not a milestone's: the change is in the candidate every milestone and completion judge.
            "scope": None,
            "times_reported": 1,
            "last_reported_at": at,
            "last_reported_in": checked,
            "assigned_task": None,
            "package": package,
            "paths": paths,
            "source_revision": revision,
        }
        findings_ledger.ledger(state).append(row)
    elif row.get("source_revision") != revision or row.get("paths") != paths:
        row.update(
            finding=_text(package, paths, base),
            evidence=evidence,
            times_reported=row.get("times_reported", 1) + 1,
            last_reported_at=at,
            last_reported_in=checked,
            paths=paths,
            source_revision=revision,
        )
    return row
