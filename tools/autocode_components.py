"""CLI for building a multi-component system from an architecture record.

    autocode components ARCHITECTURE --workspace REPO [--auto-approve] \\
        [--integrate TARGET [--run-local]] [--engine codex] [model options...]

``ARCHITECTURE`` (absolute, or relative to ``--workspace``) holds
``components.json``, ``dependency_trace.json`` and ``contracts/*.schema.json``
— the design one AutoCode task can already produce; see
scenarios/catalog/architecture-two-services. ``--workspace`` is an existing Git
repository with that architecture already committed. See docs/task-run.md and
``autocode_multicomponent.py`` for what this drives.
An individual component may supply a ``ui_run`` (a completed accepted UI run,
relative to ARCHITECTURE or absolute) or ``figma_file`` URL. Design inputs use
the existing Codex Figma workflow; see docs/task-lanes.md and docs/figma.md.

Each component builds in ``<workspace>/.autocode-components/<id>``, and
progress is saved in ``.autocode-components/manifest.json``. Running the command
again continues a partial build: components already done are left alone, and a
component that stopped for input resumes from where it stopped. A stopped
component's own run is an ordinary AutoCode run, so you can also answer or
approve it directly with ``autocode --workspace WORKSPACE --run-dir RUN_DIR``
(the ``run_dir`` this command prints) before running this command again. If the
architecture changed since the saved build, the command refuses to resume;
remove ``.autocode-components/`` to rebuild from scratch.

With ``--run-local``, the integrated system is then started with Docker Compose
and checked with ``ARCHITECTURE/smoke.json``; see ``autocode_local_run.py``.
"""

from __future__ import annotations

import argparse
import json
import math
import shlex
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

try:
    from . import autocode_local_run as local_run
    from . import autocode_multicomponent as mc
except ImportError:
    import autocode_local_run as local_run
    import autocode_multicomponent as mc


def _resolve(path: Path, workspace: Path) -> Path:
    return path if path.is_absolute() else (workspace / path)


def _ensure_worktree(repo: Path, target: Path) -> None:
    """Create ``target`` as a fresh worktree from HEAD if it does not already exist."""
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=AutoCode",
            "-c",
            "user.email=autocode@localhost",
            "worktree",
            "add",
            str(target),
            "HEAD",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _local_run_plan(parser, args, architecture):
    """Refuse --run-local before anything is built unless it could run."""
    if not args.run_local:
        parser.error(f"{'--keep-running' if args.keep_running else '--health-timeout'} needs --run-local")
    if not args.integrate:
        parser.error("--run-local needs --integrate TARGET: it runs the integrated system")
    if args.health_timeout is not None and not (math.isfinite(args.health_timeout) and args.health_timeout > 0):
        parser.error("--health-timeout must be a positive, finite number of seconds")
    try:
        plan = local_run.prepare(
            architecture.directory, {cid: component.runtime for cid, component in architecture.components.items()}
        )
        plan = replace(plan, endpoint=local_run.check_docker())
    except (ValueError, local_run.DockerUnavailable) as error:
        parser.error(f"--run-local: {error}")
    return plan


def _run_local(plan, target: Path, workspace: Path, args, exit_code: int, integration: dict) -> dict:
    if exit_code != 0 or integration.get("detail") == "no finished component":
        return {"status": "not_run", "detail": "not every component finished and integrated cleanly"}
    return local_run.LocalRun(
        plan,
        target.resolve(),
        local_run.workdir(workspace),
        health_timeout=args.health_timeout or local_run.HEALTH_TIMEOUT,
        keep_running=args.keep_running,
    ).run()


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("architecture", type=Path, help="architecture directory, absolute or relative to --workspace")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Git repository with the architecture already committed (default: cwd)",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="approve each component's plan and reviews, and answer questions with AutoCode's "
        "own proposed defaults, without asking a person. Pass this only when a person has "
        "delegated that decision for this build; it is not itself that decision.",
    )
    parser.add_argument(
        "--integrate",
        type=Path,
        metavar="TARGET",
        help="combine finished components into this worktree of the same repository, relative "
        "to --workspace unless absolute; created fresh from HEAD if it does not exist",
    )
    parser.add_argument(
        "--run-local",
        action="store_true",
        help="after integrating, start the combined system with Docker Compose, wait for each "
        "component to be ready, run ARCHITECTURE/smoke.json against it, then tear it down "
        "(needs --integrate, Docker Compose 2.17 or newer and a Docker daemon on this machine)",
    )
    parser.add_argument(
        "--runtime-evidence-provenance",
        choices=("fake", "live", "unknown"),
        help="With --run-local, disclose real or simulated runtime smoke; default unknown",
    )
    parser.add_argument(
        "--health-timeout",
        type=float,
        metavar="SECONDS",
        help="with --run-local: how long each start layer may take to become ready (default: "
        f"{local_run.HEALTH_TIMEOUT:g})",
    )
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="with --run-local: leave the system running afterwards instead of tearing it down",
    )
    parser.add_argument("--engine", choices=["codex", "opencode"])
    parser.add_argument("--provider", help="see docs/providers.md")
    parser.add_argument(
        "--joint-planning",
        action="store_true",
        help="separate requirements, planning and independent review per component; default for "
        "new OpenCode runs, opt-in for --engine codex (see docs/models.md)",
    )
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument(
        "--options",
        default="",
        metavar="FLAGS",
        help="extra flags passed to every component's `autocode` invocation verbatim, "
        'shell-quoted, e.g. --options "--terra-model x --sol-model y" — see '
        "docs/models.md and `autocode --help` for the full set",
    )
    parser.add_argument(
        "--max-advances",
        type=int,
        default=20,
        metavar="N",
        help="CLI relaunches per component before an honest stop (default: 20)",
    )
    parser.add_argument("--timeout", type=int, metavar="SECONDS", help="wall-clock budget per component")
    args = parser.parse_args(argv)
    if args.runtime_evidence_provenance and not args.run_local:
        parser.error("--runtime-evidence-provenance needs --run-local")

    workspace = args.workspace.resolve()
    if not (workspace / ".git").exists():
        parser.error(f"workspace is not a Git repository: {workspace}")
    try:
        architecture = mc.Architecture.load(_resolve(args.architecture, workspace))
        architecture.batches()  # fail fast on a cycle before starting anything
    except mc.ArchitectureError as error:
        parser.error(str(error))
    plan = None
    if args.run_local or args.keep_running or args.health_timeout is not None:
        plan = _local_run_plan(parser, args, architecture)

    options: list[str] = []
    for flag, value in (
        ("--engine", args.engine),
        ("--provider", args.provider),
        ("--reasoning-effort", args.reasoning_effort),
    ):
        if value:
            options += [flag, value]
    if args.joint_planning:
        options.append("--joint-planning")
    options += shlex.split(args.options)

    try:
        build = mc.MultiComponentBuild(
            workspace, architecture, options=tuple(options), timeout=args.timeout, max_advances=args.max_advances
        )
        saved = build.saved_components()
    except mc.ArchitectureError as error:
        parser.error(str(error))
    unknown = sorted(
        cid
        for cid in architecture.components
        if cid not in saved and (workspace / ".autocode-components" / cid).exists()
    )
    if unknown:
        parser.error(
            f"a worktree already exists for {', '.join(unknown)} but the saved build manifest does not "
            f"record it, so this command cannot tell what is in it. Inspect it directly, then remove "
            f".autocode-components/<id> to rebuild it from scratch."
        )

    try:
        results = build.build(auto_approve=args.auto_approve)
    except mc.ArchitectureError as error:
        parser.error(str(error))

    summary: dict[str, Any] = {
        "components": {
            cid: {
                "status": result.status,
                "resumed": result.resumed,
                "workspace": str(result.workspace),
                "run_dir": str(result.run.run_dir) if result.run else None,
                "view": result.view,
                "error": result.error,
            }
            for cid, result in results.items()
        }
    }
    statuses = {info["status"] for info in summary["components"].values()}
    exit_code = 1 if "error" in statuses else 2 if "needs_input" in statuses else 0

    if args.integrate:
        target = _resolve(args.integrate, workspace)
        try:
            _ensure_worktree(workspace, target)
        except subprocess.CalledProcessError as error:
            parser.error(f"could not prepare integration worktree {target}: {error.stderr}")
        try:
            summary["integration"] = build.integrate(target)
        except mc.ArchitectureError as error:
            parser.error(str(error))
        if summary["integration"]["failed"]:
            exit_code = max(exit_code, 1)
        if plan is not None:
            summary["local_run"] = _run_local(plan, target, workspace, args, exit_code, summary["integration"])
            if summary["local_run"]["status"] != "passed":
                exit_code = max(exit_code, 1)

    if exit_code == 0:
        try:
            from . import autocode_evidence_aggregate as evidence_aggregate
            from . import autocode_source_snapshot as source_snapshot
        except ImportError:
            import autocode_evidence_aggregate as evidence_aggregate
            import autocode_source_snapshot as source_snapshot
        try:
            child_runs = []
            for cid, result in results.items():
                run = result.run or mc.TaskRun(result.workspace, Path(saved[cid]["run_dir"]))
                child_runs.append((cid, run))
            product = target if args.integrate else workspace
            local = summary.get("local_run") or {}
            runtime_kind = args.runtime_evidence_provenance or "unknown"
            runtime_checks, gaps = [], []
            if local:
                try:
                    from . import autocode_util as util
                except ImportError:
                    import autocode_util as util
                smoke: dict[str, Any] = {
                    "provenance": {
                        "kind": runtime_kind,
                        "basis": "caller_declared" if args.runtime_evidence_provenance else "unavailable",
                    },
                    "status": local.get("status"),
                    "steps": [
                        {key: row.get(key) for key in ("index", "name", "service", "method", "path", "status", "ok")}
                        for row in local.get("steps", [])
                    ],
                }
                smoke_path = build.manifest_path.parent / "local-smoke-evidence.json"
                util.atomic_json(smoke_path, smoke)
                for row in smoke["steps"]:
                    runtime_checks.append(
                        {
                            "command": f"HTTP {row['method']} {row['service']} {row['path']} ({row['name']})",
                            "purpose": "local runtime smoke",
                            "output": str(smoke_path),
                            "output_sha256": util.file_hash(smoke_path),
                            "results": {
                                "http_status": row["status"],
                                "ok": row["ok"],
                                "runtime_provenance": smoke["provenance"],
                            },
                        }
                    )
                gaps.append(f"Local runtime smoke provenance: {runtime_kind}; caller disclosure is not attestation")
                if runtime_kind == "fake":
                    gaps.append(
                        "Simulated runtime smoke does not prove real containers, SQL/database behavior or deployment"
                    )
                elif runtime_kind == "unknown":
                    gaps.append("Local runtime smoke does not establish whether the runtime or database was simulated")
            assert architecture.directory is not None
            summary["evidence_report"] = evidence_aggregate.publish(
                build.manifest_path.parent,
                kind="components",
                identity=architecture.directory.name,
                status="COMPLETE",
                child_runs=child_runs,
                source_revision=source_snapshot.snapshot(product)["revision"],
                outcome="Build " + architecture.directory.name,
                checks=runtime_checks,
                binding_values={
                    "architecture": architecture.fingerprint(),
                    "integration": summary.get("integration"),
                    "local_run_status": local.get("status"),
                },
                not_verified=[
                    *gaps,
                    "Component completion/local smoke does not prove every integration or production deployment behavior",
                ],
            )
        except (OSError, ValueError, mc.TaskRunError) as error:
            summary["evidence_report"] = {
                "availability": "export_failed",
                "error": "Evidence report export failed: " + type(error).__name__,
                "reasons": ["Evidence report export failed: " + type(error).__name__],
            }
        build.record_evidence_report(summary["evidence_report"])
    print(json.dumps(summary, indent=2, default=str))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(cli())
