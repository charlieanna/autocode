"""Hold only the owned synthetic competing writer for the editor test."""

import os
import sys
from pathlib import Path

if os.environ.get("AUTOCODE_GUARD_HOST_HOLD") == "writer":
    Path(os.environ["AUTOCODE_GUARD_HOST_MARKER"]).write_text("ready")
    sys.stdin.readline()
    os._exit(0)  # Never execute the synthetic writer action.
