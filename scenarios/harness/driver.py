"""Drive AutoCode's public CLI through one scenario, serving its gates like a user.

The driver only calls the CLI. It decides what to do from the status view that
`autocode --status` reports (docs/task-run.md), and reads ``state.json`` only
afterwards, for evidence and metrics. It never writes state. Gates served:
clarifying questions (answered with AutoCode's proposed default, and recorded),
plan approval, human-review acceptance, and planning-budget feedback. A pause
that needs a person is left for the verdict to judge.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import profiles
from .project import overlay_paths

REPO = Path(__file__).resolve().parents[2]
FAKE_PROVIDER = Path(__file__).resolve().parent / "fake_codex.py"
# Codex-engine flags for fake runs. The fake ignores models, but AutoCode's
# Codex path wants bare GPT names and distinct builder and verifier models.
FAKE_FLAGS = ["--engine", "codex", "--joint-planning", "--astra-model", "gpt-6-astra",
              "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol",
              "--completion-model", "gpt-6-astra", "--glm-model", "gpt-5.6-sol",
              "--plan-reviewer-model", "gpt-6-astra"]


class DriveError(RuntimeError):
    """The harness could not take the run any further."""


def fake_setup(scenario, root: Path, solution: Path) -> tuple[list[str], dict]:
    """Put the scripted provider on PATH as ``codex``; return CLI flags and environment."""
    bindir = root / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FAKE_PROVIDER, bindir / "codex")
    (bindir / "codex").chmod(0o755)
    config = root / "fake-config.json"
    config.write_text(json.dumps({"title": scenario.title, "brief": scenario.brief,
                                  "reference": str(solution), "check": scenario.fake_check,
                                  "paths": overlay_paths(solution), "fault": scenario.fake_fault}))
    return [*FAKE_FLAGS, *scenario.fake_flags], {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
                        "SCENARIO_FAKE_CONFIG": str(config)}


def live_setup(profile_name: str) -> tuple[list[str], dict]:
    return profiles.flags(profiles.resolve(profile_name)), {}


class Driver:
    def __init__(self, project: Path, root: Path, flags: list[str], env: dict, *,
                 autocode: list[str], max_steps: int, timeout_seconds: int):
        self.project, self.root, self.flags, self.autocode = project, root, flags, autocode
        self.env = {**os.environ, "AUTOCODE_HOME": str(root / "registry"), "PYTHONDONTWRITEBYTECODE": "1", **env}
        self.max_steps, self.deadline = max_steps, time.monotonic() + timeout_seconds
        self.steps: list[dict] = []
        self.answers: list[dict] = []
        self.run_dir: Path | None = None
        self.log = root / "steps.jsonl"

    def state(self) -> dict:
        """The saved state, read only for evidence and metrics after the run."""
        path = self.run_dir / "state.json" if self.run_dir else None
        return json.loads(path.read_text()) if path and path.is_file() else {}

    def view(self) -> dict:
        """The run's status view from `autocode --status` (docs/task-run.md)."""
        proc = self.call("status", "--status", action=True, record=False)
        try:
            return json.loads(proc.stdout)["view"]
        except (ValueError, KeyError) as error:
            raise DriveError(f"--status returned no status view: {error}") from None

    def call(self, kind: str, *extra: str, task: str | None = None, action: bool = False,
             record: bool = True) -> subprocess.CompletedProcess:
        """One CLI invocation. Actions must exit 0; launches may also exit 2 (stopped for input)."""
        if record and len(self.steps) >= self.max_steps:
            raise DriveError(f"step budget used up after {len(self.steps)} CLI calls")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DriveError(f"time budget used up after {len(self.steps)} CLI calls")
        cmd = [*self.autocode, *([task] if task else []), "--workspace", str(self.project),
               *(["--run-dir", str(self.run_dir)] if self.run_dir else ["--in-place"]),
               *([] if action else ["--no-chat", *self.flags]), *extra]
        started = time.monotonic()
        try:
            proc = subprocess.run(cmd, env=self.env, cwd=self.root, capture_output=True, text=True,
                                  timeout=remaining)
        except subprocess.TimeoutExpired:
            raise DriveError(f"{kind} was still running when the time budget ran out") from None
        if record:
            step = {"kind": kind, "args": list(extra), "exit": proc.returncode,
                    "seconds": round(time.monotonic() - started, 1),
                    "stdout_tail": proc.stdout[-1500:], "stderr_tail": proc.stderr[-1500:]}
            self.steps.append(step)
            with self.log.open("a") as handle:
                handle.write(json.dumps(step) + "\n")
        # Usage errors also exit 2, so recognize argparse's message rather than trusting the code.
        usage_error = proc.returncode == 2 and proc.stderr.startswith("usage:")
        if usage_error or proc.returncode not in ((0,) if action else (0, 2)):
            raise DriveError(f"{kind} exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-500:]}")
        return proc

    def drive(self, brief: str) -> dict:
        self.call("start", task=brief)
        runs = self.project / ".autocode" / "runs"
        candidates = sorted(runs.glob("*/state.json"), key=lambda path: path.stat().st_mtime) if runs.is_dir() else []
        if not candidates:
            last = self.steps[-1]
            raise DriveError("the first CLI call did not create a run: "
                             + (last["stderr_tail"] or last["stdout_tail"]).strip()[-500:])
        self.run_dir = candidates[-1].parent
        while True:
            view = self.view()
            need = view["needs"]
            if view["done"] or need["kind"] == "resume":
                return view
            if need["kind"] == "continue":
                self.call("resume")
                after = self.view()
                keys = ("status", "next_stage", "iteration", "phase")
                if not after["done"] and all(after[key] == view[key] for key in keys):
                    raise DriveError(f"no progress at {view['status']!r} (next_stage={view['next_stage']!r})")
            else:
                self.serve(need)

    def serve(self, need: dict) -> None:
        """Answer one gate the way a cooperative user would, recording every answer."""
        kind = need["kind"]
        if kind == "approve_plan":
            self.call("approve-plan", "--approve-goal", need["token"], action=True)
        elif kind == "answer":
            for question in need["questions"]:
                options = question.get("options") or []
                answer = question.get("proposed_default") or (options[0] if options else "yes")
                self.answers.append({"id": question["id"], "question": question.get("question"),
                                     "why": question.get("why"), "answer": answer})
                self.call("answer", "--answer", f"{question['id']}={answer}", action=True)
        elif kind == "review":
            for criterion in need["criteria"]:
                self.answers.append({"id": criterion, "question": need.get("question"), "answer": "approved"})
                self.call("approve-review", "--approve-review", criterion, "--review-token", need["token"],
                          action=True)
        elif kind == "planning_budget":
            self.call("feedback", "--feedback", "The previous planning cycle used up its review budget. "
                      "Produce a complete final plan now and finalize it.", action=True)
        else:
            raise DriveError(f"no way to serve a {kind!r} gate")


def default_autocode() -> list[str]:
    return [sys.executable, str(REPO / "tools" / "autocode.py")]


def metrics(state: dict) -> dict:
    """Stage counts, model time and tokens, from the run's own stage records.

    ``by_stage`` breaks count and seconds down per stage name, so a slow run shows
    where its time went; ``report_repairs`` counts the rounds spent only fixing the
    format of another stage's report (issue #15 names them as trimming candidates).
    """
    stages = state.get("stages") or []
    tokens = {"input": 0, "output": 0, "unknown_stages": 0}
    by_stage: dict[str, dict] = {}
    for stage in stages:
        usage = (stage.get("metrics") or {}).get("provider_tokens") or {}
        if usage.get("input_tokens") is None and stage.get("stage") != "orchestrator":
            tokens["unknown_stages"] += 1
        tokens["input"] += usage.get("input_tokens") or 0
        tokens["output"] += usage.get("output_tokens") or 0
        row = by_stage.setdefault(stage.get("stage") or "?", {"count": 0, "seconds": 0.0})
        row["count"] += 1
        row["seconds"] = round(row["seconds"] + (stage.get("duration_seconds") or 0), 1)
    # Stages the runner does itself (orchestration, regression proof, resolver receipts) call no model.
    model_stage_names = [stage.get("stage") for stage in stages
                         if not stage.get("runner_owned") and stage.get("stage") != "orchestrator"]
    return {"stages": len(stages), "stage_names": [stage.get("stage") for stage in stages],
            "model_stages": len(model_stage_names), "model_stage_names": model_stage_names,
            "model_seconds": round(sum(stage.get("duration_seconds") or 0 for stage in stages), 1),
            "report_repairs": sum(1 for name in model_stage_names if str(name).endswith("_report_repair")),
            "by_stage": by_stage, "tokens": tokens}
