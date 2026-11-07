#!/usr/bin/env python3
"""Run one focused fake-provider check in the supported Linux container."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

IMAGE = "autocode-linux-fake:ubuntu24.04-py311-node22-go1.25.5"
CONTAINER_ROOT = "/workspace"
DOCKERFILE = "linux-fake.Dockerfile"
UNITTEST_SELECTOR = re.compile(r"tests\.test_[A-Za-z0-9_]+(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
SCENARIO_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
LIVE_FLAGS = ("--profile", "--provider", "--i-authorize-live-model-spend", "--run-live")

PREFLIGHT = """\
import os
import sys
sys.path.insert(0, "/workspace/tools")
import autocode_session_boundary as boundary
import autocode_tool_containment as containment

problem = containment.sandbox_problem()
if problem:
    raise SystemExit(problem)
listener = boundary.install()
os.close(listener)
print("Linux session and tool-containment boundaries are available")
"""


@dataclass(frozen=True)
class Selection:
    mode: str
    value: str


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run exactly one fake provider-backed unittest selector or named scenario in Linux Docker.",
        allow_abbrev=False,
    )
    selected = result.add_mutually_exclusive_group(required=True)
    selected.add_argument("--unittest", metavar="SELECTOR", help="one tests.test_* module, class, or test selector")
    selected.add_argument("--scenario", metavar="ID", help="one scenario catalog ID; --fake is automatic")
    return result


def parse_selection(argv: list[str], *, repo: Path) -> Selection:
    argument_parser = parser()
    for token in argv:
        if any(token == flag or token.startswith(flag + "=") for flag in LIVE_FLAGS):
            argument_parser.error("live-provider and model-spend flags are not accepted by this fake-only tool")
        if token == "--fake":
            argument_parser.error("--fake is automatic for --scenario and is not accepted separately")
    args = argument_parser.parse_args(argv)
    if args.unittest is not None:
        if not UNITTEST_SELECTOR.fullmatch(args.unittest):
            argument_parser.error(
                "--unittest must be one explicit tests.test_* module, class, or test; discovery and wildcards are refused"
            )
        return Selection("unittest", args.unittest)

    if not SCENARIO_ID.fullmatch(args.scenario) or args.scenario == "all":
        argument_parser.error("--scenario must be one explicit lowercase catalog ID; lists and wildcards are refused")
    definition = repo / "scenarios" / "catalog" / args.scenario / "scenario.toml"
    if not definition.is_file():
        argument_parser.error(f"unknown scenario ID: {args.scenario}")
    return Selection("scenario", args.scenario)


def selected_command(selection: Selection) -> list[str]:
    if selection.mode == "unittest":
        return ["/opt/autocode-venv/bin/python", "-m", "unittest", selection.value]
    if selection.mode == "scenario":
        return [
            "/opt/autocode-venv/bin/python",
            "scenarios/run.py",
            "run",
            selection.value,
            "--fake",
        ]
    raise ValueError(f"unknown selector mode: {selection.mode}")


def docker_build_command(repo: Path) -> list[str]:
    scripts = repo / "scripts"
    return [
        "docker",
        "build",
        "--pull",
        "--file",
        str(scripts / DOCKERFILE),
        "--tag",
        IMAGE,
        str(scripts),
    ]


def host_identity() -> tuple[int, int]:
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        uid, gid = os.getuid(), os.getgid()
        if uid:
            return uid, gid
    return 1000, 1000


def docker_run_prefix(repo: Path) -> list[str]:
    source = str(repo.resolve())
    if "," in source:
        raise ValueError("the checkout path cannot contain a comma because Docker --mount cannot quote it")
    uid, gid = host_identity()
    return [
        "docker",
        "run",
        "--rm",
        "--read-only",
        "--user",
        f"{uid}:{gid}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--security-opt=seccomp=unconfined",
        "--security-opt=systempaths=unconfined",
        "--pid=host",
        "--network=none",
        "--pids-limit=1024",
        "--tmpfs",
        f"/home/autocode:rw,nosuid,nodev,noexec,size=64m,uid={uid},gid={gid},mode=0700",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,exec,size=1g,mode=1777",
        "--tmpfs",
        "/run:rw,nosuid,nodev,noexec,size=16m",
        "--mount",
        f"type=bind,source={source},target={CONTAINER_ROOT}",
        "--workdir",
        str(CONTAINER_ROOT),
        "--env",
        "HOME=/home/autocode",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        IMAGE,
    ]


def preflight_command(repo: Path) -> list[str]:
    return docker_run_prefix(repo) + ["/opt/autocode-venv/bin/python", "-c", PREFLIGHT]


def run_command(repo: Path, selection: Selection) -> list[str]:
    return docker_run_prefix(repo) + selected_command(selection)


def docker_problem() -> str | None:
    executable = shutil.which("docker")
    if not executable:
        return "Docker is not installed or is not on PATH"
    try:
        result = subprocess.run(
            [executable, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"cannot query the Docker server: {error}"
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        return f"the Docker server is unavailable: {detail or f'exit {result.returncode}'}"
    return None


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[1]
    arguments = list(sys.argv[1:] if argv is None else argv)
    selection = parse_selection(arguments, repo=repo)
    problem = docker_problem()
    if problem:
        print(f"Cannot run the supported Linux fake-test container: {problem}", file=sys.stderr)
        return 2

    try:
        subprocess.run(docker_build_command(repo), check=True)
        probe = subprocess.run(preflight_command(repo), capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"Cannot prepare the supported Linux fake-test container: {error}", file=sys.stderr)
        return 2
    if probe.returncode:
        detail = (probe.stderr or probe.stdout).strip()
        print(
            "Docker cannot supply AutoCode's required Linux PID, seccomp, or bubblewrap boundary: "
            + (detail or f"preflight exited {probe.returncode}"),
            file=sys.stderr,
        )
        return 2
    print(probe.stdout.strip())
    try:
        return subprocess.run(run_command(repo, selection)).returncode
    except (OSError, ValueError) as error:
        print(f"Cannot start the selected Linux fake test: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
