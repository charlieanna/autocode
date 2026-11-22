"""Bind the editor's CLI child to the owned scripted provider before launch."""

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tests import retained_report_fixture_support as fixture  # noqa: E402


def main():
    root = Path(os.environ["AUTOCODE_HOST_READS"]).resolve(strict=True).parent
    fixture.verify(root, REPO, os.environ)
    bindir = root / "bin"
    os.environ["PATH"] = os.pathsep.join((str(bindir), str(Path(sys.executable).parent), "/usr/bin", "/bin"))
    if shutil.which("codex") != str(bindir / "codex"):
        raise ValueError("The editor did not select its owned scripted provider")
    if "--resume-paused" in sys.argv[1:]:
        # The test records ordering only; it never chooses or changes an action.
        # Unbuffered stdout preserves the CLI's revalidation message before a
        # later fake-provider launch in this fixture-owned trace.
        child = subprocess.Popen(
            [sys.executable, "-u", str(REPO / "tools/autocode.py"), *sys.argv[1:]],
            stdout=subprocess.PIPE,
            text=True,
        )
        assert child.stdout is not None
        for line in child.stdout:
            observed = None
            if line.strip() == "The retained report passed current validation without launching a retry.":
                observed = "report_revalidated"
            elif line.startswith("Completion Reviewer: started;"):
                observed = "next_stage_start"
            if observed:
                with (root / "provider-stages.jsonl").open("a") as trace:
                    trace.write(json.dumps({"stage": observed}) + "\n")
            sys.stdout.write(line)
            sys.stdout.flush()
        return child.wait()
    held = runpy.run_path(str(REPO / "vscode-extension/test/host/held_cli.py"))
    return held["main"](sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
