"""Process handshakes for adversarial tests of the public task-run lifecycle.

These helpers know nothing about AutoCode's state or implementation. The
scripted provider announces that it reached a requested stage through a FIFO,
then waits for one release byte. Tests can crash the CLI at that exact boundary
without timing races or fixed sleeps.
"""

from __future__ import annotations

import json
import os
import selectors
import time
from pathlib import Path


def install(fake, attack: dict, trace) -> None:
    """Install a provider barrier without replacing any legitimate stage report."""
    if attack.get("case") != "provider_barrier":
        raise ValueError(f"unknown lifecycle attack: {attack.get('case')}")
    original = fake.report_for

    def report(stage, data):
        if stage == os.environ.get("LIFECYCLE_HOLD_STAGE"):
            trace("attack_injected", stage=stage, attack="provider_barrier")
            hold_provider(stage)
        return original(stage, data)

    fake.report_for = report


def hold_provider(stage: str) -> None:
    """Provider-side hook; ordinary runs have none of these environment keys."""
    if stage != os.environ.get("LIFECYCLE_HOLD_STAGE"):
        return
    ready = os.environ["LIFECYCLE_READY_FIFO"]
    release = os.environ["LIFECYCLE_RELEASE_FIFO"]
    # Open the release FIFO first. The test keeps both ends open, so announcing
    # readiness guarantees that a release can never be lost between opens.
    with open(release, "rb", buffering=0) as waiter:
        with open(ready, "w") as announcer:
            announcer.write(json.dumps({"stage": stage, "pid": os.getpid()}) + "\n")
            announcer.flush()
        if waiter.read(1) != b"R":
            raise RuntimeError("lifecycle provider barrier closed without release")


class ProviderBarrier:
    """One captured stage boundary; caller owns the containing test directory."""

    def __init__(self, root: Path, stage: str = "terra"):
        self.root = root
        self.stage = stage
        self.ready = root / "provider-ready.fifo"
        self.release_path = root / "provider-release.fifo"
        os.mkfifo(self.ready, mode=0o600)
        os.mkfifo(self.release_path, mode=0o600)
        self.ready_fd = os.open(self.ready, os.O_RDWR | os.O_NONBLOCK)
        self.release_fd = os.open(self.release_path, os.O_RDWR | os.O_NONBLOCK)
        self.environment = {
            "LIFECYCLE_HOLD_STAGE": stage,
            "LIFECYCLE_READY_FIFO": str(self.ready),
            "LIFECYCLE_RELEASE_FIFO": str(self.release_path),
        }
        self._buffer = b""

    def wait(self, timeout: float = 30) -> dict:
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.ready_fd, selectors.EVENT_READ)
            while b"\n" not in self._buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError(f"scripted provider did not reach {self.stage!r}")
                self._buffer += os.read(self.ready_fd, 65536)
        line, self._buffer = self._buffer.split(b"\n", 1)
        announcement = json.loads(line)
        if announcement.get("stage") != self.stage:
            raise AssertionError(f"wrong stage reached barrier: {announcement}")
        return announcement

    def release(self) -> None:
        os.write(self.release_fd, b"R")

    def close(self) -> None:
        for name in ("ready_fd", "release_fd"):
            descriptor = getattr(self, name, None)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)
        for path in (self.ready, self.release_path):
            path.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# Also copied as sitecustomize for one controller-only artifact boundary. The
# real CLI publishes its own result; the hook neither imports runtime nor reads
# or edits private state. Provider exec is isolated from this startup hook.
if os.environ.get("LIFECYCLE_CONTROLLER_CHECKPOINT"):
    target = os.path.abspath(os.environ["LIFECYCLE_CONTROLLER_CHECKPOINT"])
    original_replace = os.replace

    def replace(source, destination, *args, **kwargs):
        result = original_replace(source, destination, *args, **kwargs)
        if os.path.abspath(destination) == target:
            hold_provider(os.environ["LIFECYCLE_HOLD_STAGE"])
        return result

    os.replace = replace
