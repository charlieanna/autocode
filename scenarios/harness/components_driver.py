"""Architecture, component TaskRuns, integration and local smoke through the public CLI."""

import contextlib
import json
import os
import shlex
import sys
import time

from . import catalog
from .component_services import running
from .driver import REPO, DriveError, Driver, InterruptedDrive, default_autocode, fake_setup, metrics
from .processes import CallTimeout, SupervisionUnavailable, run_cli
from .project import git, overlay_paths


def drive(scenario, args, out, project, flags, env):
    architecture_scenario = catalog.load(scenario.components_architecture)
    architecture_out = out / "architecture-run"
    architecture_out.mkdir()
    architecture_flags, architecture_env = (
        fake_setup(architecture_scenario, architecture_out, architecture_scenario.reference)
        if args.fake
        else (flags, env)
    )
    driver = Driver(
        project,
        architecture_out,
        architecture_flags,
        architecture_env,
        autocode=args.autocode or default_autocode(),
        max_steps=args.max_steps or scenario.max_steps,
        timeout_seconds=60 * (args.timeout_minutes or scenario.timeout_minutes),
    )
    driver.drive(architecture_scenario.brief)
    view = driver.view()
    if not view.get("complete") and view.get("status") != "TASK_COMPLETE":
        raise DriveError(f"architecture did not complete: {view.get('status')}")
    # Only the newly materialized scenario repository is committed.
    git(project, "add", "architecture")
    git(project, "-c", "maintenance.auto=false", "commit", "-qm", "Scenario architecture")
    environment = {**driver.env, **env}
    if args.fake:
        bindir = out / "component-bin"
        bindir.mkdir()
        binary = bindir / "codex"
        binary.write_text(
            f"#!{sys.executable}\n" + (REPO / "tools" / "fixtures" / "multicomponent_fake.py").read_text()
        )
        binary.chmod(0o755)
        files = {name: (scenario.reference / name).read_text() for name in overlay_paths(scenario.reference)}
        overlay = scenario.dir / args.fake_solution
        files.update({name: (overlay / name).read_text() for name in overlay_paths(overlay)})
        rows = json.loads((project / "architecture" / "components.json").read_text())
        manifest = {}
        for row in rows:
            cid = row["id"]
            delivered = {name: text for name, text in files.items() if name.startswith(f"components/{cid}/")}
            manifest[cid] = {
                "description": row["description"],
                "files": delivered,
                "check": shlex.join([sys.executable, "-m", "py_compile", *delivered]),
                "observations": str(out / "component-prompts"),
            }
        path = out / "component-manifest.json"
        path.write_text(json.dumps(manifest))
        environment.update(PATH=f"{bindir}{os.pathsep}{environment['PATH']}", FAKE_MANIFEST=str(path))
    target = project / "integration"
    steps = []

    def call(local=False):
        command = [
            *(args.autocode or default_autocode()),
            "components",
            "architecture",
            "--workspace",
            str(project),
            "--auto-approve",
            "--integrate",
            str(target),
            "--options",
            shlex.join(flags),
            "--timeout",
            str(max(1, int(driver.deadline - time.monotonic()))),
        ]
        if local:
            command += ["--run-local", "--health-timeout", "30", '--runtime-evidence-provenance',
                        'live' if getattr(args, 'local_docker', False) else 'fake']
        remaining = driver.deadline - time.monotonic()
        if remaining <= 0:
            raise InterruptedDrive("components time budget exhausted; remaining work ungraded")
        kind = "components-local" if local else "components"
        try:
            proc = run_cli(
                command,
                cwd=out,
                env=environment,
                timeout=remaining,
                lifeline={"root": out / "cli-calls", "kind": kind, "deadline": driver.deadline},
            )
        except SupervisionUnavailable as error:
            raise DriveError(str(error)) from None
        except CallTimeout as error:
            steps.append(
                {
                    "kind": kind,
                    "exit": None,
                    "interruption": type(error).__name__,
                    "timeout_seconds": error.timeout,
                    "cleanup_errors": error.cleanup_errors,
                }
            )
            (out / "components-steps.json").write_text(json.dumps(steps, indent=2))
            detail = (
                "; cleanup incomplete: " + "; ".join(error.cleanup_errors)
                if error.cleanup_errors
                else "; captured workers stopped"
            )
            raise InterruptedDrive(f"{kind} was still running when the time budget ran out{detail}") from None
        steps.append({"kind": kind, "exit": proc.returncode, "stderr": proc.stderr})
        (out / "components-steps.json").write_text(json.dumps(steps, indent=2))
        if proc.returncode < 0 or proc.returncode in (129, 130, 143):
            raise InterruptedDrive(f"components CLI interrupted (exit {proc.returncode}); remaining work ungraded")
        try:
            summary = json.loads(proc.stdout)
        except ValueError:
            raise DriveError(
                f"components did not return JSON (exit {proc.returncode}): {proc.stderr[-2000:]}"
            ) from None
        summary["exit_code"] = proc.returncode
        (out / ("components-local.json" if local else "components-build.json")).write_text(
            json.dumps(summary, indent=2)
        )
        return summary

    summary = call()
    if summary["exit_code"] != 0:
        return driver, target, summary, steps
    real = getattr(args, "local_docker", False)
    if not real:
        binary = out / "component-bin" / "docker"
        binary.write_text(f"#!{sys.executable}\n" + (REPO / "tools" / "fixtures" / "fake_docker.py").read_text())
        binary.chmod(0o755)
        environment.update(
            FAKE_DOCKER_LOG=str(out / "docker.jsonl"), DOCKER_HOST="unix:///fake-local.sock", DOCKER_CONTEXT=""
        )
    with contextlib.nullcontext(None) if real else running(target, project / "architecture") as ports:
        if ports:
            environment["FAKE_DOCKER_PORTS"] = json.dumps(ports)
        summary = call(local=True)
    return driver, target, summary, steps


def component_metrics(summary):
    stages = []
    for row in summary.get("components", {}).values():
        attempts = (((row.get("view") or {}).get("usage") or {}).get("accounting") or {}).get("attempts") or []
        stages.extend({**attempt, "metrics": {"provider_tokens": attempt.get("tokens") or {}}} for attempt in attempts)
    result = metrics({"stages": stages})
    result["model_routes"] = [
        {"stage": row.get("stage"), "engine": row.get("engine"), "model": row.get("model")}
        for row in stages
        if not row.get("runner_owned")
    ]
    return result
