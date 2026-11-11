"""``checkout_wait`` in the status view: a run whose next step waits for another run to leave its folder.

Two chats in one folder take turns: the Planner, Builder and the stages after them need the checkout to
themselves (autocode_checkout_lock). A run whose next step finds it held exits at a ready boundary with
nothing changed, and its status used to say only "Next: Planner": a client offered a bare Continue, and
pressing it found the folder still busy (2026-11-11, two VS Code chats in one folder). The status read
now says what the run is waiting for, so a client shows the wait and continues the run once the folder
is free, instead of handing the person a button to press until it works.

Display only, inspected without taking the lock; the next advance still decides under the lock itself.
Lower layer: the checkout lock, the build worktree and role names; never the runner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_build_worktree as build_worktree
    from . import autocode_checkout_lock as checkout_lock
    from . import autocode_roles as roles
except ImportError:
    import autocode_build_worktree as build_worktree
    import autocode_checkout_lock as checkout_lock
    import autocode_roles as roles


def project(state: RunState, public: dict, *, workspace=None, run_dir=None) -> dict | None:
    """``{"run_dir", "since", "text"}`` while this run is ready to go on but another run holds its folder."""
    if workspace is None or run_dir is None or public.get("done") or public.get("status") != "RUNNING":
        return None
    if (public.get("needs") or {}).get("kind") != "continue":
        return None
    if (public.get("liveness") or {}).get("kind") in ("supervised", "unsupervised"):
        return None
    controller = public.get("controller") or {}
    if controller.get("kind") == "running" and controller.get("alive") is True:
        return None
    if state.get("active_stage") or state.get("active_runner_check"):
        return None
    # A run about to move to its own worktree plans there, not in this folder.
    if not checkout_lock.needed_by(state) or build_worktree.due(state):
        return None
    other = checkout_lock.held_by_another(workspace, run_dir)
    if other is None:
        return None
    stage = state.get("next_stage")
    job = roles.screen_name(stage, state) if isinstance(stage, str) and stage else "The next step"
    text = (
        f"{job} is waiting for this folder: another chat is working in it, and two chats would change "
        "the same files at once. Nothing was changed."
    )
    return {**other, "text": text}
