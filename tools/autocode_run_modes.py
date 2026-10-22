"""`autocode modes list --json`: the ways a client may start a new run.

A run mode is a named set of start flags for a new run. Clients (the VS Code
extension, the terminal chat, the dashboard) offer these choices and pass the
chosen mode's ``start_args`` unchanged when they start a run; they never build
a list of their own. Every mode keeps the plan approval, the Tester and the
Completion Reviewer: a mode changes how much planning a run does before you
approve it, never what counts as done. Listing launches nothing and reads no run.

Every chat starts with the Requirements conversation (owner decision,
2026-10-22), so ``full`` is the only mode; the "quick" mode that let a clear
request skip Requirements is gone. A client still reads the list and must take
a saved choice the list no longer names as the default.
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
        "label": "Full",
        "description": (
            "Requirements talks with you until it understands the request, then the plan is "
            "prepared, reviewed and shown to you for approval before anything is built."
        ),
        "start_args": ["--joint-planning", "--spec-first"],
        "spend_confirmation": "planning",
    },
)


def view() -> dict:
    """The version-1 run-mode list (docs/task-run.md#run-modes-for-clients)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "default": DEFAULT,
        "modes": [{**mode, "start_args": list(mode["start_args"])} for mode in MODES],
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
