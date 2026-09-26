#!/usr/bin/env python3
"""Drive one AutoCode run and record how the tool itself behaves.

Tracks stage transitions, pause reasons, model routes, and wall-clock per stage
into a JSONL log so a later review can judge the orchestrator, not just the
product output. Human gates (--show-goal / --approve-goal) are served from
state.json; no canned answers.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def log(event: dict, log_path: Path) -> None:
    event = {"ts": time.time(), **event}
    with log_path.open("a") as fh:
        fh.write(json.dumps(event) + "\n")
    print(f"[track] {event.get('kind')} {event.get('detail', '')}", flush=True)


def read_state(run_dir: Path) -> dict:
    path = run_dir / "state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def find_run_dir(workspace: Path, task_slug: str, prefer_newest: bool = True) -> Path | None:
    root = workspace / ".autocode" / "runs"
    if not root.is_dir():
        return None
    candidates = [p for p in root.iterdir() if p.is_dir() and (p / "state.json").exists()]
    if not candidates:
        return None
    # Prefer an exact slug match on the directory name (new runs embed the task slug).
    slug = task_slug.lower()[:60].replace(" ", "-")
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in slug)
    for cand in candidates:
        if slug and slug[:30] in cand.name.lower():
            return cand
    for cand in candidates:
        state = read_state(cand)
        if state and (state.get("task") or "").strip().startswith(task_slug.strip()[:40]):
            return cand
    if prefer_newest:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    return candidates[0]


def serve_gate(run_dir: Path, autocode_bin: str, workspace: Path, log_path: Path, timeout: int) -> None:
    state = read_state(run_dir)
    status = state.get("status") or ""
    if status != "AWAITING_GOAL_APPROVAL":
        return
    shown = subprocess.run(
        [sys.executable, autocode_bin, "--workspace", str(workspace), "--run-dir", str(run_dir), "--show-goal"],
        capture_output=True, text=True, timeout=timeout,
    )
    log({"kind": "show_goal", "detail": shown.stdout[-500:], "rc": shown.returncode}, log_path)
    token = None
    for line in (shown.stdout or "").splitlines():
        if "r" in line and ":" in line and len(line) < 120:
            # token looks like rN:<hash>
            for part in line.split():
                if part.startswith("r") and ":" in part:
                    token = part
    if not token:
        # fall back to displayed_goal in state
        token = state.get("displayed_goal")
    if not token:
        log({"kind": "gate_error", "detail": "no approval token"}, log_path)
        return
    approved = subprocess.run(
        [sys.executable, autocode_bin, "--workspace", str(workspace), "--run-dir", str(run_dir),
         "--approve-goal", token],
        capture_output=True, text=True, timeout=timeout,
    )
    log({"kind": "approve_goal", "detail": approved.stdout[-500:], "rc": approved.returncode, "token": token}, log_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Track an AutoCode run's tool behavior.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--task", default="", help="Task text (or use --task-file)")
    parser.add_argument("--task-file", type=Path, help="Read the task text from a file")
    parser.add_argument("--model-flags", nargs=argparse.REMAINDER, default=[],
                        help="Flags after this are passed through to autocode")
    parser.add_argument("--run-dir", type=Path, help="Watch this run dir instead of discovering one")
    parser.add_argument("--poll", type=float, default=20.0)
    parser.add_argument("--max-seconds", type=float, default=3600.0)
    parser.add_argument("--step-timeout", type=float, default=900.0)
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    autocode_bin = str(HERE / "autocode.py")
    track_dir = workspace / ".autocode" / "tracking"
    track_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = track_dir / f"track-{stamp}.jsonl"

    task = args.task_file.read_text().strip() if args.task_file else args.task
    if not task and not args.run_dir:
        parser.error("provide --task, --task-file, or --run-dir")
    if args.run_dir:
        run_dir = args.run_dir.resolve()
        if not (run_dir / "state.json").exists():
            parser.error(f"--run-dir {run_dir} has no state.json")
        proc = None
        log({"kind": "watch_existing", "detail": str(run_dir)}, log_path)
    else:
        cmd = [sys.executable, autocode_bin, task, "--workspace", str(workspace), "--no-chat"]
        if args.in_place:
            cmd.append("--in-place")
        # strip a leading -- separator from REMAINDER
        flags = [f for f in args.model_flags if f != "--"]
        cmd.extend(flags)

        log({"kind": "launch", "detail": " ".join(cmd), "models": flags}, log_path)
        env = os.environ.copy()
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        proc = subprocess.Popen(cmd, cwd=str(workspace), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    if not args.run_dir:
        run_dir = None
    started = time.time()
    last_status = None
    last_stage = None
    stage_started = {}
    idle = 0.0

    while True:
        if proc is not None and proc.poll() is not None:
            tail = (proc.stdout.read() or "")[-2000:]
            log({"kind": "process_exit", "detail": f"rc={proc.returncode}", "tail": tail}, log_path)
            break
        if proc is None and run_dir:
            state = read_state(run_dir)
            status = state.get("status") or ""
            if status in ("TASK_COMPLETE", "COMPLETE", "REWORK_REQUIRED", "PLAN_REWORK_REQUIRED"):
                log({"kind": "watch_terminal", "detail": status}, log_path)
                break
        if time.time() - started > args.max_seconds:
            if proc is not None:
                proc.kill()
            log({"kind": "timeout", "detail": f"max_seconds={args.max_seconds}"}, log_path)
            break

        if run_dir is None and task:
            run_dir = find_run_dir(workspace, task[:80])
            if run_dir:
                log({"kind": "run_dir", "detail": str(run_dir)}, log_path)

        if run_dir:
            state = read_state(run_dir)
            status = state.get("status")
            active = state.get("active_stage") or {}
            stage = active.get("stage") or state.get("next_stage")
            models = (state.get("settings") or {}).get("roles") or state.get("models") or {}
            if status != last_status:
                log({"kind": "status", "detail": status, "models": models}, log_path)
                last_status = status
                try:
                    import score_autocode_run as scorer
                    report = scorer.score_run(run_dir)
                    snap = track_dir / f"score-snap.jsonl"
                    with snap.open("a") as fh:
                        fh.write(json.dumps({
                            "ts": time.time(), "status": status,
                            "scores": {k: v["score"] for k, v in report["scores"].items()},
                            "est_usd": report["token_usage"]["estimated_api_equivalent_usd"],
                            "totals": report["token_usage"]["totals"],
                        }) + "\n")
                except Exception as exc:
                    log({"kind": "score_error", "detail": str(exc)}, log_path)
            if stage != last_stage:
                if last_stage and last_stage in stage_started:
                    log({"kind": "stage_end", "detail": last_stage,
                         "seconds": round(time.time() - stage_started[last_stage], 1)}, log_path)
                if stage:
                    stage_started[stage] = time.time()
                log({"kind": "stage", "detail": stage}, log_path)
                last_stage = stage
                idle = 0.0
            else:
                idle += args.poll

            if status == "AWAITING_GOAL_APPROVAL":
                serve_gate(run_dir, autocode_bin, workspace, log_path, int(args.step_timeout))
            if status in ("TASK_COMPLETE", "COMPLETE"):
                log({"kind": "complete", "detail": status}, log_path)
                break
            if status and status.startswith("PAUSED"):
                log({"kind": "paused", "detail": status,
                     "error": state.get("error"),
                     "next": state.get("next_stage")}, log_path)
                # one resume attempt for interrupted; leave other pauses for the operator
                if status == "PAUSED_INTERRUPTED":
                    subprocess.run(
                        [sys.executable, autocode_bin, "--workspace", str(workspace),
                         "--run-dir", str(run_dir), "--resume-paused", "--no-chat"],
                        timeout=args.step_timeout,
                    )
                    log({"kind": "resume_attempt", "detail": "PAUSED_INTERRUPTED"}, log_path)

        time.sleep(args.poll)

    # final summary
    if run_dir:
        state = read_state(run_dir)
        summary = {
            "kind": "summary",
            "status": state.get("status"),
            "next_stage": state.get("next_stage"),
            "iterations": state.get("iteration"),
            "stages": [(s.get("stage"), s.get("name")) for s in (state.get("stages") or [])],
            "run_dir": str(run_dir),
            "wall_seconds": round(time.time() - started, 1),
        }
        log(summary, log_path)
        print(json.dumps(summary, indent=2))
        # final scorecard
        try:
            import score_autocode_run as scorer
            report = scorer.score_run(run_dir)
            out = track_dir / f"score-{run_dir.name}.md"
            out.write_text(scorer.render(report))
            (track_dir / f"score-{run_dir.name}.json").write_text(json.dumps(report, indent=2))
            log({"kind": "score", "detail": str(out),
                 "scores": {k: v["score"] for k, v in report["scores"].items()},
                 "est_usd": report["token_usage"]["estimated_api_equivalent_usd"]}, log_path)
        except Exception as exc:
            log({"kind": "score_error", "detail": str(exc)}, log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
