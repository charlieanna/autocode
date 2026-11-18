"""Early base-suite baseline check started when the plan is approved (#3348).

The regression proof needs the suite's result on the base commit. That result
depends only on the base commit, the test command and the proof's scope, all
of which are known when the plan is approved. This module overlaps that
deterministic runner check with the model stage (Builder) in an isolated scratch
tree. The first proof reuses it if the binding matches, waits if it is still
running, or discards and recomputes if there is a mismatch.

Launches no model, installs nothing into the project, and introduces no second
controller (AGENTS.md rule 5).
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_follow_up as follow_up
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_package_scope as package_scope
    from . import autocode_regression_state as regression_state
    from . import autocode_run_base as run_base
    from . import autocode_util as util
    from . import autocode_verification_schedule as schedule
    from . import autocode_verify as verify
except ImportError:
    import autocode_follow_up as follow_up
    import autocode_launch_inputs as launch_inputs
    import autocode_package_scope as package_scope
    import autocode_regression_state as regression_state
    import autocode_run_base as run_base
    import autocode_util as util
    import autocode_verification_schedule as schedule
    import autocode_verify as verify

STAGE = "regression_baseline"


class EarlyBaselineHandle:
    """Thread-safe handle for an in-flight background base-suite check."""

    def __init__(
        self,
        directory: Path,
        output_log: Path,
        base: str,
        command: str,
        binding: dict[str, Any],
        started_at: str,
        started_monotonic: float,
        run_dir: Path,
    ) -> None:
        self.directory = Path(directory)
        self.output_log = Path(output_log)
        self.base = base
        self.command = command
        self.binding = binding
        self.started_at = started_at
        self.started_monotonic = started_monotonic
        self.run_dir = Path(run_dir)
        self.finished_at: str | None = None
        self.duration_seconds: float = 0.0
        self.completed = threading.Event()
        self.result: dict[str, Any] | None = None
        self.path: str | None = None
        self.sha256: str | None = None
        self.error: Exception | None = None
        self.timed_out = False
        self.thread: threading.Thread | None = None
        self.stopped = False

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=timeout)


_ACTIVE_HANDLES: dict[str, EarlyBaselineHandle] = {}
_LOCK = threading.Lock()


def is_enabled(state: RunState) -> bool:
    """Whether early baseline is enabled by settings and project configuration."""
    settings = state.get("settings") or {}
    if settings.get("early_baseline") is False:
        return False
    regression_cfg = settings.get("regression") or {}
    return regression_cfg.get("early_baseline") is not False


def suite_timeout(state: RunState) -> int | None:
    """Timeout in seconds for the suite run on base."""
    settings = state.get("settings") or {}
    regression_cfg = settings.get("regression") or {}
    if "test_timeout" in regression_cfg:
        return regression_cfg["test_timeout"]
    limits = settings.get("limits") or {}
    limit = limits.get("tool_timeout_seconds")
    if limit is None:
        return verify.DEFAULT_TIMEOUT
    return max(limit, verify.DEFAULT_TIMEOUT) if limit else None


def proof_dependencies(state: RunState, workspace: Path) -> dict[str, Any]:
    project_workspace = state.get("project_workspace")
    source = Path(project_workspace) if project_workspace else workspace
    roots = [source]
    return {
        "dependencies_from": source,
        "roots": roots,
    }


def reviewed_patch(state: RunState, workspace: Path) -> Path | None:
    reviewed = follow_up.review_findings(state) or {}
    name = str(reviewed.get("change_patch") or "").strip()
    if not name:
        return None
    project_workspace = state.get("project_workspace")
    candidates = [
        Path(workspace) / name,
        *([Path(str(project_workspace)) / name] if project_workspace else []),
    ]
    return next((path for path in candidates if path.is_file()), candidates[0])


def select_suite(state: RunState, workspace: Path, run_dir: Path, *, all_due: bool = False):
    """Determine the suite command, framework, and base commit for the base-suite run.

    Uses the exact same selection rules as regression._prove_once.
    """
    workspace = Path(workspace)
    base = run_base.base_commit(state, workspace)
    if not base:
        return None

    settings = state.get("settings") or {}
    options = settings.get("regression") or settings
    command = options.get("test_command")
    framework = (
        verify.command_framework(command, test_report=options.get("test_report"))
        or package_scope.own(settings, command, verify.command_framework)
        if command
        else None
    )
    python = (
        (framework.python if framework and framework.name in ("pytest", "unittest") else None)
        or options.get("python")
        or verify.python_for(state.get("project_workspace") or workspace)
    )

    detection = {"base": base}
    if "package" in settings:
        detection.update(package_scope.detection(settings))
    framework = framework or verify.detect_framework(workspace, python=python, **detection)

    suite = options.get("test_command") or (framework.suite if framework else None)
    if not suite:
        return None

    dependencies = proof_dependencies(state, workspace)
    base_patch = reviewed_patch(state, workspace)
    inputs = launch_inputs.from_state(state) if hasattr(launch_inputs, "from_state") else None
    return suite, framework, base, dependencies, base_patch, inputs


def compute_binding(
    state: RunState,
    workspace: Path,
    base: str,
    suite: str,
    framework: Any,
    dependencies: dict[str, Any],
    base_patch: Path | None,
    inputs: Any,
) -> dict[str, Any]:
    """Compute the immutable binding for the base-suite check."""
    binding = {
        "base": base,
        "command": suite,
        "framework": framework.to_dict() if framework else None,
        "base_patch": schedule.tree_identity(base_patch) if base_patch else None,
        "timeout": suite_timeout(state),
        "runtime": verify.baseline_identity(
            workspace,
            command=suite,
            dependencies_from=dependencies.get("dependencies_from"),
            ignored_inputs=inputs,
        ),
    }
    if framework is not None and getattr(framework, "name", None) == "junit":
        binding["runtime"]["reuse_supported"] = False
        binding["runtime"]["cache_policy"] = "fresh_junit_runtime_execution"
    return binding


def maybe_start(
    state: RunState,
    workspace: Path | str,
    run_dir: Path | str,
    *,
    persist: Any = None,
) -> EarlyBaselineHandle | None:
    """Start the base suite in background if eligible, overlapping with the Builder."""
    if not is_enabled(state):
        return None

    workspace = Path(workspace)
    run_dir = Path(run_dir)
    run_dir_key = str(run_dir.resolve())

    with _LOCK:
        existing = _ACTIVE_HANDLES.get(run_dir_key)
        if existing and (existing.is_alive() or existing.completed.is_set()):
            return existing

        cached = regression_state.mapping(state)
        if cached.get("path") and Path(str(cached["path"])).exists():
            return None

        selection = select_suite(state, workspace, run_dir)
        if selection is None:
            return None

        suite, framework, base, dependencies, base_patch, inputs = selection
        binding = compute_binding(state, workspace, base, suite, framework, dependencies, base_patch, inputs)

        # 4. Bounded: Only where reuse is supported
        if not binding.get("runtime", {}).get("reuse_supported"):
            return None

        directory = run_dir.resolve() / "regression" / ("baseline-" + uuid.uuid4().hex)
        log_path = directory / "baseline" / "suite-on-base.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        started_at = util.now()
        started_monotonic = time.monotonic()
        handle = EarlyBaselineHandle(
            directory=directory,
            output_log=log_path,
            base=base,
            command=suite,
            binding=binding,
            started_at=started_at,
            started_monotonic=started_monotonic,
            run_dir=run_dir,
        )
        _ACTIVE_HANDLES[run_dir_key] = handle

        # 3 & 5. Shown and accounted: Runner check in status view
        state["active_runner_check"] = {
            "stage": STAGE,
            "summary": "Testing original code in background",
            "started_at": started_at,
            "command": suite,
            "output": str(log_path),
            "started_early": True,
        }
        if persist is not None:
            persist()

        def _worker() -> None:
            try:
                result = verify.baseline(
                    workspace,
                    base,
                    directory,
                    framework=framework,
                    suite_command=suite,
                    timeout=binding["timeout"],
                    dependencies_from=dependencies.get("dependencies_from"),
                    base_patch=base_patch,
                    ignored_inputs=inputs,
                )
                path = directory / "baseline.json"
                util.atomic_json(path, result)
                handle.finished_at = util.now()
                handle.duration_seconds = round(max(0.0, time.monotonic() - started_monotonic), 2)
                handle.result = result
                handle.path = str(path)
                handle.sha256 = util.file_hash(path)
                handle.timed_out = bool(result.get("receipt", {}).get("timed_out"))
            except Exception as err:
                handle.error = err
            finally:
                handle.completed.set()

        thread = threading.Thread(target=_worker, name="autocode-early-baseline", daemon=True)
        handle.thread = thread
        thread.start()
        return handle


def get_or_join(
    state: RunState,
    workspace: Path | str,
    run_dir: Path | str,
    binding: dict[str, Any],
    *,
    progress: Any = None,
    persist: Any = None,
) -> dict[str, Any] | None:
    """Retrieve the early baseline result if completed/matching, wait if in-flight, or discard."""
    run_dir_key = str(Path(run_dir).resolve())

    with _LOCK:
        handle = _ACTIVE_HANDLES.get(run_dir_key)

    if handle is None:
        return None

    # Check binding match
    if handle.binding != binding:
        with _LOCK:
            _ACTIVE_HANDLES.pop(run_dir_key, None)
            diff_keys = [k for k in binding if handle.binding.get(k) != binding.get(k)]
            state.setdefault("user_events", []).append(
                {
                    "kind": "early_baseline_discarded",
                    "actor": "runner",
                    "at": util.now(),
                    "reason": f"Binding mismatch on keys: {diff_keys}",
                    "early_binding": handle.binding,
                    "current_binding": binding,
                }
            )
            check = state.get("active_runner_check")
            if isinstance(check, dict) and check.get("stage") == STAGE:
                state.pop("active_runner_check", None)
            if persist is not None:
                persist(Path(run_dir) / "state.json", state)
        return None

    # Still running with matching binding: wait for it to finish
    if not handle.completed.is_set():
        if progress is not None:
            progress(
                "Waiting for the background base suite to finish",
                command=handle.command,
                output=handle.output_log,
                phase="baseline",
            )
        timeout = binding.get("timeout")
        handle.join(timeout=timeout)

    with _LOCK:
        _ACTIVE_HANDLES.pop(run_dir_key, None)

        if handle.error is not None or handle.result is None or handle.path is None:
            check = state.get("active_runner_check")
            if isinstance(check, dict) and check.get("stage") == STAGE:
                state.pop("active_runner_check", None)
            return None

        # A timed-out early run is not reused
        if handle.timed_out:
            check = state.get("active_runner_check")
            if isinstance(check, dict) and check.get("stage") == STAGE:
                state.pop("active_runner_check", None)
            return None

        # Verify artifact integrity
        if (
            util.file_hash(handle.path) != handle.sha256
            or not schedule.intact(handle.result["receipt"], root=Path(handle.path).parent)
        ):
            return None

        result = handle.result
        path = handle.path

        # Write baseline record in regression state
        cached = regression_state.mapping(state)
        whole = cached.get("whole") or {}
        regression_state.write_record(
            state,
            {
                **({"whole": whole} if whole and whole.get("command") != handle.command else {}),
                "base": handle.base,
                "command": handle.command,
                "path": str(path),
                "health": result["health"],
                "base_patch": str(binding.get("base_patch")) if binding.get("base_patch") else None,
                "binding": binding,
                "sha256": handle.sha256,
                "started_early": True,
            },
        )

        # 5. Shown and accounted: runner-owned stage row marked as started early
        stage_record = {
            "stage": STAGE,
            "role": "runner",
            "iteration": state.get("iteration"),
            "started_at": handle.started_at,
            "finished_at": handle.finished_at or util.now(),
            "runner_owned": True,
            "engine": "runner",
            "duration_seconds": handle.duration_seconds,
            "started_early": True,
            "metrics": {
                "provider_tokens": {
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                }
            },
            "summary": f"Early base suite {result['health']}",
            "output": str(path),
        }
        state.setdefault("stages", []).append(stage_record)
        state.setdefault("history", []).append(stage_record)

        check = state.get("active_runner_check")
        if isinstance(check, dict) and check.get("stage") == STAGE:
            state.pop("active_runner_check", None)

        if persist is not None:
            persist(Path(run_dir) / "state.json", state)

        return result


def reconcile_at_boundary(state: RunState, run_dir: Path | str, *, persist: Any = None) -> None:
    """Reconcile in-flight early baseline at a pause or interruption boundary."""
    run_dir_key = str(Path(run_dir).resolve())
    with _LOCK:
        handle = _ACTIVE_HANDLES.pop(run_dir_key, None)
        if handle and handle.is_alive():
            handle.stopped = True
        check = state.get("active_runner_check")
        if isinstance(check, dict) and check.get("stage") == STAGE:
            state.pop("active_runner_check", None)
            if persist is not None:
                persist(Path(run_dir) / "state.json", state)
