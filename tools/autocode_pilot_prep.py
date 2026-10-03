#!/usr/bin/env python3
"""Preparation coordinator for task runs that depend on declared public inputs.

Narrow script entry beside the shipped CLI (no new CLI flag on ``autocode``):
it verifies every declared input, builds the verification copy with the same
Git-enumerated construction the runner's scratch copies start from, and only
then starts the run through the public task-run interface. The copy is built
with exactly ``make_tree(repo, head, dest, workspace, changed_files(workspace,
head))`` -- ``dependencies_from`` deliberately omitted, a documented deviation
from ``scratch_run`` (autocode_verify.py), because this copy verifies
declared-input identity rather than running tests: no dependency symlinks and
no generated-source copies enter it. The destination is a fresh
``<workspace>/.autocode/prep/<invocation-id>/source``; both inventory seams
(``autocode_util.snapshot`` and ``autocode_verify.changed_files``) filter names
under ``.autocode/``, so a rerun never copies a previous prep copy into a new
one.

The declared-input preflight (``autocode_input_preflight``) checks each
declared input's path, type, mode, size and sha256 in the original workspace
and against that actual copy. Any ignored, missing or mismatched input is a
setup error: it prints to stderr and exits 2 before any provider launch, with
no TaskRun started and no run directory created. On success the coordinator
writes a prep report naming the verified copy root and starts the run with the
brief passed unchanged; it never answers a question, approves a plan or
completes anything on the user's behalf.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import autocode_input_preflight as input_preflight
    from . import autocode_taskrun as taskrun
    from . import autocode_verify as verify
except ImportError:  # executed as a script, with tools/ on sys.path
    import autocode_input_preflight as input_preflight
    import autocode_taskrun as taskrun
    import autocode_verify as verify

MODEL_FLAGS = ("--astra-model", "--terra-model", "--sol-model", "--completion-model",
               "--glm-model", "--plan-reviewer-model")


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="autocode_pilot_prep",
        description="Verify declared public inputs, build the verification copy, then start the run.")
    parser.add_argument("--workspace", required=True, help="the Git workspace the run will work in")
    parser.add_argument("--manifest", required=True,
                        help="declared-input manifest: path, type, mode, size, sha256 per input")
    parser.add_argument("--brief", required=True, help="task brief file, passed unchanged to the run")
    parser.add_argument("--report", required=True, help="where to write the prep report JSON")
    parser.add_argument("--engine", help="engine option forwarded whenever the run starts or advances")
    parser.add_argument("--joint-planning", action="store_true",
                        help="planning option forwarded whenever the run starts or advances")
    for flag in MODEL_FLAGS:
        parser.add_argument(flag, help="model option forwarded whenever the run starts or advances")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="per-invocation CLI timeout for the task run (seconds)")
    return parser.parse_args(argv)


def engine_options(args) -> tuple[str, ...]:
    """The TaskRun engine options implied by the coordinator's flags."""
    options = []
    if args.engine:
        options += ["--engine", args.engine]
    if args.joint_planning:
        options.append("--joint-planning")
    for flag in MODEL_FLAGS:
        value = getattr(args, flag.lstrip("-").replace("-", "_"))
        if value:
            options += [flag, value]
    return tuple(options)


def _fail(errors) -> int:
    for error in errors:
        print(f"declared-input setup error: {error}", file=sys.stderr)
    return 2


def main(argv=None) -> int:
    args = parse_args(argv)
    workspace = Path(args.workspace).resolve()
    try:
        brief = Path(args.brief).read_text()
    except OSError as error:
        return _fail([f"cannot read the task brief {args.brief}: {error}"])
    invocation = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    destination = workspace / ".autocode" / "prep" / invocation / "source"
    try:
        head = subprocess.run(["git", "-C", str(workspace), "rev-parse", "HEAD"],
                              capture_output=True, text=True)
        if head.returncode:
            return _fail([f"{args.workspace} is not a Git repository with a HEAD commit: "
                          f"{head.stderr.strip()}"])
        head = head.stdout.strip()
        copy_root = verify.make_tree(workspace, head, destination, workspace,
                                     verify.changed_files(workspace, head))
        result = input_preflight.preflight(args.manifest, workspace, copy_root)
    except (RuntimeError, OSError, ValueError) as error:
        return _fail([f"preparation failed while building or checking the verification copy: {error}"])
    if result["errors"]:
        return _fail(result["errors"])
    try:
        run = taskrun.TaskRun.start(workspace, brief, options=engine_options(args), timeout=args.timeout)
    except taskrun.TaskRunError as error:
        print(f"the task run failed to start after preparation: {error}", file=sys.stderr)
        return 1
    report = {"workspace": str(workspace), "manifest": str(Path(args.manifest)),
              "brief": str(Path(args.brief)), "copy_root": str(copy_root), "head": head,
              "inputs": result["ok"], "run_dir": str(run.run_dir)}
    try:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    except OSError as error:
        print(f"the run started at {run.run_dir} but the prep report could not be written: {error}",
              file=sys.stderr)
        return 1
    print(f"verified {len(result['ok'])} declared input(s) in {copy_root}; run started at {run.run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
