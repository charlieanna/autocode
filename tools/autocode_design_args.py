"""Figma scope and complete-file intake option registration for the CLI.

The main parser calls these at their original positions so help ordering,
accepted flags and defaults remain stable. This module owns no run state.
"""

from pathlib import Path


def add_scope(parser):
    """Register the operator's external scope artifact and trusted digest."""
    parser.add_argument(
        "--figma-approved-scope",
        type=Path,
        help="External reviewed product scope artifact for a new run or stopped reference revision; requires independent SHA256 and complete v2 manifest",
    )
    parser.add_argument(
        "--figma-approved-scope-sha256",
        help="Trusted operator SHA256 of --figma-approved-scope; never loaded from the design bundle",
    )


def add_additional_file(parser):
    """Keep repeated additional-file input separate from the primary reference."""
    parser.add_argument(
        "--figma-additional-file",
        action="append",
        default=[],
        help="Additional approved Figma file for complete native intake; repeat for multiple files",
    )
