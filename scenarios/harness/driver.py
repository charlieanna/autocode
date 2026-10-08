"""Drive AutoCode's public CLI through one scenario, serving its gates like a user.

The driver only calls the CLI. It decides what to do from the status view that
`autocode --status` reports (docs/task-run.md), and reads ``state.json`` only
afterwards, for evidence and metrics. It never writes state. Gates served:
clarifying questions (answered with AutoCode's proposed default, and recorded),
plan approval, human-review acceptance, and planning-budget feedback. A pause
that needs a person is left for the verdict to judge, and so is an AutoResolver
escalation that it could not continue safely (``PERSON_ONLY_SCOPES``): answering
one with a proposed default would hide an honest stop. Only a scenario's explicit
``[fake] answers`` answer such a request (for example the model a quota-stopped
role continues on): they are the person's own decision, never a default, so the
driver then resumes the pause that answer leaves, once.

A scenario with follow-up turns (issue #51) continues the same run: once it
completes, the driver says the next turn's message with ``--follow-up`` and
drives on. Turns follow completion only, because ``--follow-up`` continues only a
finished run (docs/cli.md); a run that stops first never hears the next turn
(``TurnNotReached``). ``turn_marks`` records where each turn began, so the run
record can be split per turn afterwards, including what each turn changed in the
workspace (``workspace_files``: read from disk, never from AutoCode's state).
A file a later turn overwrites (a design review revised in place) is gone by the
end, so the files each turn changed are also copied, as that turn left them, to
``turn-files/<turn>/`` in the evidence directory (``keep_turn_files``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import attempts, profiles
from .processes import CallTimeout, SupervisionUnavailable, run_cli
from .project import overlay_paths

REPO = Path(__file__).resolve().parents[2]
FAKE_PROVIDER = Path(__file__).resolve().parent / "fake_codex.py"
# Codex-engine flags for fake runs. The fake ignores models, but AutoCode's
# Codex path wants bare GPT names and distinct builder and verifier models.
FAKE_FLAGS = ["--engine", "codex", "--joint-planning", "--astra-model", "gpt-6-astra",
              "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol",
              "--completion-model", "gpt-6-astra", "--glm-model", "gpt-5.6-sol",
              "--plan-reviewer-model", "gpt-6-astra"]


# AutoResolver request scopes that ask a person to look at a stopped run (for
# example, a time limit was reached), not a question about requirements.
PERSON_ONLY_SCOPES = ("operational_exhaustion", "blocker")


def leaves_for_person(need: dict) -> bool:
    """Whether this need is an honest stop the driver must not answer for the user."""
    return need["kind"] in ("resume", "retry_job", "recover_source") or (need["kind"] == "answer"
                                        and need.get("resolver_scope") in PERSON_ONLY_SCOPES)


class DriveError(RuntimeError):
    """The harness could not take the run any further."""


class UserAnswerRequired(DriveError):
    """The question needs a person because the driver has no usable answer."""


class InterruptedDrive(DriveError):
    """The CLI was interrupted; its delivery and remaining usage are ungraded."""


class TurnNotReached(DriveError):
    """The run stopped before a follow-up turn could be said: the product stopped, not the harness.
    ``turn`` is the number of the turn that was never said (2 for the first follow-up)."""

    def __init__(self, message: str, turn: int):
        super().__init__(message)
        self.turn = turn


def _question_answer(question: dict) -> str:
    """Do not mistake an explicit absence of a default for a user decision."""
    default = question.get("proposed_default")
    no_default = re.compile(r"^\s*no\s+(?:proposed\s+|recommended\s+)?default"
                            r"(?:\s*[.;:!?—–-]|\s*$|\s+(?:is\s+)?"
                            r"(?:available|provided|specified|selected|offered)\b)", re.I)
    if isinstance(default, str) and default.strip() and not no_default.match(default):
        return default

    for option in question.get("options") or []:
        if not isinstance(option, str) or not option.strip() or no_default.match(option):
            continue
        if re.match(r"^\s*(?:other\b|(?:specify|provide|choose)\s+(?:another|your own)\b|"
                    r"(?:ask|consult)\s+(?:the\s+)?user\b)",
                    option, re.I):
            continue
        return option
    raise UserAnswerRequired(f"question {question['id']} has no usable default and no concrete option; "
                             "a user answer is required")


def fake_setup(scenario, root: Path, solution: Path) -> tuple[list[str], dict]:
    """Put the scripted provider on PATH as ``codex``; return CLI flags and environment."""
    bindir = root / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FAKE_PROVIDER, bindir / "codex")
    (bindir / "codex").chmod(0o755)
    config = root / "fake-config.json"
    config.write_text(json.dumps({"title": scenario.title, "brief": scenario.brief,
                                  "reference": str(solution), "check": scenario.fake_check,
                                  "paths": overlay_paths(solution), "fault": scenario.fake_fault,
                                  "turns": [turn.say for turn in scenario.turns],
                                  "probe": scenario.fake_probe,
                                  "milestones": list(scenario.fake_milestones),
                                  "criteria": dict(scenario.fake_criteria),
                                  "turn_paths": [list(row) for row in scenario.fake_turn_paths]}))
    return [*FAKE_FLAGS, *scenario.fake_flags], {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
                        "SCENARIO_FAKE_CONFIG": str(config)}


# Flags only a new run takes (docs/cli.md): the public CLI refuses each on a saved run, even when it names the
# saved value, so the driver passes them on the first call only. A profile may pass --builder-strong-model.
NEW_RUN_FLAGS = ("--workflow", "--builder-strong-model")


def saved_run_flags(flags: list[str]) -> list[str]:
    """``flags`` without the new-run-only ones and their values (``--flag value`` or ``--flag=value``)."""
    kept: list[str] = []
    for index, arg in enumerate(flags):
        if arg in NEW_RUN_FLAGS or arg.startswith(tuple(flag + "=" for flag in NEW_RUN_FLAGS)) \
                or (index and flags[index - 1] in NEW_RUN_FLAGS):
            continue
        kept.append(arg)
    return kept


def live_setup(profile_name: str, provider: str | None = None) -> tuple[list[str], dict]:
    return profiles.flags(profiles.with_provider(profiles.resolve(profile_name), provider)), {}


class Driver:
    def __init__(self, project: Path, root: Path, flags: list[str], env: dict, *,
                 autocode: list[str], max_steps: int, timeout_seconds: int, explicit_answers=()):
        self.project, self.root, self.flags, self.autocode = project, root, list(flags), autocode
        # The person's own answers, by question id; served even where no default may be used.
        self.explicit_answers = dict(explicit_answers)
        self.resume_after_explicit = False
        self.env = {**os.environ, "AUTOCODE_HOME": str(root / "registry"), "PYTHONDONTWRITEBYTECODE": "1", **env}
        self.max_steps, self.deadline = max_steps, time.monotonic() + timeout_seconds
        self.steps: list[dict] = []
        self.answers: list[dict] = []
        self.run_dir: Path | None = None
        self.log = root / "steps.jsonl"
        # One mark per turn after the first: when it was said, how many CLI calls
        # and answers came before it, the view the previous turn ended with, and
        # the workspace files at that moment.
        self.turn_marks: list[dict] = []
        self.start_files: dict[str, str] = {}

    def state(self) -> dict:
        """The saved state, read only for evidence and metrics after the run."""
        path = self.run_dir / "state.json" if self.run_dir else None
        return json.loads(path.read_text()) if path and path.is_file() else {}

    def view(self) -> dict:
        """The run's status view from `autocode --status` (docs/task-run.md)."""
        proc = self.call("status", "--status", action=True, record=False)
        try:
            view = json.loads(proc.stdout)["view"]
        except (ValueError, KeyError) as error:
            raise DriveError(f"--status returned no status view: {error}") from None
        attempts.observe(self.root, view)
        return view

    def call(self, kind: str, *extra: str, task: str | None = None, action: bool = False,
             record: bool = True) -> subprocess.CompletedProcess:
        """One CLI invocation. Actions must exit 0; launches may also exit 2 (stopped for input)."""
        if record and len(self.steps) >= self.max_steps:
            raise DriveError(f"step budget used up after {len(self.steps)} CLI calls")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DriveError(f"time budget used up after {len(self.steps)} CLI calls")
        flags = saved_run_flags(self.flags) if self.run_dir else self.flags
        cmd = [*self.autocode, *([task] if task else []), "--workspace", str(self.project),
               *(["--run-dir", str(self.run_dir)] if self.run_dir else ["--in-place"]),
               *([] if action else ["--no-chat", *flags]), *extra]
        started = time.monotonic()
        try:
            proc = run_cli(cmd, env=self.env, cwd=self.root, timeout=remaining,
                           lifeline={"root": self.root / "cli-calls", "kind": kind, "deadline": self.deadline})
        except SupervisionUnavailable as error:
            raise DriveError(str(error)) from None
        except CallTimeout as error:
            detail = ("; cleanup incomplete: " + "; ".join(error.cleanup_errors)
                      if error.cleanup_errors else "; captured workers stopped")
            raise InterruptedDrive(f"{kind} was still running when the time budget ran out{detail}") from None
        if record:
            step = {"kind": kind, "args": list(extra), "exit": proc.returncode,
                    "seconds": round(time.monotonic() - started, 1),
                    "stdout_tail": proc.stdout[-1500:], "stderr_tail": proc.stderr[-1500:]}
            self.steps.append(step)
            with self.log.open("a") as handle:
                handle.write(json.dumps(step) + "\n")
        # Usage errors also exit 2, so recognize argparse's message rather than trusting the code.
        usage_error = proc.returncode == 2 and proc.stderr.startswith("usage:")
        if proc.returncode < 0:
            raise InterruptedDrive(f"{kind} exited on signal {-proc.returncode}; delivery and usage remain ungraded")
        if usage_error or proc.returncode not in ((0,) if action else (0, 2)):
            raise DriveError(f"{kind} exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-500:]}")
        return proc

    def drive(self, brief: str, turns=()) -> dict:
        self.start_files = workspace_files(self.project)
        self.call("start", task=brief)
        runs = self.project / ".autocode" / "runs"
        candidates = sorted(runs.glob("*/state.json"), key=lambda path: path.stat().st_mtime) if runs.is_dir() else []
        if not candidates:
            last = self.steps[-1]
            raise DriveError("the first CLI call did not create a run: "
                             + (last["stderr_tail"] or last["stdout_tail"]).strip()[-500:])
        self.run_dir = candidates[-1].parent
        view = self.until_stopped()
        for number, turn in enumerate(turns, start=1):
            reached = turn_state(view)
            if turn.after not in reached:
                raise TurnNotReached(f"stopped before turn {number + 1}: it is said after {turn.after!r}, but the "
                                     f"run ended {' / '.join(reached)} (status {view['status']!r})", number + 1)
            files = workspace_files(self.project)
            before = self.turn_marks[-1]["files"] if self.turn_marks else self.start_files
            self.turn_marks.append({"said_at": datetime.now(timezone.utc).isoformat(), "say": turn.say,
                                    "steps": len(self.steps), "answers": len(self.answers), "view": view,
                                    "files": files, "kept": str(self.keep_turn_files(number - 1, before, files))})
            self.call("follow-up", "--follow-up", turn.say, action=True)
            view = self.until_stopped()
        return view

    def keep_turn_files(self, index: int, before: dict[str, str], after: dict[str, str]) -> Path:
        """Copy the files turn ``index`` created or changed (``before`` to ``after``, two
        ``workspace_files`` snapshots) from the workspace now to turn-files/<index>/, and return it."""
        target = self.root / "turn-files" / str(index)
        target.mkdir(parents=True, exist_ok=True)
        for path in changed_between(before, after):
            if path in after and (self.project / path).is_file():
                (target / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.project / path, target / path)
        return target

    def until_stopped(self, say_at: str | None = None) -> dict:
        """Drive until the run is done or needs something the driver does not serve.
        ``say_at`` (``needs:<kind>``) stops at that need instead of serving it, so the
        caller can serve it its own way (plan_compare compares plans at ``needs:approve_plan``)."""
        while True:
            view = self.view()
            need = view["needs"]
            if not view["done"] and self.answered_explicitly(need):
                self.serve(need)
                self.resume_after_explicit = need.get("resolver_scope") in PERSON_ONLY_SCOPES
                continue
            if not view["done"] and need["kind"] == "resume" and self.resume_after_explicit:
                # The person's explicit answer to a stopped run was their decision to continue it.
                self.resume_after_explicit = False
                self.call("resume", "--resume-paused")
                continue
            if view["done"] or leaves_for_person(need) or say_at == f"needs:{need['kind']}":
                return view
            if need["kind"] == "continue":
                self.call("resume")
                after = self.view()
                keys = ("status", "next_stage", "iteration", "phase")
                if not after["done"] and all(after[key] == view[key] for key in keys):
                    raise DriveError(f"no progress at {view['status']!r} (next_stage={view['next_stage']!r})")
            else:
                try:
                    self.serve(need)
                except UserAnswerRequired:
                    # A question the fixture cannot answer is a product stop;
                    # the scenario's expected ending and oracle judge it.
                    return view

    def answered_explicitly(self, need: dict) -> bool:
        return (need["kind"] == "answer" and bool(self.explicit_answers) and bool(need.get("questions"))
                and all(question["id"] in self.explicit_answers for question in need["questions"]))

    def use_model(self, role: str, model: str) -> None:
        """The person named ``model`` for ``role``: later relaunches must not pass the old one back."""
        flag = "--" + role.replace("_", "-") + "-model"
        if flag in self.flags[:-1]:
            self.flags[self.flags.index(flag) + 1] = model

    def serve(self, need: dict) -> None:
        """Answer one gate the way a cooperative user would, recording every answer."""
        kind = need["kind"]
        if kind == "approve_plan":
            self.call("approve-plan", "--approve-goal", need["token"], action=True)
        elif kind == "answer":
            # One CLI call with every answer: a published AutoResolver request
            # is consumed by the first answered invocation, so per-question
            # calls would answer once and then fail the token check.
            pairs = []
            answers = []
            for question in need["questions"]:
                explicit = question["id"] in self.explicit_answers
                answer = self.explicit_answers[question["id"]] if explicit else _question_answer(question)
                answers.append({"id": question["id"], "question": question.get("question"),
                                "why": question.get("why"), "answer": answer,
                                **({"explicit": True} if explicit else {})})
                pairs.append(f"{question['id']}={answer}")
                if explicit and (need.get("route") or {}).get("question_id") == question["id"]:
                    self.use_model(need["route"]["role"], answer)
            args = [item for pair in pairs for item in ("--answer", pair)]
            if need.get("resolver_token"):
                args += ["--resolver-token", need["resolver_token"]]
            self.answers.extend(answers)
            self.call("answer", *args, action=True)
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


def turn_state(view: dict) -> list[str]:
    """Every ``after`` value a turn could name that this view satisfies."""
    need = (view.get("needs") or {}).get("kind")
    return ["complete"] if view.get("done") else ["stop", *([f"needs:{need}"] if need else [])]


def split_by_turn(state: dict, marks: list[dict]) -> list[list[dict]]:
    """The run's stage records, one list per turn, split at the moment each
    follow-up was said. A stage belongs to the turn in which it finished."""
    said = [_moment(mark["said_at"]) for mark in marks]
    turns: list[list[dict]] = [[] for _ in range(len(marks) + 1)]
    for stage in state.get("stages") or []:
        moment = _moment(stage.get("finished_at") or stage.get("started_at"))
        index = sum(1 for when in said if moment and when and moment >= when)
        turns[index].append(stage)
    return turns


def workspace_files(root: Path) -> dict[str, str]:
    """Every file in the delivered workspace with its content hash; AutoCode's own .autocode/
    and Git's .git/ are left out, and so are bytecode caches."""
    found = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (path.is_file() and relative.parts[0] not in (".git", ".autocode")
                and "__pycache__" not in relative.parts and path.suffix != ".pyc"):
            found[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def changed_between(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """The files created, changed or deleted between two ``workspace_files`` snapshots."""
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def _moment(text) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def default_autocode() -> list[str]:
    return [sys.executable, str(REPO / "tools" / "autocode.py")]


def model_routes(state: dict) -> list[dict]:
    """Audit recorded model requests, including an unfinished final attempt.

    Role settings can change after a launch, so they cannot establish which
    models were requested. An active record can precede process creation.
    Unknown routes stay unknown instead of being inferred.
    """
    records = list(state.get("stages") or [])
    if state.get("active_stage"):
        records.append(state["active_stage"])
    routes = []
    for record in records:
        if record.get("runner_owned") or record.get("stage") == "orchestrator":
            continue
        model = record.get("model")
        command = record.get("command") or []
        for i, arg in enumerate(command):
            if arg == "--model" and i + 1 < len(command):
                model = command[i + 1]
                break
            if isinstance(arg, str) and arg.startswith("--model="):
                model = arg.partition("=")[2]
                break
        else:
            # Python's module switch is not a model flag. Prefer an explicit
            # long flag above; only native provider commands use this alias.
            if command and Path(command[0]).name in {"codex", "opencode", "kilo", "kilocode"}:
                for i, arg in enumerate(command[:-1]):
                    if arg == "-m":
                        model = command[i + 1]
                        break
        routes.append({"stage": record.get("stage"), "engine": record.get("engine"),
                       "model": model or None})
    return routes


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
            "model_routes": model_routes(state),
            "model_stages": len(model_stage_names), "model_stage_names": model_stage_names,
            "model_seconds": round(sum(stage.get("duration_seconds") or 0 for stage in stages), 1),
            "report_repairs": sum(1 for name in model_stage_names if str(name).endswith("_report_repair")),
            "by_stage": by_stage, "tokens": tokens}
