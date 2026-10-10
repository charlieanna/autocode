"""An offline guided example, driven only through the public task-run interface."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from .autocode_control_tokens import CONTROL_TOKEN_ENV_VARS
    from .autocode_taskrun import TaskRun, TaskRunError
except ImportError:
    from autocode_control_tokens import CONTROL_TOKEN_ENV_VARS
    from autocode_taskrun import TaskRun, TaskRunError

HERE = Path(__file__).resolve().parent
BRIEF = "Build greeting"
OPTIONS = (
    "--engine",
    "codex",
    "--joint-planning",
    "--no-adaptive-planning",
    "--astra-model",
    "gpt-6-astra",
    "--terra-model",
    "gpt-5.6-terra",
    "--sol-model",
    "gpt-5.6-sol",
    "--completion-model",
    "gpt-6-astra",
    "--glm-model",
    "gpt-5.6-sol",
    "--plan-reviewer-model",
    "gpt-6-astra",
    "--evidence-provenance",
    "fake",
    "--max-iterations",
    "3",
    "--max-seconds",
    "300",
    "--max-stage-seconds",
    "120",
)


def _save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _prepare(root: Path, name: str, *, failed_check=False) -> TaskRun:
    project = root / name
    project.mkdir()
    (project / "README.md").write_text("# Offline greeting demo\n\nNo model or account is used.\n")
    (project / "greet.py").write_text('import sys\nraise SystemExit("Greeting not implemented yet")\n')
    # Git also receives the isolated environment, never the user's configuration.
    home = root / (name + "-environment")
    home.mkdir()
    config = home / "config"
    config.mkdir()
    registry = home / "registry"
    codex_home = home / "codex"
    codex_home.mkdir()
    temporary = home / "tmp"
    temporary.mkdir()
    bin_dir = home / "bin"
    bin_dir.mkdir()
    for filename in ("fake_codex.py", "goal_fixtures.py"):
        shutil.copyfile(HERE / filename, bin_dir / filename)
    (bin_dir / "codex").write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " -B "
        + shlex.quote(str(bin_dir / "fake_codex.py"))
        + ' "$@"\n'
    )
    (bin_dir / "codex").chmod(0o700)
    environment = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "CODEX_HOME": str(codex_home),
        "AUTOCODE_HOME": str(registry),
        "TMPDIR": str(temporary),
        "PATH": os.pathsep.join((str(bin_dir), str(Path(sys.executable).parent), "/usr/bin", "/bin")),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "AUTOCODE_FIXTURE_MODE": "standard",
    }
    if failed_check:
        environment.update(AUTOCODE_FIXTURE_MODE="no-human", AUTOCODE_FIXTURE_UNREPRODUCIBLE_CHECK="1")
    for args in (
        ("init", "-q", str(project)),
        ("-C", str(project), "add", "README.md", "greet.py"),
        (
            "-C",
            str(project),
            "-c",
            "user.name=AutoCode Demo",
            "-c",
            "user.email=demo@example.invalid",
            "commit",
            "-qm",
            "Offline demo",
        ),
    ):
        subprocess.run(["/usr/bin/git", *args], env=environment, check=True, capture_output=True, text=True, timeout=30)
    # TaskRun merges its environment with its caller's. This explicit CLI launcher
    # drops inherited credentials, fixture switches and Git/provider settings before
    # executing the ordinary CLI. Only TaskRun's private controller tokens survive.
    launcher = home / "launch.py"
    launcher.write_text(
        "import os, sys\n"
        + "safe = "
        + repr(environment)
        + "\n"
        + "tokens = "
        + repr(sorted(CONTROL_TOKEN_ENV_VARS))
        + "\n"
        + "safe.update({k: os.environ[k] for k in tokens if os.environ.get(k)})\n"
        + "os.execve("
        + repr(sys.executable)
        + ", ["
        + repr(sys.executable)
        + ", '-B', "
        + repr(str(HERE / "autocode.py"))
        + ", *sys.argv[1:]], safe)\n"
    )
    return TaskRun.start(
        project,
        BRIEF,
        options=OPTIONS,
        start_options=("--workflow", "build"),
        command=(sys.executable, "-I", "-B", str(launcher)),
        env=dict.fromkeys(CONTROL_TOKEN_ENV_VARS, ""),
        timeout=360,
        cwd=project,
    )


def _plan(run: TaskRun) -> dict:
    view = run.advance_until_input()
    if view["needs"]["kind"] == "answer":
        question = view["needs"]["questions"][0]
        if question["id"] != "Q1":
            raise TaskRunError("The demo stopped at an unexpected question")
        print("Requirements: choose the local CLI for this sample.", flush=True)
        run.answer(question["id"], "CLI: local use", resolver_token=view["needs"].get("resolver_token"))
        view = run.advance_until_input()
    if view["needs"]["kind"] != "approve_plan":
        raise TaskRunError("The demo did not produce an approval-ready plan: " + view["status"])
    print("Plan ready: greet a name, reject invalid input, then independently check it.", flush=True)
    (run.workspace.parent / (run.workspace.name + "-environment") / "displayed-plan.txt").write_text(run.show_goal())
    print("Demo approval: approve the displayed sample plan before building.", flush=True)
    run.approve_plan(view["needs"]["token"])
    print("Builder and Tester: write greet.py and execute the CLI checks.", flush=True)
    return run.advance_until_input()


def run_demo(root: Path) -> dict:
    started = time.monotonic()
    print("Offline demo — deterministic fake provider; no account, network or model spend.", flush=True)
    print("Demo files: " + str(root), flush=True)
    print("First, a check that passes only in the fake Tester's session.", flush=True)
    rejected = _prepare(root, "refused-check", failed_check=True)
    refusal = _plan(rejected)
    reason = refusal.get("stop_reason") or ""
    if refusal["done"] or "was reported as exit 0" not in reason or "exited 1" not in reason:
        raise TaskRunError("The deliberately false check was not refused: " + refusal["status"])
    _save(root / "refused-check.json", refusal)
    print("Refused: the Tester claimed PASS, but AutoCode's independent rerun failed. Done = false.", flush=True)
    print("Now run the same sample with reproducible checks.", flush=True)
    accepted = _prepare(root, "greeting")
    pending = _plan(accepted)
    replay = pending["evidence"].get("check_replay") or {}
    if pending["done"] or pending["needs"]["kind"] != "review" or replay.get("verdict") != "PASS":
        raise TaskRunError("The demo did not stop for human acceptance after passing checks: " + pending["status"])
    if [row.get("id") for row in pending["evidence"]["acceptance"]] != ["C1"] or not all(
        row.get("status") == "verified" for row in pending["evidence"]["acceptance"]
    ):
        raise TaskRunError("The fake Completion Reviewer's claim was not visible")
    _save(root / "refused-completion.json", pending)
    print("Completion gate: the fake reviewer says verified; AutoCode still refuses done until acceptance.", flush=True)
    print("Demo acceptance: approve the greeting artifact using its current review token.", flush=True)
    need = pending["needs"]
    for criterion in need["criteria"]:
        accepted.approve_review(criterion, need["token"])
    final = accepted.advance_until_input()
    if not final["done"]:
        raise TaskRunError("The accepted demo did not complete: " + final["status"])
    report = accepted.evidence_report()
    _save(root / "completed.json", accepted.status(inspect_evidence=True))
    record = {
        "provenance": "fake",
        "duration_seconds": round(time.monotonic() - started, 3),
        "refused_check_run": str(rejected.run_dir),
        "completed_run": str(accepted.run_dir),
        "evidence_report": report,
        "completion_refused_before_acceptance": True,
        "independent_check_refused": True,
    }
    _save(root / "demo.json", record)
    print("Complete: independently reproduced checks and demo acceptance are recorded.", flush=True)
    print("Run directory: " + str(accepted.run_dir), flush=True)
    print("Evidence record: " + str(report["json_path"]), flush=True)
    print("Demo record: " + str(root / "demo.json"), flush=True)
    print("Fake results demonstrate the workflow; real-model reliability is recorded in RELIABILITY.md.", flush=True)
    print("All demo output stays in this directory. Remove it when finished inspecting.", flush=True)
    return record


def cli(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="autocode demo",
        description="Watch an offline plan, approval, build and completion gate with a fake provider.",
    )
    parser.add_argument(
        "--directory", type=Path, help="Create a new demo directory here (default: a fresh temporary directory)."
    )
    args = parser.parse_args(argv)
    try:
        if args.directory is None:
            root = Path(tempfile.mkdtemp(prefix="autocode-demo-")).resolve()
        else:
            root = args.directory.expanduser().absolute()
            root.mkdir(mode=0o700)
            root = root.resolve()
        run_demo(root)
    except (OSError, subprocess.SubprocessError, TaskRunError) as error:
        print("autocode demo: " + str(error), file=sys.stderr)
        return 1
    return 0
