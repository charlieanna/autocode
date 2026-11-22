"""The AutoCode CLI as the Extension Host suite launches it, with a status read it can hold (#3860).

Every argument goes unchanged to this checkout's CLI. Each ``--status`` read is recorded in the
file ``AUTOCODE_HOST_READS`` names, as a ``start PID BIRTH`` line before it runs and an ``end PID CODE``
line after, so the suite can count the readers still outstanding. A read that starts while the file
``AUTOCODE_HOST_HOLD`` names exists records ``held PID`` and waits before it starts the CLI: the
read is held past the editor's deadline and released when the suite removes the file. Nothing else is
changed, and the read itself is the CLI's own. Its child PID/birth is also recorded so
cleanup cannot mistake wrapper exit for completion of the real CLI.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

CLI = Path(__file__).resolve().parents[3] / "tools" / "autocode.py"


def record(line: str) -> None:
    with open(os.environ["AUTOCODE_HOST_READS"], "a", encoding="utf-8") as reads:
        reads.write(line + "\n")


def _offline_fixture():
    """Pin the scripted route in each editor child, even after login PATH resolution."""
    root = Path(os.environ["AUTOCODE_HOST_READS"]).resolve(strict=True).parent
    provider = root / "bin" / "codex"
    recorded = root / "fake-config.json"
    source = CLI.parents[1] / "scenarios/harness/fake_codex.py"
    if (
        recorded.is_symlink()
        or not recorded.is_file()
        or Path(os.environ["SCENARIO_FAKE_CONFIG"]) != recorded
        or provider.parent.is_symlink()
        or provider.is_symlink()
        or not provider.is_file()
        or provider.read_bytes() != source.read_bytes()
        or not os.access(provider, os.X_OK)
    ):
        raise ValueError("The editor fixture cannot identify its scripted provider")
    os.environ["PATH"] = os.pathsep.join((str(provider.parent), os.environ.get("PATH", "")))
    if shutil.which("codex") != str(provider):
        raise ValueError("The editor fixture did not select its scripted provider")


def main(argv: list[str]) -> int:
    if argv[:1] == ["--offline-fixture"]:
        _offline_fixture()
        argv = argv[1:]
    if "--status" not in argv:
        return subprocess.call([sys.executable, str(CLI), *argv])
    record(f"start {os.getpid()} {psutil.Process().create_time()}")
    hold = Path(os.environ["AUTOCODE_HOST_HOLD"])
    if hold.exists():
        record(f"held {os.getpid()}")
    while hold.exists():
        time.sleep(0.1)
    child = subprocess.Popen([sys.executable, str(CLI), *argv])
    try:
        started = psutil.Process(child.pid).create_time()
    except psutil.NoSuchProcess:
        # A child already reaped needs no liveness observation.
        if child.poll() is None:
            raise
    else:
        record(f"child {child.pid} {started}")
    code = child.wait()
    record(f"end {os.getpid()} {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
