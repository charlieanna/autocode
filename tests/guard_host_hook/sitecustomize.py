"""Hold only an owned real CLI process at a deterministic editor-test boundary."""

import os
import sys
from pathlib import Path

kind = os.environ.get("AUTOCODE_GUARD_HOST_HOLD")
marker = os.environ.get("AUTOCODE_GUARD_HOST_MARKER")


def hold():
    Path(marker).write_text("ready")
    sys.stdin.readline()


if kind == "writer":
    hold()
    os._exit(0)  # This synthetic competing writer never takes its requested action.
elif kind == "status":
    import autocode_status_command

    original_render = autocode_status_command.render

    def render(*args, **kwargs):
        hold()  # The real status has reached its render, after loading the run.
        return original_render(*args, **kwargs)

    autocode_status_command.render = render
