"""Creation and saved-action argument boundaries for core conversations."""

from __future__ import annotations

import argparse
from pathlib import Path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--conversation-handoff",
        type=Path,
        help="Validated conversation receipt to attach when creating a task; never grants approval",
    )
    parser.add_argument(
        "--conversation-session",
        action="store_true",
        help="Initialize a core conversation with its creation handoff, before ordinary planning; never grants approval",
    )
    parser.add_argument(
        "--conversation-request",
        type=Path,
        help="Owned sealed conversation request for one exact saved feedback or answer; never starts a provider",
    )


def validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.conversation_session and (args.run_dir or not args.conversation_handoff):
        parser.error("--conversation-session requires a creation-only --conversation-handoff")
    if args.conversation_request and (not args.run_dir or not (args.answer or args.feedback is not None)):
        parser.error("--conversation-request requires a saved run and its exact feedback or answer")
