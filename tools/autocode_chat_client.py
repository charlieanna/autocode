"""`autocode chat`: talk to one run in the terminal, through the task-run interface only.

The terminal is the third client a person can use, after the VS Code extension and
the browser dashboard, and it behaves like the extension: it reads
``autocode --status``, prints the conversation the view carries
(``view["chat"]``), asks the person only for the decisions the view leaves to them,
and runs exactly the command the view names (autocode_client_actions). It never
imports the controller, never reads ``state.json`` and never decides on its own what
the run should do next; where the view names no action it can take, it prints the
command for the person to run instead.

    autocode chat "Add a --verbose flag"            start a run here and talk to it
    autocode chat --mode quick "Fix the typo"       start with a run mode from `autocode modes list`
    autocode chat                                   pick up this project's open run, or ask what to do
    autocode chat --run-dir RUN                     pick up that run
    autocode chat "..." -- --engine claude          options for a new run, after --
    autocode chat --brief=-v                        a brief that starts with -

Besides the status view it reads only `autocode runs --json` (the project's chats:
the open one to pick up, and `/runs`, `/open` and `/new` in the full-screen chat)
and `autocode modes list --json` (a new run's mode). The full-screen chat's
`/diff` and `/undo` take a saved code step's `compare` and `restore` actions, which the
status view names (`view["code_checkpoints"]`); going back makes a new run on a new
branch, and the chat carries on with it.

While a stage works, Ctrl-C asks the run to pause after the current step (the
view's named pause), a second Ctrl-C interrupts the step and a third stops AutoCode. Leaving the chat at a
question or a plan changes nothing; `autocode chat` picks the run up again.

Exit codes: 0 the run completed, 2 it is waiting for a person (or the person left
the chat), 1 the chat could not continue.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

try:
    from . import autocode_chat_panel as panel
    from . import autocode_client_actions as actions
    from . import autocode_events_command as events
except ImportError:
    import autocode_chat_panel as panel
    import autocode_client_actions as actions
    import autocode_events_command as events

AUTOCODE = (sys.executable, str(Path(__file__).resolve().parent / "autocode.py"))
POLL_SECONDS = 2.0
YES = ("y", "yes")
# What a relaunch must change, as TaskRun.advance_until_input and the scenario driver check.
PROGRESS_KEYS = ("status", "next_stage", "iteration", "phase", "needs")


class Left(Exception):
    """The person left the chat at a prompt; the run is unchanged."""


class Restored(Exception):
    """The person went back to a saved step: the chat now talks to the new run it made."""


class Switched(Exception):
    """The person opened another chat with /open: the chat now talks to that run."""


class NewChat(Exception):
    """The person asked for a new chat with /new: ask for a request and start a run."""


class Opened(Exception):
    """/open before any run is being talked to: the run the person chose."""

    def __init__(self, row: dict):
        super().__init__(row.get("run_dir"))
        self.row = row


class History:
    """This project's chats, from `autocode runs --json`: what /runs lists and /open opens.

    It reads only the project's run list, as `open_chat` does. Opening a chat raises,
    so the question being asked is left and the conversation carries on with that run.
    """

    def __init__(self, workspace: Path, *, command=AUTOCODE, env=None):
        self.workspace, self.command, self.env = Path(workspace), tuple(command), env
        self.rows: list[dict] = []

    def list(self) -> list[dict]:
        self.rows = panel.run_rows(project_runs(self.workspace, command=self.command, env=self.env))
        return self.rows

    def slash(self, name: str, argument: str, current=None):
        """/runs, /open N and /new; the other client commands need a run to act on."""
        if name == "/runs":
            try:
                return panel.runs_text(self.list(), current)
            except actions.ClientGap as error:
                return str(error)
        if name == "/new":
            if current is None:
                return "This is a new chat: type what you want done."
            raise NewChat
        if name == "/open":
            rows = self.rows or self.list()
            if not argument.isdigit() or not 1 <= int(argument) <= len(rows):
                return f"There is no chat {argument or 'to open'}. Type /runs to see them, then /open N."
            row = rows[int(argument) - 1]
            if current is not None and str(row.get("run_dir")) == str(current):
                return "That is this chat."
            if row.get("availability") not in (None, "available"):
                return f"That chat cannot be read: {row.get('reason') or row.get('availability')}."
            raise Opened(row)
        return "Open or start a chat first: type a request, or /runs to see this project's chats."


class ChatClient:
    """One run, one person, one terminal."""

    def __init__(
        self,
        workspace: Path,
        run_dir: Path,
        *,
        command=AUTOCODE,
        env: dict | None = None,
        ask: Callable[[str], str] = input,
        say: Callable[[str], None] = print,
        poll_seconds: float = POLL_SECONDS,
        observe: Callable[[dict], None] | None = None,
        wait: Callable[[float], str | None] | None = None,
    ):
        self.workspace, self.run_dir = Path(workspace), Path(run_dir)
        self.command = tuple(command)
        self.env = env
        self._ask, self._say = ask, say
        self.poll_seconds = poll_seconds
        # A renderer's view of the run, and its wait between polls; "pause" asks for the view's pause.
        self._observe = observe
        self._wait = wait or (lambda seconds: time.sleep(seconds))
        self.seen: set[str] = set()
        self.said: set[str] = set()  # message texts already shown, so a question is not printed twice
        self.started = False  # the first view prints the whole conversation, later ones only what is new
        self.last_line: tuple = ()
        self.restore_requests: dict[str, str] = {}  # one stable id per step, so a retried restore reconciles
        # /runs, /open and /new: the project's chats, and how a new one starts (start flags, run options).
        self.history: History | None = None
        self.new_run: tuple[str | None, list[str]] | None = None  # a new run's --mode and options
        self.switched: Callable[[Path, Path], None] | None = None  # the renderer starts the new chat's screen

    # ---- the CLI ---------------------------------------------------------------------------

    def cli(self, argv, *, env=None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [*self.command, *argv],
            capture_output=True,
            text=True,
            env={**(self.env or os.environ), **(env or {})},
            cwd=self.workspace,
        )

    def own(self, view: dict) -> tuple[Path, Path]:
        self.workspace, self.run_dir = actions.own(view, self.workspace, self.run_dir)
        return self.workspace, self.run_dir

    def status(self) -> dict:
        proc = self.cli(["--status", "--workspace", str(self.workspace), "--run-dir", str(self.run_dir)])
        try:
            view = json.loads(proc.stdout)["view"]
        except (ValueError, KeyError):
            raise actions.ClientGap(f"--status did not return a status view: {tail(proc)}") from None
        self.own(view)
        if self._observe is not None:
            self._observe(view)
        return view

    def take(self, command: actions.NamedCommand) -> None:
        """Run a named action against this run; an advancing one is watched while it works."""
        argv = [*command.argv, "--workspace", str(self.workspace), "--run-dir", str(self.run_dir)]
        if actions.advancing(command):
            self.advance(argv, command.env)
            return
        proc = self.cli(argv, env=command.env)
        if proc.returncode != 0:
            raise actions.ClientGap(f"AutoCode refused that: {tail(proc)}")

    def advance(self, argv, env=None) -> None:
        """One call that works until the run stops; meanwhile show what the view says.

        The child runs in its own process group, so Ctrl-C reaches only this client:
        the first asks for the view's boundary pause, the second interrupts the child
        and a third stops it. The chat never exits while the child still runs.
        """
        with tempfile.TemporaryFile("w+") as log:
            child = subprocess.Popen(
                [*self.command, *argv],
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                env={**(self.env or os.environ), **(env or {})},
                cwd=self.workspace,
                process_group=0,
            )
            interrupts, polls = 0, 0
            while True:
                try:
                    while child.poll() is None:
                        with contextlib.suppress(actions.ClientGap):  # a new run has not saved a checkpoint yet
                            self.show(self.status(), live=True)
                        # The first reads come sooner, so what the person just decided shows as taken.
                        polls += 1
                        if self._wait(self.poll_seconds if polls > 3 else min(self.poll_seconds, 0.5)) == "pause":
                            self.request_pause(again="Try /pause again in a moment.")
                    break
                except KeyboardInterrupt:
                    interrupts += 1
                    try:
                        if interrupts == 1 and self.request_pause():
                            continue
                    except KeyboardInterrupt:  # Ctrl-C again while the pause was being asked for
                        interrupts += 1
                    if interrupts == 2:
                        child.send_signal(signal.SIGINT)
                        self.say("Interrupting the current step... Ctrl-C again stops AutoCode.")
                    else:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(child.pid, signal.SIGTERM)
                        self.say("Stopping AutoCode...")
            if interrupts > 2 and child.returncode not in (0, 2):
                raise actions.ClientGap("stopped AutoCode in the middle of a step; `autocode chat` picks the run up")
            if child.returncode not in (0, 2):
                log.seek(0)
                raise actions.ClientGap(f"AutoCode exited {child.returncode}: {log.read()[-2000:].strip()}")

    def request_pause(self, *, again: str = "Press Ctrl-C again to interrupt the current step.") -> bool:
        try:
            view = self.status()
            words = actions.pause(view, f"chat-pause-{secrets.token_hex(8)}", self.workspace, self.run_dir)
        except actions.ClientGap as error:
            self.say(f"{error}. {again}")
            return True
        proc = self.cli(words)
        if proc.returncode != 0:
            self.say(f"The pause was not accepted: {tail(proc)}. {again}")
            return True
        self.say(f"{(view.get('pause') or {}).get('effect') or 'Pausing after this step.'} Ctrl-C interrupts it now.")
        return True

    # ---- saved code steps ------------------------------------------------------------------

    def compare(self, checkpoint_id: str) -> dict:
        """The view's named compare: what changed since the saved step, and the token a restore needs."""
        words = actions.checkpoint(self.status(), checkpoint_id, "compare", self.workspace, self.run_dir)
        return self.checkpoint_result(self.cli(words))

    def restore(self, checkpoint_id: str, comparison: dict) -> None:
        """The view's named restore, bound to the comparison the person read; then talk to the new run."""
        words = actions.checkpoint(self.status(), checkpoint_id, "restore", self.workspace, self.run_dir)
        request = self.restore_requests.setdefault(checkpoint_id, f"chat-restore-{secrets.token_hex(8)}")
        token = {"AUTOCODE_CHECKPOINT_EXPECTED_TOKEN": str(comparison.get("expected_token") or "")}
        result = self.checkpoint_result(
            self.cli([request if word == "REQUEST_ID" else word for word in words], env=token)
        )
        del self.restore_requests[checkpoint_id]
        self.workspace, self.run_dir = Path(result["workspace"]), Path(result["run_dir"])
        self.say(
            f"\nAutoCode: Went back on a new branch, {result.get('branch')}. Your earlier work stays on "
            f"{result.get('preserved_branch')}. The new run is paused and checks every requirement again."
        )
        raise Restored

    @staticmethod
    def checkpoint_result(proc: subprocess.CompletedProcess) -> dict:
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            payload = {}
        if proc.returncode != 0 or not isinstance(payload, dict) or "error" in payload:
            message = ((payload.get("error") or {}).get("message") if isinstance(payload, dict) else None) or tail(proc)
            raise actions.ClientGap(f"AutoCode refused that: {message}")
        return payload

    def working_diff(self, view: dict, rows: list[dict]) -> str:
        """``/diff now``: the view's named, display-only diff of what the running Builder has changed."""
        try:
            result = self.checkpoint_result(self.cli(actions.live_diff(view, self.workspace, self.run_dir)))
        except actions.ClientGap as error:
            return str(error)
        ids = [str(row.get("id")) for row in rows]
        since = result.get("since")
        phrase = "the run started" if since == "start" or since not in ids else f"step {ids.index(since) + 1}"
        return panel.comparison_text(result, since=phrase, working=True)

    def slash(self, name: str, argument: str):
        """``/diff`` and ``/undo`` from the full-screen chat: what to show, or a question to confirm first.
        ``/runs``, ``/open`` and ``/new`` go to the project's chats; opening one leaves this run as it is."""
        if name in ("/runs", "/open", "/new"):
            if self.history is None:
                return "This chat has no project list."
            try:
                return self.history.slash(name, argument, current=self.run_dir)
            except Opened as opened:
                self.talk_to(Path(opened.row.get("workspace") or self.workspace), Path(opened.row["run_dir"]))
                raise Switched from None
        view = self.status()
        rows = panel.steps(view)
        if not argument:
            return panel.steps_text(view, name)
        if name == "/diff" and argument == "now":
            return self.working_diff(view, rows)
        if not argument.isdigit() or not 1 <= int(argument) <= len(rows):
            return f"There is no step {argument}. " + (f"Choose 1 to {len(rows)}." if rows else "No code is saved yet.")
        number, row = int(argument), rows[int(argument) - 1]
        try:
            comparison = self.compare(str(row.get("id")))
        except actions.ClientGap as error:
            return str(error)
        if name == "/diff":
            return panel.comparison_text(comparison, number)
        if comparison.get("blocked_reason"):
            return panel.blocked_text(view, comparison["blocked_reason"])

        def go_back() -> str:
            try:
                self.restore(str(row.get("id")), comparison)
            except actions.ClientGap as error:
                return str(error)
            return "Done."  # restore leaves the question by raising Restored

        files = len(comparison.get("changed_files") or [])
        return panel.Confirm(
            f"Step {number}: {row.get('label') or row.get('id')}. {files} file{' differs' if files == 1 else 's differ'} "
            "from it now. Going back makes a new run on a new branch; your current branch and its "
            "later work stay as they are, and every requirement is checked again.",
            f"Go back to step {number}?",
            go_back,
        )

    # ---- the person ------------------------------------------------------------------------

    def say(self, text: str) -> None:
        self._say(text)

    def ask(self, prompt: str) -> str:
        try:
            return self._ask(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            raise Left from None

    def show(self, view: dict, *, live=False) -> None:
        """New conversation messages, the notice and the progress line, each once."""
        chat = view.get("chat") or {}
        for row in chat.get("messages") or []:
            key = str(row.get("id"))
            if row.get("streaming") or key in self.seen:
                continue
            self.seen.add(key)
            if row.get("role") == "user" and self.started:
                continue  # the person just typed it
            text = (row.get("text") or "").strip()
            if row.get("role") != "user" and text in self.said:
                continue  # already shown, as the question being asked
            self.said.add(text)
            who = "You" if row.get("role") == "user" else "AutoCode"
            self.say(f"\n{who}: {text}")
        self.started = True
        progress = view.get("progress") or {}
        line = (chat.get("notice"), progress.get("line"))
        if line != self.last_line:
            self.last_line = line
            if chat.get("notice"):
                self.say(f"\n{chat['notice']}")
            if progress.get("line") and (live or not chat.get("notice")):
                self.say(f"  · {progress['line']}")

    # ---- the conversation ------------------------------------------------------------------

    def talk_to(self, workspace: Path, run_dir: Path) -> None:
        """Carry on with another run: its whole conversation is shown afresh."""
        self.workspace, self.run_dir = Path(workspace), Path(run_dir)
        self.seen, self.said, self.started, self.last_line = set(), set(), False, ()
        if self.switched is not None:
            self.switched(self.workspace, self.run_dir)
        else:
            self.say(f"\n--- {self.run_dir.name} ---")

    def begin(self, brief: str, start_args, options) -> None:
        """Start a new run here and talk to it, as `start` does for the first one."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        workspace = self.workspace.resolve()
        self.talk_to(workspace, workspace / ".autocode" / "runs" / f"{stamp}-chat-{secrets.token_hex(3)}")
        self.say(f"\nYou: {brief}")
        self.started = True
        self.advance(
            [brief, "--in-place", "--no-chat", *start_args, *options]
            + ["--workspace", str(workspace), "--new-run-dir", str(self.run_dir)]
        )

    def new_chat(self) -> None:
        """/new: ask for a request and start a run; nothing typed keeps the current chat."""
        brief = self.ask("What do you want done? ")
        if not brief:
            self.say("Nothing started; this chat carries on.")
            return
        mode, options = self.new_run or (None, [])
        self.begin(brief, run_mode(mode, command=self.command, env=self.env), options)

    def converse(self) -> int:
        try:
            while True:
                try:
                    if (stop := self.step()) is not None:
                        return stop
                except (Restored, Switched):
                    continue  # going back to a saved step, or /open: carry on with that run
                except NewChat:
                    self.new_chat()
        except Left:
            self.say(f"\nLeft the chat; the run is unchanged. Pick it up with: autocode chat --run-dir {self.run_dir}")
            return 2
        except actions.ClientGap as error:
            self.say(f"\n{error}")
            return 1

    def step(self) -> int | None:
        """Act on one status view; an exit code once the chat ends here."""
        view = self.status()
        self.show(view)
        if view.get("done"):
            if not self.follow_up(view):
                self.say("\nDone.")
                return 0
            return None
        need = view.get("needs") or {}
        if need.get("kind") == "continue":
            if actions.working(view):
                self.watch()
            else:
                self.take(actions.named(view, self.workspace, self.run_dir))
                self.require_progress(view)
            return None
        return None if self.respond(view) else 2

    def require_progress(self, before: dict) -> None:
        """A relaunch that changed nothing would be relaunched forever: stop and say where it stands."""
        after = self.status()
        if after.get("done") or any(after.get(key) != before.get(key) for key in PROGRESS_KEYS):
            return
        raise actions.ClientGap(
            f"AutoCode made no progress at {before.get('status')} (next stage {before.get('next_stage')}); "
            f"see autocode --status --run-dir {self.run_dir}"
        )

    def watch(self) -> None:
        """Another process is advancing the run: show its progress until it stops."""
        try:
            while actions.working(view := self.status()):
                self.show(view, live=True)
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            if not self.request_pause():
                raise Left from None

    def respond(self, view: dict) -> bool:
        """Act on what the view asks of a person. False: the chat stops here, the run waits."""
        need = view.get("needs") or {}
        offer = (view.get("chat") or {}).get("input") or {}
        if need.get("kind") == "approve_plan":
            return self.approve(view)
        if offer.get("kind") == "resolver_response":
            return self.resolver_reply(view)
        try:
            command = actions.named(view, self.workspace, self.run_dir)
            decided = actions.decisions(view, command)
        except actions.ClientGap:
            return self.hand_off(view)  # another run's command (a dependency), or none at all (a stop)
        if actions.unsupplied(view, command):
            return self.hand_off(view)
        if (view.get("chat") or {}).get("phase") == "requirements" and need.get("kind") == "answer":
            return self.requirements(view, command)
        if any(row.kind in ("answer", "model") for row in decided):
            return self.answer(view, command, decided)
        return self.confirm(view, command, decided)

    def hand_off(self, view: dict) -> bool:
        """A need this client takes no action for: say why, and the command the view names, if any."""
        need = view.get("needs") or {}
        label = view.get("pause_category_label") or need.get("kind")
        why = need.get("reason") or view.get("stop_reason") or ""
        self.say(f"\nAutoCode needs you for this one ({label}). {why}".rstrip())
        if view.get("next_command"):
            self.say(f"Run it yourself:\n  {view['next_command']}")
        else:
            self.say(f"There is nothing to run from here; see autocode --explain --run-dir {self.run_dir}")
        return False

    def approve(self, view: dict) -> bool:
        token = actions.approval_token(view)
        plan = actions.plan_for_approval(view)
        if not token or plan is None:
            raise actions.ClientGap("The complete plan is unavailable; review it with autocode --show-goal first")
        self.say("\n" + render_plan(plan))
        self.say(f"(Everything the plan records: autocode --show-goal --run-dir {self.run_dir})")
        context = (view.get("chat") or {}).get("context") or ""
        reply = self.ask("\nApprove this plan? [y/N], or type what to change: ")
        if reply.lower() in YES:
            fresh = self.status()
            if actions.approval_token(fresh) != token:
                self.say("The plan changed while you were reading it; here is the new one.")
                return True
            self.take(actions.named(fresh, self.workspace, self.run_dir))
            return self.continue_after_input()
        if reply.lower() in ("", "n", "no"):
            self.say(f"Plan not approved. Pick it up with: autocode chat --run-dir {self.run_dir}")
            return False
        return self.send(reply, context)

    def requirements(self, view: dict, command: actions.NamedCommand) -> bool:
        """The Requirements conversation: the person replies in their own words, as in the extension.

        Its question is already in the conversation. The one decision it offers is
        confirming the understanding it summarized (``chat.review``).
        """
        chat = view.get("chat") or {}
        review, context = chat.get("review"), chat.get("context") or ""
        summary = ((review or {}).get("summary") or "").strip()
        if summary and summary not in self.said:
            self.said.add(summary)
            self.say(f"\nAutoCode: {summary}")
        prompt = "\nYou: " if not review else f"\n{review['label']}? [y/N], or type what to change: "
        reply = self.ask(prompt)
        if review and reply.lower() in YES:
            fresh = self.status()
            if (fresh.get("chat") or {}).get("context") != context:
                self.say("The conversation changed meanwhile; here is where it stands now.")
                return True
            self.take(actions.fill(fresh, command, {review["question_id"]: review["answer"]}))
            return self.continue_after_input()
        declined = reply.lower() in ("", "n", "no") if review else not reply
        if declined:
            self.say(f"Nothing sent. Pick it up with: autocode chat --run-dir {self.run_dir}")
            return False
        return self.send(reply, context)

    def answer(self, view: dict, command: actions.NamedCommand, decided: list[actions.Decision]) -> bool:
        answers = {}
        for row in decided:
            answers[row.key] = self.decide(row, view.get("needs") or {})
        fresh = self.status()
        if fresh.get("needs") != view.get("needs"):
            self.say("The question changed while you were answering; here is the current one.")
            return True
        self.take(actions.fill(fresh, command, answers))
        return self.continue_after_input()

    def decide(self, row: actions.Decision, need: dict) -> str:
        if row.kind == "integer":
            spec = row.integer or {}
            while True:
                text = self.ask(f"\n{spec.get('label')} ({spec.get('minimum')} to {spec.get('maximum')}): ")
                try:
                    return actions.integer_value(text, spec)
                except actions.ClientGap as error:
                    self.say(str(error))
        if row.kind == "model":
            route = need.get("route") or {}
            if asked := (row.question or {}).get("question"):
                self.say(f"\nAutoCode: {asked}")
            if route.get("stopped_model"):
                self.say(f"  Stopped on: {route['stopped_model']} ({route.get('cause') or 'quota'})")
            if route.get("candidates"):
                self.say("  Models it can continue on: " + ", ".join(map(str, route["candidates"])))
            while not (text := self.ask(f"\nWhich model should {row.key.removeprefix('route-')} continue on? ")):
                pass
            return text
        question = row.question or {}
        asked = (question.get("question") or "").strip()
        if asked not in self.said:
            self.said.add(asked)
            self.say(f"\nAutoCode: {asked or row.key}")
        if question.get("why"):
            self.say(f"  Why: {question['why']}")
        options = question.get("options") or []
        for number, option in enumerate(options, 1):
            self.say(f"  {number}. {option}")
        default = (question.get("proposed_default") or "").strip()
        hint = f"Enter for '{default}'" if default else ""
        hint = ", ".join(filter(None, [f"1-{len(options)}" if options else "", hint]))
        while True:
            text = self.ask(f"You{f' ({hint})' if hint else ''}: ")
            if text.isdigit() and 1 <= int(text) <= len(options):
                return options[int(text) - 1]
            if text:
                return text
            if default:
                return default
            self.say("Please type an answer.")

    def resolver_reply(self, view: dict) -> bool:
        context = (view.get("chat") or {}).get("context") or ""
        reply = self.ask("\nYour reply (Enter to leave it for now): ")
        if not reply:
            self.say(f"Nothing sent. The run is paused; pick it up with: autocode chat --run-dir {self.run_dir}")
            return False
        self.take(actions.message(self.status(), reply, context, self.workspace, self.run_dir))
        return True

    def confirm(self, view: dict, command: actions.NamedCommand, decided: list[actions.Decision]) -> bool:
        """A stop a person decides on: say why, and take the named action only on their yes."""
        need = view.get("needs") or {}
        label = view.get("pause_category_label") or need.get("kind")
        why = need.get("reason") or view.get("stop_reason") or (view.get("progress") or {}).get("needs_you") or ""
        if need.get("kind") == "review":
            criteria = actions.review_criteria(view)
            if criteria is None:
                return self.hand_off(view)  # never ask to accept criteria the person cannot read
            self.say(f"\nAutoCode: {need.get('question') or 'Review these criteria before accepting them.'}")
            for row in criteria:
                self.say(f"  [{row['id']}] {row['criterion']}")
            self.say(f"(The evidence for each: autocode review --show --run-dir {self.run_dir})")
        else:
            self.say(f"\nAutoCode stopped: {label}. {why}".rstrip())
        if need.get("recovery_hint"):
            self.say(need["recovery_hint"])
        answers = {row.key: self.decide(row, need) for row in decided}
        prompt = "Accept these criteria" if need.get("kind") == "review" else "Continue"
        # On this run, so its paths are left out; an issued token stays out of the terminal too.
        self.say(f"Next it runs: autocode {shlex.join(actions.fill(view, command, answers).argv)}")
        if self.ask(f"{prompt}? [y/N]: ").lower() not in YES:
            self.say(f"Left as it is. Pick it up with: autocode chat --run-dir {self.run_dir}")
            return False
        fresh = self.status()
        if fresh.get("needs") != view.get("needs"):
            self.say("The run changed meanwhile; here is where it stands now.")
            return True
        self.take(actions.fill(fresh, command, answers))
        return True

    def follow_up(self, view: dict) -> bool:
        offer = (view.get("chat") or {}).get("input") or {}
        if offer.get("kind") != "follow_up":
            return False
        context = (view.get("chat") or {}).get("context") or ""
        try:
            reply = self.ask("\nAnything else? Type a follow-up, or press Enter to finish: ")
        except Left:
            return False
        return bool(reply) and self.send(reply, context)

    def send(self, text: str, context: str) -> bool:
        """A message through the view's chat input, then on with the run, as the extension does."""
        self.take(actions.message(self.status(), text, context, self.workspace, self.run_dir))
        return self.continue_after_input()

    def continue_after_input(self) -> bool:
        view = self.status()
        if not view.get("done") and (view.get("needs") or {}).get("kind") == "continue" and not actions.working(view):
            self.take(actions.named(view, self.workspace, self.run_dir))
        return True


def render_plan(plan: dict) -> str:
    """The plan a person approves, in the extension's sections: what is checked, the limits, what it may do."""
    lines = ["Plan to approve:"]
    for row in plan["acceptance_criteria"]:
        lines.append(f"  [{row['id']}] {row['criterion']}")
        lines.append(f"      Checked by: {row['verification_method']}")
        if row["human_review"]:
            lines.append("      You check this one yourself.")
    for title, rows in (("Constraints", plan["constraints"]), ("What it may do", plan["permission_boundaries"])):
        if rows:
            lines.append(f"{title}:")
            lines.extend(f"  - {text}" for text in rows)
    return "\n".join(lines)


def tail(proc: subprocess.CompletedProcess) -> str:
    return ((proc.stderr or "").strip() or (proc.stdout or "").strip())[-2000:] or f"exit {proc.returncode}"


def start(workspace: Path, brief: str, start_args, options, *, command=AUTOCODE, env=None, **kwargs) -> ChatClient:
    """A client for a fresh run directory it names, so it watches the run it created."""
    workspace = workspace.resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = workspace / ".autocode" / "runs" / f"{stamp}-chat-{secrets.token_hex(3)}"
    client = ChatClient(workspace, run_dir, command=command, env=env, **kwargs)
    client.say(f"\nYou: {brief}")
    client.started = True
    client.advance(
        [
            brief,
            "--in-place",
            "--no-chat",
            *start_args,
            *options,
            "--workspace",
            str(workspace),
            "--new-run-dir",
            str(run_dir),
        ]
    )
    return client


def run_mode(mode: str | None, *, command=AUTOCODE, env=None) -> list[str]:
    """The start flags of a mode `autocode modes list` names; the core's default without one."""
    proc = subprocess.run([*command, "modes", "list", "--json"], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise actions.ClientGap(f"could not list run modes: {tail(proc)}")
    listed = json.loads(proc.stdout)
    wanted = mode or listed.get("default")
    for row in listed.get("modes") or []:
        if row.get("id") == wanted:
            return list(row.get("start_args") or [])
    names = ", ".join(row.get("id", "") for row in listed.get("modes") or [])
    raise actions.ClientGap(f"no run mode {wanted!r}; choose one of: {names}")


def project_runs(workspace: Path, *, command=AUTOCODE, env=None) -> list[dict]:
    """Every run of the project, as `autocode runs --json` lists them."""
    proc = subprocess.run(
        [*command, "runs", "--workspace", str(workspace), "--json"], capture_output=True, text=True, env=env
    )
    if proc.returncode == 2 and not proc.stdout.strip() and proc.stderr.strip().startswith("No runs found"):
        return []  # no runs in this project yet
    if proc.returncode != 0:
        raise actions.ClientGap(f"could not list this project's runs: {tail(proc)}")
    return list(json.loads(proc.stdout)["runs"])


def unfinished(workspace: Path, *, command=AUTOCODE, env=None) -> list[dict]:
    """The project's runs that need a person or are still working, as `autocode runs --json` lists them."""
    return [
        row
        for row in project_runs(workspace, command=command, env=env)
        if row.get("bucket") in ("needs you", "working")
    ]


def open_chat(
    workspace: Path,
    mode=None,
    options=(),
    *,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    hint: bool = False,
    **kwargs,
):
    """`autocode chat` with nothing named: pick up the run left open here, or ask what to do.

    One unfinished run is picked up; with several the person chooses one or a new
    task. With none, the chat asks for a request and starts a run with the core's
    mode and options from the command line, if any. None: the person left without choosing.
    In the full-screen chat ``/runs`` and ``/open N`` work at that question too (``Opened``).
    """
    command, env = kwargs.get("command", AUTOCODE), kwargs.get("env")
    runs = unfinished(workspace, command=command, env=env)
    earlier = project_runs(workspace, command=command, env=env) if hint and not runs else []
    try:
        if len(runs) == 1:
            say(f"Picking up {runs[0]['name']}.")
            return ChatClient(workspace, Path(runs[0]["run_dir"]), ask=ask, say=say, **kwargs)
        if runs:
            say("Runs waiting here:")
            for number, row in enumerate(runs, 1):
                say(f"  {number}. {row['name']} ({row.get('pause_category_label') or row['bucket']})")
            while (choice := ask(f"Pick one (1-{len(runs)}), or type a new request: ").strip()).isdigit():
                if 1 <= int(choice) <= len(runs):
                    return ChatClient(workspace, Path(runs[int(choice) - 1]["run_dir"]), ask=ask, say=say, **kwargs)
                say(f"There is no run {choice}.")
            brief = choice
        else:
            if earlier:
                say(f"{len(earlier)} earlier chat{'s' if len(earlier) != 1 else ''} here. Type /runs to see them.")
            brief = ask("What do you want done? ").strip()
    except Opened as opened:
        row = opened.row
        return ChatClient(Path(row.get("workspace") or workspace), Path(row["run_dir"]), ask=ask, say=say, **kwargs)
    except (EOFError, KeyboardInterrupt):
        brief = ""
    if not brief:
        say("Nothing started.")
        return None
    return start(workspace, brief, run_mode(mode, command=command, env=env), options, ask=ask, say=say, **kwargs)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="autocode chat", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    result.add_argument("brief", nargs="?", help="what you want done; starts a new run (omit to pick up a run)")
    result.add_argument("--brief", dest="brief_option", metavar="TEXT", help="the brief, for one that starts with -")
    result.add_argument("--workspace", type=Path, help="the project; the current directory by default")
    result.add_argument("--run-dir", type=Path, help="pick up this run instead of the project's open one")
    result.add_argument("--mode", help="run mode for a new run, from `autocode modes list` (default: the core's)")
    result.add_argument("--line", action="store_true", help="the line-by-line chat, even in a terminal")
    return result


def terminal_ui(*, force_line: bool = False):
    """The full-screen chat when both ends are a terminal; None keeps the line interface.

    A terminal without curses support falls back to line mode rather than failing
    the chat.
    """
    if force_line or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        from . import autocode_chat_tui as tui
    except ImportError:
        try:
            import autocode_chat_tui as tui
        except (ImportError, OSError):
            return None
    try:
        return tui.ChatTUI()
    except RuntimeError as error:
        print(f"autocode chat: {error}; using line mode", file=sys.stderr)
        return None


def cli(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    options: list[str] = []
    if "--" in argv:
        index = argv.index("--")
        argv, options = argv[:index], argv[index + 1 :]
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.brief_option is not None:
        if args.brief is not None:
            arguments.error("give the brief once, as an argument or with --brief")
        args.brief = args.brief_option
    if args.brief is not None and args.run_dir is not None:
        arguments.error("a brief starts a new run; --run-dir picks up an existing one, not both")
    if args.run_dir is not None and (options or args.mode):
        arguments.error("run options and --mode apply to a new run, not to --run-dir")
    ui = None
    try:
        if args.brief is not None and not args.brief.strip():
            arguments.error("the brief is empty")
        ui = terminal_ui(force_line=args.line)
        ask, say = (ui.ask, ui.say) if ui is not None else (input, print)
        seen = {"observe": ui.observe, "wait": ui.wait} if ui is not None else {}
        workspace = (args.workspace or Path.cwd()).expanduser()
        history = History(workspace.resolve())
        if ui is not None and args.run_dir is None:
            ui.set_context(workspace)
            ui.bind(history.slash)  # /runs and /open work before a chat is open
        if args.brief is not None:
            client = start(workspace, args.brief, run_mode(args.mode), options, ask=ask, say=say, **seen)
        elif args.run_dir is not None:
            run_dir, workspace = events.resolve(args.workspace, args.run_dir)
            client = ChatClient(workspace, run_dir, ask=ask, say=say, **seen)
        else:
            client = open_chat(workspace.resolve(), args.mode, options, ask=ask, say=say, hint=ui is not None, **seen)
            if client is None:
                return 2
        client.history = history
        client.new_run = (args.mode, options)
        if ui is not None:
            ui.set_context(client.workspace, client.run_dir)
            ui.bind(client.slash)
            client.switched = ui.switch
        return client.converse()
    except (actions.ClientGap, OSError, ValueError) as error:
        if ui is not None:
            ui.close()  # first, so the message lands on the terminal and not on the closing screen
        print(f"autocode chat: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if ui is not None:
            ui.close()
        print("\nautocode chat: interrupted.", file=sys.stderr)
        return 130
    finally:
        if ui is not None:
            ui.close()


if __name__ == "__main__":
    raise SystemExit(cli())
