"""Prepare an offline source-recovery status for the actual VS Code host test.

The original run is created by the scripted provider fixture. This helper then
stages its stopped state as a source pause after an actual scratch-copy refusal;
the editor still reads and acts only through this checkout's real public CLI.
No live provider or credentials are used.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import autocode_util as util  # noqa: E402
import autocode_verify as verify  # noqa: E402

from tests import provider_auth_fixture  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: python tests/source_recovery_vscode_fixture.py ROOT")
    root = Path(argv[0]).resolve()
    driver, _ = provider_auth_fixture.stopped_planner_run(root)
    workspace, run = driver.project, driver.run_dir
    shared = workspace / "shared"
    shared.mkdir()
    (shared / "test_original.py").write_text("original = True\n")
    subprocess.run(["git", "-C", str(workspace), "add", "shared"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-qm",
            "source fixture",
        ],
        check=True,
        capture_output=True,
    )
    external = root / "external"
    external.mkdir()
    (external / "test_original.py").write_text("external = True\n")
    shared.rename(workspace / "old-shared")
    shared.symlink_to(external, target_is_directory=True)
    try:
        verify.scratch_run(workspace, root / "copy-evidence", command="true", timeout=30)
    except util.Paused as error:
        if error.status != "PAUSED_SOURCE_UNAVAILABLE":
            raise
        reason = str(error)
    else:
        raise AssertionError("Unsafe source unexpectedly allowed the scratch check")
    state_path = run / "state.json"
    state = json.loads(state_path.read_text())
    state.update(
        status="PAUSED_SOURCE_UNAVAILABLE",
        phase="PAUSED_OR_BLOCKED",
        stop_reason=reason,
        active_stage=None,
        resolver={},
        # The previous scripted failure left iteration-one artifacts. Keep
        # them intact and let the offline resumed attempt use a fresh slot.
        iteration=2,
    )
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    env = {
        key: driver.env[key]
        for key in (
            "AUTOCODE_HOME",
            "XDG_CONFIG_HOME",
            "CODEX_HOME",
            "PATH",
            "SCENARIO_FAKE_CONFIG",
            "PYTHONDONTWRITEBYTECODE",
        )
    }
    command = [sys.executable, str(REPO / "tools/autocode.py")]
    status = subprocess.run(
        [*command, "--run-dir", str(run), "--workspace", str(workspace), "--status", "--json"],
        cwd=workspace,
        env=driver.env,
        text=True,
        capture_output=True,
        timeout=40,
        check=True,
    )
    payload = json.loads(status.stdout)
    view = payload.get("view", payload)
    if view.get("status") != "PAUSED_SOURCE_UNAVAILABLE" or view.get("next_command") is not None:
        raise AssertionError("Unsafe source was unexpectedly offered a continuation")
    print(json.dumps({"workspace": str(workspace), "runDir": str(run), "cli": command, "env": env, "view": view}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
