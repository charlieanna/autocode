"""A headless client that completes a run through the task-run interface only (#1012).

The client reads ``autocode --status`` and takes the one action the status view
names: ``view["next_command"]``, which AutoCode formats from ``view["needs"]``
(docs/task-run.md, "Status view"). It fills only the decisions the view leaves to
a person (an ``ANSWER``) and passes its own engine and model options when a
command advances the run, as ``TaskRun`` does. A need whose command it cannot
take from the view alone is a ``ConformanceGap``: a gap to fix in the CLI or the
status view, never one to work around here. The client never imports AutoCode,
reads ``state.json`` or parses prose; ``Driver.state`` is read only afterwards,
for evidence.

What a person does on their own initiative comes from the scenario's
``[conformance]`` table, each at a moment the person can see in the view
(``Moment``): interrupting a running stage with Ctrl-C, and queueing feedback
through ``autocode intervention submit`` (docs/interventions.md) while a stage
runs. A pause that follows one of those is the person's to resume, so the
client takes its named action; any other pause is an honest stop, left for the
verdict. Follow-up turns are said as the ``Driver`` says them, with
``--follow-up`` once the run completes.

With the scripted provider, a ``ProviderBarrier`` holds the watched stage until
the client has looked at the view, so the moment cannot be missed or raced.
With live models the client polls the view while the stage runs instead.
"""

from __future__ import annotations

import json
import shlex
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from .adversarial import birth_identity
from .attack_lifecycle import ProviderBarrier
from .driver import (
    DriveError,
    Driver,
    InterruptedDrive,
    _question_answer,
    leaves_for_person,
    questions_answerable,
)

# Placeholders the view leaves for a decision it does not supply (autocode_pause_command).
UNSUPPLIED = ("GOAL_FILE", "N", "WHAT CHANGED", "TOKEN", "RUN_DIR", "--explain")
ADVANCING = ("--no-chat", "--resume-paused")
LIVE_POLL_SECONDS = 5.0


class ConformanceGap(DriveError):
    """The status view named no action the client could take: a gap in the CLI or the view."""


@dataclass(frozen=True)
class Moment:
    """Something a person does while a stage runs, once the view shows that stage working.

    ``stage`` is the stage code name the scripted provider holds; ``job`` is the name the
    view shows for it (``progress.stage``); ``after_failed`` waits for a failed requirement.
    """

    do: str  # "interrupt" or "feedback"
    stage: str
    job: str
    after_failed: bool = False
    text: str = ""

    def ready(self, view: dict) -> bool:
        progress = view.get("progress") or {}
        working = progress.get("stage") == self.job and str(progress.get("headline", "")).endswith("working")
        failed = (progress.get("requirements") or {}).get("failed", 0)
        return working and (failed > 0 or not self.after_failed)


def setup(root: Path, env: dict, *, hold: bool) -> dict:
    """A scripted Plan Reviewer's first review is blocking, so the plan goes through its findings and
    a revision (live or hybrid alike). With ``hold`` (the scripted provider throughout), the provider
    entry also becomes the adversarial wrapper (harness/adversarial_provider.py) with the lifecycle
    barrier installed; it holds a stage only for a call whose environment names one.
    """
    if hold:
        config_path = Path(env["SCENARIO_FAKE_CONFIG"])
        config = json.loads(config_path.read_text())
        config["adversarial"] = {"root": str(root), "module": "lifecycle", "case": "provider_barrier"}
        config_path.write_text(json.dumps(config))
        provider = Path(__file__).with_name("adversarial_provider.py")
        entry = root / "bin" / "codex"
        entry.write_text(f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(provider)!r}, run_name='__main__')\n")
        entry.chmod(0o755)
    return {**env, "SCENARIO_FAKE_BLOCKING_REVIEW": "1"}


def moments(table: dict) -> list[Moment]:
    """The scenario's ``[conformance]`` table as the moments the person acts at, in order."""
    found = []
    if "interrupt" in table:
        row = table["interrupt"]
        found.append(Moment("interrupt", row["stage"], row["job"], bool(row.get("after_failed"))))
    if "feedback" in table:
        row = table["feedback"]
        found.append(Moment("feedback", row["stage"], row["job"], text=row["text"]))
    return found


class ConformanceDriver(Driver):
    def __init__(self, *args, conformance: dict, scripted: bool, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending = moments(conformance)
        self.scripted = scripted
        self.person_paused = False
        # One row per decision: what the view said, and what the client did about it.
        self.journal: list[dict] = []

    def until_stopped(self, say_at: str | None = None) -> dict:
        while True:
            view = self.view()
            need = view["needs"]
            if view["done"]:
                self.person_paused = False
                return view
            if say_at == f"needs:{need['kind']}":
                return view
            if leaves_for_person(need):
                # Only a pause that follows the person's own interrupt or feedback is theirs to continue.
                named = self.person_command(view) if self.person_paused else None
                if named is None:
                    self.note(view, "stopped", None)
                    return view
                words, env, source = named
            elif need["kind"] == "answer" and not questions_answerable(need["questions"], self.explicit_answers):
                self.note(view, "stopped", None)
                return view
            else:
                self.person_paused = False
                words, env, source = self.named_command(view)
            words = self.decide(need, words)
            self.take(view, words, env, source)

    def person_command(self, view: dict) -> tuple[list[str], dict, str] | None:
        """What the view names for continuing a pause the person caused, or None to stop.

        A resume or a job retry takes its ``next_command``. An operational request asks for the
        person's own words; the client continues it only through an action the need names that
        needs none, ``retry_action`` (one explicit scoped retry).
        """
        need = view["needs"]
        if need["kind"] in ("resume", "retry_job"):
            return self.named_command(view)
        if need.get("retry_action"):
            return shlex.split(need["retry_action"]), {}, "needs.retry_action"
        return None

    def named_command(self, view: dict) -> tuple[list[str], dict, str]:
        """``view["next_command"]`` as argv after ``autocode``, its token environment, and where it came from.

        An active run has no ``next_command`` (docs/task-run.md); its ``continue`` need's action is
        the needs table's own, ``--no-chat``, which takes no token. Every other need must name one.
        """
        text = view.get("next_command")
        if not text and view["needs"]["kind"] == "continue":
            return ["--no-chat"], {}, "needs.kind"
        if not text:
            raise ConformanceGap(f"needs {view['needs']['kind']!r} at {view['status']!r} names no next_command")
        words = shlex.split(text)
        env = {}
        while words and words[0] != "autocode" and "=" in words[0]:
            name, value = words.pop(0).split("=", 1)
            env[name] = value
        if words[:1] != ["autocode"]:
            raise ConformanceGap(f"next_command does not start with autocode: {text!r}")
        words = words[1:]
        for flag, expected in (("--run-dir", self.run_dir), ("--workspace", self.project)):
            if flag in words:
                index = words.index(flag)
                named = words[index + 1]
                del words[index : index + 2]
                if Path(named).resolve() != Path(expected).resolve():
                    raise ConformanceGap(f"next_command names {flag} {named}, not this run's {expected}")
        return words, env, "next_command"

    def decide(self, need: dict, words: list[str]) -> list[str]:
        """Fill the person's decisions the command leaves open; anything else unsupplied is a gap."""
        filled = []
        for word in words:
            if word in UNSUPPLIED:
                raise ConformanceGap(f"needs {need['kind']!r} leaves {word!r} open, which the view does not supply")
            question, _, placeholder = word.partition("=")
            if placeholder in ("ANSWER", "MODEL"):
                word = f"{question}={self.answer_for(need, question, placeholder)}"
            filled.append(word)
        return filled

    def answer_for(self, need: dict, question_id: str, placeholder: str) -> str:
        if question_id in self.explicit_answers:
            answer = self.explicit_answers[question_id]
        elif placeholder == "MODEL":
            raise ConformanceGap(f"{question_id} asks a person to name a model; the scenario names none")
        else:
            question = next((row for row in need.get("questions") or [] if row["id"] == question_id), None)
            if question is None:
                raise ConformanceGap(f"next_command answers {question_id}, which needs.questions does not list")
            answer = _question_answer(question)
        self.answers.append({"id": question_id, "answer": answer, "explicit": question_id in self.explicit_answers})
        return answer

    def take(self, view: dict, words: list[str], env: dict, source: str) -> None:
        kind = view["needs"]["kind"]
        advancing = any(flag in words for flag in ADVANCING)
        extra = [word for word in words if word != "--no-chat"]
        self.note(view, "took", shlex.join(words), source)
        saved = dict(self.env)
        self.env.update(env)
        try:
            if advancing and self.pending:
                self.watched(kind, extra)
            else:
                self.call(kind, *extra, action=not advancing)
        finally:
            self.env = saved
        if advancing:
            after = self.view()
            keys = ("status", "next_stage", "iteration", "phase")
            if not after["done"] and all(after[key] == view[key] for key in keys) and after["needs"] == view["needs"]:
                raise ConformanceGap(f"taking {shlex.join(words)!r} made no progress at {view['status']!r}")

    def watched(self, kind: str, extra: list[str]) -> None:
        """One advancing call, watched for the next moment the person acts at."""
        moment = self.pending[0]
        barrier = ProviderBarrier(self.root, moment.stage) if self.scripted else None
        if barrier:
            self.env.update(barrier.environment)
        outcome: dict = {}
        acted = threading.Event()

        def run():
            try:
                outcome["result"] = self.call(kind, *extra)
            except InterruptedDrive as error:
                outcome["error"] = error
            except BaseException as error:  # surfaced on the main thread
                outcome["error"] = error

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        try:
            while worker.is_alive():
                if barrier:
                    try:
                        barrier.wait(timeout=0.5)
                    except TimeoutError:
                        continue
                    if not acted.is_set() and moment.ready(view := self.view()):
                        acted.set()
                        self.act(moment, view)
                        if moment.do == "interrupt":
                            continue  # the controller stops the held provider
                    barrier.release()
                else:
                    worker.join(LIVE_POLL_SECONDS)
                    if worker.is_alive() and not acted.is_set() and moment.ready(view := self.view()):
                        acted.set()
                        self.act(moment, view)
            worker.join()
        finally:
            if barrier:
                barrier.close()
                for key in barrier.environment:
                    self.env.pop(key, None)
        error = outcome.get("error")
        if error and not (acted.is_set() and moment.do == "interrupt" and isinstance(error, InterruptedDrive)):
            raise error

    def act(self, moment: Moment, view: dict) -> None:
        self.pending.pop(0)
        self.person_paused = True
        if moment.do == "interrupt":
            self.note(view, "interrupted", moment.job)
            self.interrupt(view)
        else:
            self.note(view, "feedback", moment.text)
            self.call(
                "intervention",
                "--request-id",
                f"conformance-{len(self.journal)}",
                "--kind",
                "feedback",
                "--text",
                moment.text,
                "--json",
                action=True,
                subcommand="intervention submit",
            )

    def interrupt(self, view: dict) -> None:
        """Ctrl-C to the controller the view names as running, checked by its birth identity."""
        controller = view.get("controller") or {}
        identity = (controller.get("receipt") or {}).get("identity") or {}
        if controller.get("kind") != "running" or not identity.get("pid"):
            raise ConformanceGap(f"the view names no running controller to interrupt: {controller.get('kind')!r}")
        try:
            process = psutil.Process(identity["pid"])
            if birth_identity(process) != identity.get("birth_identity"):
                raise ConformanceGap("the view's controller birth identity does not match the running process")
            process.send_signal(signal.SIGINT)
        except psutil.NoSuchProcess:
            raise ConformanceGap("the controller the view names as running has exited") from None

    def call(self, kind, *extra, task=None, action=False, record=True, subcommand=None):
        if subcommand is None:
            return super().call(kind, *extra, task=task, action=action, record=record)
        # A subcommand takes its words first; the run is named the same way.
        saved = self.autocode
        self.autocode = [*saved, *subcommand.split()]
        try:
            return super().call(kind, *extra, action=True, record=record)
        finally:
            self.autocode = saved

    def note(self, view: dict, did: str, what, source: str | None = None) -> None:
        progress = view.get("progress") or {}
        row = {
            "source": source,
            "at": round(time.monotonic(), 1),
            "status": view.get("status"),
            "needs": (view.get("needs") or {}).get("kind"),
            "next_command": view.get("next_command"),
            "stage": progress.get("stage"),
            "headline": progress.get("headline"),
            "failed": (progress.get("requirements") or {}).get("failed", 0),
            "turn": view.get("turn"),
            "did": did,
            "what": what,
        }
        self.journal.append(row)
        with (self.root / "conformance.jsonl").open("a") as handle:
            handle.write(json.dumps(row) + "\n")
