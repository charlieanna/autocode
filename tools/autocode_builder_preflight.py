"""Exercise the isolated verification runner before spending on implementation."""

from __future__ import annotations

import shlex
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_runner_check as runner_check
    from . import autocode_source_scope as source_scope
    from . import autocode_test_environment as test_environment
    from . import autocode_util as util
    from . import autocode_verify as verify
except ImportError:
    import autocode_launch_inputs as launch_inputs
    import autocode_runner_check as runner_check
    import autocode_source_scope as source_scope
    import autocode_test_environment as test_environment
    import autocode_util as util
    import autocode_verify as verify


def check(state: RunState, workspace, run_dir, worker, persist):
    """Return readiness evidence, never test credit or permission to reuse correctness evidence."""
    if state.get("next_stage") != "builder" or not worker or worker.get("planning"):
        return None
    if (Path(run_dir) / "pause-requested").exists():
        raise util.Paused("PAUSED_REQUESTED", "Pause requested before Builder runner admission")
    directory = Path(run_dir) / "preflight" / uuid.uuid4().hex
    python = test_environment.run_python(state, workspace)
    marker = "AUTOCODE_RUNNER_READY_" + uuid.uuid4().hex
    script = f"import subprocess, sys; subprocess.run([sys.executable, '-c', {f'print({marker!r})'!r}], check=True)"
    command = shlex.join([python, "-c", script])
    source = source_scope.snapshot(workspace, state)["revision"]
    ignored = launch_inputs.supply(state, workspace, run_dir)
    if ignored and ignored.unverified:
        raise util.Paused(
            "PAUSED_TASK_PREFLIGHT", "Runner inputs unavailable before Builder: " + "; ".join(ignored.unverified)
        )
    with runner_check.track(
        state, run_dir, "task_preflight", "Checking the isolated test runner before Builder", persist
    ) as update:
        update("Checking nested command execution", command=command)
        result = verify.scratch_run(
            workspace,
            directory,
            command=command,
            check_kind="compile",
            python=python,
            timeout=min((state.get("settings") or {}).get("limits", {}).get("tool_timeout_seconds") or 120, 120),
            expected_source_revision=source,
            ignored_inputs=ignored,
            dependencies_from=state.get("project_workspace") or workspace,
        )
    errors = []
    if result.get("exit_code") != 0 or result.get("timed_out") or marker not in result.get("tail", "").splitlines():
        errors.append("The isolated test runner did not complete its nested command before Builder")
    if source_scope.snapshot(workspace, state)["revision"] != source:
        errors.append("Source changed during runner readiness; fresh admission is required before Builder")
    return {
        "kind": "prerequisite",
        "phase": "build",
        "execution_context": "isolated_runner",
        "execution_identity": {"source_revision": source, "test_python": python, "provider_sandbox_attested": False},
        "status": "BLOCKED" if errors else "READY",
        "checked_at": util.now(),
        "errors": errors,
        "checks": [result],
        "receipt": str(directory / "receipt.json"),
    }
