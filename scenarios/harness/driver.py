"""Drive AutoCode's public CLI through one scenario, serving its gates like a user.

The driver only calls the CLI. It decides what to do from the status view that
`autocode --status` reports (docs/task-run.md), and reads ``state.json`` only
afterwards, for evidence and metrics. It never writes state. Gates served:
clarifying questions (answered with AutoCode's proposed default, and recorded),
plan approval, human-review acceptance, and planning-budget feedback. A pause
that needs a person, or a question without a usable default or concrete option,
is left for the verdict to judge, and so is an AutoResolver
escalation that it could not continue safely (``PERSON_ONLY_SCOPES``): answering
one with a proposed default would hide an honest stop. Only a scenario's explicit
``[fake] answers`` answer such a request (for example the model a quota-stopped
role continues on): they are the person's own decision, never a default, so the
driver then resumes the pause that answer leaves, once. Likewise only a scenario's
``[fake] acknowledge`` continues a budget pause the person owns (a cost cap,
PAUSED_COST_LIMIT): it names the flags the person types with ``--resume-paused``
(a higher cap), which the driver passes once per pause and keeps for later calls.

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
from datetime import UTC, datetime
from pathlib import Path

from . import attempts, plan_round_trip, profiles
from .processes import CallTimeout, SupervisionUnavailable, run_cli
from .project import overlay_paths

REPO = Path(__file__).resolve().parents[2]
FAKE_PROVIDER = Path(__file__).resolve().parent / "fake_codex.py"
# Codex-engine flags for fake runs. The fake ignores models, but AutoCode's
# Codex path wants bare GPT names and distinct builder and verifier models.
FAKE_FLAGS = [
    "--evidence-provenance",
    "fake",
    "--engine",
    "codex",
    "--joint-planning",
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
]
# Fake runs use the scripted Codex provider and ChatGPT identity. Ambient API-key
# variables would trip joint-planning's billing-route gate (PAUSED_BILLING_ROUTE)
# even though no real model is called. Driver pops these when fake_setup marks them.
FAKE_BILLING_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY")
STRIP_BILLING_AUTH = "SCENARIO_STRIP_BILLING_AUTH"


# AutoResolver request scopes that ask a person to look at a stopped run (for
# example, a time limit was reached), not a question about requirements.
PERSON_ONLY_SCOPES = ("operational_exhaustion", "blocker")

# What the harness sends when a planning cycle used up its review budget (a ``planning_budget`` gate).
PLANNING_BUDGET_FEEDBACK = (
    "The previous planning cycle used up its review budget. Produce a complete final plan now and finalize it."
)


def leaves_for_person(need: dict) -> bool:
    """Whether this need is an honest stop the driver must not answer for the user."""
    return need["kind"] in ("resume", "retry_job", "recover_source") or (
        need["kind"] == "answer" and need.get("resolver_scope") in PERSON_ONLY_SCOPES
    )


def attribute(gates: list[dict], run_dir) -> None:
    """Record on each of ``gates`` that names no run yet that it was given to the task run in ``run_dir``.

    A components scenario records one ``approvals`` list for all its task runs, so each gate the
    harness gives one of them says which, and harness.approval_provenance matches it to that run's own rows. A
    scenario of one run names none: every gate is that run's. Nothing is named before a run exists."""
    if run_dir is None:
        return
    for gate in gates:
        if isinstance(gate, dict):
            gate.setdefault("run_dir", str(run_dir))


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


# Question ids AutoCode gives a spec-first run's "confirm what Requirements understood" stop (#1200,
# tools/autocode_requirements_review.PREFIX); the harness drives the CLI and never imports tools/.
REQUIREMENTS_REVIEW = "requirements-review-"


def _question_answer(question: dict) -> str:
    """Do not mistake an explicit absence of a default for a user decision."""
    default = question.get("proposed_default")
    no_default = re.compile(
        r"^\s*no\s+(?:proposed\s+|recommended\s+)?default"
        r"(?:\s*[.;:!?—–-]|\s*$|\s+(?:is\s+)?"
        r"(?:available|provided|specified|selected|offered)\b)",
        re.I,
    )
    if isinstance(default, str) and default.strip() and not no_default.match(default):
        return default

    for option in question.get("options") or []:
        if not isinstance(option, str) or not option.strip() or no_default.match(option):
            continue
        if re.match(
            r"^\s*(?:other\b|(?:specify|provide|choose)\s+(?:another|your own)\b|"
            r"(?:ask|consult)\s+(?:the\s+)?user\b)",
            option,
            re.I,
        ):
            continue
        return option
    raise UserAnswerRequired(
        f"question {question['id']} has no usable default and no concrete option; a user answer is required"
    )


def questions_answerable(questions: list[dict], explicit_answers: dict) -> bool:
    """Can the whole batch be answered without inventing a person's decision?"""
    for question in questions:
        if question["id"] in explicit_answers:
            continue
        try:
            _question_answer(question)
        except DriveError:
            return False
    return True


def fake_setup(scenario, root: Path, solution: Path) -> tuple[list[str], dict]:
    """Put the scripted provider on PATH as ``codex``; return CLI flags and environment."""
    bindir = root / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FAKE_PROVIDER, bindir / "codex")
    shutil.copy2(FAKE_PROVIDER.with_name("fake_changed_code.py"), bindir / "fake_changed_code.py")
    (bindir / "codex").chmod(0o755)
    config = root / "fake-config.json"
    config.write_text(
        json.dumps(
            {
                "title": scenario.title,
                "brief": scenario.brief,
                "reference": str(solution),
                "check": scenario.fake_check,
                "paths": overlay_paths(solution),
                "fault": scenario.fake_fault,
                "turns": [turn.say for turn in scenario.turns],
                "probe": scenario.fake_probe,
                "milestones": list(scenario.fake_milestones),
                "criteria": dict(scenario.fake_criteria),
                "turn_paths": [list(row) for row in scenario.fake_turn_paths],
            }
        )
    )
    flags = [*FAKE_FLAGS, *scenario.fake_flags]
    flags[flags.index("--evidence-provenance") + 1] = "mixed" if scenario.fake_live_calls else "fake"
    # Isolate Codex identity from the host ~/.codex (a gocode model_provider or
    # base URL there pauses joint planning even when API keys are absent).
    codex_home = root / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    # Likewise the host's ~/.config/autocode: a default_provider or provider config there would change
    # what a fake run does on that machine only (#1938). This run's user config home starts empty.
    config_home = root / "user-config"
    config_home.mkdir(parents=True, exist_ok=True)
    if scenario.fake_plan_review_call_limit is not None:
        # The fixture author declares this before the run starts. Use the same public configuration
        # as a person, scoped to this fake run; the driver never invents an allowance at a pause.
        user_config = config_home / "autocode" / "config.toml"
        user_config.parent.mkdir(parents=True, exist_ok=True)
        user_config.write_text(f"plan_review_call_limit = {scenario.fake_plan_review_call_limit}\n")
    return flags, {
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "SCENARIO_FAKE_CONFIG": str(config),
        "CODEX_HOME": str(codex_home),
        STRIP_BILLING_AUTH: "1",
        "XDG_CONFIG_HOME": str(config_home),
    }


# Flags only a new run takes (docs/cli.md): the public CLI refuses each on a saved run, even when it names the
# saved value, so the driver passes them on the first call only. A profile may pass --builder-strong-model; a
# scenario's [fake] flags may pass --package (feature-monorepo-package).
NEW_RUN_FLAGS = ("--workflow", "--builder-strong-model", "--package")


def saved_run_flags(flags: list[str]) -> list[str]:
    """``flags`` without the new-run-only ones and their values (``--flag value`` or ``--flag=value``)."""
    kept: list[str] = []
    for index, arg in enumerate(flags):
        if (
            arg in NEW_RUN_FLAGS
            or arg.startswith(tuple(flag + "=" for flag in NEW_RUN_FLAGS))
            or (index and flags[index - 1] in NEW_RUN_FLAGS)
        ):
            continue
        kept.append(arg)
    return kept


def with_flag_values(flags: list[str], given: list[str]) -> list[str]:
    """``flags`` with each option ``given`` sets (``--name value`` or ``--name=value``) carrying its new value
    wherever ``flags`` passes that option, in either form. An option ``flags`` does not pass is left to the
    saved run, which keeps what the person last set."""
    values = {}
    for index, word in enumerate(given):
        if word.startswith("--") and "=" in word:
            name, _, value = word.partition("=")
            values[name] = value
        elif word.startswith("--") and index + 1 < len(given) and not given[index + 1].startswith("--"):
            values[word] = given[index + 1]
    updated = list(flags)
    for index, word in enumerate(updated):
        name = word.partition("=")[0]
        if word in values and index + 1 < len(updated):
            updated[index + 1] = values[word]
        elif "=" in word and name in values:
            updated[index] = f"{name}={values[name]}"
    return updated


def live_setup(profile_name: str, provider: str | None = None) -> tuple[list[str], dict]:
    return [
        *profiles.flags(profiles.with_provider(profiles.resolve(profile_name), provider)),
        "--evidence-provenance",
        "live",
    ], {}


class Driver:
    def __init__(
        self,
        project: Path,
        root: Path,
        flags: list[str],
        env: dict,
        *,
        autocode: list[str],
        max_steps: int,
        timeout_seconds: int,
        explicit_answers=(),
        acknowledge=(),
        fixture_root: Path | None = None,
        round_trip_plans: bool = False,
    ):
        self.project, self.root, self.flags, self.autocode = project, root, list(flags), autocode
        # A catalog run exports each plan it approves and reads the file back first (harness.plan_round_trip).
        self.round_trip_plans = round_trip_plans
        # The person's own answers, by question id; served even where no default may be used.
        self.explicit_answers = dict(explicit_answers)
        self.resume_after_explicit = False
        # The person's own acknowledgement of a budget pause ([fake] acknowledge): pause status -> the flags
        # they type with --resume-paused. Each pause is acknowledged at most once per drive (``acknowledged``).
        self.acknowledge = {pause: list(flags) for pause, flags in dict(acknowledge).items()}
        self.acknowledged: set[str] = set()
        self.env = {**os.environ, "AUTOCODE_HOME": str(root / "registry"), "PYTHONDONTWRITEBYTECODE": "1", **env}
        # Dict merge cannot remove keys; fake_setup marks billing auth to strip so ambient
        # OPENAI_API_KEY / CODEX_API_KEY do not pause joint-planning fake runs.
        if self.env.pop(STRIP_BILLING_AUTH, None):
            for key in FAKE_BILLING_ENV:
                self.env.pop(key, None)
        emitter_root = root.resolve()
        if fixture_root is not None:
            try:
                enclosing = Path(fixture_root)
                if (
                    not enclosing.is_absolute()
                    or enclosing != enclosing.resolve(strict=True)
                    or not enclosing.is_dir()
                    or enclosing == enclosing.parent
                ):
                    raise ValueError("Fixture root must be a real bounded directory")
                for owned in (root, project):
                    path = Path(owned).absolute()
                    if path != path.resolve(strict=True) or not path.is_dir():
                        raise ValueError("Driver roots must be real directories")
                    path.relative_to(enclosing)
            except (OSError, RuntimeError, ValueError):
                raise DriveError(
                    "Explicit fixture root must be a real directory containing both Driver artifacts and project"
                ) from None
            emitter_root = enclosing
        self.env.update(
            AUTOCODE_REPLAY_EMITTER_COMMAND=json.dumps([*autocode, "replay", "--emit-fixture-events"]),
            AUTOCODE_REPLAY_FIXTURE_ROOT=str(emitter_root),
        )
        self.max_steps, self.budget_started = max_steps, time.monotonic()
        self.deadline = self.budget_started + timeout_seconds
        # Where the one time budget went, for the message of a call it cuts off (#967): each recorded
        # call's seconds in order, and the calls that are not steps (status reads) counted and summed
        # by kind, so the account stays as short as the step budget.
        self.spent: list[tuple[str, float]] = []
        self.unrecorded: dict[str, list] = {}
        self.steps: list[dict] = []
        # Every answer the harness gave (result.json "answers"): ``source`` says whether it was the
        # person's own ([fake] answers, "person"), AutoCode's proposed default or first concrete
        # option ("proposed_default"), or a review acceptance ("approval"). A campaign reports the
        # three apart: a served default is a harness decision, not the product's autonomy (#455).
        self.answers: list[dict] = []
        # Every gate the harness passed on the person's behalf (result.json "approvals"): the plan
        # (``approve_plan``), each human-review criterion (``review``), more planning review calls
        # (``planning_budget``) and a budget pause the scenario's person acknowledged (``acknowledge``);
        # a components run adds its --auto-approve. In a components scenario each gate given to one of
        # its task runs also names that run (``attribute``).
        self.approvals: list[dict] = []
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

    def call(
        self,
        kind: str,
        *extra: str,
        task: str | None = None,
        action: bool = False,
        record: bool = True,
        bare: bool = False,
    ) -> subprocess.CompletedProcess:
        """One CLI invocation. Actions must exit 0; launches may also exit 2 (stopped for input).
        A ``bare`` call is `autocode` followed by ``extra`` alone (``plan --check FILE``), and its
        exit code is the caller's to judge."""
        if record and len(self.steps) >= self.max_steps:
            raise DriveError(f"step budget used up after {len(self.steps)} CLI calls")
        # One reading both admits the call and starts its timing.
        started = time.monotonic()
        remaining = self.deadline - started
        if remaining <= 0:
            raise DriveError(f"time budget used up after {len(self.steps)} CLI calls. {self.budget_account(started)}")
        flags = saved_run_flags(self.flags) if self.run_dir else self.flags
        cmd = (
            [*self.autocode, *extra]
            if bare
            else [
                *self.autocode,
                *([task] if task else []),
                "--workspace",
                str(self.project),
                *(["--run-dir", str(self.run_dir)] if self.run_dir else ["--in-place"]),
                *([] if action else ["--no-chat", *flags]),
                *extra,
            ]
        )
        try:
            proc = run_cli(
                cmd,
                env=self.env,
                cwd=self.root,
                timeout=remaining,
                lifeline={"root": self.root / "cli-calls", "kind": kind, "deadline": self.deadline},
            )
        except SupervisionUnavailable as error:
            raise DriveError(str(error)) from None
        except CallTimeout as error:
            detail = (
                "; cleanup incomplete: " + "; ".join(error.cleanup_errors)
                if error.cleanup_errors
                else "; captured workers stopped"
            )
            stopped = time.monotonic()
            account = self.budget_account(stopped, (kind, remaining, stopped - started))
            self.spent.append((f"{kind} (stopped)", stopped - started))
            raise InterruptedDrive(
                f"{kind} was still running when the time budget ran out{detail}. {account}"
            ) from None
        seconds = time.monotonic() - started
        if not record:
            count = self.unrecorded.setdefault(kind, [0, 0.0])
            count[0], count[1] = count[0] + 1, count[1] + seconds
        else:
            self.spent.append((kind, seconds))
            step = {
                "kind": kind,
                "args": list(extra),
                "exit": proc.returncode,
                "seconds": round(seconds, 1),
                "stdout_tail": proc.stdout[-1500:],
                "stderr_tail": proc.stderr[-1500:],
            }
            self.steps.append(step)
            with self.log.open("a") as handle:
                handle.write(json.dumps(step) + "\n")
        # Usage errors also exit 2, so recognize argparse's message rather than trusting the code.
        usage_error = proc.returncode == 2 and proc.stderr.startswith("usage:")
        if proc.returncode < 0:
            raise InterruptedDrive(f"{kind} exited on signal {-proc.returncode}; delivery and usage remain ungraded")
        if usage_error or (not bare and proc.returncode not in ((0,) if action else (0, 2))):
            raise DriveError(f"{kind} exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-500:]}")
        return proc

    def budget_account(self, now: float, cut_off: tuple[str, float, float] | None = None) -> str:
        """Where the time budget went by ``now``, for the error that ends a drive on it (#967): every
        earlier call in order, the status reads together, the harness's own time between calls, and
        ``cut_off`` (kind, seconds left when it started, seconds until it was stopped). A drive whose
        fixture is removed afterwards keeps no steps.jsonl, so this message is the only record of the cause.
        A conformance drive reads the status while the call it watches runs; when such calls add up to more
        than the time that passed, the account says they overlapped instead of claiming no time between."""
        calls = [f"{kind} {seconds:.1f} s" for kind, seconds in self.spent]
        calls += [
            f"{count} {kind} call{'' if count == 1 else 's'} {seconds:.1f} s"
            for kind, (count, seconds) in self.unrecorded.items()
        ]
        elapsed = now - self.budget_started
        between = elapsed - sum(seconds for _, seconds in self.spent)
        between -= sum(seconds for _, seconds in self.unrecorded.values())
        if cut_off:
            between -= cut_off[2]
        calls.append(f"{max(between, 0):.1f} s between calls" if round(between, 1) >= 0 else "calls overlapped")
        account = f"Of the {self.deadline - self.budget_started:.0f} s budget: " + ", ".join(calls)
        if cut_off:
            kind, left, seconds = cut_off
            account += f"; {kind} started with {left:.1f} s left and was stopped after {seconds:.1f} s"
        return account

    def drive(self, brief: str, turns=()) -> dict:
        self.start_files = workspace_files(self.project)
        self.call("start", task=brief)
        runs = self.project / ".autocode" / "runs"
        candidates = sorted(runs.glob("*/state.json"), key=lambda path: path.stat().st_mtime) if runs.is_dir() else []
        if not candidates:
            last = self.steps[-1]
            raise DriveError(
                "the first CLI call did not create a run: "
                + (last["stderr_tail"] or last["stdout_tail"]).strip()[-500:]
            )
        self.run_dir = candidates[-1].parent
        view = self.until_stopped()
        for number, turn in enumerate(turns, start=1):
            reached = turn_state(view)
            if turn.after not in reached:
                raise TurnNotReached(
                    f"stopped before turn {number + 1}: it is said after {turn.after!r}, but the "
                    f"run ended {' / '.join(reached)} (status {view['status']!r})",
                    number + 1,
                )
            files = workspace_files(self.project)
            before = self.turn_marks[-1]["files"] if self.turn_marks else self.start_files
            self.turn_marks.append(
                {
                    "said_at": datetime.now(UTC).isoformat(),
                    "say": turn.say,
                    "steps": len(self.steps),
                    "answers": len(self.answers),
                    "view": view,
                    "files": files,
                    "kept": str(self.keep_turn_files(number - 1, before, files)),
                }
            )
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

    def resume_after_crash(self) -> None:
        """Take the Resume a controller exit offers after a completed, accounted report (#1698).

        The person decides to continue such a run; the view names `--resume-paused` for it."""
        view = self.view()
        if view["needs"]["kind"] == "resume" and (view.get("controller") or {}).get("completed_stage"):
            self.call("resume", "--resume-paused")

    def until_stopped(self, say_at: str | None = None) -> dict:
        """Drive until the run is done or needs something the driver does not serve.
        ``say_at`` (``needs:<kind>``) stops at that need instead of serving it, so the
        caller can serve it its own way (plan_compare compares plans at ``needs:approve_plan``)."""
        pending_view = None
        while True:
            view = self.view() if pending_view is None else pending_view
            pending_view = None
            need = view["needs"]
            self.assert_needs_command_accepted(view)
            if pause := self.pause_to_acknowledge(view):
                self.acknowledge_pause(pause)
                continue
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
            if need["kind"] == "answer" and not questions_answerable(need["questions"], self.explicit_answers):
                return view
            if need["kind"] == "continue":
                self.call("resume")
                after = self.view()
                keys = ("status", "next_stage", "iteration", "phase")
                if not after["done"] and all(after[key] == view[key] for key in keys):
                    raise DriveError(f"no progress at {view['status']!r} (next_stage={view['next_stage']!r})")
                pending_view = after
            else:
                try:
                    self.serve(need)
                except UserAnswerRequired:
                    # A question the fixture cannot answer is a product stop;
                    # the scenario's expected ending and oracle judge it.
                    return view

    def assert_needs_command_accepted(self, view: dict) -> None:
        """#1010: a displayed next_command must be argv the public CLI accepts.

        A refused action becomes DriveError (ERROR verdict) instead of a silent hold.
        Checked once per distinct command string; dependency and terminal stops
        offer no command and skip.
        """
        text = view.get("next_command")
        if not text or not view.get("needs"):
            return
        checked = getattr(self, "_accepted_next_commands", None)
        if checked is None:
            self._accepted_next_commands = checked = set()
        if text in checked:
            return
        from . import needs_command

        try:
            needs_command.accept(text)
        except needs_command.Rejected as error:
            raise DriveError(
                f"needs {view['needs'].get('kind')!r} next_command is refused by the CLI: {error}"
            ) from None
        checked.add(text)

    def pause_to_acknowledge(self, view: dict) -> str | None:
        """The pause this view stops at that the scenario's person acknowledges and has not yet, else None.

        AutoResolver turns a budget pause into a request for a person: the status is then WAITING_FOR_USER and
        the pause is the need's ``pause_origin``; once that request is consumed the status is the pause itself.
        """
        if not self.acknowledge or view.get("done"):
            return None
        pause = (view.get("needs") or {}).get("pause_origin") or view.get("status")
        return pause if pause in self.acknowledge and pause not in self.acknowledged else None

    def acknowledge_pause(self, pause: str) -> None:
        """Continue ``pause`` the way the person said they would, once: ``--resume-paused`` with their flags.
        Later calls pass those flags too, so a saved-run call never re-applies the scenario's old value."""
        flags = self.acknowledge[pause]
        self.acknowledged.add(pause)
        self.flags = with_flag_values(self.flags, flags)
        self.approvals.append({"gate": "acknowledge", "pause": pause, "flags": list(flags)})
        self.call("acknowledge", "--resume-paused", *flags)

    def answered_explicitly(self, need: dict) -> bool:
        return (
            need["kind"] == "answer"
            and bool(self.explicit_answers)
            and bool(need.get("questions"))
            and all(question["id"] in self.explicit_answers for question in need["questions"])
        )

    def use_model(self, role: str, model: str) -> None:
        """The person named ``model`` for ``role``: later relaunches must not pass the old one back."""
        flag = "--" + role.replace("_", "-") + "-model"
        if flag in self.flags[:-1]:
            self.flags[self.flags.index(flag) + 1] = model

    def serve(self, need: dict) -> None:
        """Answer one gate the way a cooperative user would, recording every answer."""
        kind = need["kind"]
        if kind == "approve_plan":
            gate = {"gate": "approve_plan", "token": need["token"]}
            if self.round_trip_plans and self.run_dir:
                gate["plan_file"] = plan_round_trip.take(self)
            self.approvals.append(gate)
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
                if question["id"].startswith(REQUIREMENTS_REVIEW):
                    # A spec-first run's confirmation is a gate, like plan approval: not a question to count.
                    self.approvals.append(
                        {"gate": "approve_requirements", "question_id": question["id"], "answer": answer}
                    )
                    pairs.append(f"{question['id']}={answer}")
                    continue
                answers.append(
                    {
                        "id": question["id"],
                        "question": question.get("question"),
                        "why": question.get("why"),
                        "answer": answer,
                        "source": "person" if explicit else "proposed_default",
                        **({"explicit": True} if explicit else {}),
                    }
                )
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
                self.answers.append(
                    {"id": criterion, "question": need.get("question"), "answer": "approved", "source": "approval"}
                )
                self.approvals.append({"gate": "review", "criterion": criterion, "token": need["token"]})
                self.call("approve-review", "--approve-review", criterion, "--review-token", need["token"], action=True)
        elif kind == "planning_budget":
            # Asking for more planning review calls is the person's to authorize: the harness does it for
            # them, so it is recorded with the other approvals (#455). It is not an ``answers`` row, which
            # oracles count as questions asked.
            self.approvals.append({"gate": "planning_budget", "feedback": PLANNING_BUDGET_FEEDBACK})
            self.call("feedback", "--feedback", PLANNING_BUDGET_FEEDBACK, action=True)
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
        if (
            path.is_file()
            and relative.parts[0] not in (".git", ".autocode")
            and "__pycache__" not in relative.parts
            and path.suffix != ".pyc"
        ):
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
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


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
        routes.append({"stage": record.get("stage"), "engine": record.get("engine"), "model": model or None})
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
    model_stage_names = [
        stage.get("stage") for stage in stages if not stage.get("runner_owned") and stage.get("stage") != "orchestrator"
    ]
    return {
        "stages": len(stages),
        "stage_names": [stage.get("stage") for stage in stages],
        "model_routes": model_routes(state),
        "model_stages": len(model_stage_names),
        "model_stage_names": model_stage_names,
        "model_seconds": round(sum(stage.get("duration_seconds") or 0 for stage in stages), 1),
        "report_repairs": sum(1 for name in model_stage_names if str(name).endswith("_report_repair")),
        "by_stage": by_stage,
        "tokens": tokens,
    }
