"""Drive one AutoCode run and show what it is doing while it works.

Starts a run, or continues one, and prints AutoCode's own output as it arrives:
each stage starting and finishing, and each stage's live model activity (tools
started and finished, new model text), which AutoCode writes to stderr. Every
--interval seconds it also prints the run's status and progress line. When the
run stops, it says what the run needs from you.

With --auto-approve it approves a displayed plan by its token and continues, as
an unattended driver would. Without it, it stops at the plan so a person can
read it (autocode --run-dir RUN --show-goal) and approve it; run it again
afterwards to continue.

    # Start a new run (in a fresh worktree, as plain `autocode "BRIEF"` does):
    python3 examples/watch/watch_run.py --workspace ~/proj "Add a --json flag to export"
    # Continue the workspace's unfinished run, approving plans automatically, with
    # the flags after -- passed to every start and continue:
    python3 examples/watch/watch_run.py --workspace ~/proj --auto-approve -- --provider claude

It uses only the documented CLI (docs/task-run.md): start, continue, --status
and --approve-goal. --log FILE also appends every line it prints to FILE.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time

CHECKOUT_CLI = Path(__file__).resolve().parents[2] / "tools" / "autocode.py"
STATUS_TIMEOUT = 120


class Output:
    """Lines stamped with the seconds since the watcher started, to the terminal and an optional log."""

    def __init__(self, log: str | None):
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.log = open(log, "a", encoding="utf-8") if log else None

    def line(self, text: str) -> None:
        stamp = f"t={time.monotonic() - self.started:.0f}s"
        with self.lock:
            for part in str(text).splitlines() or [""]:
                row = f"{stamp:<8} {part}"
                print(row, flush=True)
                if self.log:
                    self.log.write(row + "\n")
                    self.log.flush()


def read_status(cli: list[str], *where: str) -> dict | None:
    """The full --status JSON (the view is under "view"), or None when no run status can be read."""
    try:
        proc = subprocess.run([*cli, "--status", *where], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=STATUS_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = proc.stdout
    # A stale-checkpoint warning can precede the JSON.
    for start in (0, text.find("{")):
        try:
            found = json.loads(text[start:]) if start >= 0 else None
        except ValueError:
            continue
        if isinstance(found, dict) and isinstance(found.get("view"), dict):
            return found
    return None


def summary(view: dict) -> str:
    line = f"status={view.get('status')} next={view.get('next_stage')}"
    progress = (view.get("progress") or {}).get("line")
    cost = ((view.get("usage") or {}).get("cost_usd") or {}).get("reported")
    return line + (f" · {progress}" if progress else "") + (f" · ${cost:.2f} so far" if cost else "")


def describe(need: dict, view: dict, run_dir: str) -> list[str]:
    """What a stopped run asks of a person, in a few lines."""
    kind = need.get("kind")
    rows = [f"the run needs you: {kind}"]
    if kind == "approve_plan":
        rows.append(f"  read the plan:  autocode --run-dir {shlex.quote(run_dir)} --show-goal")
        rows.append(f"  approve it:     autocode --run-dir {shlex.quote(run_dir)} --approve-goal {need.get('token')}")
        rows.append("  then run this watcher again to continue")
    for question in need.get("questions") or []:
        default = question.get("proposed_default")
        rows.append(f"  {question.get('id')}: {question.get('question')}" + (f" (default: {default})" if default else ""))
    if kind == "answer" and need.get("questions") and need.get("resolver_token"):
        rows.append(f"  answer:  autocode --run-dir {shlex.quote(run_dir)} --answer ID=TEXT"
                    f" --resolver-token {need['resolver_token']}")
        rows.append("  then run this watcher again to continue")
    reason = need.get("reason") or view.get("stop_reason")
    if reason:
        rows.append(f"  {str(reason)[:1000]}")
    if kind not in ("approve_plan", "answer"):
        rows.append(f"  details and next steps:  autocode --run-dir {shlex.quote(run_dir)} --status")
    return rows


class Watcher:
    def __init__(self, cli: list[str], out: Output, interval: float):
        self.cli, self.out, self.interval = cli, out, interval
        self.run_dir: str | None = None
        # AUTOCODE_VERBOSE=1 turns the live stage activity on for AutoCode versions that default it off.
        self.env = {**os.environ, "AUTOCODE_VERBOSE": "1", "PYTHONUNBUFFERED": "1"}

    def launch(self, command: list[str]) -> int:
        """Run one starting or continuing command, echoing its output as it arrives, with a status line
        every interval. Returns its exit code: 0 complete, 2 stopped for input or rejected."""
        child = subprocess.Popen([*self.cli, *command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, text=True, bufsize=1, env=self.env)
        finished = threading.Event()
        poller = threading.Thread(target=self.poll, args=(finished,), daemon=True)
        poller.start()
        try:
            for raw in child.stdout:
                text = raw.rstrip("\n")
                if text.startswith("Run: "):
                    self.run_dir = text[len("Run: "):].strip()
                self.out.line("| " + text)
            return child.wait()
        except KeyboardInterrupt:
            # Ctrl-C also reached AutoCode, which saves the run and stops; wait for it to finish doing so.
            self.out.line("watch: interrupted; waiting for AutoCode to save the run and stop")
            try:
                child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                child.terminate()
            raise
        finally:
            finished.set()
            poller.join(timeout=5)

    def poll(self, finished: threading.Event) -> None:
        while not finished.wait(self.interval):
            if self.run_dir:
                found = read_status(self.cli, "--run-dir", self.run_dir)
                if found and not finished.is_set():
                    self.out.line(summary(found["view"]))

    def approve(self, token: str) -> int:
        self.out.line(f"AUTO-APPROVE {token}")
        proc = subprocess.run([*self.cli, "--approve-goal", token, "--run-dir", self.run_dir],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, env=self.env)
        for text in (proc.stdout + proc.stderr).splitlines():
            self.out.line("| " + text)
        self.out.line(f"approve exit {proc.returncode}")
        return proc.returncode


def parse(argv):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Everything after the first -- goes to AutoCode untouched.
    options = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[:argv.index("--")] if "--" in argv else argv
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     usage="%(prog)s [options] [BRIEF] [-- AUTOCODE_FLAGS...]")
    parser.add_argument("brief", nargs="?", help="start a new run with this request; without it, continue a run")
    parser.add_argument("--workspace", default=".", help="the project (default: the current directory)")
    parser.add_argument("--run-dir", help="continue this run instead of the workspace's unfinished one")
    parser.add_argument("--interval", type=float, default=30, help="seconds between status lines (default 30)")
    parser.add_argument("--auto-approve", action="store_true",
                        help="approve each displayed plan without a person reading it, then continue")
    parser.add_argument("--log", help="also append every line to this file")
    parser.add_argument("--autocode", help="command that runs AutoCode (default: this checkout's tools/autocode.py)")
    args = parser.parse_args(argv)
    args.options = options
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.brief and args.run_dir:
        parser.error("give a brief to start a run, or --run-dir to continue one, not both")
    return args


def main(argv=None) -> int:
    args = parse(argv)
    cli = shlex.split(args.autocode) if args.autocode else [sys.executable, str(CHECKOUT_CLI)]
    out = Output(args.log)
    watcher = Watcher(cli, out, args.interval)
    workspace = str(Path(args.workspace).expanduser().resolve())
    if args.brief:
        out.line(f"watch: starting a new run in {workspace}")
        command = [args.brief, "--workspace", workspace, "--no-chat", *args.options]
    else:
        where = ["--run-dir", str(Path(args.run_dir).expanduser().resolve())] if args.run_dir else ["--workspace", workspace]
        found = read_status(cli, *where)
        if not found:
            out.line("watch: no run to continue here; give a brief to start one, or --run-dir")
            return 2
        watcher.run_dir = found.get("run_dir")
        out.line(f"watch: continuing {watcher.run_dir}")
        out.line(summary(found["view"]))
        if found["view"].get("done"):
            out.line("watch: this run is already complete")
            return 0
        command = None
    stalled = None
    try:
        while True:
            if command is not None:
                code = watcher.launch(command)
                out.line(f"watch: AutoCode exited {code}")
            command = None
            found = read_status(cli, "--run-dir", watcher.run_dir) if watcher.run_dir else None
            if not found:
                out.line("watch: no run status could be read; see AutoCode's output above")
                return 2
            view = found["view"]
            out.line(summary(view))
            if view.get("done"):
                out.line("watch: run complete")
                return 0
            need = view.get("needs") or {}
            continuing = ["--no-chat", "--run-dir", watcher.run_dir, *args.options]
            if need.get("kind") == "continue":
                # Stop rather than relaunch forever when a continue changes nothing (as TaskRun does).
                position = tuple(view.get(key) for key in ("status", "next_stage", "iteration", "phase"))
                if position == stalled:
                    out.line("watch: continuing made no progress; inspect the run with --status")
                    return 2
                stalled, command = position, continuing
            elif need.get("kind") == "approve_plan" and args.auto_approve and need.get("token"):
                if watcher.approve(need["token"]) != 0:
                    return 2
                stalled, command = None, continuing
            else:
                for row in describe(need, view, watcher.run_dir):
                    out.line("watch: " + row)
                return 2
    except KeyboardInterrupt:
        out.line("watch: stopped; run this again to continue")
        return 130


if __name__ == "__main__":
    sys.exit(main())
