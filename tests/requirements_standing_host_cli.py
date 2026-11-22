"""Guard the offline Requirements fixture before each actual editor CLI child."""

from __future__ import annotations

import os
import runpy
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def pin_provider() -> None:
    root = Path(os.environ["AUTOCODE_HOST_READS"]).resolve(strict=True).parent
    bindir = root / "bin"
    provider = bindir / "codex"
    base = bindir / "base_codex.py"
    helper = bindir / "fake_changed_code.py"
    config = root / "fake-config.json"
    script = REPO / "tests/requirements_standing_provider.py"
    expected = f'#!{sys.executable}\nimport runpy\nrunpy.run_path({str(script)!r}, run_name="__main__")\n'
    paths = (bindir, provider, base, helper, config)
    if (
        any(path.is_symlink() for path in paths)
        or not bindir.is_dir()
        or not all(path.is_file() for path in paths[1:])
        or provider.read_text() != expected
        or base.read_bytes() != (REPO / "scenarios/harness/fake_codex.py").read_bytes()
        or helper.read_bytes() != (REPO / "scenarios/harness/fake_changed_code.py").read_bytes()
        or not os.access(provider, os.X_OK)
        or os.environ.get("STANDING_BASE_PROVIDER") != str(base)
        or os.environ.get("SCENARIO_FAKE_CONFIG") != str(config)
        or os.environ.get("AUTOCODE_HOME") != str(root / "registry")
        or os.environ.get("AUTOCODE_SOURCE_HOST") != "0"
    ):
        raise ValueError("The editor cannot identify its owned scripted Requirements provider")
    os.environ["PATH"] = os.pathsep.join((str(bindir), os.environ.get("PATH", "")))
    if shutil.which("codex") != str(provider):
        raise ValueError("The editor could not select its owned scripted Requirements provider")


def main() -> int:
    pin_provider()
    held = runpy.run_path(str(REPO / "vscode-extension/test/host/held_cli.py"))
    return held["main"](sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
