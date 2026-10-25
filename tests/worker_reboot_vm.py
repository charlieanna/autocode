"""External C4 coordinator: CI-only, one owned QEMU guest, no runner reboot.

The reviewed cloud image must already exist locally with its approved SHA256.
This harness never downloads an image, administers an existing host or accepts
an arbitrary SSH target. Its workflow is manual-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

BOOT = Path("/proc/sys/kernel/random/boot_id")
GUEST = "/opt/autocode"
USER = "autocode"
ACCELERATOR = "tcg"
COORDINATOR_SECONDS = 22 * 60
RESERVES = {"reboot_request": 45, "reboot": 300, "inspection": 135, "diagnostics": 45, "cleanup": 15}
TRANSPORT_SECONDS = 15
TRACE_FIELDS = {
    "phase",
    "elapsed_seconds",
    "remaining_seconds",
    "budget_seconds",
    "status",
    "next_stage",
    "needs_kind",
    "controller_kind",
    "launch_id",
    "controller_epoch",
    "active_stages",
    "attempt_count",
    "operation",
    "result",
}
IMAGE_URL = "https://cloud-images.ubuntu.com/releases/noble/release-20261017/ubuntu-24.04-server-cloudimg-amd64.img"
IMAGE_SHA256 = "04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94"


def preparation_allowance(deadline: float, now: float) -> dict:
    remaining = max(0.0, deadline - now)
    available = max(0.0, remaining - sum(RESERVES.values()))
    guest = max(0.0, available - TRANSPORT_SECONDS)
    return {
        "remaining_seconds": remaining,
        "reserves": dict(RESERVES),
        "transport_seconds": TRANSPORT_SECONDS,
        "guest_seconds": guest,
        "ssh_seconds": guest + TRANSPORT_SECONDS if guest else 0.0,
    }


def remaining_timeout(deadline: float, maximum: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Qualification deadline exhausted; no further command is authorized")
    return min(maximum, remaining)


def trace_records(*streams) -> list[dict]:
    """Keep only the reviewed fixture's bounded allowlisted progress, never raw stderr."""
    records = []
    for stream in streams:
        if isinstance(stream, bytes):
            stream = stream.decode(errors="replace")
        for line in str(stream or "")[-65536:].splitlines():
            if not line.startswith("C4_PROGRESS "):
                continue
            try:
                row = json.loads(line.removeprefix("C4_PROGRESS "))
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            clean = {}
            for key, value in row.items():
                if key not in TRACE_FIELDS:
                    continue
                if isinstance(value, str):
                    clean[key] = value[:256]
                elif value is None or type(value) in (int, float, bool):
                    clean[key] = value
                elif isinstance(value, list):
                    clean[key] = [item[:128] for item in value[:8] if isinstance(item, str)]
            if clean:
                records.append(clean)
    return records[-64:]


def diagnostic_fields(value, *, depth=0):
    """Retain only fixed public observation fields, including within nested summaries."""
    allowed = TRACE_FIELDS | {
        "budget_seconds",
        "remaining_seconds",
        "done",
        "boot_matches",
        "runtime_sha256",
        "status",
        "launch",
        "launches",
        "errors",
        "launch_id",
        "phase",
        "ready",
        "dispatch_count",
        "service",
        "checked",
        "present",
        "active_state",
        "operation",
        "result",
        "exception_type",
        "returncode",
    }
    if depth > 4:
        return None
    if isinstance(value, dict):
        return {key: diagnostic_fields(item, depth=depth + 1) for key, item in value.items() if key in allowed}
    if isinstance(value, list):
        return [diagnostic_fields(item, depth=depth + 1) for item in value[:4]]
    if isinstance(value, str):
        return value[:256]
    return value if value is None or type(value) in (bool, int, float) else None


def public_diagnostics(machine, deadline: float, evidence: Path) -> None:
    """One read-only window, reserving cleanup; its failure cannot authorize another action."""
    available = max(0.0, min(RESERVES["diagnostics"], deadline - time.monotonic() - RESERVES["cleanup"]))
    report = {"assigned_seconds": available, "guest_seconds": max(0.0, available - TRANSPORT_SECONDS)}
    if report["guest_seconds"] <= 0:
        report["result"] = "unavailable: no diagnostic budget remains after cleanup/transport reservation"
    else:
        try:
            command = machine.phase("diagnose", budget_seconds=report["guest_seconds"])
            reply = machine.ssh(
                ["sh", "-ec", "cd /opt/autocode && exec " + shlex.join(command)], timeout=available, check=False
            )
            report["returncode"] = reply.returncode
            report["trace"] = trace_records(reply.stderr)
            if reply.returncode == 0:
                payload = json.loads(reply.stdout)
                # The guest emits only this fixed diagnostic schema, excluding task text/authority.
                report["public"] = diagnostic_fields(
                    {
                        key: payload[key]
                        for key in ("budget_seconds", "remaining_seconds", "status", "launch", "launches", "errors")
                        if key in payload
                    }
                )
            else:
                report["result"] = "unavailable: diagnostic command refused or failed"
        except subprocess.TimeoutExpired as error:
            report.update(
                result="unavailable: diagnostic SSH deadline expired", trace=trace_records(error.stdout, error.stderr)
            )
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            report.update(result="unavailable: diagnostic observation failed", exception_type=type(error).__name__)
    (evidence / "prepare-diagnostics.json").write_text(json.dumps(report, indent=2))


def admission_metadata() -> dict:
    """Read only named host facts; KVM is diagnostic because TCG is explicit."""
    kvm = {"exists": False, "readable": os.access("/dev/kvm", os.R_OK), "writable": os.access("/dev/kvm", os.W_OK)}
    try:
        device = Path("/dev/kvm").stat()
        kvm.update(
            exists=True,
            character_device=stat.S_ISCHR(device.st_mode),
            mode=oct(stat.S_IMODE(device.st_mode)),
            uid=device.st_uid,
            gid=device.st_gid,
        )
    except OSError as error:
        kvm["stat_error"] = {"type": type(error).__name__, "errno": error.errno}
    data = {
        "accelerator": ACCELERATOR,
        "github_actions": os.environ.get("GITHUB_ACTIONS"),
        "runner_environment": os.environ.get("RUNNER_ENVIRONMENT"),
        "runner_os": os.environ.get("RUNNER_OS"),
        "runner_arch": os.environ.get("RUNNER_ARCH"),
        "platform": sys.platform,
        "machine": platform.machine(),
        "uid": os.getuid() if hasattr(os, "getuid") else None,
        "has_runner_temp": bool(os.environ.get("RUNNER_TEMP")),
        "has_workspace": bool(os.environ.get("GITHUB_WORKSPACE")),
        "kvm": kvm,
    }
    checks = (
        (data["platform"] == "linux", "platform must be linux"),
        (data["machine"] == "x86_64", "machine must be x86_64"),
        (data["github_actions"] == "true", "GITHUB_ACTIONS must be true"),
        (data["runner_os"] == "Linux", "RUNNER_OS must be Linux"),
        (data["runner_environment"] == "github-hosted", "RUNNER_ENVIRONMENT must be github-hosted"),
        (data["has_runner_temp"], "RUNNER_TEMP is missing"),
        (data["has_workspace"], "GITHUB_WORKSPACE is missing"),
        (type(data["uid"]) is int and data["uid"] > 0, "uid must be a nonroot Linux user"),
    )
    data["errors"] = [error for accepted, error in checks if not accepted]
    return data


def require_ci(metadata=None) -> dict:
    data = admission_metadata() if metadata is None else metadata
    if data["errors"]:
        raise RuntimeError(
            "Only a disposable GitHub-hosted x86_64 Linux runner may start this guest: " + "; ".join(data["errors"])
        )
    return data


def qemu_command(qemu, disk, seed, serial, port, instance) -> list[str]:
    return [
        str(qemu),
        "-machine",
        "q35",
        "-accel",
        "tcg,thread=multi",
        "-cpu",
        "max",
        "-smp",
        "2",
        "-m",
        "2048",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        "file:" + str(serial),
        "-smbios",
        "type=1,uuid=" + instance,
        "-drive",
        f"file={disk},if=virtio,format=qcow2",
        "-drive",
        f"file={seed},if=virtio,format=raw,readonly=on",
        "-netdev",
        f"user,id=c4,hostfwd=tcp:127.0.0.1:{port}-:22",
        "-device",
        "virtio-net-pci,netdev=c4",
    ]


def cloud_config(instance, client_public, host_private, host_public) -> dict:
    # This data executes only inside the new disk booted by qemu_command.
    return {
        "users": [
            {
                "name": USER,
                "shell": "/bin/bash",
                "sudo": ["ALL=(ALL) NOPASSWD:ALL"],
                "ssh_authorized_keys": [client_public],
            }
        ],
        "ssh_pwauth": False,
        "disable_root": True,
        "ssh_keys": {"ed25519_private": host_private, "ed25519_public": host_public},
        "package_update": True,
        "packages": ["python3-venv", "git", "bubblewrap", "libseccomp2", "openssh-server"],
        "write_files": [
            {
                "path": "/etc/autocode-reboot-qualification.json",
                "permissions": "0444",
                "content": json.dumps({"instance": instance, "purpose": "autocode-c4-disposable-guest"}),
            }
        ],
        "runcmd": [
            ["install", "-m", "0444", "/sys/class/dmi/id/product_uuid", "/etc/autocode-reboot-product-uuid"],
            [
                "sh",
                "-ec",
                "if test -e /proc/sys/kernel/apparmor_restrict_unprivileged_userns; "
                "then sysctl -w kernel.apparmor_restrict_unprivileged_userns=0; fi",
            ],
            ["loginctl", "enable-linger", USER],
            ["sh", "-ec", "systemctl start user@$(id -u autocode).service"],
        ],
    }


def source_archive(source: Path, path: Path) -> None:
    tracked = (
        subprocess.run(
            ["git", "-C", str(source), "ls-files", "-z", "--cached"],
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .split("\0")
    )
    with tarfile.open(path, "w") as archive:
        for name in sorted(set(filter(None, tracked))):
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Source archive member escapes its checkout")
            if relative.parts[0] in (".git", ".autocode", ".scenario-runs", ".venv", ".aws", ".codex"):
                continue
            item = source / relative
            if item.is_symlink():
                raise ValueError(f"Review source symlink before guest copy: {name}")
            if item.is_file():
                archive.add(item, arcname=name, recursive=False)


class VM:
    def __init__(self, folder: Path, port: int, instance: str, outer_boot: str):
        self.folder, self.port, self.instance, self.outer_boot = folder, port, instance, outer_boot
        self.child = None
        self.deadline = None

    @property
    def ssh_options(self):
        return [
            "-F",
            "/dev/null",
            "-i",
            str(self.folder / "client"),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=" + str(self.folder / "known_hosts"),
            "-o",
            "ConnectTimeout=3",
        ]

    def ssh(self, args, *, timeout=15, check=True):
        if self.deadline is not None:
            timeout = remaining_timeout(self.deadline, timeout)
        return subprocess.run(
            ["ssh", *self.ssh_options, "-p", str(self.port), f"{USER}@127.0.0.1", shlex.join(args)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )

    def wait(self, args, *, seconds, changed_boot=None):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.child.poll() is not None:
                raise RuntimeError("The owned QEMU process exited; this is not a guest-reboot pass")
            try:
                timeout = remaining_timeout(deadline, 15)
                reply = self.ssh(args, timeout=timeout, check=False)
                if reply.returncode == 0 and (changed_boot is None or reply.stdout.strip() != changed_boot):
                    return reply.stdout.strip()
            except subprocess.TimeoutExpired:
                pass
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        raise RuntimeError(f"Guest operation exceeded {seconds}s: {args}")

    def phase(self, phase, *, budget_seconds=None):
        command = [
            GUEST + "/.venv/bin/python",
            "-m",
            "tests.worker_reboot_guest",
            phase,
            "--instance",
            self.instance,
            "--outer-boot",
            self.outer_boot,
        ]
        if budget_seconds is not None:
            command += ["--budget-seconds", str(budget_seconds)]
        return command

    def stop(self):
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)


def main() -> None:
    total_started = time.monotonic()
    deadline = total_started + COORDINATOR_SECONDS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--admission-only", action="store_true", help="Print named read-only admission facts; never set up a guest"
    )
    parser.add_argument("--image", type=Path, help="Already staged, reviewed Ubuntu 24.04 amd64 qcow2")
    args = parser.parse_args()
    admission = admission_metadata()
    if args.admission_only:
        print(json.dumps(admission, indent=2), flush=True)
        require_ci(admission)
        return
    require_ci(admission)
    if args.image is None:
        parser.error("--image is required unless --admission-only is used")
    # The early admission command needs only the standard library, before dependencies are installed.
    from tests.worker_reboot_guest import check_reboot

    source = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    image = args.image.resolve(strict=True)
    with image.open("rb") as stream:
        actual_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual_digest != IMAGE_SHA256:
        raise ValueError("Reviewed cloud-image SHA256 does not match")
    evidence = source / ".scenario-runs/worker-reboot"
    evidence.mkdir(parents=True, exist_ok=False)
    (evidence / "admission.json").write_text(json.dumps(admission, indent=2))
    folder = Path(tempfile.mkdtemp(prefix="autocode-c4-", dir=os.environ["RUNNER_TEMP"]))
    os.chmod(folder, 0o700)
    instance, outer_boot = str(uuid.uuid4()), BOOT.read_text().strip()
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    machine = VM(folder, port, instance, outer_boot)
    machine.deadline = deadline - RESERVES["cleanup"]
    commands = []
    phase_seconds = {}
    phase_name, phase_started = "coordinator_setup", total_started
    outcome = "failed"
    result = None
    prepare_trace = []

    def enter_phase(name):
        nonlocal phase_name, phase_started
        now = time.monotonic()
        phase_seconds[phase_name] = round(now - phase_started, 6)
        phase_name, phase_started = name, now

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)  # Let timeout(1) run the exact-child cleanup.

    def local(argv, *, timeout=30):
        commands.append(argv)
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=True,
            timeout=remaining_timeout(deadline - RESERVES["cleanup"], timeout),
        )

    try:
        local(["git", "-C", str(source), "diff", "--quiet", "HEAD", "--"])
        checkout_sha = local(["git", "-C", str(source), "rev-parse", "HEAD"]).stdout.strip()
        tools_tree = local(["git", "-C", str(source), "rev-parse", "HEAD:tools"]).stdout.strip()
        (evidence / "source.json").write_text(json.dumps({"checkout_sha": checkout_sha, "git_tools_tree": tools_tree}))
        for key in ("host", "client"):
            local(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(folder / key)])
        public = (folder / "host.pub").read_text().strip()
        (folder / "known_hosts").write_text(f"[127.0.0.1]:{port} {public}\n")
        config = cloud_config(instance, (folder / "client.pub").read_text(), (folder / "host").read_text(), public)
        (folder / "user-data").write_text("#cloud-config\n" + json.dumps(config))
        (folder / "meta-data").write_text(json.dumps({"instance-id": instance, "local-hostname": "autocode-c4"}))
        local(["cloud-localds", str(folder / "seed.img"), str(folder / "user-data"), str(folder / "meta-data")])
        local(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(image), str(folder / "disk.qcow2"), "12G"])
        command = qemu_command(
            "qemu-system-x86_64", folder / "disk.qcow2", folder / "seed.img", evidence / "serial.log", port, instance
        )
        commands.append(command)
        enter_phase("guest_boot")
        with (evidence / "qemu.log").open("wb") as output:
            machine.child = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
            )
        machine.wait(["true"], seconds=remaining_timeout(deadline - RESERVES["cleanup"], 300))
        enter_phase("guest_provision")
        # Cloud-init finishes before source installation or any provider launch.
        machine.ssh(["cloud-init", "status", "--wait"], timeout=600)
        checked = machine.ssh(["cat", "/etc/autocode-reboot-product-uuid"]).stdout.strip().lower()
        if checked != instance:
            raise RuntimeError("SSH target is not the owned guest instance")
        guest_boot = machine.ssh(["cat", str(BOOT)]).stdout.strip()
        if guest_boot == outer_boot:
            raise RuntimeError("Guest shares the runner boot ID: container/service restart is insufficient")
        enter_phase("source_install")
        archive = folder / "source.tar"
        source_archive(source, archive)
        local(
            ["scp", *machine.ssh_options, "-P", str(port), str(archive), f"{USER}@127.0.0.1:/home/{USER}/source.tar"],
            timeout=120,
        )
        machine.ssh(["sudo", "mkdir", GUEST])
        machine.ssh(["sudo", "tar", "-xf", "/home/autocode/source.tar", "-C", GUEST], timeout=120)
        machine.ssh(["sudo", "chown", "-R", USER + ":" + USER, GUEST], timeout=120)
        machine.ssh(["python3", "-m", "venv", GUEST + "/.venv"], timeout=60)
        machine.ssh([GUEST + "/.venv/bin/pip", "install", "psutil==7.1.0"], timeout=180)
        machine.ssh([GUEST + "/.venv/bin/pip", "install", "--no-deps", "-e", GUEST], timeout=180)

        # Python -m resolves the copied test package from this explicit guest-only working directory.
        def in_source(argv):
            return ["sh", "-ec", "cd /opt/autocode && exec " + shlex.join(argv)]

        enter_phase("fixture_prepare")
        assigned = preparation_allowance(deadline, time.monotonic())
        assigned.update(
            coordinator_limit_seconds=COORDINATOR_SECONDS,
            elapsed_before_prepare_seconds=COORDINATOR_SECONDS - assigned["remaining_seconds"],
        )
        (evidence / "preparation-budget.json").write_text(json.dumps(assigned, indent=2))
        if assigned["guest_seconds"] <= 0:
            raise TimeoutError("No preparation budget remains after reserving reboot/inspection/diagnostics/cleanup")
        prepared = machine.ssh(
            in_source(machine.phase("prepare", budget_seconds=assigned["guest_seconds"])),
            timeout=assigned["ssh_seconds"],
        )
        prepare_trace = trace_records(prepared.stderr)
        (evidence / "prepare-progress.json").write_text(json.dumps(prepare_trace, indent=2))
        before = prepared.stdout
        (evidence / "before.json").write_text(before)
        baseline = json.loads(before)  # An invalid/missing baseline never grants a reboot.
        if baseline["boot_id"] != guest_boot or baseline["launch"]["ready"] is not True:
            raise RuntimeError("Baseline is not the admitted launch on this guest boot")
        enter_phase("guest_reboot_request")
        request_deadline = min(
            time.monotonic() + RESERVES["reboot_request"],
            deadline - RESERVES["reboot"] - RESERVES["inspection"] - RESERVES["diagnostics"] - RESERVES["cleanup"],
        )
        pid = machine.child.pid
        try:
            reboot = machine.ssh(
                in_source(machine.phase("reboot")),
                timeout=remaining_timeout(request_deadline, RESERVES["reboot_request"]),
                check=False,
            )
            receipt = {"returncode": reboot.returncode, "stdout": reboot.stdout, "stderr": reboot.stderr}
        except subprocess.TimeoutExpired:
            # An accepted reboot may close SSH without a reply. Inspect this guest; never retry reboot.
            receipt = {"outcome": "SSH receipt timed out; guest boot inspection required"}
        (evidence / "reboot-receipt.json").write_text(json.dumps(receipt))
        enter_phase("guest_reconnect")
        reboot_deadline = min(
            time.monotonic() + RESERVES["reboot"],
            deadline - RESERVES["inspection"] - RESERVES["diagnostics"] - RESERVES["cleanup"],
        )
        machine.wait(
            ["cat", str(BOOT)], seconds=remaining_timeout(reboot_deadline, RESERVES["reboot"]), changed_boot=guest_boot
        )
        if machine.child.pid != pid or BOOT.read_text().strip() != outer_boot:
            raise RuntimeError("Outer host or QEMU incarnation changed")
        enter_phase("read_only_inspection")
        inspection_deadline = min(
            time.monotonic() + RESERVES["inspection"], deadline - RESERVES["diagnostics"] - RESERVES["cleanup"]
        )
        for sample in range(3):
            after = machine.ssh(
                in_source(machine.phase("inspect")), timeout=remaining_timeout(inspection_deadline, 45)
            ).stdout
            (evidence / f"after-{sample}.json").write_text(after)
            check_reboot(baseline, json.loads(after))
        with archive.open("rb") as stream:
            source_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        enter_phase("cleanup")
        result = {
            "result": "PASS",
            "accelerator": ACCELERATOR,
            "phase_seconds": dict(phase_seconds),
            "instance": instance,
            "qemu_pid": pid,
            "outer_boot_id": outer_boot,
            "image_url": IMAGE_URL,
            "image_sha256": IMAGE_SHA256,
            "source_archive_sha256": source_digest,
            "checkout_sha": checkout_sha,
            "git_tools_tree": tools_tree,
            "runtime": baseline["view"]["execution"]["runtime"],
            "limits": {"vcpus": 2, "memory_mib": 2048, "disk_gib": 12},
        }
        outcome = "passed"
    except BaseException as error:
        failure = {"type": type(error).__name__, "reason": str(error), "accelerator": ACCELERATOR, "phase": phase_name}
        if phase_name == "fixture_prepare":
            trace = (prepare_trace + trace_records(getattr(error, "stdout", None), getattr(error, "stderr", None)))[
                -64:
            ]
            (evidence / "prepare-progress.json").write_text(json.dumps(trace, indent=2))
            failure["trace"] = trace
        elif isinstance(error, subprocess.CalledProcessError):
            failure.update(stdout=error.stdout, stderr=error.stderr)
        (evidence / "failure.json").write_text(json.dumps(failure, indent=2))
        if phase_name == "fixture_prepare" and machine.child is not None and machine.child.poll() is None:
            enter_phase("prepare_diagnostics")
            public_diagnostics(machine, deadline, evidence)
        raise
    finally:
        if phase_name != "cleanup":
            enter_phase("cleanup")
        try:
            machine.stop()  # Exact child only; never pkill, shutdown or reboot on the runner.
            if result is not None:
                (evidence / "result.json").write_text(json.dumps(result, indent=2))
        except BaseException as error:
            outcome = "failed"
            (evidence / "failure.json").write_text(
                json.dumps(
                    {
                        "type": type(error).__name__,
                        "reason": str(error),
                        "accelerator": ACCELERATOR,
                        "phase": "cleanup",
                    },
                    indent=2,
                )
            )
            raise
        finally:
            (evidence / "commands.json").write_text(json.dumps(commands, indent=2))
            now = time.monotonic()
            phase_seconds[phase_name] = round(now - phase_started, 6)
            (evidence / "phase-durations.json").write_text(
                json.dumps(
                    {
                        "accelerator": ACCELERATOR,
                        "outcome": outcome,
                        "last_phase": phase_name,
                        "phase_seconds": phase_seconds,
                        "total_seconds": round(now - total_started, 6),
                    },
                    indent=2,
                )
            )
        # Private seed/SSH material and disk remain under RUNNER_TEMP for runner disposal, never uploaded.


if __name__ == "__main__":
    main()
