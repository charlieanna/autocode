"""`autocode modes list --json`: the ways a client may start a new run.

A run mode is a named set of start flags for a new run. Clients (the VS Code
extension, the terminal chat, the dashboard) offer these choices and pass the
chosen mode's ``start_args`` unchanged when they start a run; they never build
a list of their own. Every mode keeps the plan approval. ``full`` (shown as the
slow lane) keeps the Plan Reviewer, the Tester and the Completion Reviewer;
``fast`` (the fast lane the person picks, autocode_fast_lane) sends the plan to
the person without a Plan Reviewer and checks the build with one combined
review. A mode never changes what counts as done. Listing launches nothing and
reads no run.

Every chat starts with the Requirements conversation (owner decision,
2026-10-22); the "quick" mode that let a clear request skip Requirements is
gone. ``plan`` runs the same planning flow and ends at the accepted plan
(autocode_plan_only): nothing is built. ``review`` (Review only, owner decision
2026-11-27) runs the review job on the same path and writes nothing but its
findings (autocode_review_only). A client still reads the list and must take a
saved choice the list no longer names as the default.
"""

from __future__ import annotations

import argparse
import json

SCHEMA_VERSION = 1
DEFAULT = "full"

# Each mode's start_args are flags of the main parser (autocode_args), passed
# after the brief. tests/test_run_modes.py parses every one, so a renamed flag
# fails there rather than in a client.
MODES = (
    {
        "id": "full",
        "label": "Slow lane",
        "description": (
            "Requirements talks with you until it understands the request, then the plan is "
            "prepared, reviewed and shown to you for approval before anything is built. After the build "
            "a Tester and then a Completion Reviewer check the work."
        ),
        "start_args": ["--joint-planning", "--spec-first"],
        "spend_confirmation": "planning",
    },
    {
        # The person's choice for a task they know is small (owner decision 2026-11-26, autocode_fast_lane).
        "id": "fast",
        "label": "Fast lane",
        "description": (
            "The same Requirements conversation, then the plan comes to you for approval without a Plan Reviewer, "
            "and one combined review checks the work after the build. The regression proof and the other "
            "evidence checks still decide whether the task may finish."
        ),
        "start_args": ["--joint-planning", "--spec-first", "--fast-lane"],
        "spend_confirmation": "planning",
    },
    {
        "id": "plan",
        "label": "Plan only",
        "description": (
            "The same Requirements conversation, plan and independent plan review, then it stops: accepting the "
            "reviewed plan ends the chat with the plan as its result. Nothing is built or changed."
        ),
        "start_args": ["--joint-planning", "--spec-first", "--plan-only"],
        "spend_confirmation": "planning",
    },
    {
        # A review of a pull request, branch, patch or uncommitted change (owner decision 2026-11-27).
        "id": "review",
        "label": "Review only",
        "description": (
            "Reviews a pull request, branch, patch or uncommitted change and changes nothing in the project. The same "
            "Requirements conversation and a reviewed plan you approve, then the review is written as findings by file "
            "and line, and a Tester confirms each blocking finding before you see it."
        ),
        "start_args": ["--joint-planning", "--spec-first", "--review-only"],
        "spend_confirmation": "planning",
    },
)


def view() -> dict:
    """The version-1 run-mode list (docs/task-run.md#run-modes-for-clients)."""
    try:
        from . import autocode_worker_cli as worker
    except ImportError:
        import autocode_worker_cli as worker
    return {
        "schema_version": SCHEMA_VERSION,
        "default": DEFAULT,
        "modes": [{**mode, "start_args": list(mode["start_args"])} for mode in MODES],
        "execution": worker.capabilities(),
    }


def parser():
    result = argparse.ArgumentParser(prog="autocode modes", description=__doc__)
    actions = result.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="List the run modes a client may offer for a new run")
    listing.add_argument("--json", action="store_true", help="Return the public run-mode list")
    return result


def cli(argv) -> int:
    args = parser().parse_args(argv)
    listed = view()
    if args.json:
        print(json.dumps(listed, indent=2))
        return 0
    for mode in listed["modes"]:
        default = " (default)" if mode["id"] == listed["default"] else ""
        print(f"{mode['id']}{default}: {mode['label']}")
        print(f"  {mode['description']}")
        print(f'  autocode "…" {" ".join(mode["start_args"])}')
    return 0
