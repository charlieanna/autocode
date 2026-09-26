#!/usr/bin/env python3
"""Live-trial driver: run one scenario against one model profile and record evidence.

Verdicts come from the scenario's independent oracle, never from the runner's
own completion claim. Human gates are driven through the documented CLI flags
(``--answer``, ``--approve-goal``); the harness never writes ``state.json``.

Usage:
    python3 tools/live_trial.py LIVE-01 --profile fixture
    python3 tools/live_trial.py LIVE-01 --profile sol-hi --i-authorize-live-model-spend

``--profile fixture`` is offline and proves the harness before any spend.
Live profiles require the explicit authorization flag and a real provider.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_profiles as profiles  # noqa: E402
import live_scenarios as scenarios  # noqa: E402
from autopilot_testkit import Bundle, source_revision  # noqa: E402

AUTOCODE = HERE / "autocode.py"
PROVIDER_BIN = HERE / "live_fixture_provider.py"


class TrialError(RuntimeError):
    """Harness-level failure: the trial could not be attempted."""


# --- workspace -----------------------------------------------------------

def make_workspace(root: Path) -> Path:
    project = root / "project"
    project.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(
        ["git", "-C", str(project), "-c", "user.name=LiveTrial",
         "-c", "user.email=live@example.test", "commit", "--allow-empty", "-qm", "baseline"],
        check=True)
    return project


def install_fixture_provider(root: Path) -> dict:
    """Expose the scripted provider as the ``codex`` binary on PATH.

    Also isolates XDG_CONFIG_HOME/CODEX_HOME to fixture-local empty
    directories: without this, check_subscription() reads the contributor's
    real ~/.codex/config.toml (e.g. a customized model_provider) and can pause
    with PAUSED_BILLING_ROUTE even though the fixture correctly reports a
    ChatGPT login itself, breaking this profile's offline claim.
    """
    bindir = root / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    target = bindir / "codex"
    shutil.copy2(PROVIDER_BIN, target)
    target.chmod(0o755)
    config_home = root / "xdg-config"
    config_home.mkdir(parents=True, exist_ok=True)
    codex_home = root / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    return {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
            "XDG_CONFIG_HOME": str(config_home), "CODEX_HOME": str(codex_home)}


# --- runner driving ------------------------------------------------------

def autocode_command(project: Path, profile: dict, task: str | None,
                     run_dir: Path | None, extra: list[str]) -> list[str]:
    cmd = [sys.executable, str(AUTOCODE)]
    if task is not None:
        cmd.append(task)
    cmd += ["--workspace", str(project), "--no-chat", "--in-place"]
    if run_dir is not None:
        cmd += ["--run-dir", str(run_dir)]
    if profile["provider"] == "fixture":
        # The scripted provider is installed as the `codex` binary on PATH.
        # Joint planning is required so planning stages use the rich schemas.
        # Native Codex joint planning demands bare GPT model names even though
        # the fixture ignores them. Cross-model verification still applies:
        # builder and verifier roles must not share a model id/family.
        cmd += ["--engine", "codex", "--joint-planning",
                "--astra-model", "gpt-6-astra",
                "--terra-model", "gpt-5.6-terra",
                "--sol-model", "gpt-5.6-sol",
                "--completion-model", "gpt-6-astra",
                "--glm-model", "gpt-5.6-sol",
                "--plan-reviewer-model", "gpt-6-astra"]
    else:
        cmd += profiles.cli_overrides(profile)
        cmd += ["--provider", profile["provider"]]
        if profile.get("joint_planning", True):
            cmd += ["--joint-planning"]
    return cmd + extra


def invoke(cmd: list[str], env: dict, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def load_state(run_dir: Path) -> dict:
    path = run_dir / "state.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def drive(project: Path, root: Path, profile: dict, task: str,
          budget_stages: int, timeout: int, bundle: Bundle) -> dict:
    """Run the public CLI, serving human gates from the saved state."""
    env = dict(os.environ, AUTOCODE_HOME=str(root / "registry"),
               PYTHONDONTWRITEBYTECODE="1")
    if profile["provider"] == "fixture":
        env.update(install_fixture_provider(root))
        env.pop("AUTOCODE_PROVIDER", None)

    steps: list[dict] = []
    started = time.monotonic()
    run_dir: Path | None = None

    # 0 = finished, 2 = paused for a human gate. Both are successful CLI exits.
    def step(kind: str, cmd: list[str], *, allow_codes=(0, 2)) -> subprocess.CompletedProcess:
        if time.monotonic() - started > timeout:
            raise TrialError(f"wall-clock budget exceeded after {len(steps)} CLI steps")
        if len(steps) >= budget_stages:
            raise TrialError(f"stage budget exceeded after {len(steps)} CLI steps")
        bundle.log("cli_step", kind=kind, cmd=cmd)
        proc = invoke(cmd, env, root, timeout)
        record = {"kind": kind, "cmd": cmd, "returncode": proc.returncode,
                  "stdout_tail": proc.stdout[-800:], "stderr_tail": proc.stderr[-800:]}
        steps.append(record)
        bundle.log("cli_result", kind=kind, returncode=proc.returncode,
                   stdout_tail=record["stdout_tail"], stderr_tail=record["stderr_tail"])
        if proc.returncode not in allow_codes:
            raise TrialError(
                f"{kind} exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout)[-500:]}")
        return proc

    # 1. First launch creates the run under <workspace>/.autocode/runs/.
    step("start", autocode_command(project, profile, task, None, []))
    run_dir = _discover_run_dir(project)
    if run_dir is None:
        raise TrialError("first invocation did not create a run directory")
    bundle.log("run_dir", path=str(run_dir))

    # 2. Serve human gates until the run reaches a terminal or blocked state.
    seen: set[str] = set()
    for _ in range(budget_stages):
        state = load_state(run_dir)
        status = state.get("status", "")
        if status and status not in seen:
            seen.add(status)
        bundle.state(f"gate.{len(steps)}", {k: state.get(k) for k in (
            "status", "phase", "next_stage", "displayed_goal", "pending_questions")})

        if status in ("TASK_COMPLETE", "COMPLETE"):
            break
        if status.startswith("PAUSED_") and not _resumable(state):
            break

        if _serve_gate(state, run_dir, project, profile, step):
            continue

        # No gate to serve: one ordinary invocation, then re-check.
        before_state = state
        step("resume", autocode_command(project, profile, None, run_dir, []))
        after = load_state(run_dir)
        after_status = after.get("status", "")
        if after_status in ("TASK_COMPLETE", "COMPLETE"):
            break
        if after_status.startswith("PAUSED_") and not _resumable(after):
            break
        if after_status == before_state.get("status", "") and not _progressed(before_state, after):
            raise TrialError(
                f"run is stuck at {after_status!r} with no gate and no progress "
                f"(next_stage={after.get('next_stage')!r})")

    final = load_state(run_dir)
    bundle.state("final", final)
    bundle.log("drive_finished", status=final.get("status"), steps=len(steps))
    return {"state": final, "steps": steps, "run_dir": run_dir}


def _progressed(before: dict, after: dict) -> bool:
    keys = ("next_stage", "iteration", "phase", "status")
    return any(before.get(k) != after.get(k) for k in keys)


def _discover_run_dir(project: Path) -> Path | None:
    """The first invocation creates the run dir; recover its path."""
    runs = project / ".autocode" / "runs"
    if not runs.is_dir():
        return None
    candidates = sorted((p for p in runs.iterdir() if (p / "state.json").is_file()),
                        key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _resumable(state: dict) -> bool:
    status = state.get("status", "")
    return (status in ("AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER", "PAUSED_REQUESTED")
            or status.startswith("PAUSED_"))


def _serve_gate(state: dict, run_dir: Path, project: Path, profile: dict, step) -> bool:
    """Answer one human gate from saved state. Returns False when none applies."""
    status = state.get("status", "")

    if status == "PAUSED_PLANNING_BUDGET":
        # Two plan-review calls used. The documented recovery is explicit
        # --feedback requesting a new planning cycle.
        step("feedback", autocode_command(project, profile, None, run_dir, [
            "--feedback",
            "The previous planning cycle exhausted its review budget. Produce a "
            "complete final contract now with all four milestones and their "
            "declared affected_paths, then finalize."]),
            allow_codes=(0, 2))
        return True

    if status == "AWAITING_GOAL_APPROVAL":
        token = state.get("displayed_goal")
        if not token:
            raise TrialError("AWAITING_GOAL_APPROVAL without displayed_goal")
        step("approve-goal", autocode_command(
            project, profile, None, run_dir, ["--approve-goal", token]),
            allow_codes=(0, 2))
        return True

    questions = state.get("pending_questions") or []
    # Answer whenever questions are outstanding: a pause does not make them
    # optional, and refusing to answer is how DAG-10 drops become permanent.
    if questions:
        for question in questions:
            qid = question.get("id")
            options = question.get("options") or []
            default = question.get("proposed_default") or (options[0] if options else "yes")
            step("answer", autocode_command(
                project, profile, None, run_dir, ["--answer", f"{qid}={default}"]),
                allow_codes=(0, 2))
        return True

    if status == "WAITING_FOR_USER" or state.get("user_request"):
        request = state.get("user_request") or {}
        if request.get("kind") == "human_review":
            token = state.get("displayed_review") or state.get("displayed_goal")
            for cid in request.get("criteria", []):
                arg = f"{cid}={token}" if token else cid
                step("accept-review", autocode_command(
                    project, profile, None, run_dir, ["--accept-review", arg]),
                    allow_codes=(0, 2))
            return True

    return False


# --- verdict -------------------------------------------------------------

def judge(run: dict, spec: dict, project: Path, bundle: Bundle) -> scenarios.OracleResult:
    state = run["state"]
    status = state.get("status", "")
    kind = scenarios.classify_runner_status(status)

    # The oracle scores the delivered workspace regardless of how the run ended,
    # so a blocked run with correct work is reported as such, not as a failure
    # of the product.
    oracle = spec["oracle"](project)
    if oracle.status in (scenarios.ERROR, scenarios.DEFERRED):
        return oracle

    if kind == "complete":
        if oracle.status == scenarios.PASS and not oracle.failed:
            return oracle
        return scenarios.OracleResult(
            scenarios.FALSE_COMPLETE,
            f"runner claimed {status} but {spec['oracle_name']} scored {oracle.summary}",
            oracle.checks)

    if kind != "paused":
        return scenarios.OracleResult(
            scenarios.ERROR,
            f"runner stopped at unexpected status {status!r}; {spec['oracle_name']} {oracle.summary}",
            oracle.checks)

    # A genuine pause remains distinct from successful delivery.
    bundle.log("runner_paused", status=status)
    return scenarios.OracleResult(
        scenarios.HONEST_BLOCKER,
        f"runner stopped at {status or 'unknown'}; {spec['oracle_name']} {oracle.summary}",
        oracle.checks)


def write_report(bundle: Bundle, scenario_id: str, spec: dict,
                 profile_name: str, profile: dict, result: scenarios.OracleResult,
                 run: dict) -> Path:
    payload = {
        "scenario": scenario_id, "title": spec["title"],
        "profile": profile_name, "profile_detail": profile,
        "verdict": result.status, "summary": result.summary,
        "oracle": spec["oracle_name"], "checks": result.checks,
        "runner_status": run["state"].get("status"),
        "runner_phase": run["state"].get("phase"),
        "baseline": spec.get("baseline"),
        "source": source_revision(),
    }
    path = bundle.dir / "live-trial.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


# --- entry point ---------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", help="scenario id, e.g. LIVE-01")
    parser.add_argument("--profile", default="fixture",
                        help="model profile from live_profiles (default: fixture)")
    parser.add_argument("--workspace", type=Path,
                        help="parent directory for the disposable project (default: temp)")
    parser.add_argument("--source", type=Path,
                        help="existing project directory to use as the baseline")
    parser.add_argument("--budget-stages", type=int, default=40,
                        help="max CLI invocations before an honest budget stop")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="wall-clock seconds for the whole trial")
    parser.add_argument("--i-authorize-live-model-spend", action="store_true",
                        help="required for any non-fixture profile")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    spec = scenarios.scenario(args.scenario)
    profile = profiles.resolve(args.profile)

    if profile["provider"] != "fixture" and not args.i_authorize_live_model_spend:
        print(f"refusing live spend: rerun with --i-authorize-live-model-spend "
              f"(profile={args.profile}, model={profiles.describe(profile)})", file=sys.stderr)
        return 2

    bundle = Bundle(f"LIVE-{args.scenario.split('-')[-1]}")
    bundle.log("trial_started", scenario=args.scenario, profile=args.profile,
               model=profiles.describe(profile), authorized=args.i_authorize_live_model_spend)

    temp: tempfile.TemporaryDirectory | None = None
    if args.workspace:
        root = Path(args.workspace).resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        temp = tempfile.TemporaryDirectory(prefix="autopilot-live-")
        root = Path(temp.name).resolve()

    try:
        project = make_workspace(root)
        # Optional pre-existing project: "feature in an existing project" trials.
        source = getattr(args, "source", None) or spec.get("seed_from")
        if source:
            src = Path(source).resolve()
            if not src.is_dir():
                raise TrialError(f"--source is not a directory: {src}")
            shutil.copytree(src, project, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(
                                ".git", ".autocode", ".autocode-ui", "__pycache__",
                                ".venv", "node_modules", "*.pyc", ".tmp-*"))
            subprocess.run(["git", "-C", str(project), "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", str(project), "-c", "user.name=LiveTrial",
                 "-c", "user.email=live@example.test", "commit", "-qm",
                 "baseline: existing project"], check=True)
            bundle.log("seeded_from", source=str(src))
        seed = spec.get("seed") or {}
        for rel, body in seed.items():
            target = project / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        if seed:
            subprocess.run(["git", "-C", str(project), "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", str(project), "-c", "user.name=LiveTrial",
                 "-c", "user.email=live@example.test", "commit", "-qm", "seed scenario assets"],
                check=True)
            bundle.log("seeded", files=sorted(seed))
        bundle.log("workspace_ready", project=str(project))
        run = drive(project, root, profile, spec["task"],
                    args.budget_stages, args.timeout, bundle)
        result = judge(run, spec, project, bundle)
        write_report(bundle, args.scenario, spec, args.profile, profile, result, run)
        summary = (f"{args.scenario} [{args.profile}] {result.status}: {result.summary}")
        try:
            bundle.finish(result.status, summary)
        except AssertionError:
            # Bundle persists FAIL before raising for unittest callers.
            if result.status != scenarios.FAIL:
                raise
        print(summary)
        print(f"evidence: {bundle.dir}")
        if result.status == scenarios.HONEST_BLOCKER:
            return 2
        return 0 if result.status == scenarios.PASS else 1
    except TrialError as error:
        bundle.finish(scenarios.ERROR, str(error))
        print(f"{args.scenario} ERROR: {error}", file=sys.stderr)
        print(f"evidence: {bundle.dir}", file=sys.stderr)
        return 1
    finally:
        if temp is not None:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
