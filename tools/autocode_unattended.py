"""Run AutoCode for an unattended caller, such as another coding agent.

The caller can start a run, continue one that is not paused (a bare relaunch,
never ``autocode resume``, which acknowledges a pause), or read its status. It
can never make a decision that belongs to the operator: answering or delegating
questions, approving a plan, accepting completion, closing a finding, answering
AutoResolver, resuming a pause, retrying or diagnosing a failed stage, or
submitting feedback. AutoCode's
own deterministic controller still decides every transition; this wrapper
only refuses those flags, runs AutoCode once with no terminal input, and
prints the saved status when it stops.

Exit codes follow AutoCode: 0 when an action was saved or the task
completed, 2 when AutoCode stopped for the operator (or refused the call).

`--analyze --run-dir RUN [--workspace W] [--out DIR]` launches nothing. It
reads a saved run and prints what AutoCode did: outcome, acceptance
criteria, findings, stages with their report files, token use, and the code
changes against the task's base commit, plus a summary of AutoCode's
activity log. `--out` also saves the report, the full diff and a copy of
RUN/activity.jsonl.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

# Every flag that records an operator decision or recovers from a pause.
OPERATOR_FLAGS = (
    "--answer", "--feedback", "--follow-up", "--delegate", "--delegate-all", "--reject-assumption",
    "--approve-goal", "--edit-goal", "--approve-review", "--reconcile-review",
    "--accept-completion", "--review-token", "--resume-paused", "--retry-failed-stage",
    "--diagnose-failed-stage", "--retry-builder", "--retry-report", "--abandon-stage",
    "--accept-transport-change", "--planning-review-call-limit", "--migrate-only",
    "--close-finding", "--close-reason", "--resolver-response",
    "--chat",
)
# Subcommands that submit interventions or run other flows.
OPERATOR_SUBCOMMANDS = ("tasks", "ui", "program", "compare-baseline", "capture", "registry", "intervention")

STOP_NOTICE = """\
AUTOCODE STOPPED FOR THE OPERATOR (exit {rc}).
Report the output above to the operator verbatim and stop.
Do not edit files, answer questions, approve, retry or resume on the operator's behalf."""

DONE_NOTICE = """\
AUTOCODE COMPLETED THE TASK.
Analyze the work with: autocode-unattended --analyze --run-dir {run_dir}"""


def refused(argv: list[str]) -> str | None:
    """Return why argv is refused, or None when it is allowed."""
    if argv and argv[0] in OPERATOR_SUBCOMMANDS:
        return f"the '{argv[0]}' subcommand is operator-only"
    if _resume_word(argv):
        # On a paused run `autocode resume` stands for --resume-paused (autocode_args), with its companions.
        return ("'autocode resume' acknowledges a pause, an operator decision; run autocode directly to "
                "make it, or relaunch without the word to continue a run that is not paused")
    for arg in argv:
        if arg == "--":
            break
        if not arg.startswith("--") or len(arg) <= 2:
            continue
        name = arg.split("=", 1)[0]
        # argparse accepts unambiguous prefixes, so also refuse abbreviations.
        # --no-chat is allowed: it is forced anyway.
        if name == "--no-chat":
            continue
        for flag in OPERATOR_FLAGS:
            if flag.startswith(name):
                return f"{flag} is an operator decision; run autocode directly to make it"
    return None


def _resume_word(argv: list[str]) -> bool:
    """Whether autocode reads argv as `autocode resume`; not task text or an option's value (--workspace resume)."""
    words = argv[:argv.index("--")] if "--" in argv else argv
    if "resume" not in words:
        return False
    try:
        from . import autocode_args
    except ImportError:
        import autocode_args
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return autocode_args.is_resume_command(argv)
    except SystemExit:
        # autocode refuses this argv anyway; refuse the word rather than guess.
        return True


def autocode_command() -> list[str]:
    override = os.environ.get("AUTOCODE_UNATTENDED_COMMAND")
    if override:
        return override.split()
    return [sys.executable, str(Path(__file__).resolve().with_name("autocode.py"))]


def option_value(argv: list[str], flag: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg == flag and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None


def git(workspace: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(workspace), *args], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else f"(git {' '.join(args)} failed: {result.stderr.strip()})\n"


def activity_summary(run_dir: Path) -> list[str]:
    """Summarize RUN/activity.jsonl, AutoCode's own always-on activity log."""
    path = run_dir / "activity.jsonl"
    lines = ["", "## Activity", ""]
    try:
        entries = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return lines + ["No activity log (runs before activity logging have none)."]
    calls = [e for e in entries if e.get("event") == "invocation"]
    finished = [e for e in entries if e.get("event") == "stage_finished"]
    stops = [e for e in entries if e.get("event") == "transition" and "status" in e.get("changed", [])
             and e.get("status") not in ("RUNNING",)]
    seconds = sum(e.get("duration_seconds") or 0 for e in finished)
    by_caller: dict[str, int] = {}
    for call in calls:
        by_caller[call.get("caller", "direct")] = by_caller.get(call.get("caller", "direct"), 0) + 1
    lines += [f"Log: `{path.name}` ({len(entries)} events, {entries[0]['at']} to {entries[-1]['at']})" if entries else "Log is empty.",
              f"- Calls that changed the run: {len(calls)} ("
              + ", ".join(f"{count} {caller}" for caller, count in sorted(by_caller.items())) + ")",
              f"- Stages finished: {len(finished)}, {round(seconds, 1)}s in stages, "
              f"{sum(1 for e in finished if e.get('exit_code') not in (0, None))} nonzero exits, "
              f"{sum(1 for e in finished if e.get('timed_out'))} timeouts, "
              f"{sum(1 for e in finished if e.get('rejected'))} rejected",
              "", "Stops and completion:", ""]
    lines += [f"- {e['at']}: {e.get('status')}" + (f" ({e['stop_reason']})" if e.get("stop_reason") else "")
              for e in stops] or ["- none"]
    lines += ["", "Operator decisions and other flagged calls:", ""]
    decisions = [c for c in calls if set(c.get("flags", [])) - {"--workspace", "--run-dir", "--no-chat", "--engine"}]
    lines += [f"- {c['at']} [{c.get('caller', 'direct')}]: {' '.join(c.get('flags', []))}" for c in decisions] or ["- none"]
    return lines


def cost_by_role(stages: list[dict]) -> list[str]:
    """Model calls, seconds and tokens per role (#15). Runner-owned stages spend no tokens."""
    rows: dict[str, dict] = {}
    repairs = 0
    for stage in stages:
        name = str(stage.get("stage") or "")
        if name == "orchestrator" or stage.get("runner_owned"):
            continue  # runner-owned steps (orchestrator, regression_proof) launch no model
        role = stage.get("route_role") or stage.get("role") or name or "unknown"
        repairs += name.endswith("_report_repair")
        row = rows.setdefault(role, {"calls": 0, "seconds": 0.0, "in": 0, "out": 0})
        used = (stage.get("metrics") or {}).get("provider_tokens") or {}
        row["calls"] += 1
        row["seconds"] += stage.get("duration_seconds") or 0
        row["in"] += used.get("input_tokens") or 0
        row["out"] += used.get("output_tokens") or 0
    lines = ["", "## Cost by role", "", "| Role | Model calls | Seconds | Tokens in/out |", "| --- | ---: | ---: | --- |"]
    for role, row in sorted(rows.items(), key=lambda item: -item[1]["calls"]):
        lines.append(f"| {role} | {row['calls']} | {round(row['seconds'], 1)} | {row['in']}/{row['out']} |")
    total = {key: sum(row[key] for row in rows.values()) for key in ("calls", "seconds", "in", "out")}
    lines.append(f"| **total** | {total['calls']} | {round(total['seconds'], 1)} | {total['in']}/{total['out']} |")
    lines += ["", f"Report-format repair calls: {repairs}."]
    return lines


def analyze(run_dir: Path, out: Path | None) -> int:
    """Print a read-only report of a saved run. Launches no AutoCode stage."""
    run_dir = run_dir.resolve()
    state_path = run_dir / "state.json"
    if not state_path.is_file():
        print(f"autocode-unattended: no state.json in {run_dir}", file=sys.stderr)
        return 2
    state = json.loads(state_path.read_text())
    workspace = Path(state.get("workspace") or run_dir.parents[2])
    meta_path = workspace / ".autocode" / "task-workspace.json"
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    base = state.get("base_commit") or meta.get("base_commit")  # the run's own; a shared worktree's file moves on
    lines = [f"# AutoCode run analysis: {state.get('status')}", "",
             f"- Task: {state.get('task')}", f"- Run: `{run_dir}`", f"- Workspace: `{workspace}`",
             f"- Branch: {state.get('task_branch') or '(in place)'}", f"- Base commit: {base or 'unknown'}",
             f"- Iteration: {state.get('iteration')}", f"- Phase: {state.get('phase')}"]
    if state.get("stop_reason"):
        lines.append(f"- Stop reason: {state['stop_reason']}")
    if state.get("status") != "TASK_COMPLETE":
        lines.append("- Note: the run has not completed; this is a snapshot of unfinished work.")
    contract = (state.get("goal_contract") or {}).get("body") or {}
    criteria = contract.get("acceptance_criteria") or state.get("acceptance_criteria") or []
    if contract.get("intended_outcome"):
        lines += ["", "## Intended outcome", "", contract["intended_outcome"]]
    decision = state.get("last_decision") if isinstance(state.get("last_decision"), dict) else {}
    report = decision.get("report") if isinstance(decision.get("report"), dict) else decision
    outcomes = {row.get("id"): row for row in report.get("acceptance_criteria") or [] if isinstance(row, dict)}
    human = set(state.get("human_reviews") or {}) if isinstance(state.get("human_reviews"), dict) else set()
    validation = state.get("validation") if isinstance(state.get("validation"), dict) else {}
    validated = {row.get("id"): row.get("status") for row in validation.get("criterion_results") or []
                 if isinstance(row, dict)}
    lines += ["", "## Acceptance criteria", ""]
    for item in criteria:
        if not isinstance(item, dict):
            lines.append(f"- {item}")
            continue
        outcome = outcomes.get(item.get("id")) or {}
        reviewed = ", human-reviewed" if item.get("id") in human else ""
        # A Validator FAIL and an unchecked criterion must both be visible (#17).
        validator = validated.get(item.get("id"))
        validator_note = f", validator: {validator}" if validator is not None else (
            ", validator: unchecked" if validation.get("source_revision") else "")
        lines.append(f"- **{item.get('id', '?')}** [{outcome.get('status', 'no outcome recorded')}"
                     f"{validator_note}{reviewed}]: "
                     f"{item.get('criterion') or item.get('text') or json.dumps(item)}")
        if item.get("verification_method"):
            lines.append(f"  - Verification: {item['verification_method']}")
        if outcome.get("evidence"):
            lines.append(f"  - Evidence: {outcome['evidence']}")
    if not criteria:
        lines.append("None recorded.")
    if report.get("agreed_limitations"):
        lines += ["", "Agreed limitations: " + "; ".join(map(str, report["agreed_limitations"]))]
    ledger = state.get("findings_ledger") or []
    lines += ["", "## Findings", ""]
    for row in ledger:
        lines.append(f"- {row.get('id')} [{row.get('status')}, {row.get('severity')}, {row.get('source')}, "
                     f"reported {row.get('times_reported', 1)}x]: {row.get('finding')}")
    if not ledger:
        lines.append("None recorded.")
    lines += ["", "## Stages", "",
              "Report paths are relative to the run directory.", "",
              "| # | Stage | Role | Iteration | Finished | Seconds | Exit | Tokens in/out | Report |",
              "| ---: | --- | --- | ---: | --- | ---: | ---: | --- | --- |"]
    for number, stage in enumerate(state.get("stages") or [], 1):
        used = (stage.get("metrics") or {}).get("provider_tokens") or {}
        seconds = stage.get("duration_seconds")
        output = str(stage.get("output") or "")
        if output.startswith(str(run_dir) + os.sep):
            output = output[len(str(run_dir)) + 1:]
        lines.append(f"| {number} | {stage.get('stage')} | {stage.get('role') or ''} | {stage.get('iteration', '')} "
                     f"| {stage.get('finished_at') or stage.get('completed_at') or ''} "
                     f"| {'' if seconds is None else round(seconds, 1)} | {stage.get('exit_code', '')} "
                     f"| {used.get('input_tokens') or 0}/{used.get('output_tokens') or 0} | `{output}` |")
    lines += cost_by_role(state.get("stages") or [])
    lines += activity_summary(run_dir)
    lines += ["", "## Code changes", ""]
    diff = ""
    if base:
        lines += ["Against the base commit, including uncommitted work:", "", "```",
                  git(workspace, "diff", "--stat", base).rstrip() or "(no changes to tracked files)", "```"]
        diff = git(workspace, "diff", base)
    status = git(workspace, "status", "--porcelain", "--untracked-files=all").rstrip()
    untracked = [line[3:] for line in status.splitlines() if line.startswith("?? ") and not line[3:].startswith(".autocode/")]
    if untracked:
        lines += ["", "Untracked files (new, not committed): " + ", ".join(f"`{name}`" for name in untracked)]
        for name in untracked:
            # --no-index exits 1 when files differ, so read its output directly.
            diff += subprocess.run(["git", "-C", str(workspace), "diff", "--no-index", "--", os.devnull, name],
                                   stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False).stdout
    lines += ["", "Commits on the task branch:", "", "```",
              (git(workspace, "log", "--oneline", f"{base}..HEAD") if base else git(workspace, "log", "--oneline", "-10")).rstrip() or "(none)",
              "```"]
    text = "\n".join(lines) + "\n"
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "analysis.md").write_text(text)
        (out / "changes.diff").write_text(diff)
        if (run_dir / "activity.jsonl").is_file():
            shutil.copy2(run_dir / "activity.jsonl", out / "activity.jsonl")
        text += f"\nSaved `{out / 'analysis.md'}` and the full diff to `{out / 'changes.diff'}`.\n"
    sys.stdout.write(text)
    return 0


def run(argv: list[str]) -> int:
    if "--analyze" in argv:
        parser = argparse.ArgumentParser(prog="autocode-unattended --analyze", allow_abbrev=False)
        parser.add_argument("--analyze", action="store_true")
        parser.add_argument("--run-dir", type=Path, required=True)
        parser.add_argument("--workspace", type=Path, help="Accepted for symmetry; the run records its own")
        parser.add_argument("--out", type=Path)
        args = parser.parse_args(argv)
        return analyze(args.run_dir, args.out)
    reason = refused(argv)
    if reason:
        print(f"autocode-unattended: refused: {reason}", file=sys.stderr)
        return 2
    command = autocode_command()
    status_only = "--status" in argv or "--dry-run" in argv
    run_dir = option_value(argv, "--run-dir")
    # AutoCode's activity log records calls made through this wrapper as "unattended".
    env = {**os.environ, "AUTOCODE_CALLER": "unattended"}
    with subprocess.Popen([*command, *argv, "--no-chat"], stdin=subprocess.DEVNULL, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            match = re.match(r"Run: (.+)$", line.rstrip("\n"))
            if match:
                run_dir = match.group(1)
    rc = process.returncode
    if status_only:
        return rc
    if run_dir:
        print("\n--- autocode status ---", flush=True)
        status = [*command, "--run-dir", run_dir, "--status"]
        workspace = option_value(argv, "--workspace")
        if workspace:
            status += ["--workspace", workspace]
        result = subprocess.run(status, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, check=False)
        sys.stdout.write(result.stdout)
    if rc != 0:
        print("\n" + STOP_NOTICE.format(rc=rc), flush=True)
    elif run_dir and completed(Path(run_dir)):
        print("\n" + DONE_NOTICE.format(run_dir=run_dir), flush=True)
    return rc


def completed(run_dir: Path) -> bool:
    try:
        return json.loads((run_dir / "state.json").read_text()).get("status") == "TASK_COMPLETE"
    except (OSError, ValueError):
        return False


def cli() -> int:
    if sys.argv[1:2] in (["-h"], ["--help"]):
        argparse.ArgumentParser(
            prog="autocode-unattended",
            description=__doc__.split("\n\n")[1],
            epilog="Takes AutoCode's own arguments, minus operator decisions: "
                   + ", ".join(OPERATOR_FLAGS)
                   + ". Use --analyze --run-dir RUN [--out DIR] to report on a saved run without launching anything."
            ).print_help()
        return 0
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(cli())
