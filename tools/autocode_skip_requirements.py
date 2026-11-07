"""An unattended worker's run: Requirements' questions take their defaults, and optionally the plan approves (#1731).

Owner decisions, 2026-10-21. Every way in leads to the same conversation, because "the first plan will always
have issues anyway"; a worker that runs AutoCode with nobody watching passes a flag instead:

- ``--skip-requirements``: where the run would stop for the person's answers, every published question that
  could be bulk-delegated (``goals.delegable``: a proposed default, marked delegable, not a protected category
  such as cost, permission or the requested outcome, not a rejected assumption) is answered with its default
  and recorded as delegated with ``source: skip_requirements``. The Planner and Plan Reviewer still run in
  full. A question that cannot be delegated still stops the run for a person, and so does plan approval.
- ``--skip-to-build``: the same, and once the Plan Reviewer has finalized the plan the run approves it and goes
  on to the Orchestrator. The approval is the person's choice made when they started the run, so it is recorded
  as theirs (``actor: user_cli``, with this checkout's provenance and the run's require-approver enforced) and
  marked ``source: skip_to_build``. Without that flag AutoCode never approves its own plan ("never self
  approve ... only if user wants to skip and go to orchestrator for building").

Every check a typed answer or approval passes still applies here. Given the runner module (as autocode_build_loop
is), it never imports autocode.
"""

from __future__ import annotations

import copy
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_approval_provenance as approval_provenance
    from . import autocode_goal_lifecycle as lifecycle
    from . import autocode_goals as goals
    from . import autocode_resolver_human as resolver_human
except ImportError:
    import autocode_approval_provenance as approval_provenance
    import autocode_goal_lifecycle as lifecycle
    import autocode_goals as goals
    import autocode_resolver_human as resolver_human

DELEGATED = "skip_requirements"
APPROVED = "skip_to_build"
MAX_ROUNDS = 5  # rounds of questions answered by default before a person is asked instead


def add_arguments(parser) -> None:
    """The two new-run flags (autocode_args)."""
    parser.add_argument(
        "--skip-requirements",
        action="store_true",
        help="New run, for an unattended worker: answer each delegable question with its proposed default "
        "(recorded as delegated); the Planner and Plan Reviewer still run and the plan still waits for your approval",
    )
    parser.add_argument(
        "--skip-to-build",
        action="store_true",
        help="New run: --skip-requirements, and approve the plan once the Plan Reviewer has finalized it, "
        "then build. Without it AutoCode never approves its own plan",
    )


def new_run_settings(args, joint) -> dict:
    """The settings a new run saves for these flags; --skip-to-build implies --skip-requirements."""
    to_build = bool(getattr(args, "skip_to_build", False))
    if to_build and not joint:
        raise ValueError("--skip-to-build needs joint planning: the Plan Reviewer must finalize the plan it approves")
    if to_build:
        return {"skip_requirements": True, "skip_to_build": True}
    return {"skip_requirements": True} if getattr(args, "skip_requirements", False) else {}


def resume_refused(settings: dict, args) -> bool:
    """A saved run keeps the choice it started with; repeating that choice is allowed."""
    asked = new_run_settings(args, True)
    return bool(asked) and any(bool(settings.get(key)) != value for key, value in asked.items())


def enabled(state: RunState) -> bool:
    return bool((state.get("settings") or {}).get("skip_requirements"))


def approves(state: RunState) -> bool:
    return bool((state.get("settings") or {}).get("skip_to_build"))


def rounds(state: RunState) -> int:
    """How many question rounds this run answered by default."""
    return len(
        {
            event.get("round")
            for event in state.get("user_events") or []
            if isinstance(event, dict) and event.get("source") == DELEGATED
        }
    )


def settle(runner, state: RunState, run_dir, *, persist=None, native=None) -> bool:
    """Answer or approve where the settings allow it; True when the run is RUNNING again."""
    if not enabled(state) or state.get("status") not in ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"):
        return False
    runner.normalize_human_boundary(state, run_dir)
    published = resolver_human.current(state)
    if not published:
        return False
    if state["status"] == "WAITING_FOR_USER" and published["scope"] == "clarification":
        return _delegate(runner, state, run_dir, published, persist=persist, native=native)
    if state["status"] == "AWAITING_GOAL_APPROVAL" and published["scope"] == "goal_approval" and approves(state):
        return _approve(runner, state, run_dir, published, persist=persist, native=native)
    return False


def _delegate(runner, state: RunState, run_dir, published, *, persist, native) -> bool:
    if rounds(state) >= MAX_ROUNDS:
        print(f"--skip-requirements answered {MAX_ROUNDS} rounds of questions by default; these wait for you.")
        return False
    offered = {q["id"] for q in published["questions"]}
    chosen = [q for q in goals.delegable(state) if q["id"] in offered]
    if not chosen:
        return False

    def delegate(candidate):
        for question in chosen:
            goals.answer(
                candidate,
                question["id"],
                "accept default",
                delegated=True,
                source=DELEGATED,
                round=published["request_id"],
            )

    if not _act(runner, state, run_dir, published, delegate, persist=persist, native=native):
        return False
    print("--skip-requirements: answered by default: " + ", ".join(q["id"] for q in chosen), flush=True)
    return state["status"] == "RUNNING"


def _approve(runner, state: RunState, run_dir, published, *, persist, native) -> bool:
    if native is not None:
        return False  # a native visual run approves only through its own reviewed operator intent
    rendered = lifecycle.present(state, run_dir)
    if run_dir is not None:
        (persist or runner.write_json)(run_dir / "state.json", state)
    print(rendered)
    selected = state["displayed_goal"]
    if not _act(
        runner,
        state,
        run_dir,
        published,
        lambda candidate: lifecycle.approve(candidate, selected, source=APPROVED),
        persist=persist,
        native=native,
    ):
        return False
    print(f"--skip-to-build: plan {selected} approved as you asked when starting this run.", flush=True)
    return state["status"] == "RUNNING"


def _act(runner, state: RunState, run_dir, published, decision, *, persist, native) -> bool:
    """Apply ``decision`` exactly as the terminal conversation applies a person's reply (autocode.chat_checkpoint)."""
    candidate = copy.deepcopy(state)
    resolver_human.require_response(candidate, published["request_id"], published["request_token"])
    if not approval_provenance.decide(decision, state, candidate, sys.argv[1:]):
        return False  # the run's require-approver refused this checkout's Git identity: nothing changed
    runner.finish_human_action(candidate, published)
    runner.normalize_human_boundary(candidate, run_dir)
    runner.commit_user_action(state, candidate, run_dir, persist=persist or runner.write_json, native=native)
    return True
