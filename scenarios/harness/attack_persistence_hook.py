"""TEST ONLY: inject an OS failure at a test-owned atomic rename.

Copied as sitecustomize into a disposable CLI environment. This never imports
AutoCode, edits saved state, or touches another process's paths.
"""
import errno
import json
import os
from pathlib import Path

spec = os.environ.get("AUTOCODE_TEST_IO_FAULT")
if spec:
    config = json.loads(Path(spec).read_text())
    target = os.path.abspath(config["target"])
    marker = Path(config["marker"])
    original = os.replace

    def replace(source, destination, *args, **kwargs):
        mode = config["mode"]
        persistent = mode in ("disk_full", "io_error")
        if os.path.abspath(destination) != target or (marker.exists() and not persistent):
            return original(source, destination, *args, **kwargs)
        marker.write_text(json.dumps({"pid": os.getpid(), "mode": config["mode"],
                                      "target": target, "boundary": "os.replace"}))
        if mode in ("disk_full", "transient_disk_full"):
            raise OSError(errno.ENOSPC, "injected full test filesystem", target)
        if mode == "io_error":
            raise OSError(errno.EIO, "injected test I/O failure", target)
        if mode == "crash_before_replace":
            os._exit(97)
        if mode == "crash_after_replace":
            original(source, destination, *args, **kwargs)
            os._exit(97)
        raise RuntimeError("Unknown test fault: " + mode)

    os.replace = replace
