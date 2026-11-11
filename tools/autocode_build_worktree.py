"""A chat talks with the person in their checkout and plans and builds in a task worktree of its own.

A terminal chat starts its run in the folder the person chose. Requirements only reads the code and
talks with the person, so it runs there and sees the folder as it is. Before the first stage that
needs the code to itself (the Planner, #3081), ``move`` gives the run a task worktree and branch of its
own (``autocode_workspaces.create``, from the checkout's committed HEAD) and carries the run there under
the same run name. The plan is reviewed, approved and built against that one fixed tree: a pull, commit
or edit in the person's folder meanwhile cannot strand the plan's review or approval (#3080), the
folder is never written to, and two chats in one folder no longer wait for each other. A run saved
while planning in the folder (#3077) still moves once its plan is approved.

The run's state, with its requirements, conversation and history, is carried as it is. What only
held for the person's checkout is not: the in-place launch records (``launch_sources``, the package
launch record) and the provider sessions, which belong to the folder they ran in. The new run starts
from the worktree's commit (``base_commit``), and its files there are what the build changes. The run
directory left in the checkout keeps every artifact the plan and stages refer to, and its state names
the new run (``moved_to``): a status read or action on the old path is answered by the new run
(``resolve``), and listings leave the old record out (``moved``).

One key on the run state, ``build_worktree``, written only here:

- ``{"state": "planning"}``: started with ``--build-in-worktree`` (``request``); still in the checkout.
  ``"branch"`` names an existing local branch to plan and build on (``--worktree-branch``) instead of a
  new one; the worktree checks it out, and nothing removes that branch afterwards.
  A move saves where it goes (``moving_to``, ``left_out``) before it writes there, so a crashed move
  is finished, never started again in a second worktree.
- ``{"state": "moved", "moved_to": {...}}``: the record left in the checkout.
- ``{"state": "building", "moved_from": {...}, "branch": ..., "existing_branch": bool, "left_out": [...], "after_events": N}``: the
  moved run; autocode_chat_history says once where it works and what it left out, after the person's
  N-th message (``after_events``, the messages before the move), or after the plan's approval in a run
  that moved then.

AutoCode never merges the branch; a completed run commits its delivery there (autocode_worktrees.deliver).
Lower layer: Git, files, the registry and the controller-owner receipt; never ``autocode``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_activity_log as activity_log
    from . import autocode_changeset_store as changeset_store
    from . import autocode_checkout_lock as checkout_lock
    from . import autocode_contract_identity as contract
    from . import autocode_controller_owner as controller_owner
    from . import autocode_coordination_store as coordination_store
    from . import autocode_package_writes as package_writes
    from . import autocode_registry as registry
    from . import autocode_run_destination as run_destination
    from . import autocode_run_status as run_status
    from . import autocode_util as util
    from . import autocode_workspaces as workspaces
    from . import autocode_worktree_choice as worktree_choice
except ImportError:
    import autocode_activity_log as activity_log
    import autocode_changeset_store as changeset_store
    import autocode_checkout_lock as checkout_lock
    import autocode_contract_identity as contract
    import autocode_controller_owner as controller_owner
    import autocode_coordination_store as coordination_store
    import autocode_package_writes as package_writes
    import autocode_registry as registry
    import autocode_run_destination as run_destination
    import autocode_run_status as run_status
    import autocode_util as util
    import autocode_workspaces as workspaces
    import autocode_worktree_choice as worktree_choice

KEY: Final = "build_worktree"
PLANNING, MOVED, BUILDING = "planning", "moved", "building"
# Records that hold only for the in-place run in the person's checkout. A task worktree's run has
# none: its base is the worktree's commit, and its ignored inputs follow the worktree's own policy.
IN_PLACE_ONLY = ("launch_sources", "package_outside_at_launch")
# How many left-out paths the notice names before it counts the rest.
NAMED = 5


def request(state: RunState, workspace, branch: str | None = None) -> bool:
    """Mark a new run to plan and build in its own worktree; False when it builds here.

    A folder AutoCode just made a project of, or a task worktree, is already the run's own, and a
    checkout with no commit yet has nothing to start a worktree from: these build where they are.
    ``branch`` is an existing local branch to work on instead of a new one: ValueError says why it
    cannot be (another worktree or chat has it, or there is no such branch, or no worktree here)."""
    workspace = Path(workspace)
    try:
        own = bool(workspaces.metadata(workspace) or state.get("workspace_bootstrap"))
        if not own and Path(workspaces.git(workspace, "rev-parse", "--show-toplevel")).resolve() != workspace.resolve():
            own = True
        if not own:
            workspaces.git(workspace, "rev-parse", "--verify", "HEAD")  # a worktree starts from a commit
    except (OSError, ValueError, json.JSONDecodeError):
        own = True
    if own:
        if branch:
            raise ValueError(
                f"--worktree-branch {branch}: this chat cannot move to a worktree of its own from {workspace}; "
                "start it from the top of a Git checkout with a commit"
            )
        return False
    if branch and (problem := worktree_choice.branch_problem(workspace, branch)):
        raise ValueError(f"--worktree-branch {branch}: {problem}")
    state[KEY] = {"state": PLANNING, **({"branch": branch} if branch else {})}
    return True


def due(state: RunState) -> bool:
    """Whether the run is about to launch its first stage that needs the code to itself (#3081).

    Requirements only reads the person's folder and talks with them, so it runs there. Everything after
    it, the Planner and the Plan Reviewer included, runs in the worktree: the plan is reviewed and
    approved against the same fixed tree the Builder changes, so a pull, commit or edit in the person's
    folder meanwhile cannot strand the plan's review or its approval (#3080). A run that already planned
    in the folder (saved before this rule) still moves once its plan is approved.

    A run another run is linked to by its path (a change set, a coordination, a coordinated member)
    keeps building where it is, as every run did before: moving it would break that link."""
    if (state.get(KEY) or {}).get("state") != PLANNING or not checkout_lock.needed_by(state):
        return False
    if _planned_here(state) and not contract.approved(state):
        return False
    return not _linked(state)


def _planned_here(state: RunState) -> bool:
    """Whether the Planner already drafted a plan in the folder: that plan moves once it is approved."""
    return bool(state.get("goal_contract"))


def _linked(state: RunState) -> bool:
    if (state.get("settings") or {}).get("coordination_member"):
        return True
    run_dir = state.get("run_dir")
    try:
        return bool(run_dir and (changeset_store.find(run_dir) or coordination_store.read(run_dir)))
    except (OSError, ValueError, changeset_store.ChangesetError):
        return True  # an unreadable link record: never move what it may name


def moved(state: RunState) -> dict | None:
    """Where a run left in the person's checkout went ({"workspace", "run_dir", "branch"}), or None."""
    record = state.get(KEY) if isinstance(state, dict) else None
    if not isinstance(record, dict) or record.get("state") != MOVED or not isinstance(record.get("moved_to"), dict):
        return None
    return record["moved_to"]


def resolve(workspace, run_dir, state: RunState) -> tuple[Path, Path, RunState] | None:
    """The run a moved record names, as (worktree, run directory, state), when it is exactly that run.

    The new run must sit in a task worktree of the same checkout, under the same name, and say it
    came from this record; anything else is not followed."""
    target = moved(state)
    if target is None:
        return None
    tree, child = Path(str(target.get("workspace") or "")), Path(str(target.get("run_dir") or ""))
    try:
        data = workspaces.metadata(tree)
        if (
            not data
            or Path(data["project_workspace"]) != Path(workspace).resolve()
            or child != tree / ".autocode" / "runs" / Path(run_dir).name
            or child.is_symlink()
            or (child / "state.json").is_symlink()
        ):
            return None
        saved = util.read(child / "state.json")
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
    origin = (saved.get(KEY) or {}).get("moved_from") or {}
    if saved.get("workspace") != str(tree) or origin.get("run_dir") != str(Path(run_dir).resolve()):
        return None
    return tree, child, saved


def gone(workspace, state: RunState) -> str:
    """Why a run that moved to build cannot be reached through the path it had in the checkout."""
    tree = Path(str((moved(state) or {}).get("workspace") or ""))
    return "This chat moved to its own worktree to build. " + workspaces.missing_worktree(
        Path(workspace).resolve(), tree
    )


def _left_out(workspace) -> list[str]:
    """The person's uncommitted and untracked paths in the checkout: the worktree starts without them."""
    changed = workspaces.git(workspace, "diff", "--name-only", "-z", "HEAD")
    untracked = workspaces.git(workspace, "ls-files", "--others", "--exclude-standard", "-z")
    return sorted({name for name in (changed + "\0" + untracked).split("\0") if name})


def _prepared(state: RunState, workspace: Path, run_dir: Path, persist) -> dict:
    """The worktree this move goes to: one an interrupted move already made, else a new one.

    The intent is saved on the run before anything is written in the worktree, so a move that
    crashed is finished on the next try rather than started again in a second worktree."""
    record = state.get(KEY) or {}
    target = record.get("moving_to")
    try:
        if isinstance(target, dict) and workspaces.metadata(Path(target["workspace"])):
            return record
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        pass
    left_out = _left_out(workspace)
    branch = record.get("branch") if isinstance(record.get("branch"), str) else None
    if branch and (problem := worktree_choice.branch_problem(workspace, branch, run_dir)):
        # Another worktree took the branch the person chose while this chat talked with them: two chats never
        # share one. Nothing is changed; the same command plans and builds on it once it is free.
        raise checkout_lock.CheckoutBusy(f"This chat waits to plan on {branch}: {problem} Nothing was changed.")
    created = workspaces.create(workspace, state.get("task") or "task", branch=branch)
    tree = Path(created["workspace"])
    record = {
        "state": PLANNING,
        **({"branch": branch} if branch else {}),
        "moving_to": {
            "workspace": str(tree),
            "run_dir": str(tree / ".autocode" / "runs" / run_dir.name),
            "branch": created["branch"],
            "base_commit": created["base_commit"],
        },
        "left_out": left_out,
    }
    state[KEY] = record
    persist(run_dir / "state.json", state)
    return record


def move(state: RunState, workspace, run_dir, persist) -> dict:
    """Carry an approved run from the person's checkout to a new task worktree; return where it went.

    ``state`` is the run in the checkout, saved there by ``persist(path, state)`` with the record
    of where it went; the caller holds its run lock and stops its loop. The new run is saved ready to
    continue: its next invocation builds there, and nothing is written in the checkout's files.
    Safe to repeat after a crash: the worktree and new run an interrupted move made are reused."""
    workspace, run_dir = Path(workspace).resolve(), Path(run_dir).resolve()
    record = _prepared(state, workspace, run_dir, persist)
    target, at = dict(record["moving_to"]), util.now()
    tree, child_dir = Path(target["workspace"]), Path(target["run_dir"])
    if not (child_dir / "state.json").is_file():
        run_destination.discard_unsaved(child_dir)  # an interrupted move's empty directory
        child_dir = run_destination.reserve(tree, child_dir)
        child = copy.deepcopy(state)
        for key in IN_PLACE_ONLY:
            child.pop(key, None)  # type: ignore[misc]
        child["workspace"], child["run_dir"] = str(tree), str(child_dir)
        child["project_workspace"], child["task_branch"] = str(workspace), target["branch"]
        child["base_commit"], child["sessions"] = target["base_commit"], {}
        package_writes.at_launch(child, tree)
        child[KEY] = {
            "state": BUILDING,
            "moved_from": {"workspace": str(workspace), "run_dir": str(run_dir), "at": at},
            "branch": target["branch"],
            "existing_branch": bool(record.get("branch")),
            "base_commit": target["base_commit"],
            "left_out": record.get("left_out") or [],
        }
        if not contract.approved(state):
            child[KEY]["after_events"] = len(state.get("user_events") or [])
        with util.run_lock(child_dir):
            persist(child_dir / "state.json", child)
            _carry_activity(run_dir, child_dir)
            # This controller saved the new run on purpose, ready for its next command: say so in the
            # receipt a status read checks, as an approval that launches nothing does (#2332).
            with controller_owner.Owner(child, child_dir, persist) as owner:
                owner.result(2, ready=True)
    child = cast("RunState", util.read(child_dir / "state.json"))
    target = {key: target[key] for key in ("workspace", "run_dir", "branch")} | {"at": at}
    state[KEY] = {"state": MOVED, "moved_to": target}
    # Never shown: status reads and actions on this record are answered by the new run (resolve).
    run_status.pause(state, "PAUSED_REQUESTED", f"Moved to its own worktree to build: {child_dir}", paused_at=at)
    persist(run_dir / "state.json", state)
    registry.move_run(workspace, run_dir, tree, child_dir, child)
    return target


def _carry_activity(source: Path, destination: Path) -> None:
    """The run's activity log so far, so a client's live log carries on in the new run."""
    old = source / activity_log.LOG_NAME
    if old.is_file() and not old.is_symlink():
        (destination / activity_log.LOG_NAME).write_bytes(old.read_bytes())


def notice(state: RunState) -> str | None:
    """What the person reads once, where the run moved, about where it plans and builds."""
    record = state.get(KEY) or {}
    if record.get("state") != BUILDING:
        return None
    origin = (record.get("moved_from") or {}).get("workspace") or "your folder"
    branch = record.get("branch") or "its own branch"
    work = "plan and build" if "after_events" in record else "build"
    whose, start = (
        ("your branch", f"the last commit on {branch}")
        if record.get("existing_branch")
        else ("its own branch", "your last commit")
    )
    text = (
        f"I {work} this on {whose}, {branch}, in a separate copy of {origin}, so your folder and "
        f"other chats there are not changed while I work. It starts from {start}; uncommitted "
        "changes and ignored files (such as installed dependencies or local settings) are not part of it."
    )
    left = [str(path) for path in record.get("left_out") or []]
    if left:
        named = ", ".join(left[:NAMED]) + (f" and {len(left) - NAMED} more" if len(left) > NAMED else "")
        text += f" Not included from your folder: {named}."
    return text + " When it is done, review and merge the branch; I never merge it myself."
