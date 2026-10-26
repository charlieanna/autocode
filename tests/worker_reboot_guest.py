"""Split C4 fixture phases for one marked disposable guest; never an ordinary test.

Preparation may dispatch only the offline fixture. Inspection performs public
reads only. The reboot phase refuses an unmarked or differently identified host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import autocode_client_actions as actions

ROOT = Path(__file__).resolve().parents[1]
CORE = (sys.executable, str(ROOT / "tools/autocode.py"))
FIXTURE = Path("/home/autocode/reboot-fixture")
MARKER = Path("/etc/autocode-reboot-qualification.json")
BOOT = Path("/proc/sys/kernel/random/boot_id")
# Cloud-init copies the root-only DMI value to this root-owned readable attestation.
PRODUCT = Path("/etc/autocode-reboot-product-uuid")
FILES = ("greet.py", "test_greet.py", "README.md")


def require_guest(instance: str, outer_boot: str) -> None:
    marker = json.loads(MARKER.read_text())
    if (
        sys.platform != "linux"
        or os.getuid() == 0
        or marker != {"instance": instance, "purpose": "autocode-c4-disposable-guest"}
        or PRODUCT.read_text().strip().lower() != instance.lower()
        or BOOT.read_text().strip() == outer_boot
    ):
        raise RuntimeError("This command requires its exact disposable QEMU guest, never the runner or another host")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def attempt_ids(view: dict) -> list[dict]:
    keys = ("identity", "attempt_id", "worker_attempt", "stage", "events", "output", "model", "engine", "launch_route")
    return [
        {key: row.get(key) for key in keys}
        for row in (view.get("usage") or {}).get("accounting", {}).get("attempts", [])
    ]


def completed_usage(view: dict) -> dict:
    """Known recorded accounting of settled attempts; active finalization may add facts."""
    fields = ("tokens", "requests", "reported_cost_usd", "historical_estimated_cost_usd", "duration_seconds")
    return {
        row["identity"]: {key: row.get(key) for key in fields}
        for row in (view.get("usage") or {}).get("accounting", {}).get("attempts", [])
        if row.get("finished_at") and row.get("active") is False
    }


def retains_known(before, after) -> bool:
    if before is None:
        return True  # Unknown baseline usage is not a zero or a fixed fact.
    if isinstance(before, dict):
        return isinstance(after, dict) and all(
            value is None or (key in after and retains_known(value, after[key])) for key, value in before.items()
        )
    if isinstance(before, list):
        return (
            isinstance(after, list)
            and len(before) == len(after)
            and all(retains_known(old, new) for old, new in zip(before, after, strict=True))
        )
    return before == after


def check_reboot(before: dict, after: dict) -> None:
    """Public no-replay oracle; graceful shutdown may retain a checked failure."""
    old, new = before["view"], after["view"]
    assert before["boot_id"] != after["boot_id"], "The kernel boot ID did not change: no literal reboot proof"
    assert old["execution"]["boot_id"] == before["boot_id"], "Baseline launch belongs to another boot"
    assert after["launch"]["ready"] is False, "An old-boot launch must not be ready"
    assert "host rebooted" in after["launch"]["reason"], "Launch inspection must identify the reboot"
    assert after["launch"]["dispatch_count"] == before["launch"]["dispatch_count"] == 1
    assert new["execution"]["inspection"]["boot_matches"] is False
    assert new["execution"]["launch_id"] == old["execution"]["launch_id"]
    assert new["execution"]["controller_epoch"] == old["execution"]["controller_epoch"]
    assert new["controller"]["checked"] is True and new["controller"]["alive"] is False
    assert new["controller"]["kind"] not in ("running", "busy", "unknown")
    assert not actions.working(new), "A rebooted worker must not be advertised as working"
    assert new["done"] is False and new.get("delivery") is None
    assert attempt_ids(new) == attempt_ids(old), "A retained attempt was removed, changed or replayed"
    assert retains_known(completed_usage(old), completed_usage(new)), (
        "Recorded completed-attempt usage was lost or changed"
    )
    assert new["routes"] == old["routes"] and new.get("approvals") == old.get("approvals")
    assert after["files"] == before["files"], "Source edits changed across reboot/read-only reconnect"
    assert after["provider_calls"] == before["provider_calls"], "A provider was invoked during reboot/reconnect"
    assert new["execution"]["runtime"] == old["execution"]["runtime"]
    # An ordinary named recovery or an explicit retained-attempt handoff is honest.
    # A raw RUNNING checkpoint can remain while an uncertain attempt needs reconciliation.
    need = new.get("needs") or {}
    stopped = (
        new["status"].startswith(("PAUSED_", "BLOCKED_"))
        and need.get("kind")
        and need["kind"] != "continue"
        and isinstance(new.get("next_command"), str)
        and bool(new["next_command"].strip())
    )
    retained = after["status"]["stale"] is True and new.get("next_command") is None
    assert stopped or retained, "No honest recovery boundary or retained-attempt handoff was exposed"


def handle() -> dict:
    return json.loads((FIXTURE / "handle.json").read_text())


class Budget:
    """One wall deadline for a guest phase, including fixture imports and reads."""

    def __init__(self, seconds: float, *, clock=None):
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("The guest budget must be a positive finite number")
        self.seconds = float(seconds)
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.deadline = self.started + self.seconds

    def remaining(self) -> float:
        return max(0.0, self.deadline - self.clock())

    def timeout(self) -> float:
        remaining = self.remaining()
        if remaining <= 0:
            raise TimeoutError("The guest operation budget expired")
        return min(15.0, remaining)

    def pause(self) -> None:
        time.sleep(min(0.05, self.timeout()))


def safe_label(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) else None


def object_value(value) -> dict:
    return value if isinstance(value, dict) else {}


def safe_view_summary(view: dict) -> dict:
    view = object_value(view)
    attempts = object_value(object_value(view.get("usage")).get("accounting")).get("attempts")
    attempts = [row for row in attempts if isinstance(row, dict)] if isinstance(attempts, list) else []
    execution = object_value(view.get("execution"))
    return {
        "status": safe_label(view.get("status")),
        "next_stage": safe_label(view.get("next_stage")),
        "needs_kind": safe_label(object_value(view.get("needs")).get("kind")),
        "controller_kind": safe_label(object_value(view.get("controller")).get("kind")),
        "launch_id": safe_label(execution.get("launch_id")),
        "controller_epoch": safe_label(execution.get("controller_epoch")),
        "active_stages": list(
            dict.fromkeys(label for row in attempts if row.get("active") and (label := safe_label(row.get("stage"))))
        )[:8],
        "attempt_count": len(attempts),
    }


def safe_launch_summary(value: dict) -> dict:
    value = object_value(value)
    service = object_value(value.get("service"))
    return {
        "launch_id": safe_label(value.get("launch_id")),
        "phase": safe_label(value.get("phase")),
        "ready": value.get("ready") if type(value.get("ready")) is bool else None,
        "dispatch_count": value.get("dispatch_count") if type(value.get("dispatch_count")) is int else None,
        "service": {
            "checked": service.get("checked") if type(service.get("checked")) is bool else None,
            "present": service.get("present") if type(service.get("present")) is bool else None,
            "active_state": safe_label(service.get("active_state")),
        },
    }


class Progress:
    """At most 64 meaningful allowlisted observations, separate from baseline stdout."""

    def __init__(self, budget: Budget):
        self.budget, self.last, self.count = budget, None, 0
        self.closed = False

    def emit(self, phase: str, *, view=None, operation=None, result=None, terminal=False) -> None:
        item = {"phase": safe_label(phase)}
        if view is not None:
            item.update(safe_view_summary(view))
        if operation is not None:
            item["operation"] = safe_label(operation)
        if result is not None:
            item["result"] = safe_label(result)
        if self.closed or item == self.last or self.count >= (64 if terminal else 63):
            return
        self.last, self.count = item.copy(), self.count + 1
        self.closed = terminal
        item.update(
            elapsed_seconds=round(max(0.0, self.budget.clock() - self.budget.started), 3),
            remaining_seconds=round(self.budget.remaining(), 3),
            budget_seconds=self.budget.seconds,
        )
        print("C4_PROGRESS " + json.dumps(item, separators=(",", ":")), file=sys.stderr, flush=True)


class PublicFailure(RuntimeError):
    def __init__(self, operation, result, *, exception_type=None, returncode=None):
        self.summary = {"operation": operation, "result": result}
        if exception_type:
            self.summary["exception_type"] = exception_type
        if returncode is not None:
            self.summary["returncode"] = returncode
        super().__init__(f"Guest public operation {operation} was {result}")


def run_bounded(argv, budget: Budget, *, env, operation):
    """A single call has neither an independent allowance nor automatic retry."""
    try:
        timeout = budget.timeout()
        reply = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, TimeoutError) as error:
        raise PublicFailure(operation, "timeout", exception_type=type(error).__name__) from None
    except OSError as error:
        raise PublicFailure(operation, "unavailable", exception_type=type(error).__name__) from None
    if reply.returncode:
        raise PublicFailure(operation, "refused", returncode=reply.returncode)
    return reply


def fixture_env() -> dict:
    """Only this disposable fixture's overrides; never serialize inherited environment."""
    return {
        "PATH": str(FIXTURE / "bin") + os.pathsep + os.environ["PATH"],
        "HOME": str(Path.home()),
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "AUTOCODE_HOME": str(FIXTURE / "registry"),
        "AUTOCODE_WORKER_HOME": str(FIXTURE / "launches"),
        "XDG_CONFIG_HOME": str(FIXTURE / "config"),
        "CODEX_HOME": str(FIXTURE / "codex-home"),
        "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


class PublicDriver:
    def __init__(self, budget: Budget, env=None, progress=None):
        self.budget, self.env = budget, fixture_env() if env is None else env
        self.progress = Progress(budget) if progress is None else progress
        self.workspace = FIXTURE / "project"
        self.run_dir = self.workspace / ".autocode/runs/literal-reboot"

    def call(self, args, operation, private_env=None) -> dict:
        try:
            reply = run_bounded(
                [*CORE, *args], self.budget, env={**os.environ, **self.env, **(private_env or {})}, operation=operation
            )
        except PublicFailure as error:
            self.progress.emit("public_cli", operation=operation, result=error.summary["result"])
            raise
        try:
            value = json.loads(reply.stdout)
            if not isinstance(value, dict):
                raise ValueError("not an object")
        except (ValueError, TypeError):
            raise PublicFailure(operation, "invalid_json", exception_type="ValueError") from None
        return value

    def status(self) -> dict:
        result = self.call(["--workspace", str(self.workspace), "--run-dir", str(self.run_dir), "--status"], "status")
        view = result.get("view")
        if not isinstance(view, dict):
            raise PublicFailure("status", "invalid_json")
        self.progress.emit("status", view=view)
        return view

    def inspect(self, receipt) -> dict:
        launch_id = receipt.get("launch_id")
        expected = ["autocode", "worker", "inspect", launch_id]
        if not isinstance(launch_id, str) or receipt.get("inspect_command") != expected:
            raise PublicFailure("inspect", "unsupported_receipt")
        observed = self.call(expected[1:], "inspect")
        if observed.get("launch_id") != launch_id:
            raise PublicFailure("inspect", "wrong_launch")
        self.progress.emit("launch", operation="inspect", result="ready" if observed.get("ready") else "waiting")
        return observed

    def await_ready(self, receipt):
        while True:
            observed = self.inspect(receipt)
            if observed.get("ready"):
                bound = observed.get("binding") or {}
                if bound.get("workspace") != str(self.workspace) or bound.get("run_dir") != str(self.run_dir):
                    raise PublicFailure("inspect", "wrong_run")
                return observed
            self.budget.pause()

    def launch(self, brief, options):
        self.progress.emit("launch", operation="modes", result="started")
        modes = self.call(["modes", "list", "--json"], "modes")
        execution = next(
            (row for row in (modes.get("execution") or {}).get("modes", []) if row.get("id") == "durable_local"), {}
        )
        argv = actions.worker_transport(
            execution, ["--brief", brief, "--in-place", *options], self.workspace, self.run_dir, fresh=True
        )
        self.progress.emit("launch", operation="dispatch", result="started")
        return self.await_ready(self.call(argv, "launch"))

    def dispatch(self, view, command=None):
        if actions.own(view, self.workspace, self.run_dir) != (self.workspace, self.run_dir):
            raise PublicFailure("dispatch", "wrong_run")
        command = command or actions.named(view, self.workspace, self.run_dir)
        argv = actions.worker_transport(view.get("execution") or {}, command.argv, self.workspace, self.run_dir)
        self.progress.emit("dispatch", view=view, operation="dispatch", result="started")
        return self.await_ready(self.call(argv, "dispatch", command.env))

    def wait_stopped(self):
        while True:
            view = self.status()
            controller = object_value(view.get("controller"))
            owner_running = (
                controller.get("kind") == "running"
                and controller.get("checked") is True
                and controller.get("alive") is True
            )
            if not actions.working(view) and not owner_running:
                return view
            self.budget.pause()

    def wait_held(self, gates):
        while True:
            view = self.status()
            if any(ready.exists() for ready, _ in gates.values()) and actions.working(view):
                return view
            if not actions.working(view) and (view.get("needs") or {}).get("kind") != "continue":
                raise PublicFailure("wait_held", "unexpected_boundary")
            self.budget.pause()


def snapshot(budget=None) -> dict:
    budget = Budget(30.0) if budget is None else budget
    saved = handle()
    workspace, run_dir = Path(saved["workspace"]), Path(saved["run_dir"])
    env = {**os.environ, **saved["env"]}
    status = run_bounded(
        [*CORE, "--workspace", str(workspace), "--run-dir", str(run_dir), "--status"],
        budget,
        env=env,
        operation="baseline_status",
    )
    inspected = run_bounded(
        [*CORE, "worker", "inspect", saved["launch_id"]], budget, env=env, operation="baseline_inspect"
    )
    public = json.loads(status.stdout)
    return {
        "boot_id": BOOT.read_text().strip(),
        "status": {key: public.get(key) for key in ("stale", "next_action", "active_stage_finished")},
        "view": public["view"],
        "launch": json.loads(inspected.stdout),
        "files": {name: digest(workspace / name) for name in FILES},
        "provider_calls": digest(FIXTURE / "provider-calls.jsonl"),
    }


def install_provider(provider: Path) -> dict:
    shutil.copy2(ROOT / "tools/live_fixture_provider.py", provider)
    provider.chmod(0o755)
    marker = '    task = data.get("current_task") or {}'
    source = provider.read_text()
    if source.count(marker) != 1:
        raise RuntimeError("Offline provider fixture changed; review its dispatch counter injection")
    calls = FIXTURE / "provider-calls.jsonl"
    injected = (
        f'    with open({str(calls)!r}, "a") as c4_calls:\n'
        '        c4_calls.write(json.dumps({"stage": stage}) + "\\n")\n'
        "        c4_calls.flush()\n"
        "        os.fsync(c4_calls.fileno())\n"
    )
    gates = {}
    for stage in ("sol", "astra_checkpoint"):
        ready, gate = FIXTURE / (stage + "-ready"), FIXTURE / (stage + "-gate")
        os.mkfifo(gate)
        gates[stage] = (ready, gate)
        injected += (
            f"    if stage == {stage!r} and not Path({str(ready)!r}).exists():\n"
            f"        Path({str(ready)!r}).touch()\n"
            f"        with open({str(gate)!r}) as fixture_gate:\n"
            "            fixture_gate.read()\n"
        )
    provider.write_text(source.replace(marker, injected + marker, 1))
    return gates


def prepare(budget: Budget, progress=None) -> dict:
    progress = Progress(budget) if progress is None else progress
    progress.emit("bootstrap", operation="fixture_imports", result="started")
    from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

    budget.timeout()  # The same deadline includes those heavy fixture imports.
    if FIXTURE.exists():
        raise RuntimeError("The disposable fixture already exists; never reuse an interrupted setup")
    workspace = FIXTURE / "project"
    workspace.mkdir(parents=True)
    env = fixture_env()
    environment = {**os.environ, **env}
    progress.emit("setup", operation="git_init", result="started")
    run_bounded(["git", "init", "-q", str(workspace)], budget, env=environment, operation="git_init")
    run_bounded(
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.name=C4 fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "fixture base",
        ],
        budget,
        env=environment,
        operation="git_seed",
    )
    bindir = FIXTURE / "bin"
    bindir.mkdir()
    for name in ("config", "codex-home"):
        (FIXTURE / name).mkdir()
    gates = install_provider(bindir / "codex")
    progress.emit("setup", operation="provider_fixture", result="ready")
    driver = PublicDriver(budget, env=env, progress=progress)
    driver.launch(BRIEF, (*FIXTURE_OPTIONS, "--max-parallel-builders", "1"))
    for _ in range(8):
        view = driver.wait_stopped()
        need = view.get("needs") or {}
        command = actions.named(view, driver.workspace, driver.run_dir)
        if need.get("kind") == "approve_plan":
            driver.dispatch(view, command)
            break
        if need.get("kind") == "answer":
            decisions = actions.decisions(view, command)
            if not decisions or any(
                row.kind != "answer" or not row.key.startswith("requirements-review-") for row in decisions
            ):
                raise PublicFailure("planning", "unexpected_question")
            command = actions.fill(view, command, {row.key: "plan" for row in decisions})
        elif need.get("kind") != "continue":
            raise PublicFailure("planning", "unexpected_boundary")
        driver.dispatch(view, command)
    else:
        raise PublicFailure("planning", "decision_limit")
    view = driver.wait_held(gates)
    saved = {
        "workspace": str(driver.workspace),
        "run_dir": str(driver.run_dir),
        "launch_id": view["execution"]["launch_id"],
        "env": env,
    }
    (FIXTURE / "handle.json").write_text(json.dumps(saved))
    baseline = snapshot(budget)
    assert baseline["launch"]["ready"] is True
    assert any(
        any(value is not None for value in (row.get("tokens") or {}).values())
        for row in completed_usage(baseline["view"]).values()
    ), "The baseline needs known completed-attempt usage"
    assert all((workspace / name).is_file() for name in FILES), "Reboot must happen after Builder edits"
    (FIXTURE / "baseline.json").write_text(json.dumps(baseline, indent=2))
    run_bounded(["sync"], budget, env=environment, operation="fixture_sync")
    progress.emit("baseline", view=baseline["view"], result="ready")
    return baseline


def diagnose(budget: Budget, progress=None) -> dict:
    """No handle, built file or advancing action is required for public diagnosis."""
    progress = Progress(budget) if progress is None else progress
    driver = PublicDriver(budget, progress=progress)
    result = {
        "budget_seconds": budget.seconds,
        "remaining_seconds": budget.remaining(),
        "status": None,
        "launch": None,
        "launches": [],
        "errors": [],
    }
    try:
        view = driver.status()
        result["status"] = safe_view_summary(view)
    except PublicFailure as error:
        result["errors"].append(error.summary)
        view = None
    launch_id = ((view or {}).get("execution") or {}).get("launch_id")
    if budget.remaining() > 0 and isinstance(launch_id, str) and re.fullmatch(r"[0-9a-f]{32}", launch_id):
        try:
            result["launch"] = safe_launch_summary(driver.call(["worker", "inspect", launch_id], "diagnose_inspect"))
        except PublicFailure as error:
            result["errors"].append(error.summary)
    elif budget.remaining() > 0:
        try:
            listing = driver.call(
                ["worker", "launches", "--workspace", str(driver.workspace), "--run-dir", str(driver.run_dir)],
                "diagnose_launches",
            )
            rows = listing.get("launches")
            result["launches"] = [safe_launch_summary(row) for row in rows[:4]] if isinstance(rows, list) else []
        except PublicFailure as error:
            result["errors"].append(error.summary)
    result["errors"] = result["errors"][:4]
    result["remaining_seconds"] = budget.remaining()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "diagnose", "inspect", "reboot"))
    parser.add_argument("--instance", required=True)
    parser.add_argument("--outer-boot", required=True)
    parser.add_argument("--budget-seconds", type=float)
    args = parser.parse_args()
    if args.phase in ("prepare", "diagnose") and args.budget_seconds is None:
        parser.error("--budget-seconds is required for prepare and diagnose")
    budget = Budget(args.budget_seconds) if args.phase in ("prepare", "diagnose") else None
    progress = Progress(budget) if budget is not None else None
    if progress is not None:
        progress.emit("bootstrap", result="started")
    try:
        require_guest(args.instance, args.outer_boot)
        if args.phase == "prepare":
            result = prepare(budget, progress)
        elif args.phase == "diagnose":
            result = diagnose(budget, progress)
        elif args.phase == "inspect":
            result = snapshot()
        else:
            before = json.loads((FIXTURE / "baseline.json").read_text())
            current = snapshot()
            if current["boot_id"] != before["boot_id"] or not actions.working(current["view"]):
                raise RuntimeError("The retained baseline is no longer the active pre-reboot fixture")
            os.sync()
            os.execvp("sudo", ["sudo", "/usr/bin/systemctl", "reboot"])
            raise AssertionError("unreachable")
    except BaseException as error:
        if progress is not None:
            result_label = error.summary.get("result") if isinstance(error, PublicFailure) else type(error).__name__
            progress.emit("failed", operation=args.phase, result=safe_label(result_label) or "error", terminal=True)
        raise
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
