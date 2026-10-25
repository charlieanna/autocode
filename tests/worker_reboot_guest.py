"""Split C4 fixture phases for one marked disposable guest; never an ordinary test.

Preparation may dispatch only the offline fixture. Inspection performs public
reads only. The reboot phase refuses an unmarked or differently identified host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tests.test_worker_host import CORE, PersistentHost, actions, services, taskrun

ROOT = Path(__file__).resolve().parents[1]
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


def snapshot() -> dict:
    saved = handle()
    run = taskrun.TaskRun(Path(saved["workspace"]), Path(saved["run_dir"]), command=tuple(CORE), env=saved["env"])
    status = subprocess.run(
        [*CORE, "--workspace", str(run.workspace), "--run-dir", str(run.run_dir), "--status"],
        env=saved["env"],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    inspected = subprocess.run(
        [*CORE, "worker", "inspect", saved["launch_id"]],
        env=saved["env"],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    public = json.loads(status.stdout)
    return {
        "boot_id": BOOT.read_text().strip(),
        "status": {key: public.get(key) for key in ("stale", "next_action", "active_stage_finished")},
        "view": public["view"],
        "launch": json.loads(inspected.stdout),
        "files": {name: digest(run.workspace / name) for name in FILES},
        "provider_calls": digest(FIXTURE / "provider-calls.jsonl"),
    }


def prepare() -> dict:
    if FIXTURE.exists():
        raise RuntimeError("The disposable fixture already exists; never reuse an interrupted setup")
    workspace = FIXTURE / "project"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(
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
        check=True,
    )
    bindir = FIXTURE / "bin"
    bindir.mkdir()
    provider = bindir / "codex"
    shutil.copy2(ROOT / "tools/live_fixture_provider.py", provider)
    provider.chmod(0o755)
    for name in ("config", "codex-home"):
        (FIXTURE / name).mkdir()
    env = {
        "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
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
    if not services.UserServices(env=env).capability()["available"]:
        raise RuntimeError("Guest lacks admitted systemd user services; no provider proof is available")
    # Reuse the host fixture's public TaskRun driver, not its test methods or temporary-directory setup.
    fixture = PersistentHost("runTest")
    fixture.workspace, fixture.env = workspace, env
    fixture.receipts, fixture.gates, fixture.evidence = [], {}, []
    marker = '    task = data.get("current_task") or {}'
    source = provider.read_text()
    if source.count(marker) != 1:
        raise RuntimeError("Offline provider fixture changed; review its dispatch counter injection")
    calls = FIXTURE / "provider-calls.jsonl"
    counter = (
        f'    with open({str(calls)!r}, "a") as c4_calls:\n'
        '        c4_calls.write(json.dumps({"stage": stage}) + "\\n")\n'
        "        c4_calls.flush()\n"
        "        os.fsync(c4_calls.fileno())\n"
    )
    # Inject before adding the gates so every model dispatch is counted before it can block.
    provider.write_text(source.replace(marker, counter + marker, 1))
    for stage in ("sol", "astra_checkpoint"):
        fixture.hold(stage)
    fixture.start("literal-reboot")
    fixture.dispatch(fixture.plan())
    view = fixture.wait(
        lambda value: any(ready.exists() for ready, _ in fixture.gates.values()) and actions.working(value),
        seconds=180,
    )
    saved = {
        "workspace": str(fixture.run.workspace),
        "run_dir": str(fixture.run.run_dir),
        "launch_id": view["execution"]["launch_id"],
        "env": env,
    }
    (FIXTURE / "handle.json").write_text(json.dumps(saved))
    baseline = snapshot()
    assert baseline["launch"]["ready"] is True
    assert any(
        any(value is not None for value in (row.get("tokens") or {}).values())
        for row in completed_usage(baseline["view"]).values()
    ), "The baseline must include known recorded usage from a completed fixture attempt"
    assert all((workspace / name).is_file() for name in FILES), "Reboot must happen after Builder edits"
    (FIXTURE / "baseline.json").write_text(json.dumps(baseline, indent=2))
    os.sync()
    return baseline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "inspect", "reboot"))
    parser.add_argument("--instance", required=True)
    parser.add_argument("--outer-boot", required=True)
    args = parser.parse_args()
    require_guest(args.instance, args.outer_boot)
    if args.phase == "prepare":
        result = prepare()
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
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
