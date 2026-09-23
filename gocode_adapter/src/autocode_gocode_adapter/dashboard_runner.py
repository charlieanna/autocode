#!/usr/bin/env python3
"""Persistent dashboard subprocess entry point for a pinned upstream checkout."""

from pathlib import Path
import os
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autocode_gocode_adapter.launcher import main


if __name__ == "__main__":
    checkout = os.environ.get("AUTOCODE_GOCODE_CHECKOUT")
    if not checkout:
        raise SystemExit("AUTOCODE_GOCODE_CHECKOUT is not configured")
    if sys.argv[1:] == ["--models"]:
        raise SystemExit(main(["models", "--workspace", str(Path.cwd())]))
    raise SystemExit(main(["run", "--checkout", checkout, "--", *sys.argv[1:]]))
