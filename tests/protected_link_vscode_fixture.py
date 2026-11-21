"""Create an offline protected-test-link hold for the real extension host.

The pause is staged only after a genuine pinned scratch-copy refusal. The
extension reads and acts on the run exclusively through the public CLI.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "tools")]

import autocode_source_scope as scope  # noqa: E402
import autocode_util as util  # noqa: E402
import autocode_verify as verify  # noqa: E402

from tests import source_recovery_vscode_fixture as fixture  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: protected_link_vscode_fixture.py ROOT")
    root = Path(argv[0]).resolve()
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        fixture.main([str(root)])
    info = json.loads(stdout.getvalue())
    workspace, run = Path(info["workspace"]), Path(info["runDir"])
    shared = workspace / "shared"
    shared.unlink()
    (workspace / "old-shared").rename(shared)
    source = shared / "test_original.py"
    source.unlink()
    source.symlink_to(root / "external/test_original.py")
    state_path = run / "state.json"
    state = json.loads(state_path.read_text())
    revision = scope.snapshot(workspace, state)["revision"]
    try:
        verify.scratch_run(
            workspace,
            root / "protected-copy",
            command="true",
            timeout=30,
            expected_source_revision=revision,
            protected_test_snapshot_links=True,
        )
    except util.Paused as error:
        if error.status != "PAUSED_SOURCE_UNAVAILABLE":
            raise
        policy = getattr(error, "policy", None)
        if policy is not None:
            state["source_link_recovery"] = policy
        state["stop_reason"] = str(error)
    else:
        raise AssertionError("Unsafe protected test link was unexpectedly admitted")
    state_path.write_text(json.dumps(state, indent=2) + "\n")
    env = {**os.environ, **info["env"]}
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY"):
        env.pop(key, None)
    status = subprocess.run(
        [*info["cli"], "--workspace", str(workspace), "--run-dir", str(run), "--status", "--json"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
        check=True,
    )
    view = json.loads(status.stdout)["view"]
    info["view"] = view
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
