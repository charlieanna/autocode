"""Test-owned clock and cleanup fault for an externally crashed build CLI.

The provider, deadline callbacks, ownership receipts and recovery CLI are real.
Only time advancement and the two cleanup threads are controlled by the test.
This module is copied into a temporary root; production files are never edited.
"""

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def prepare(root, tools):
    hooks = root / "timeout-hooks"
    hooks.mkdir()
    shutil.copy2(__file__, hooks / "timeout_fault.py")
    (hooks / "sitecustomize.py").write_text(
        "import os\nfrom pathlib import Path\nimport timeout_fault\n"
        "timeout_fault.install(Path(os.environ['BUILD_AUDIT_TIMEOUT_ROOT']), " + repr(str(tools)) + ")\n"
    )
    (hooks / "keeper.py").write_text(
        "import os,sys\nfrom pathlib import Path\n"
        "sys.path.insert(0,str(Path(__file__).resolve().parent))\n"
        "import timeout_fault\n"
        "timeout_fault.install(Path(os.environ['BUILD_AUDIT_TIMEOUT_ROOT']), " + repr(str(tools)) + ", keeper=True)\n"
        "import autocode_supervision_keeper as keeper\n"
        "raise SystemExit(keeper.main(int(sys.argv[1]),int(sys.argv[2])))\n"
    )
    advance(root, 0)
    return hooks


def advance(root, value):
    pending = root / "clock.pending"
    pending.write_text(str(value))
    pending.replace(root / "clock")


def install(root, tools, *, keeper=False):
    if getattr(time, "_build_audit_timeout_fault", False):
        raise RuntimeError("The timeout clock fixture was installed twice")
    time._build_audit_timeout_fault = True
    real_clock = time.monotonic
    release = root / "cleanup-release"
    time.monotonic = lambda: real_clock() if release.exists() else float((root / "clock").read_text())

    if Path(sys.argv[0]).name == "codex":
        # The readiness barrier follows the provider's real SIGTERM disposition.
        original_signal = signal.signal

        def disposition(sig, handler):
            previous = original_signal(sig, handler)
            if sig == signal.SIGTERM and handler == signal.SIG_IGN:
                pending = root / "provider-ready.pending"
                pending.write_text(str(os.getpid()))
                pending.replace(root / "provider-ready")
            return previous

        signal.signal = disposition
        return

    class ClockTimer(threading.Thread):
        def __init__(self, interval, function, args=None, kwargs=None):
            super().__init__()
            self.interval, self.function = interval, function
            self.args, self.kwargs = args or (), kwargs or {}
            self.finished = threading.Event()

        def start(self):
            self.deadline = time.monotonic() + self.interval
            if not keeper and getattr(self.function, "__name__", "") == "stop_at_deadline" and self.interval == 1:
                pending = root / "controller-deadline-ready.pending"
                pending.write_text(str(os.getpid()))
                pending.replace(root / "controller-deadline-ready")
            super().start()

        def run(self):
            while time.monotonic() < self.deadline:
                if self.finished.wait(0.01):
                    return
            if not self.finished.is_set():
                self.function(*self.args, **self.kwargs)

        def cancel(self):
            self.finished.set()

    threading.Timer = ClockTimer

    sys.path.insert(0, str(tools))
    import autocode_process as processes
    import autocode_util as util

    original_stop, original_atomic = processes.ProcessTree.stop, util.atomic_json

    def stop(tree, child):
        while not release.exists():
            threading.Event().wait(0.01)
        return original_stop(tree, child)

    processes.ProcessTree.stop = stop

    def atomic(path, value):
        original_atomic(path, value)
        if keeper:
            if value.get("phase") == "stopping" and value.get("cause") == "stage_deadline":
                original_atomic(root / "keeper-timeout.json", value)
        elif isinstance(value, dict):
            active = value.get("active_stage") or {}
            if (
                Path(path).name == "state.json"
                and active.get("stage") == "terra"
                and active.get("activity", {}).get("timeout_kind") == "stage"
            ):
                original_atomic(
                    root / "controller-timeout.json", {key: active[key] for key in ("stage", "activity", "supervision")}
                )
                os.kill(os.getpid(), signal.SIGSTOP)

    util.atomic_json = atomic

    if not keeper:
        original_popen = subprocess.Popen

        def launch(command, *args, **kwargs):
            if (
                isinstance(command, list)
                and len(command) > 2
                and Path(str(command[2])).name == "autocode_supervision_keeper.py"
            ):
                if (
                    len(command) != 5
                    or command[1] not in ("-E", "-I")
                    or Path(command[2]).resolve() != (Path(tools) / "autocode_supervision_keeper.py").resolve()
                ):
                    raise AssertionError("Unexpected production keeper launch argv")
                command = [*command[:2], str(root / "timeout-hooks" / "keeper.py"), *command[3:]]
            return original_popen(command, *args, **kwargs)

        subprocess.Popen = launch
