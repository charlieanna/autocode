"""A change outside a package run's package is a runner finding (#711).

``--package PATH`` (autocode_package_scope) is the Builder's write scope, and its handoff says so
(``context``). The bug-fix Investigator's handoff names the package and its suite (``context`` too),
and autocode_bug_job.check refuses a reproduced diagnosis that names a file outside it; no other
stage is given the package. Before each Tester and Completion Reviewer dispatch (``sol``,
``astra_checkpoint``, ``astra_review``; autocode_regression.before_review calls ``check``), whether
or not the run owes a regression proof, the runner compares the candidate with the run's base as the
regression proof reads it (autocode_verify.changed_files, with the approved plan's declared source
paths). The Code Reviewer, Designer and Design Reviewer dispatches are not checked. A change outside
the package opens one blocking finding with source ``runner`` in the findings ledger, naming each
path and what happened to it. The ordinary route carries it: the completion gate and a milestone's
acceptance refuse while it is open, every stage sees it in ``open_findings``, and a correction task
is assigned it. Only this module closes it, when a later check finds no change outside the package;
no model report can (autocode_findings applies a reviewer's dispositions to its own source only). A
user who closes it with --close-finding accepts nothing: --accept-completion runs the same check
(``refuse``) after its own guards and refuses while the change is there, and the next check before
review opens a new finding.

A design check of this run that passes removes the blockers file an earlier, stopped check wrote
(autocode_design_check_job.apply), so no stale one is left outside the package to exempt or to blame
on the Builder.

What counts is what changed_files reports: a file added, modified (its content or its mode) or
deleted, a rename being both. It leaves out AutoCode's run state, bytecode, the runner's dependency
links and an untracked file Git ignores (unless the approved plan declares its path). Outside the
package, the check also sorts out what is not the Builder's change:

- what the checkout held at launch that the run's base cannot hold: an untracked Git repository or a
  submodule's uncommitted work (``at_launch``). Left out, with everything under it, while it is still
  a Git repository. An untracked one (held as ``added``) that the person later removes entirely names
  no change of the candidate at all, so it does not hold the run; a deleted submodule (held as
  ``modified``) and a path that is no longer a Git repository are named separately, and the finding
  tells the Builder to leave them to the person, never to delete or reset anything there. A run saved
  before this record existed has none: its check leaves out every nested repository and submodule work it
  finds outside the package, since it cannot tell whether the checkout held it at launch.
- the files the runner wrote for this run whose content it recorded: the bug-fix workflow's
  diagnosis note (exempt only while it is what autocode_bug_job.apply wrote: a reproduced one while
  autocode_bug_job.diagnosis_artifact identifies it unchanged, one that did not reproduce while its
  content is the saved investigation's note), a review's or design review's report (exempt only
  while it matches its saved ``report_sha256``) and a design check's blockers file (exempt only
  while it matches its saved ``blockers_sha256``).
  Once changed, the finding names it separately: put back what the runner wrote, do not delete it.
- the files the runner wrote that it recorded only by path: a design check's blockers file saved
  before the hash was kept, and what an earlier, finished turn of the run left (autocode_follow_up's
  ``previous.wrote``). Left out by path, so an edit to one is not seen.

The regression proof's list of changes outside the package leaves out the same paths (``exempt``,
passed to autocode_verify.verify as ``package_exempt``), apart from the person's Git work that is no
longer as it was. Anything else counts: a lockfile or workspace file at the repository root is shared
by every package, so a change to it is reported too.

It imports nothing above it: autocode_regression (the check before review, the launch record of an
in-place run, the proof's ``exempt``), autocode_run_setup (the launch record of a new task
worktree), autocode_stage_context and units.autoresolver (``context``) and the --accept-completion
step (autocode_action_decisions, ``refuse``) call it. State: ``package_outside_at_launch`` ({path:
added or modified}, written once by ``at_launch`` in every new package run whose base is known, {}
when nothing is there; read here); and findings_ledger rows with source ``runner`` and ``package``
(the run's package), ``paths`` ({path: added, modified or deleted}), ``runner_files`` ({path:
change}), ``held`` ({path: deleted or no longer a Git repository}) and ``source_revision`` (the
source the check last found them on), fields read only here. It also reads ``investigation``,
``review``, ``design_review``, ``design_check.blockers`` and ``design_check.blockers_sha256``,
``turns[].previous.wrote`` and, for the
Investigator's handoff, ``settings.protected_tests``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_bug_job as bug_job
    from . import autocode_findings as findings_ledger
    from . import autocode_package_scope as package_scope
    from . import autocode_prompts as prompts
    from . import autocode_run_base as run_base
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
    from . import autocode_verify as verify
    from .autocode_runner_files import runner_files as _runner_files
except ImportError:
    import autocode_bug_job as bug_job
    import autocode_findings as findings_ledger
    import autocode_package_scope as package_scope
    import autocode_prompts as prompts
    import autocode_run_base as run_base
    import autocode_source_scope as source_scope
    import autocode_util as util
    import autocode_verify as verify
    from autocode_runner_files import runner_files as _runner_files

SOURCE = "runner"
# The dispatches the candidate is judged before: the Tester and the Completion Reviewer (the checkpoint
# stage is either, autocode_roles). The Builder's own dispatch is not one; its handoff names the scope.
STAGES = ("sol", "astra_checkpoint", "astra_review")


def context(settings, stage="terra") -> dict | None:
    """What a package run's handoff tells ``stage`` about its package; None for a run of the whole repository.

    The Builder's is its write scope (``write_scope``, autocode_stage_context). The bug-fix Investigator's
    (``package_scope``, units.autoresolver) also names the command the run's protected tests run with
    (``suite``: the suite detected in the package, or --test-command), when the run has one."""
    package = package_scope.of(settings)
    if package is None:
        return None
    if stage == bug_job.STAGE:
        suite = (settings.get("protected_tests") or {}).get("command")
        return {
            "package": package + "/",
            **({"suite": suite} if isinstance(suite, str) and suite.strip() else {}),
            "instruction": prompts.get("fragments/package-scope/investigator.md").format(package=package),
        }
    return {
        "package": package + "/",
        "instruction": prompts.get("fragments/package-scope/builder.md").format(package=package),
    }


def repository(workspace, path) -> bool:
    """Whether ``path`` is a directory, not a link, holding ``.git``: a nested Git repository or a
    checked-out submodule."""
    target = Path(workspace, str(path).rstrip("/"))
    return not target.is_symlink() and target.is_dir() and os.path.lexists(target / ".git")


def at_launch(state: RunState, workspace, package=None) -> None:
    """Record what a new package run's checkout holds outside ``package`` (else the saved one) that its
    base cannot hold, once ``base_commit`` is saved; {} when it holds nothing.

    An in-place run's base (autocode_verify.commit_worktree) holds the checkout's uncommitted and
    untracked files, but not an untracked Git repository or a submodule's uncommitted work, which
    changed_files reports against any base: they are the person's, not a change the Builder made.
    A run without a package or a base records nothing."""
    package = package or package_scope.of(state.get("settings"))
    base = state.get("base_commit")
    if not package or not base:
        return
    outside = package_scope.changes_outside(package, verify.changed_files(workspace, base))
    state["package_outside_at_launch"] = {
        path: change for path, change in outside.items() if repository(workspace, path)
    }


def _held(state: RunState, workspace, outside) -> tuple[list[str], dict[str, str]]:
    """(the person's Git work outside the package to leave out, with everything under it; what the launch
    record names that is no longer there as it was: {path: deleted or no longer a Git repository}).

    A run saved without the record leaves out every nested repository or submodule work among the
    changes outside the package (``outside``): it cannot tell whether the checkout held it at launch.
    A record held as ``added`` (an untracked repository, which the base cannot hold) whose path is now
    gone entirely names no change of the candidate at all: the person removed their own work, and the
    run does not hold its absence against completion. One held as ``modified`` (a submodule, whose
    gitlink the base does hold) is still named when deleted."""
    if "package_outside_at_launch" not in state:
        return [path for path in outside if repository(workspace, path)], {}
    kept, gone = [], {}
    for path, held_at_launch in (state.get("package_outside_at_launch") or {}).items():
        if repository(workspace, path):
            kept.append(path)
            continue
        there = os.path.lexists(Path(workspace, path.rstrip("/")))
        if not there and held_at_launch == "added":
            continue
        gone[path] = "no longer a Git repository" if there else "deleted"
    return kept, gone


def _by_path(state: RunState) -> set[str]:
    """The runner-written files the run recorded by path only: a design check's blockers and what an
    earlier, finished turn of the run left."""
    paths = [(state.get("design_check") or {}).get("blockers")]
    for turn in state.get("turns") or []:
        wrote = ((turn.get("previous") or {}) if isinstance(turn, dict) else {}).get("wrote")
        paths += wrote if isinstance(wrote, list) else []
    return {path for path in paths if isinstance(path, str) and path.strip()}


def _sort(state: RunState, workspace, package, changes) -> tuple[dict, dict, dict, list[str]]:
    """Sort the changes outside ``package``: (the Builder's, {path: change}; the runner-written files
    changed since it wrote them, {path: change}; the person's Git work that is no longer there as it
    was, {path: what}; the paths left out, each with everything under it)."""
    outside = package_scope.changes_outside(package, changes)
    kept, gone = _held(state, workspace, outside)
    recorded, unchanged = _runner_files(state, workspace)
    left_out = sorted({*kept, *unchanged, *(_by_path(state) - recorded)})
    remaining = package_scope.changes_outside(package, outside, [*left_out, *gone])
    runner = {path: change for path, change in remaining.items() if path in recorded}
    builder = {path: change for path, change in remaining.items() if path not in recorded}
    return builder, runner, gone, left_out


def exempt(state: RunState, workspace, base=None) -> list[str]:
    """The paths outside the package that the regression proof does not count either: what ``_sort``
    leaves out. Only a run saved without the launch record reads the changes (against ``base``)."""
    package = package_scope.of(state.get("settings"))
    if package is None:
        return []
    changes = verify.changed_files(workspace, base) if "package_outside_at_launch" not in state and base else {}
    return _sort(state, workspace, package, changes)[3]


def open_row(state: RunState):
    """The open finding about changes outside the package, or None."""
    return next((row for row in findings_ledger.open_entries(state, SOURCE) if row.get("package")), None)


def _listed(rows) -> str:
    return ", ".join(f"{path} ({change})" for path, change in rows.items())


def _text(package, builder, runner, gone, base) -> str:
    text = f"Files outside the package {package}/ changed" + (f": {_listed(builder)}" if builder else "")
    text += f". A --package run may change only files inside {package}/"
    if builder:
        text += (
            f": restore each of these to its content and mode at the run's base revision {str(base)[:12]} "
            "(remove an added file, restore a deleted one; a file restored with the same content but a "
            "different mode still counts as modified)"
        )
    text += "."
    if runner:
        text += (
            f" The runner wrote {', '.join(runner)} for this run, and it changed since: put back what the "
            "runner wrote, and do not delete it."
        )
    if gone:
        text += (
            f" When the run started, the checkout held the person's own Git work here, which the run's base "
            f"cannot hold, and it is no longer as it was: {_listed(gone)}. Do not delete, move or reset "
            "anything there: the person must restore it."
        )
    return text


def reconcile(state: RunState, workspace, changes, revision, base):
    """Open, refresh or close the run's finding about changes outside its package; return the open row or None.

    ``changes`` is autocode_verify.changed_files for the current source (``revision``) in ``workspace``
    against the run's ``base``; ``_sort`` says what counts. A run without a package is left unchanged.
    """
    package = package_scope.of(state.get("settings"))
    if package is None:
        return None
    builder, runner, gone, _ = _sort(state, workspace, package, changes)
    found = {"paths": builder, "runner_files": runner, "held": gone}
    row, at = open_row(state), util.now()
    checked = f"Runner check of source {str(revision)[:12]} against the run's base {str(base)[:12]}"
    if not (builder or runner or gone):
        if row is not None:
            findings_ledger.settle(
                row,
                "resolved",
                resolved_at=at,
                resolved_in=checked,
                resolution_evidence=f"{checked}: no file outside the package {package}/ changed",
            )
        return None
    count = len(builder) + len(runner) + len(gone)
    evidence = f"{checked}: {count} path{'s' if count != 1 else ''} outside {package}/ changed"
    if row is None:
        row = {
            "id": findings_ledger.allocate_id(state),
            "source": SOURCE,
            "severity": "high",
            "finding": _text(package, builder, runner, gone, base),
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
            **found,
            "source_revision": revision,
        }
        findings_ledger.ledger(state).append(row)
    elif row.get("source_revision") != revision or any(row.get(key) != value for key, value in found.items()):
        row.update(
            finding=_text(package, builder, runner, gone, base),
            evidence=evidence,
            times_reported=row.get("times_reported", 1) + 1,
            last_reported_at=at,
            last_reported_in=checked,
            source_revision=revision,
            **found,
        )
    return row


def check(state: RunState, workspace):
    """A package run's check of the candidate against the run's base (``reconcile``), with the approved
    plan's declared source paths: the open finding, or None. A run without a package or base is not checked."""
    if package_scope.of(state.get("settings")) is None or not (base := run_base.base_commit(state, workspace)):
        return None
    return reconcile(
        state,
        workspace,
        verify.changed_files(workspace, base, source_paths=source_scope.paths(state)),
        source_scope.snapshot(workspace, state)["revision"],
        base,
    )


def refuse(state: RunState, workspace) -> None:
    """Raise ValueError while a package run's candidate changes something outside its package (``check``).

    --accept-completion follows no judging dispatch, so its action step calls this after
    autocode.accept_completion's own guards: a person who closed the finding with --close-finding has
    not accepted the change (#711)."""
    row = check(state, workspace)
    if row:
        raise ValueError("Completion acceptance requires no change outside the package. " + row["finding"])
