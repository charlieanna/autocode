"""External C4 coordinator: CI-only, one owned QEMU guest, no runner reboot.

The reviewed cloud image must already exist locally with its approved SHA256.
This harness never downloads an image, administers an existing host or accepts
an arbitrary SSH target. No workflow currently invokes it.
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
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

from tests.worker_reboot_guest import check_reboot

BOOT = Path("/proc/sys/kernel/random/boot_id")
GUEST = "/opt/autocode"
USER = "autocode"
IMAGE_URL = "https://cloud-images.ubuntu.com/releases/noble/release-20261017/ubuntu-24.04-server-cloudimg-amd64.img"
IMAGE_SHA256 = "04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94"


def require_ci() -> None:
    if (
        sys.platform != "linux"
        or platform.machine() != "x86_64"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or not os.environ.get("RUNNER_TEMP")
        or not os.environ.get("GITHUB_WORKSPACE")
        or os.getuid() == 0
    ):
        raise RuntimeError("Only a disposable GitHub-hosted x86_64 Linux runner may start this guest")
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise RuntimeError("KVM is required; do not silently replace this host qualification with emulation")


def qemu_command(qemu, disk, seed, serial, port, instance) -> list[str]:
    return [
        str(qemu),
        "-machine",
        "q35,accel=kvm",
        "-cpu",
        "host",
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
                reply = self.ssh(args, check=False)
                if reply.returncode == 0 and (changed_boot is None or reply.stdout.strip() != changed_boot):
                    return reply.stdout.strip()
            except subprocess.TimeoutExpired:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"Guest operation exceeded {seconds}s: {args}")

    def phase(self, phase):
        return [
            GUEST + "/.venv/bin/python",
            "-m",
            "tests.worker_reboot_guest",
            phase,
            "--instance",
            self.instance,
            "--outer-boot",
            self.outer_boot,
        ]

    def stop(self):
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True, help="Already staged, reviewed Ubuntu 24.04 amd64 qcow2")
    args = parser.parse_args()
    require_ci()
    source = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    image = args.image.resolve(strict=True)
    with image.open("rb") as stream:
        actual_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual_digest != IMAGE_SHA256:
        raise ValueError("Reviewed cloud-image SHA256 does not match")
    evidence = source / ".scenario-runs/worker-reboot"
    evidence.mkdir(parents=True, exist_ok=False)
    folder = Path(tempfile.mkdtemp(prefix="autocode-c4-", dir=os.environ["RUNNER_TEMP"]))
    os.chmod(folder, 0o700)
    instance, outer_boot = str(uuid.uuid4()), BOOT.read_text().strip()
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    machine = VM(folder, port, instance, outer_boot)
    commands = []

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)  # Let timeout(1) run the exact-child cleanup.

    def local(argv, *, timeout=30):
        commands.append(argv)
        return subprocess.run(argv, capture_output=True, text=True, check=True, timeout=timeout)

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
        with (evidence / "qemu.log").open("wb") as output:
            machine.child = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
            )
        machine.wait(["true"], seconds=180)
        # Cloud-init finishes before source installation or any provider launch.
        machine.ssh(["cloud-init", "status", "--wait"], timeout=600)
        checked = machine.ssh(["cat", "/etc/autocode-reboot-product-uuid"]).stdout.strip().lower()
        if checked != instance:
            raise RuntimeError("SSH target is not the owned guest instance")
        guest_boot = machine.ssh(["cat", str(BOOT)]).stdout.strip()
        if guest_boot == outer_boot:
            raise RuntimeError("Guest shares the runner boot ID: container/service restart is insufficient")
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

        before = machine.ssh(in_source(machine.phase("prepare")), timeout=180).stdout
        (evidence / "before.json").write_text(before)
        baseline = json.loads(before)  # An invalid/missing baseline never grants a reboot.
        if baseline["boot_id"] != guest_boot or baseline["launch"]["ready"] is not True:
            raise RuntimeError("Baseline is not the admitted launch on this guest boot")
        pid = machine.child.pid
        try:
            reboot = machine.ssh(in_source(machine.phase("reboot")), timeout=15, check=False)
            receipt = {"returncode": reboot.returncode, "stdout": reboot.stdout, "stderr": reboot.stderr}
        except subprocess.TimeoutExpired:
            # An accepted reboot may close SSH without a reply. Inspect this guest; never retry reboot.
            receipt = {"outcome": "SSH receipt timed out; guest boot inspection required"}
        (evidence / "reboot-receipt.json").write_text(json.dumps(receipt))
        machine.wait(["cat", str(BOOT)], seconds=180, changed_boot=guest_boot)
        if machine.child.pid != pid or BOOT.read_text().strip() != outer_boot:
            raise RuntimeError("Outer host or QEMU incarnation changed")
        for sample in range(3):
            after = machine.ssh(in_source(machine.phase("inspect")), timeout=45).stdout
            (evidence / f"after-{sample}.json").write_text(after)
            check_reboot(baseline, json.loads(after))
        with archive.open("rb") as stream:
            source_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        (evidence / "result.json").write_text(
            json.dumps(
                {
                    "result": "PASS",
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
                },
                indent=2,
            )
        )
    except BaseException as error:
        failure = {"type": type(error).__name__, "reason": str(error)}
        if isinstance(error, subprocess.CalledProcessError):
            failure.update(stdout=error.stdout, stderr=error.stderr)
        (evidence / "failure.json").write_text(json.dumps(failure, indent=2))
        raise
    finally:
        machine.stop()  # Exact child only; never pkill, shutdown or reboot on the runner.
        (evidence / "commands.json").write_text(json.dumps(commands, indent=2))
        # Private seed/SSH material and disk remain under RUNNER_TEMP for runner disposal, never uploaded.


if __name__ == "__main__":
    main()
