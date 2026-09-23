#!/usr/bin/env python3
"""Persistent dashboard subprocess entry point for a pinned upstream checkout."""

from pathlib import Path
import os
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autocode_provider_adapter.launcher import main


if __name__ == "__main__":
    checkout = os.environ.get("AUTOCODE_GOCODE_CHECKOUT")
    record = os.environ.get("AUTOCODE_GOCODE_PIN_RECORD")
    provider = os.environ.get("AUTOCODE_PROVIDER")
    if not checkout or not record or not provider:
        raise SystemExit("Autocode checkout, pin record and provider are not configured")
    if sys.argv[1:] == ["--models"]:
        raise SystemExit(main(["models", "--provider", provider, "--workspace", str(Path.cwd())]))
    raise SystemExit(main(["run", "--provider", provider, "--checkout", checkout, "--record", record,
                           "--", *sys.argv[1:]]))
