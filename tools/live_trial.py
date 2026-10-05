#!/usr/bin/env python3
"""Live-trial driver: run one scenario against one model profile and record evidence.

Verdicts come from the scenario's independent oracle, never from the runner's
own completion claim. Real human reviews stop for operator action; only the
offline fixture simulates review approval. The harness never writes state.json.
The timeout covers CLI driving, not workspace setup, process cleanup grace,
independent oracle execution, or evidence/report I/O.

Usage:
    python3 tools/live_trial.py LIVE-01 --profile fixture
    python3 tools/live_trial.py LIVE-01 --profile sol-hi --i-authorize-live-model-spend

``--profile fixture`` is offline and proves the harness before any spend.
Live profiles require the explicit authorization flag and a real provider.
Deployment authorization in program mode requires the separate
``--authorize-deployment`` opt-in; live model spend never grants it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import live_profiles as profiles  # noqa: E402
import live_scenarios as scenarios  # noqa: E402
import autocode_process as processes  # noqa: E402
import autocode_util as util  # noqa: E402
from autopilot_testkit import Bundle, source_revision  # noqa: E402
from score_autocode_run import usage_summary  # noqa: E402

AUTOCODE = HERE / "autocode.py"
PROVIDER_BIN = HERE / "live_fixture_provider.py"


class TrialError(RuntimeError):
    """Harness-level failure: the trial could not be attempted."""


class HumanReviewRequired(TrialError):
    """An actual user decision cannot be supplied by this harness."""


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

TRIAL_LIMITS = {'max_iterations': 8, 'max_seconds': 1800, 'max_stage_seconds': 600,
                'max_tool_seconds': 300, 'max_idle_seconds': 180, 'max_milestone_seconds': 1800}


def autocode_command(project: Path, profile: dict, task: str | None,
                     run_dir: Path | None, extra: list[str]) -> list[str]:
    cmd = [sys.executable, str(AUTOCODE)]
    if task is not None:
        cmd.append(task)
    cmd += ["--workspace", str(project), "--no-chat", "--in-place"]
    if run_dir is not None:
        cmd += ["--run-dir", str(run_dir)]
    if task is not None:
        cmd += ['--autoresolver-managed-limits']
        for name, default in TRIAL_LIMITS.items():
            value = (profile.get('limits') or {}).get(name, default)
            if type(value) is not int or value <= 0:
                raise TrialError(f'{name} must be a positive finite integer')
            cmd += ['--' + name.replace('_', '-'), str(value)]
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
        cmd += ["--engine", "opencode", "--provider", profile["provider"]]
        if profile.get("joint_planning", True):
            cmd += ["--joint-planning"]
    return cmd + extra


def invoke(cmd: list[str], env: dict, cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    # Files avoid pipe backpressure and EOF waits on orphaned descendants.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        child = subprocess.Popen(cmd, env=env, cwd=cwd, stdout=stdout, stderr=stderr,
                                 start_new_session=True)
        owned = []
        error = None
        timed_out = False
        try:
            returncode, timed_out = processes.wait_for_stage(
                child, timeout, lambda rows: owned.__setitem__(slice(None), rows))
        except (processes.ProcessError, OSError, subprocess.TimeoutExpired) as failure:
            error = failure
            returncode = child.poll()
        stdout.seek(0)
        stderr.seek(0)
        result = subprocess.CompletedProcess(cmd, returncode,
            stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace"))
        if timed_out or error:
            failure = subprocess.TimeoutExpired(cmd, timeout) if timed_out else error
            failure.stdout, failure.stderr = result.stdout, result.stderr
            failure.returncode = returncode
            failure.processes = getattr(failure, "processes", owned)
            raise failure
        return result


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
    deadline = time.monotonic() + timeout
    run_dir: Path | None = None

    # 0 = finished, 2 = paused for a human gate. Both are successful CLI exits.
    def step(kind: str, cmd: list[str], *, allow_codes=(0, 2)) -> subprocess.CompletedProcess:
        remaining = deadline - time.monotonic()
        record = {"kind": kind, "cmd": cmd, "remaining_seconds": max(0, remaining)}
        try:
            if remaining <= 0:
                raise TrialError("CLI-driving deadline exhausted before launch")
            if len(steps) >= budget_stages:
                raise TrialError("CLI invocation budget exhausted before launch")
            bundle.log("cli_step", **record)
            proc = invoke(cmd, env, root, remaining)
        except (TrialError, subprocess.TimeoutExpired, OSError, processes.ProcessError) as error:
            def text(value):
                return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
            record.update(error_type=type(error).__name__, error=str(error),
                          timed_out=isinstance(error, subprocess.TimeoutExpired) or remaining <= 0,
                          returncode=getattr(error, "returncode", None),
                          stdout_tail=text(getattr(error, "stdout", ""))[-800:],
                          stderr_tail=text(getattr(error, "stderr", ""))[-800:],
                          processes=getattr(error, "processes", []))
            steps.append(record)
            bundle.log("cli_result", **record)
            failure = TrialError(f"{kind}: {error}")
            failure.steps = steps
            raise failure from error
        record = {"kind": kind, "cmd": cmd, "returncode": proc.returncode,
                  "stdout_tail": proc.stdout[-800:], "stderr_tail": proc.stderr[-800:]}
        steps.append(record)
        bundle.log("cli_result", kind=kind, returncode=proc.returncode,
                   stdout_tail=record["stdout_tail"], stderr_tail=record["stderr_tail"])
        if proc.returncode not in allow_codes:
            failure = TrialError(
                f"{kind} exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout)[-500:]}")
            failure.steps = steps
            raise failure
        return proc

    # 1. First launch creates the run under <workspace>/.autocode/runs/.
    step("start", autocode_command(project, profile, task, None, []))
    run_dir = _discover_run_dir(project)
    if run_dir is None:
        raise TrialError("first invocation did not create a run directory")
    bundle.log("run_dir", path=str(run_dir))

    # 2. Serve human gates until the run reaches a terminal or blocked state.
    seen: set[str] = set()
    blocker = None
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

        try:
            if _serve_gate(state, run_dir, project, profile, step):
                continue
        except HumanReviewRequired as error:
            blocker = str(error)
            bundle.log("human_review_blocked", reason=blocker, run_dir=str(run_dir))
            break

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
    return {"state": final, "steps": steps, "run_dir": run_dir, "blocker": blocker}


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
    # Operational pauses need an explicit inspected recovery, not a trial loop
    # that redispatches unchanged work or replenishes its budget.
    return status in ("AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER")


def _serve_gate(state: dict, run_dir: Path, project: Path, profile: dict, step) -> bool:
    """Answer one human gate from saved state. Returns False when none applies."""
    status = state.get("status", "")

    request = state.get("user_request") or {}
    if profile['provider'] != 'fixture':
        if status == 'AWAITING_GOAL_APPROVAL':
            raise HumanReviewRequired(
                f'Goal approval required in {run_dir}. Inspect --show-goal and approve the '
                f'current displayed token {state.get("displayed_goal")!r}; the live harness cannot approve.')
        if request.get('kind') != 'human_review' and (
                state.get('pending_questions') or status == 'PAUSED_PLANNING_BUDGET'):
            raise HumanReviewRequired(
                f'An explicit user decision is required in {run_dir}. Inspect the saved questions '
                'or planning-budget pause; the live harness cannot choose answers or expand the planning budget.')
    if request.get("kind") == "human_review":
        if profile["provider"] != "fixture":
            raise HumanReviewRequired(
                f"Human review required in {run_dir}. Inspect the current artifact and "
                "review criteria, then use --approve-review CRITERION_ID --review-token "
                "TOKEN with the current displayed_review token; the live harness cannot approve.")
        token = state.get("displayed_review")
        criteria = request.get("criteria") or []
        if not token or not criteria:
            raise HumanReviewRequired("Fixture review lacks displayed_review or criteria; cannot simulate approval")
        # Submit one exact snapshot's criteria together; never reuse its token
        # across subsequent CLI invocations that may change the artifact.
        extra = [arg for cid in criteria for arg in ("--approve-review", cid)]
        step("fixture-approve-review", autocode_command(
            project, profile, None, run_dir, extra + ["--review-token", token]))
        return True

    if status == "PAUSED_PLANNING_BUDGET":
        # Two plan-review calls used. The documented recovery is explicit
        # --feedback requesting a new planning cycle.
        step("feedback", autocode_command(project, profile, None, run_dir, [
            "--feedback",
            "The previous planning cycle exhausted its review budget. Produce a "
            "complete final contract for the original request, preserving its "
            "requirements, exclusions and declared affected_paths, then finalize."]),
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
        # One answer per step: answering consumes the AutoResolver request, so the
        # next question is answered against the freshly published request token.
        question = questions[0]
        qid = question.get("id")
        options = question.get("options") or []
        default = question.get("proposed_default") or (options[0] if options else "yes")
        token = (state.get("resolver_human_request") or {}).get("request_token")
        extra = ["--answer", f"{qid}={default}"] + (["--resolver-token", token] if token else [])
        step("answer", autocode_command(project, profile, None, run_dir, extra), allow_codes=(0, 2))
        return True

    return False


# --- program mode --------------------------------------------------------

PROGRAM_TERMINAL = ("COMPLETE",)
PROGRAM_PAUSES = ("WAITING", "PAUSED_", "AUTHORIZATION_REQUIRED")
AGREEMENT_GATE = "WAITING_AGREEMENT_APPROVAL"


def program_command(project: Path, profile: dict, manifest_path: Path, *,
                    authorize_deployment: bool = False) -> list[str]:
    """`autocode program run` with the profile's role flags passed through to child runs."""
    child = autocode_command(project, profile, None, None, [])
    # Everything after the workspace/in-place/no-chat trio is child-run configuration.
    passthrough = child[child.index("--no-chat") + 1:]
    passthrough = [flag for flag in passthrough if flag != "--in-place"]
    cmd = [sys.executable, str(AUTOCODE), "program", "run", str(manifest_path), "--workspace", str(project),
           "--max-parallel", "2"]
    if authorize_deployment:
        cmd.append("--authorize-deployment")
    return cmd + passthrough


def program_approve_command(project: Path, manifest_path: Path, token: str) -> list[str]:
    """`autocode program approve` for the exact agreement token the program summary displayed."""
    return [sys.executable, str(AUTOCODE), "program", "approve", str(manifest_path), "--workspace", str(project),
            "--token", token]


def drive_program(project: Path, root: Path, profile: dict, manifest: dict,
                  budget_stages: int, timeout: int, bundle: Bundle, *,
                  authorize_deployment: bool = False) -> dict:
    """Run a scenario as a program: every workstream is a child run whose gates are served here.

    The program controller never approves anything; this driver serves the
    same explicit CLI gates it serves for a single run, per child run dir.
    The program agreement is one more such gate, under the same rule
    ``_serve_gate`` applies to plans: only the offline fixture profile approves
    it; a live profile stops there for a person.
    """
    env = dict(os.environ, AUTOCODE_HOME=str(root / "registry"), PYTHONDONTWRITEBYTECODE="1")
    if profile["provider"] == "fixture":
        env.update(install_fixture_provider(root))
    manifest_path = root / "program.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    steps: list[dict] = []
    started = time.monotonic()

    def step(kind: str, cmd: list[str], *, allow_codes=(0, 2)) -> subprocess.CompletedProcess:
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise TrialError(f"wall-clock budget exceeded after {len(steps)} CLI steps")
        if len(steps) >= budget_stages:
            raise TrialError(f"stage budget exceeded after {len(steps)} CLI steps")
        bundle.log("cli_step", kind=kind, cmd=cmd)
        proc = invoke(cmd, env, root, remaining)
        record = {"kind": kind, "cmd": cmd, "returncode": proc.returncode,
                  "stdout_tail": proc.stdout[-800:], "stderr_tail": proc.stderr[-800:]}
        steps.append(record)
        bundle.log("cli_result", kind=kind, returncode=proc.returncode,
                   stdout_tail=record["stdout_tail"], stderr_tail=record["stderr_tail"])
        if proc.returncode not in allow_codes:
            raise TrialError(f"{kind} exited {proc.returncode}: {(proc.stderr or proc.stdout)[-500:]}")
        return proc

    summary: dict = {}
    approved: set[str] = set()
    for _ in range(budget_stages):
        proc = step("program", program_command(
            project, profile, manifest_path, authorize_deployment=authorize_deployment))
        try:
            summary = json.loads(proc.stdout)
        except ValueError:
            raise TrialError(f"program run printed no JSON summary: {proc.stdout[-300:]}") from None
        bundle.state(f"program.{len(steps)}", summary)
        status = summary.get("status", "")
        if status in PROGRAM_TERMINAL:
            break
        if status == AGREEMENT_GATE:
            token = ((summary.get("agreement") or {}).get("pending") or {}).get("token")
            if not token:
                raise TrialError(f"{AGREEMENT_GATE} without agreement.pending.token")
            if profile["provider"] != "fixture":
                # Nothing has started; the summary's WAITING status is reported as an honest pause.
                bundle.log("human_review_blocked", run_dir=str(Path(summary.get("state_file", root)).parent),
                           reason=f"Program agreement approval required: inspect `autocode program show "
                                  f"{manifest_path} --workspace {project}` and approve token {token}; "
                                  "the live harness cannot approve.")
                break
            if token in approved:
                raise TrialError(f"program agreement {token} is still pending after it was approved")
            approved.add(token)
            step("approve-agreement", program_approve_command(project, manifest_path, token), allow_codes=(0,))
            continue
        served = False
        for row in summary.get("workstreams", []):
            if row.get("status") not in ("WAITING", "PAUSED") or not row.get("run_dir"):
                continue
            run_dir = Path(row["run_dir"])
            state = load_state(run_dir)
            if state.get("status", "").startswith("PAUSED_") and not _resumable(state):
                continue
            if _serve_gate(state, run_dir, Path(row["workspace"]), profile, step):
                served = True
        if not served and not _program_advances(summary):
            # No gate this driver may serve and nothing the program itself will advance: an honest
            # stop, a blocked worker, or a run at a pause that needs a person. Never poke it with a
            # blind resume loop.
            break
    else:
        raise TrialError("program did not finish within the stage budget")
    final_status = summary.get("status", "")
    bundle.log("drive_finished", status=final_status, steps=len(steps))
    state = {"status": final_status, "program": summary}
    return {"state": state, "steps": steps, "run_dir": Path(summary.get("state_file", root)).parent,
            "product": Path(summary["integration_workspace"]) if summary.get("integration_workspace") else project}


def _program_advances(summary: dict) -> bool:
    """Whether another `program run` pass moves something: the program says RUNNING, or a child that
    was answered, approved or sent the program's own feedback waits only for its next invocation."""
    if summary.get("status") == "RUNNING":
        return True
    return any(row.get("status") in ("WAITING", "PAUSED", "RUNNING") and row.get("run_status") == "RUNNING"
               and not row.get("blocked_reason") for row in summary.get("workstreams", []))


def classify_program_status(status: str) -> str:
    if status in PROGRAM_TERMINAL:
        return "complete"
    if any(status.startswith(prefix) for prefix in PROGRAM_PAUSES):
        return "paused"
    return "stopped"


# --- verdict -------------------------------------------------------------

def judge(run: dict, spec: dict, project: Path, bundle: Bundle) -> scenarios.OracleResult:
    if run.get("blocker"):
        return scenarios.OracleResult(scenarios.HONEST_BLOCKER, run["blocker"], [])
    state = run["state"]
    status = state.get("status", "")
    kind = (classify_program_status(status) if "program" in state
            else scenarios.classify_runner_status(status))

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
        "report_version": 2,
        "attempt_id": hashlib.sha256(str(bundle.dir.resolve()).encode()).hexdigest(),
        "run_identity": hashlib.sha256(str(Path(run['run_dir']).resolve()).encode()).hexdigest()
            if run.get('run_dir') else None,
        "scenario": scenario_id, "title": spec["title"],
        "task_type": spec.get("task_type", "live"),
        "profile": profile_name, "profile_detail": profile,
        "verdict": result.status, "summary": result.summary,
        "oracle": spec["oracle_name"], "checks": result.checks,
        "runner_status": run["state"].get("status"),
        "runner_phase": run["state"].get("phase"),
        "mode": run.get("mode", "run"),
        "product": str(run.get("product", "")),
        "baseline": spec.get("baseline"),
        "source": source_revision(),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "steps": run.get("steps", []),
        "error": run.get("error"),
        "candidate_revision": run.get("candidate_revision"),
        "oracle_sha256": run.get("oracle_sha256"),
        "blocker": run.get("blocker"),
        "measurement": {
            "schema_version": 1,
            "execution_kind": "fixture" if profile.get("provider") == "fixture" else "live",
            "workload_kind": run.get("workload_kind"),
            "baseline_revision": run.get("baseline_revision"),
            "baseline_content_sha256": run.get("baseline_content_sha256"),
            "task_sha256": hashlib.sha256(spec["task"].encode()).hexdigest(),
            "elapsed_seconds": run.get("elapsed_seconds"),
            "usage": usage_summary(run["state"]),
        },
    }
    path = bundle.dir / "live-trial.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


# --- entry point ---------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    def positive(value):
        value = int(value)
        if value <= 0:
            raise argparse.ArgumentTypeError('must be a positive finite integer')
        return value

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", nargs="?", help="scenario id, e.g. LIVE-01 or BUGFIX-01 (see --list)")
    parser.add_argument("--list", action="store_true", help="print the registered scenarios and exit")
    parser.add_argument("--profile", default="fixture",
                        help="model profile from live_profiles (default: fixture)")
    parser.add_argument("--workspace", type=Path,
                        help="parent directory for the disposable project (default: temp)")
    parser.add_argument("--source", type=Path,
                        help="existing project directory to use as the baseline")
    parser.add_argument("--budget-stages", type=positive, default=40,
                        help="max CLI invocations before an honest budget stop")
    parser.add_argument("--timeout", type=positive, default=1800,
                          help="CLI-driving seconds only; excludes setup, cleanup grace, oracle and report I/O")
    for name, default in TRIAL_LIMITS.items():
        parser.add_argument('--' + name.replace('_', '-'), type=positive, default=default,
                            help=f'Persisted runtime bound for a new trial (default: {default})')
    parser.add_argument("--i-authorize-live-model-spend", action="store_true",
                        help="required for any non-fixture profile")
    parser.add_argument("--authorize-deployment", action="store_true",
                        help="explicitly authorize deployment workstreams in --mode program; "
                             "independent of live model spend authorization")
    parser.add_argument("--mode", choices=["run", "program"], default="run",
                        help="run: one autocode run (default); program: `autocode program run` with the "
                             "scenario's program_manifest, gates served per child run")
    parser.add_argument("--score-only", type=Path, metavar="PROJECT",
                        help="do not drive anything: score an already delivered workspace with the oracle")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list:
        for scenario_id, spec in sorted(scenarios.registry().items()):
            print(f"{scenario_id:<12} {spec.get('task_type', 'live'):<13} {spec['title']}")
        return 0
    if not args.scenario:
        print("a scenario id is required (see --list)", file=sys.stderr)
        return 2
    spec = scenarios.scenario(args.scenario)
    profile = profiles.resolve(args.profile)
    profile['limits'] = {name: getattr(args, name) for name in TRIAL_LIMITS}
    if spec.get('synthetic_seed_only') and (args.source or spec.get('seed_from')):
        print(f'{args.scenario} requires its frozen synthetic seed; --source is not allowed', file=sys.stderr)
        return 2

    if args.score_only:
        project = args.score_only.resolve()
        bundle = Bundle(args.scenario)
        bundle.log("score_only", project=str(project))
        oracle = spec["oracle"](project)
        run = {"state": {"status": "SCORE_ONLY"}, "steps": [], "run_dir": project,
               "mode": "score-only", "product": project}
        write_report(bundle, args.scenario, spec, "none", {"provider": "none"}, oracle, run)
        try:
            bundle.finish(oracle.status, oracle.summary)
        except AssertionError:
            pass
        print(f"{args.scenario} [score-only] {oracle.status}: {oracle.summary}")
        print(f"evidence: {bundle.dir}")
        return 0 if oracle.status == scenarios.PASS else 2 if oracle.status == scenarios.DEFERRED else 1

    if args.mode == "program" and not spec.get("program_manifest"):
        print(f"{args.scenario} has no program_manifest; run it with --mode run", file=sys.stderr)
        return 2

    if profile["provider"] != "fixture" and not args.i_authorize_live_model_spend:
        print(f"refusing live spend: rerun with --i-authorize-live-model-spend "
              f"(profile={args.profile}, model={profiles.describe(profile)})", file=sys.stderr)
        return 2

    bundle = Bundle(args.scenario)
    bundle.log("trial_started", scenario=args.scenario, profile=args.profile,
               model=profiles.describe(profile), authorized=args.i_authorize_live_model_spend,
               deployment_authorized=args.authorize_deployment)

    temp: tempfile.TemporaryDirectory | None = None
    if args.workspace:
        root = Path(args.workspace).resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        temp = tempfile.TemporaryDirectory(prefix="autopilot-live-")
        root = Path(temp.name).resolve()

    started = time.monotonic()
    baseline_revision = None
    baseline_content_sha256 = None
    oracle_hash = None
    workload_kind = "repository" if (args.source or spec.get("seed_from")) else "synthetic"
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
        bundle.log("workspace_ready", project=str(project), mode=args.mode)
        oracle_path = Path(scenarios.__file__).resolve()
        oracle_hash = hashlib.sha256(oracle_path.read_bytes()).hexdigest()
        baseline_snapshot = util.snapshot(project)
        baseline_revision = baseline_snapshot['revision']
        baseline_content_sha256 = util.digest(baseline_snapshot['files'])
        bundle.log('oracle_fingerprint', path=str(oracle_path), sha256=oracle_hash,
                   baseline_revision=baseline_revision, baseline_content_sha256=baseline_content_sha256)
        if args.mode == "program":
            run = drive_program(project, root, profile, spec["program_manifest"],
                                args.budget_stages, args.timeout, bundle,
                                authorize_deployment=args.authorize_deployment)
        else:
            run = drive(project, root, profile, spec["task"],
                        args.budget_stages, args.timeout, bundle)
        run["mode"] = args.mode
        if hashlib.sha256(oracle_path.read_bytes()).hexdigest() != oracle_hash:
            raise TrialError('Oracle source changed during the trial; result cannot be trusted')
        run['candidate_revision'] = util.snapshot(project)['revision']
        run['oracle_sha256'] = oracle_hash
        # A program's product is the merged integration branch, never the untouched project root.
        result = judge(run, spec, run.get("product", project), bundle)
        if (hashlib.sha256(oracle_path.read_bytes()).hexdigest() != oracle_hash
                or util.snapshot(project)['revision'] != run['candidate_revision']):
            raise TrialError('Oracle or candidate changed during scoring; result cannot be trusted')
        run.update(baseline_revision=baseline_revision, baseline_content_sha256=baseline_content_sha256,
                   workload_kind=workload_kind,
                   elapsed_seconds=time.monotonic() - started)
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
        run_dir = _discover_run_dir(project)
        state = load_state(run_dir) if run_dir else {}
        product = project
        if args.mode == "program":
            paths = list((project / ".autocode/programs").glob("*/state.json"))
            if len(paths) == 1:
                saved = json.loads(paths[0].read_text())
                state = {"status": saved.get("status"), "program": saved}
                # Nothing is created before the agreement is approved: integration may be null.
                product = Path((saved.get("integration") or {}).get("workspace") or project)
        oracle = spec["oracle"](product)
        result = scenarios.OracleResult(scenarios.ERROR, f"{error}; {oracle.summary}", oracle.checks)
        bundle.state("final", state)
        run = {"state": state, "steps": getattr(error, "steps", []), "error": str(error),
               "run_dir": run_dir, "product": product, "mode": args.mode,
               "baseline_revision": baseline_revision,
               "baseline_content_sha256": baseline_content_sha256,
               "oracle_sha256": oracle_hash, "workload_kind": workload_kind,
               "elapsed_seconds": time.monotonic() - started}
        write_report(bundle, args.scenario, spec, args.profile, profile, result, run)
        bundle.finish(scenarios.ERROR, str(error))
        print(f"{args.scenario} ERROR: {error}", file=sys.stderr)
        print(f"evidence: {bundle.dir}", file=sys.stderr)
        return 1
    finally:
        if temp is not None:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
