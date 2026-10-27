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
    autocode chat --mode full "Fix the typo"        start with a run mode from `autocode modes list`
    autocode chat                                   pick up this project's open run, or ask what to do
    autocode chat --run-dir RUN                     pick up that run
    autocode chat "..." -- --engine claude          options for a new run, after --
    autocode chat --brief=-v                        a brief that starts with -

Besides the status view it reads only `autocode runs --json` (the project's runs: the
open one to pick up), `autocode runs --all --json` (every project's chats, for `/runs`,
`/open` and `/new` in the full-screen chat) and `autocode modes list --json` (a new
run's mode). The full-screen chat's
`/diff` and `/undo` take a saved code step's `compare` and `restore` actions, which the
status view names (`view["code_checkpoints"]`); going back makes a new run on a new
branch, and the chat carries on with it. For a run whose view lists linked runs, `/changeset`
reads `autocode changeset status --json` and takes only the set approval or joint check it
names (autocode_chat_changeset).

While a stage works, Ctrl-C asks the run to pause after the current step (the
view's named pause). For an owned worker, a second Ctrl-C interrupts the step
and a third stops AutoCode. An attached viewer requests only the named boundary
pause. Leaving the chat at a question or a plan changes nothing;
`autocode chat` picks the run up again.

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
    from . import autocode_chat_attention as attention
    from . import autocode_chat_attention_discovery as attention_discovery
    from . import autocode_chat_changeset as changeset
    from . import autocode_chat_context as contexts
    from . import autocode_chat_coordination as coordination
    from . import autocode_chat_inventory as inventory
    from . import autocode_chat_panel as panel
    from . import autocode_chat_polling as polling
    from . import autocode_chat_setup as setup
    from . import autocode_chat_worker as worker
    from . import autocode_client_actions as actions
    from . import autocode_events_command as events
except ImportError:
    import autocode_chat_attention as attention
    import autocode_chat_attention_discovery as attention_discovery
    import autocode_chat_changeset as changeset
    import autocode_chat_context as contexts
    import autocode_chat_coordination as coordination
    import autocode_chat_inventory as inventory
    import autocode_chat_panel as panel
    import autocode_chat_polling as polling
    import autocode_chat_setup as setup
    import autocode_chat_worker as worker
    import autocode_client_actions as actions
    import autocode_events_command as events

AUTOCODE = (sys.executable, str(Path(__file__).resolve().parent / "autocode.py"))
POLL_SECONDS = 2.0
# While a reply is being written the view is read this often, so each new extract shows at once. A reply
# that has not changed for STREAM_QUIET_POLLS reads (a model running tools between messages) is read at
# the ordinary pace again: every read is a new `autocode --status` process.
STREAM_POLL_SECONDS = 0.25
STREAM_QUIET_POLLS = 8
OTHERS_SECONDS = 10.0  # how often chats opened earlier this session are checked while AutoCode works
YES = ("y", "yes")
# What a relaunch must change, as TaskRun.advance_until_input and the scenario driver check.
PROGRESS_KEYS = ("status", "next_stage", "iteration", "phase", "needs")


class Left(Exception):
    """The person left the chat at a prompt; the run is unchanged."""


class Restored(Exception):
    """The person went back to a saved step: the chat now talks to the new run it made."""


class Switched(Exception):
    """The person opened another chat with /open: the chat now talks to that run."""


class SetApproved(Exception):
    """/changeset approved every plan of the change set: the chat reads this run again and carries on."""


class Refused(actions.ClientGap):
    """The core refused a named action (another runner holds the run, a stale token): the chat stays
    open, says so and reads the run again, instead of leaving with the plan and the draft (#2000)."""


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

    def __init__(self, workspace: Path, *, command=AUTOCODE, env=None, new: setup.NewChat | None = None):
        self.workspace, self.command = Path(workspace), tuple(command)
        self.env = coordination.environment(workspace, env)
        self.rows: list[dict] = []
        self.attention: attention_discovery.Discovery | None = None
        self.overview: inventory.Overview | None = None
        self.left_out = 0
        self.new = new or setup.NewChat()  # what /new and the first question start: /mode, /engine, /connect
        # Runs a backend's interactive sign-in or setup on the terminal itself (the full-screen chat steps aside).
        self.outside: Callable[[list[str], str], int] | None = None

    def read(self, argv: list[str]) -> dict:
        """One of the listings the chat may read (`modes list`, `connections list`), as JSON."""
        proc = subprocess.run([*self.command, *argv], capture_output=True, text=True, env=self.env)
        try:
            return json.loads(proc.stdout)
        except ValueError:
            raise actions.ClientGap(f"`autocode {' '.join(argv)}` failed: {tail(proc)}") from None

    def connection_rows(self) -> list[dict]:
        """`connections list`, or none when it cannot be read: the banner and /engine still show the rest."""
        try:
            return setup.connections(self.read(["connections", "list", "--json"]))
        except actions.ClientGap:
            return []

    def summary(self) -> str:
        rows = self.connection_rows()
        try:
            return setup.summary(self.read(["modes", "list", "--json"]), self.new, rows)
        except actions.ClientGap:
            return setup.summary(None, self.new, rows)

    def chose(self, done: str) -> str:
        kept = self.new.save(self.workspace)
        return done + ("" if kept else " (for this session: the project has no .autocode yet)")

    def forget_unlisted_mode(self) -> None:
        """A saved mode the core no longer lists gives way to its default; an unreadable list keeps it."""
        with contextlib.suppress(actions.ClientGap):
            self.new.mode = setup.listed_mode(self.read(["modes", "list", "--json"]), self.new.mode)

    def mode(self, argument: str) -> str:
        listed = self.read(["modes", "list", "--json"])
        if not argument:
            return setup.modes_text(listed, self.new)
        if (chosen := setup.choose_mode(listed, argument)) is None:
            return f"There is no mode {argument!r}.\n" + setup.modes_text(listed, self.new)
        self.new.mode = chosen
        return self.chose(f"New chats start in {setup.mode_label(listed, chosen)}.")

    def engine(self, argument: str) -> str:
        if not argument:
            return setup.engines_text(self.new, self.connection_rows())
        if (chosen := setup.choose_engine(argument)) is None:
            return f"There is no engine {argument!r}.\n" + setup.engines_text(self.new, self.connection_rows())
        self.new.options = setup.with_engine(self.new.options, chosen)
        return self.chose(f"New chats run on {setup.ENGINE_LABELS[chosen]}.")

    def connect(self, argument: str) -> str:
        """/connect: list the backends; /connect NAME check|login|setup|use takes what that row offers."""
        rows = setup.connections(self.read(["connections", "list", "--json"]))
        name, _, wanted = argument.partition(" ")
        if not name:
            return setup.connections_text(rows, self.new)
        row = next((row for row in rows if row["id"] == name), None)
        if row is None:
            return f"There is no connection {name!r}.\n" + setup.connections_text(rows, self.new)
        wanted = wanted.strip() or "check"
        if wanted == "use":
            if row.get("connection_status") in setup.NOT_USABLE:
                return f"{row.get('label') or name} cannot start chats yet: {setup.state(row)}."
            self.new.options = setup.with_provider(self.new.options, name)
            return self.chose(f"New chats use {row.get('label') or name}. Chats already started keep their own.")
        offered = setup.action(rows, name, wanted)
        if offered is None:
            return f"{row.get('label') or name} offers no {wanted!r} now.\n" + setup.connections_text([row], self.new)
        words = list(offered["next_command"])
        if wanted == "check":
            checked = self.read(words)
            if checked.get("id") != name:
                return f"The check answered for another connection: {checked.get('id')!r}."
            return f"{checked.get('label') or name}: {setup.state(checked)}. {checked.get('detail') or ''}".rstrip()
        if self.outside is None:
            return f"{offered.get('label')} needs a terminal: run autocode {' '.join(words)}"
        code = self.outside(
            [*self.command, *words], str(offered.get("label") or wanted)
        )  # sign-in or setup prompts on the terminal itself
        return f"{offered.get('label')} finished (exit {code}). Check it with /connect {name} check."

    def list(self) -> list[dict]:
        """Every project's chats (`runs --all`, the per-user registry), and this project's own runs,
        which are listed even before the registry knows them; grouped by project, this one first."""
        listed = [
            *project_runs(self.workspace, command=self.command, env=self.env),
            *every_project_runs(command=self.command, env=self.env),
        ]
        here = self.workspace.resolve()
        self.rows, self.left_out = panel.run_rows(listed, here=here), panel.hidden(listed, here)
        return self.rows

    def slash(self, name: str, argument: str, current=None):
        """/runs, /open N and /new; the other client commands need a run to act on."""
        if name == "/repos":
            return coordination.repositories(self, argument)
        if self.overview is not None and name in ("/open", "/new"):
            self.overview.cancel_navigation()
        if name == "/attention":
            return (
                self.attention.menu()
                if self.attention is not None
                else "Open a terminal chat to inspect saved attention."
            )
        if name == "/runs":
            if self.overview is not None:
                return self.overview.request(self, current)
            try:
                rows = self.list()
                return panel.runs_text(rows, current, here=self.workspace.resolve(), left_out=self.left_out)
            except actions.ClientGap as error:
                return str(error)
        if name in ("/mode", "/engine", "/connect"):
            try:
                return {"/mode": self.mode, "/engine": self.engine, "/connect": self.connect}[name](argument)
            except actions.ClientGap as error:
                return str(error)
        if name == "/new":
            if current is None:
                return "This is a new chat: type what you want done."
            raise NewChat
        if name == "/open":
            if not self.rows and self.overview is not None:
                return self.overview.request(self, current, then=lambda: self.slash(name, argument, current))
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
        unavailable: Callable[[str], None] | None = None,
        wait: Callable[[float], str | None] | None = None,
        stream: Callable[[str | None], None] | None = None,
        execution: dict | None = None,
        launched: Callable[[dict], None] | None = None,
    ):
        self.workspace, self.run_dir = Path(workspace), Path(run_dir)
        self.command = tuple(command)
        self.env = coordination.environment(workspace, env)
        self._ask, self._say = ask, say
        self.poll_seconds = poll_seconds
        # A renderer's view of the run, and its wait between polls; "pause" asks for the view's pause.
        self._observe = observe
        self._unavailable = unavailable
        self._wait = wait or (lambda seconds: time.sleep(seconds))
        self._stream = stream  # the reply AutoCode is writing, as the view streams it; the line chat skips it
        # Chats opened earlier this session, by run: (workspace, what each last waited on), so that one
        # starting to wait or finishing is announced, as the extension notifies for its other tabs.
        self.others: dict[str, tuple[Path, tuple | None]] = {}
        self.others_checked = 0.0
        self.identity: tuple | None = None
        # This session's chats as tabs, in the order they were opened, with a title each (the task's
        # first line); the renderer draws them (``tabs_changed``) and /tab N switches between them.
        self.tab_order: list[str] = [str(self.run_dir)]
        self.titles: dict[str, str] = {}
        self.tabs_changed: Callable[[list[dict]], None] | None = None
        self.seen: set[str | tuple[str, str]] = set()
        self.viewed: set[str] = set()  # runs that have answered --status, so a refusal before one is told apart
        self.said: set[str] = set()  # message texts already shown, so a question is not printed twice
        self._local_echoes: set[tuple[str, str]] = set()  # direct prompts awaiting their next public-message echo
        self._replied_context: str | None = None  # the chat context the last sent reply answered
        self.started = False  # the first view prints the whole conversation, later ones only what is new
        self.last_line: tuple = ()
        self.last_progress: tuple | None = None  # the stage and counts the last progress line reported
        self.last_notice: str | None = None  # the notice said last, so a progress change does not repeat it
        self._live_text: str | None = None  # the streaming reply from the last show(), for the poll pace
        self._live_quiet = 0  # reads since the streaming reply last changed
        self.restore_requests: dict[str, str] = {}  # one stable id per step, so a retried restore reconciles
        # /runs, /open and /new: the project's chats, and how a new one starts (start flags, run options).
        self.history: History | None = None
        self.switched: Callable[[Path, Path | None], bool | None] | None = (
            None  # true if renderer restored its transcript
        )
        self._display_contexts = contexts.Contexts()
        self._attention = attention.Inbox()
        self._attention_action = None
        self.attention_changed: Callable[[dict], None] | None = None
        self._composing_new = False
        self._poller = polling.StatusPoller(self.command, env=self.env)
        self._overview = inventory.Overview(self._poller, self.say)
        self._status_view: dict | None = None
        self._status_serial = 0
        self._status_error = ""
        self._read_errors: dict[str, str] = {}
        self._pause_pending = False
        self._attached = False
        self._execution_offer = execution or {}
        self._launched_worker = launched

    # ---- the CLI ---------------------------------------------------------------------------

    def cli(self, argv, *, env=None, timeout: float = polling.READ_TIMEOUT) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [*self.command, *argv],
                capture_output=True,
                text=True,
                env={**(self.env or os.environ), **(env or {})},
                cwd=self.workspace,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise actions.ClientGap("The command receipt timed out; check status before trying it again.") from None

    def close(self) -> None:
        if self._attention_action is not None:
            self._attention_action.close()
        self._poller.close()

    def own(self, view: dict) -> tuple[Path, Path]:
        self.workspace, self.run_dir = actions.own(view, self.workspace, self.run_dir)
        return self.workspace, self.run_dir

    def poll(self) -> None:
        """Pump bounded reads; also called between keys while a question is open."""
        attention.poll_acknowledgement(self)
        current = (str(self.workspace), str(self.run_dir))
        targets: dict[polling.Key, float] = {current: self.pace(self.poll_seconds)}
        targets.update({(str(workspace), run): OTHERS_SECONDS for run, (workspace, _) in self.others.items()})
        if self.history is not None:
            targets[(current[0], None)] = polling.INVENTORY_SECONDS
        targets.update(self._overview.targets())
        for key, raw, error in self._poller.tick(targets):
            if isinstance(key, polling.ProjectRuns):
                self._overview.accept(key, raw, error)
                continue
            if key[1] is None:
                self._overview.accept(key, raw, error)
                self._attention.listing(raw, error, started=self._poller.completed.get(key, 0))
                continue
            try:
                view = json.loads(raw)["view"] if not error else None
                if not error and not isinstance(view, dict):
                    error = "status response has no view"
                if error:
                    view = None
                if (
                    view is not None
                    and view.get("run_dir")
                    and Path(view["run_dir"]).resolve() != Path(key[1]).resolve()
                ):
                    raise ValueError("response belongs to another run")
            except (ValueError, KeyError, TypeError, actions.ClientGap):
                view, error = None, "status response has no valid view for this chat"
            # A run that has not answered a status read yet has no view to lose, so a failed read
            # before its first view is not an outage to tell the person about: at cold start the
            # controller is still writing the run this client just created (#1998). The tab state
            # and the observation still record the failed read.
            stale = key[1] in self.viewed
            if error:
                self._attention.unavailable(key)
                if stale and self._read_errors.get(key[1]) != error:
                    self.say(f"Status unavailable ({Path(key[1]).name}): {error}. Last view is stale.")
                self._read_errors[key[1]] = error
            else:
                recovered = self._read_errors.pop(key[1], None)
                if stale and recovered:
                    self.say(f"Status available again ({Path(key[1]).name}).")
            if key == current:
                self._status_serial += 1
                self._status_error = error
                if error and self._observe is not None and self._unavailable is not None:
                    self._unavailable(error)
                if view is not None:
                    self._attention.observe(view, received=time.monotonic())
                    self._status_view = view
                    self.accept_status(view)
            elif key[1] in self.others:
                if view is not None:
                    self.viewed.add(key[1])  # a background tab has answered a read too, so it can lose a view
                    self._attention.observe(view, received=time.monotonic())
                    self.other_status(key[1], Path(key[0]), view)
            self.show_tabs()
        if self.attention_changed is not None:
            self.attention_changed(self._attention.summary())
        for row in self._attention.notifications():
            if attention.key(row) != current or self._composing_new:
                self.say("New attention: " + attention.label(row) + ". F8 or /attention opens the inbox.")

    def status(self) -> dict:
        # An approval/message check always starts a new read, after the person's input.
        key = (str(self.workspace), str(self.run_dir))
        self._poller.command, self._poller.env = self.command, self.env
        self._poller.refresh(key)
        before = self._status_serial
        while self._status_serial == before:
            self.poll()
            if self._status_serial == before and self._wait(0.05) == "pause":
                self._pause_pending = True
        if self._status_error or self._status_view is None:
            raise actions.ClientGap(self._status_error or "status response has no view")
        return self._status_view

    def accept_status(self, view: dict) -> None:
        self.viewed.add(str(self.run_dir))
        self.own(view)
        self.identity = wait_identity(view)
        self.titles[str(self.run_dir)] = title_of(view) or self.titles.get(str(self.run_dir), "")
        self.show_tabs()
        if self._observe is not None:
            self._observe(view)

    def take(self, command: actions.NamedCommand) -> None:
        """Run a named action against this run; an advancing one is watched while it works."""
        argv = [*command.argv, "--workspace", str(self.workspace), "--run-dir", str(self.run_dir)]
        if (
            actions.advancing(command)
            or ((self._status_view or {}).get("execution") or {}).get("mode") == "durable_local"
        ):
            self.advance(argv, command.env)
            return
        proc = self.cli(argv, env=command.env)
        if proc.returncode != 0:
            raise Refused(f"AutoCode refused that: {tail(proc)}")

    def advance(self, argv, env=None) -> None:
        """One call that works until the run stops; meanwhile show what the view says.

        The child runs in its own process group, so Ctrl-C reaches only this client:
        the first asks for the view's boundary pause, the second interrupts the child
        and a third stops it. The chat never exits while the child still runs.
        """
        if argv[:1] != ["coordinate"] and (
            ("--new-run-dir" in argv and self._execution_offer.get("id") == "durable_local")
            or (((self._status_view or {}).get("execution") or {}).get("mode") == "durable_local")
        ):
            return worker.advance(self, argv, env)
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
                            self.show(self.status())
                        self.check_others()
                        # The first reads come sooner, so what the person just decided shows as taken.
                        polls += 1
                        pace = self.poll_seconds if polls > 3 else min(self.poll_seconds, 0.5)
                        if self._wait(self.pace(pace)) == "pause" or self._pause_pending:
                            self._pause_pending = False
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
            if argv[:1] == ["coordinate"] and child.returncode:
                log.seek(0)
                raise actions.ClientGap("Coordination stopped: " + log.read()[-2000:].strip())
            if str(self.run_dir) not in self.viewed:
                try:
                    self.status()
                except actions.ClientGap:
                    # Exit 2 before the run saved anything is a refusal (a missing engine, an unsupported
                    # host): say what AutoCode said, not that the run it never made has no status.
                    log.seek(0)
                    raise actions.ClientGap(f"AutoCode did not start the run: {log.read()[-2000:].strip()}") from None

    def request_pause(self, *, again: str | None = None) -> bool:
        attached = self._attached
        again = again or (
            "This viewer can only request a boundary pause."
            if attached
            else "Press Ctrl-C again to interrupt the current step."
        )
        try:
            view = self.status()
            if view.get("coordination"):
                words = coordination.named(view["coordination"].get("cancel_command"), self.run_dir, "cancel")
                proc = self.cli(words)
                self.say(
                    "Coordination cancelled at the next boundary; saved work is retained."
                    if proc.returncode == 0
                    else "Cancellation receipt is unconfirmed: " + tail(proc)
                )
                return True
            words = actions.pause(view, f"chat-pause-{secrets.token_hex(8)}", self.workspace, self.run_dir)
        except actions.ClientGap as error:
            self.say(f"{error}. {again}")
            return True
        try:
            proc = self.cli(words)
        except actions.ClientGap:
            self.say(f"The pause receipt is unavailable; check the next status. {again}")
            return True
        if proc.returncode != 0:
            self.say(f"The pause was not accepted: {tail(proc)}. {again}")
            return True
        guidance = "This viewer does not interrupt the external worker." if attached else "Ctrl-C interrupts it now."
        self.say(f"{(view.get('pause') or {}).get('effect') or 'Pausing after this step.'} {guidance}")
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
            raise Refused(f"AutoCode refused that: {message}")
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
        if name == "/attention":
            return self.attention_menu()
        if name == "/new" and self._composing_new:
            return "You are already writing a new request. Enter it to start, or leave it empty to return."
        if name == "/tab":
            return self.tab(argument)
        if name == "/changeset":
            return self.changeset()
        if name in ("/runs", "/open", "/new", "/mode", "/engine", "/connect", "/repos"):
            if self.history is None:
                return "This chat has no project list."
            self.history.overview = self._overview
            if name == "/open" and not self.history.rows:

                def identity():
                    return str(self.workspace), str(self.run_dir), contexts.draft_identity(self._status_view)

                source = identity()

                def selected():
                    if identity() != source:
                        self.say("Open cancelled because the source chat or question changed.")
                        return
                    try:
                        self.history.slash(name, argument, current=self.run_dir)
                    except Opened as opened:
                        self.talk_to(Path(opened.row.get("workspace") or self.workspace), Path(opened.row["run_dir"]))
                        raise Switched from None

                return self._overview.request(self.history, self.run_dir, then=selected)
            try:
                return self.history.slash(name, argument, current=None if self._composing_new else self.run_dir)
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

    def changeset(self):
        """``/changeset``: this run's change set, read with ``changeset status --json``, and the one
        action it names now (the set approval, else the joint check), confirmed by the person first."""
        argv = actions.changeset_status(self.status(), self.workspace, self.run_dir)
        if argv is None:
            return "This run is in no change set. `autocode changeset link` links a run in another repository."
        try:
            proc = self.cli(argv, timeout=changeset.READ_TIMEOUT)
        except actions.ClientGap as error:
            return str(error)
        try:
            summary = json.loads(proc.stdout)
        except ValueError:
            summary = None
        if proc.returncode != 0 or not isinstance(summary, dict) or "error" in summary:
            return f"AutoCode could not show the change set: {tail(proc)}"
        shown = changeset.text(summary, render_plan)
        offered = changeset.offer(summary)
        if offered is None:
            return shown
        key, question = offered
        try:
            words = actions.changeset_action(summary, key)
        except actions.ClientGap as error:
            return f"{shown}\n{error}"

        def take() -> str:
            try:
                done = self.cli(
                    words, timeout=changeset.CHECK_TIMEOUT if key == "verification" else changeset.READ_TIMEOUT
                )
            except actions.ClientGap as error:
                return str(error)
            if done.returncode != 0:
                return f"AutoCode refused that: {tail(done)}"
            if key == "verification":
                return (done.stdout or "").strip() or "Checked."
            self.say("\nAutoCode: Every plan of the change set is approved.")
            raise SetApproved

        return panel.Confirm(shown, question, take)

    # ---- the person ------------------------------------------------------------------------

    def say(self, text: str) -> None:
        self._say(text)

    def ask(self, prompt: str) -> str:
        while True:
            try:
                return self._ask(prompt).strip()
            except EOFError:
                raise Left from None
            except KeyboardInterrupt:
                if ((self._status_view or {}).get("execution") or {}).get("mode") != "durable_local" or getattr(
                    self, "_composing_new", False
                ):
                    raise Left from None
                attached, self._attached = self._attached, True
                try:
                    self.request_pause(again="Use /quit or Ctrl-D to detach this viewer.")
                except KeyboardInterrupt:
                    self.say("This viewer can only request a boundary pause; no worker signal was sent.")
                finally:
                    self._attached = attached

    def show(self, view: dict) -> None:
        """New conversation messages, the notice and the progress line, each once."""
        chat = view.get("chat") or {}
        streaming_text = None
        for row in chat.get("messages") or []:
            text = (row.get("text") or "").strip()
            ident = row.get("id")
            key = str(ident) if ident not in (None, "") else (str(row.get("role") or ""), text)
            if row.get("streaming"):
                streaming_text = row.get("text") or None  # shown while written, replaced by the finished message
                continue
            if key in self.seen:
                continue
            self.seen.add(key)
            if row.get("role") == "user" and self.started:
                continue  # the person just typed it
            self.said.add(text)
            echo = (str(row.get("role") or ""), text)
            if echo in self._local_echoes:
                self._local_echoes.remove(echo)
                continue  # the direct prompt was just shown; distinct public messages still own their IDs
            who = "You" if row.get("role") == "user" else "AutoCode"
            self.say(f"\n{who}: {text}")
        # A prompt without an immediate public counterpart must not hide a later turn's message.
        # While its counterpart is still being written, the echo waits for the finished message.
        if streaming_text is None:
            self._local_echoes.clear()
        self._live_quiet = 0 if streaming_text != self._live_text else self._live_quiet + 1
        self._live_text = streaming_text
        if self._stream is not None:
            self._stream(streaming_text)
        self.started = True
        progress = view.get("progress") or {}
        notice = chat.get("notice")
        line = (notice, progress.get("line"))
        identity = progress_identity(progress) if progress else None
        if line != self.last_line:
            self.last_line = line
            if notice and notice != self.last_notice:
                self.say(f"\n{notice}")
            # Under a notice already said, a new progress line is the only news: say it, not silence.
            # The same stage flipping between working, finished and next is not news (#1844).
            if (
                progress.get("line")
                and identity != self.last_progress
                and (streaming_text or not notice or notice == self.last_notice)
            ):
                self.say(f"  · {progress['line']}")
                self.last_progress = identity
        # A brief clear between stages is not a new state: say an identical notice once per turn (#1807).
        # A run that waits for the person ends the turn, so the notice is said again once it carries on.
        if notice:
            self.last_notice = notice
        elif view.get("done") or (view.get("needs") or {}).get("kind") != "continue":
            self.last_notice = None

    # ---- the conversation ------------------------------------------------------------------

    def talk_to(self, workspace: Path, run_dir: Path) -> None:
        """Restore another run's session display, then refresh its authority. Watch the run left."""
        self._overview.cancel_navigation()
        self._display_contexts.keep(
            contexts.identity(self.workspace, self.run_dir),
            {
                "seen": set(self.seen),
                "said": set(self.said),
                "local_echoes": set(self._local_echoes),
                "started": self.started,
                "last_line": self.last_line,
                "last_notice": self.last_notice,
                "last_progress": self.last_progress,
                "replied_context": self._replied_context,
            },
        )
        self.others[str(self.run_dir)] = (self.workspace, self.identity)
        known = self.others.pop(str(run_dir), None)
        self.workspace, self.run_dir = Path(workspace), Path(run_dir)
        self.seen, self.said, self.started, self.last_line, self.last_notice = set(), set(), False, (), None
        self._local_echoes = set()
        self.last_progress = None
        self._status_view, self._status_error = None, ""
        self._replied_context = None
        self._pause_pending = False  # an unsubmitted keyboard request cannot transfer to another run
        self._live_text, self._live_quiet = None, 0
        # A tab keeps the state last read for it until status() reads it again, so the tabs drawn below never
        # show a chat that waits for the person as working (#1757); a chat not seen before has none yet.
        self.identity = known[1] if known else None
        if str(self.run_dir) not in self.tab_order:
            self.tab_order.append(str(self.run_dir))
        if self.switched is not None:
            restored = self.switched(self.workspace, self.run_dir)
            saved = self._display_contexts.get(contexts.identity(self.workspace, self.run_dir)) if restored else None
            if saved:
                self.seen, self.said, self.started = saved["seen"], saved["said"], saved["started"]
                self._local_echoes = saved["local_echoes"]
                self.last_line, self.last_notice, self.last_progress = (
                    saved["last_line"],
                    saved["last_notice"],
                    saved["last_progress"],
                )
                self._replied_context = saved["replied_context"]
        else:
            self.say(f"\n--- {self.run_dir.name} ---")
        self.show_tabs()

    def attention_menu(self):
        def open_row(row):
            self.talk_to(Path(row["workspace"]), Path(row["run_dir"]))
            raise Switched

        def refresh():
            self._poller.refresh((str(self.workspace), None))
            menu = self.attention_menu()
            menu.note = "Refresh requested. Press r after the read to replace this snapshot; Esc keeps your draft."
            return menu

        return attention.Menu(
            self._attention.entries(),
            self._attention.summary(),
            open_row,
            lambda row: attention.acknowledge(self, row),
            refresh,
        )

    def tabs(self) -> list[dict]:
        """This session's chats, numbered as tabs: each one's title, state and whether it is shown."""
        rows: list[dict] = []
        for run in self.tab_order:
            current = run == str(self.run_dir)
            if not current and run not in self.others:
                continue
            identity = self.identity if current else self.others[run][1]
            state = "done" if identity and identity[0] == "done" else ("working" if identity is None else "needs you")
            if run in self._read_errors:
                state = "unavailable"
            title = self.titles.get(run) or Path(run).name
            rows.append({"number": len(rows) + 1, "run": run, "title": title, "state": state, "current": current})
        return rows

    def show_tabs(self) -> None:
        if self.tabs_changed is not None:
            self.tabs_changed(self.tabs())

    def tab(self, argument: str) -> str:
        """/tab N: carry on with tab N's chat; ``next`` and ``previous`` step through the tabs."""
        self._overview.cancel_navigation()
        rows = self.tabs()
        here = next(row["number"] for row in rows if row["current"])
        steps = {"next": here % len(rows) + 1, "previous": (here - 2) % len(rows) + 1}
        number = steps.get(argument) or (int(argument) if argument.isdigit() else 0)
        if not 1 <= number <= len(rows):
            return f"There is no tab {argument or 'to switch to'}. " + (
                f"Choose 1 to {len(rows)}." if len(rows) > 1 else "Ctrl-T opens another chat."
            )
        row = rows[number - 1]
        if row["current"]:
            if self._composing_new:
                # Back from the request composer (/new, Ctrl-T) to this chat: the draft is dropped and the
                # chat's display returns; a tab with no run is left, nothing running with it (#1997).
                self.say("Nothing started; this chat carries on.")
                if self.switched is not None:
                    self.switched(self.workspace, self.run_dir)
                raise Switched
            return "That is this chat."
        self.talk_to(self.others[row["run"]][0], Path(row["run"]))
        raise Switched

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
        new = self.history.new if self.history is not None else setup.NewChat()
        observer, old_workspace, old_run = self._observe, self.workspace, self.run_dir
        switched = False
        self._observe, self._composing_new = None, True
        if self.switched is not None:
            self.switched(self.workspace, None)  # a local draft, no fabricated core run identity
        try:
            if self.history is not None:
                self.say(self.history.summary())
            brief = self.ask("What do you want done? ")
        except Switched:
            switched = True  # /open during drafting selected a real run
            return
        finally:
            self._observe, self._composing_new = observer, False
            if not switched and self.switched is not None:
                self.switched(old_workspace, old_run)
        if not brief:
            self.say("Nothing started; this chat carries on.")
            return
        mode, options = new.mode, new.options
        chosen = mode_row(mode, command=self.command, env=self.env)
        if not confirm_launch(self.ask, self.say, chosen):
            self.say("Nothing started; this chat carries on.")
            return
        self.begin(brief, list(chosen.get("start_args") or []), options)

    def check_others(self, every: float = OTHERS_SECONDS) -> None:
        """Admit due reads without waiting on other chats; the poller owns their cadence."""
        if self.others:
            self.poll()

    def other_status(self, run: str, workspace: Path, view: dict) -> None:
        before = self.others[run][1]
        now = wait_identity(view)
        self.others[run] = (workspace, now)
        self.titles[run] = title_of(view) or self.titles.get(run, "")
        if now is None or now == before:
            return
        if ((view.get("attention") or {}).get("item") or {}).get("id"):
            return  # the persistent inbox reports this exact event once
        what = "finished" if now[0] == "done" else f"needs you: {panel.needs_you(view) or now[0]}"
        tab = next((row["number"] for row in self.tabs() if row["run"] == run), None)
        hint = f"Ctrl-←/→ or /tab {tab} switches to it" if tab else "Type /runs, then /open it"
        self.say(f"\nAnother chat {what} ({self.titles.get(run) or Path(run).name}). {hint}.")

    def converse(self) -> int:
        try:
            while True:
                try:
                    if (stop := self.step()) is not None:
                        return stop
                except (Restored, Switched, SetApproved):
                    continue  # going back to a saved step, /open or a set approval: carry on with that run
                except Refused as error:
                    # Nothing was stopped: another driver may hold the run, or what was shown went stale. The
                    # person is told, then asked before the run is read and asked again, so a refusal that
                    # repeats (a held lock) waits for them instead of retrying on its own (#2000).
                    self.say(
                        f"\n{error}\nThe run is unchanged. If another autocode runner holds it, wait for that one "
                        "to stop or decide there."
                    )
                    if self.ask("Press Enter to read the run again, or Ctrl-D to leave: "):
                        self.say("Nothing sent; the run is read again.")
                except NewChat:
                    self.new_chat()
                except KeyboardInterrupt:
                    # A live view can be rendered before step() enters watch().
                    # Ctrl-C in that transition has the same boundary-only scope
                    # as an attached watcher, never an implicit viewer exit/kill.
                    if not actions.working(self._status_view or {}):
                        raise
                    attached, self._attached = self._attached, True
                    try:
                        self.request_pause()
                    except KeyboardInterrupt:
                        self.say("This viewer can only request a boundary pause; no worker signal was sent.")
                    finally:
                        self._attached = attached
        except (Left, EOFError):
            self.say(f"\nLeft the chat; the run is unchanged. Pick it up with: autocode chat --run-dir {self.run_dir}")
            return 2
        except actions.ClientGap as error:
            self.say(f"\n{error}")
            return 1
        finally:
            self.close()

    def step(self) -> int | None:
        """Act on one status view; an exit code once the chat ends here."""
        try:
            view = self.status()
        except actions.ClientGap as error:
            # A removed/unreadable target must remain navigable. This prompt
            # cannot submit a task action or use an earlier view's authority.
            self.say(f"Status unavailable for {self.run_dir.name}: {error}")
            if self.ask("Enter retries status; /runs, /open or /tab selects another chat: "):
                self.say("Nothing sent; current status is required before a task decision.")
            return None
        self.show(view)
        if view.get("coordination"):
            return coordination.step(self, view, render_plan)
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
                if (view.get("execution") or {}).get("mode") == "durable_local":
                    self.say(
                        "Saved boundary. Press Enter to continue; / commands inspect or leave. Reconnecting has started nothing."
                    )
                    if self.ask("Press Enter to continue, or use / commands to inspect or leave: "):
                        self.say("Nothing sent; press Enter when you want to continue this run.")
                        return None
                    fresh = self.status()
                    if (fresh.get("execution") or {}).get("next_command") != (view.get("execution") or {}).get(
                        "next_command"
                    ):
                        self.say("The saved boundary changed; showing its current status before continuing.")
                        return None
                self.take(actions.named(view, self.workspace, self.run_dir))
                self.require_progress(view)
            return None
        self.check_others(every=0)  # before the person is asked: other chats may need them too
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

    def pace(self, seconds: float) -> float:
        """The wait before the next read: short while a reply is still changing, else ``seconds``."""
        if self._live_text and self._live_quiet < STREAM_QUIET_POLLS:
            return min(STREAM_POLL_SECONDS, seconds)
        return seconds

    def watch(self) -> None:
        """Another process is advancing the run: show its progress until it stops.

        While a reply is still changing, poll at the short streaming pace so each new
        extract appears immediately; otherwise the ordinary poll interval is enough.
        """
        self._attached = True
        try:
            while True:
                try:
                    view = self.status()
                    if not actions.working(view):
                        return
                    self.show(view)
                    self.check_others()
                    if self._wait(self.pace(self.poll_seconds)) == "pause" or self._pause_pending:
                        self._pause_pending = False
                        self.request_pause()
                except actions.ClientGap:
                    self._wait(0.1)  # unavailable status is not completion; keep input and retries alive
                except KeyboardInterrupt:
                    try:
                        self.request_pause()
                    except KeyboardInterrupt:
                        self.say("This viewer can only request a boundary pause; the external worker is unchanged.")
        finally:
            self._attached = False

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
        if (
            (view.get("chat") or {}).get("phase") == "requirements"
            and need.get("kind") == "answer"
            and not need.get("route")
            and not need.get("effort")
        ):
            return self.requirements(view, command)
        if any(row.kind in ("answer", "model", "effort") for row in decided):
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
        if hint := changeset.approval_hint(view):
            self.say(hint)
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
        if actions.working(view):
            # A reply was sent and Requirements is still working on it: this view's question is the
            # one just answered. Wait for the next one rather than prompting on it again (#1792).
            self.watch()
            return True
        question = next(
            (
                (row.get("text") or "").strip()
                for row in reversed(chat.get("messages") or [])
                # The status view's Requirements question message (autocode_chat_view.REQUIREMENTS_QUESTION).
                if str(row.get("id", "")).startswith("requirements-question") and not row.get("streaming")
            ),
            "",
        )
        if question and context == self._replied_context:
            self.say(f"\nAutoCode is still waiting on this question:\n{question}")
        elif question and question not in self.said:
            # Ask the question this prompt waits on, even if the transcript has not shown it yet.
            self.said.add(question)
            self._local_echoes.add(("assistant", question))
            self.say(f"\nAutoCode: {question}")
        summary = ((review or {}).get("summary") or "").strip()
        if summary and summary not in self.said:
            self.said.add(summary)
            self._local_echoes.add(("assistant", summary))
            self.say(f"\nAutoCode: {summary}")
        prompt = "\nYou: " if not review else f"\n{review['label']}? [y/N], or type what to change: "
        reply = self.ask(prompt)
        if review and (reply.lower() in YES or same_answer(reply, review)):
            if not self.confirm_spend(review.get("spend_confirmation")):
                self.say("Not started. The understanding still waits for you.")
                return True
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
        if len(decided) == 1:
            row = decided[0]
            options = (row.question or {}).get("options") or []
            text = answers[row.key]
            normalized = text.strip()
            labels = {str(option).strip() for option in options}
            labels.add(str((row.question or {}).get("proposed_default") or "").strip())
            conversation = view.get("chat") or {}
            if (
                row.kind == "answer"
                and options
                and normalized not in labels
                and not normalized.lstrip("+-").isdigit()
                and (conversation.get("input") or {}).get("kind") == "feedback"
            ):
                return self.send(text, conversation.get("context") or "")
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
        if row.kind == "effort":
            # A level you named that the model does not offer (#2057): choose one it declares.
            levels = actions.effort_levels({"needs": need}, row.key)
            if asked := (row.question or {}).get("question"):
                self.say(f"\nAutoCode: {asked}")
            for number, level in enumerate(levels, 1):
                self.say(f"  {number}. {level}")
            job = (need.get("effort") or {}).get("job") or row.key.removeprefix("effort-")
            while True:
                text = self.ask(f"Which effort should the {job} run at? (1-{len(levels)} or a level) ")
                if text.isdigit() and 1 <= int(text) <= len(levels):
                    return levels[int(text) - 1]
                try:
                    return actions.effort_value({"needs": need}, row.key, text)
                except actions.ClientGap as error:
                    self.say(str(error))
        question = row.question or {}
        asked = (question.get("question") or "").strip()
        if asked not in self.said:
            self.said.add(asked)
            self._local_echoes.add(("assistant", asked))
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
        offer = self.bound_offer(view)
        if offer:
            row = offer[0]
            self.say(f"To raise it instead, type a new {row['label'].lower()} ({row['minimum']} or more).")
        reply = self.ask("\nYour reply (Enter to leave it for now): ")
        if not reply:
            self.say(f"Nothing sent. The run is paused; pick it up with: autocode chat --run-dir {self.run_dir}")
            return False
        if offer and reply.strip().isdigit():
            return self.raise_bound(view, offer, reply)
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
        route = need.get("route") or {}
        # A stopped job's model question, named by the view as a command (#2005): a typed model takes it.
        model_command = route.get("command") if need.get("kind") == "retry_job" and route.get("question_id") else None
        offer = self.bound_offer(view)
        conversation = view.get("chat") or {}
        message_offer = (
            need.get("kind") == "resume"
            and (conversation.get("input") or {}).get("kind") == "feedback"
            and (conversation.get("input") or {}).get("next_command")
        )
        hint = ", or type the model to continue on" if model_command else ""
        if offer:
            hint = f", or type a new {offer[0]['label'].lower()} ({offer[0]['minimum']} or more)"
        elif message_offer and not model_command:
            hint = ", or type what to change"
        answer = self.ask(f"{prompt}? [y/N]{hint}: ")
        if model_command and answer.strip() and answer.strip().lower() not in (*YES, "n", "no"):
            return self.route_model(view, model_command, route, answer.strip())
        if offer and answer.strip() and answer.strip().lower() not in (*YES, "n", "no"):
            return self.raise_bound(view, offer, answer)
        if message_offer and answer.strip() and answer.strip().lower() not in (*YES, "n", "no"):
            return self.send(answer, conversation.get("context") or "")
        if answer.lower() not in YES:
            self.say(f"Left as it is. Pick it up with: autocode chat --run-dir {self.run_dir}")
            return False
        fresh = self.status()
        if fresh.get("needs") != view.get("needs"):
            self.say("The run changed meanwhile; here is where it stands now.")
            return True
        self.take(actions.fill(fresh, command, answers))
        return True

    def bound_offer(self, view: dict):
        """The view's separate raise of the bound holding the run (#2044), or None when it names none."""
        try:
            return actions.bound_offer(view, self.workspace, self.run_dir)
        except actions.ClientGap:
            return None

    def raise_bound(self, view: dict, offer, text: str) -> bool:
        """A number typed at a bound pause: the core's named raise with it, or why it is refused.

        Before #2044 this pause named the raise only in prose, so the chat could only say the number set
        nothing (#1950). The core now names the command; the chat fills its one slot and takes it."""
        row, command = offer
        try:
            filled = actions.fill_offer(view, row, command, text)
        except actions.ClientGap as error:
            self.say(f"{error}. Left as it is. Pick it up with: autocode chat --run-dir {self.run_dir}")
            return False
        fresh = self.status()
        if fresh.get("needs") != view.get("needs"):
            self.say("The run changed meanwhile; here is where it stands now.")
            return True
        self.say(f"Next it runs: autocode {shlex.join(filled.argv)}")
        self.take(filled)
        return True

    def route_model(self, view: dict, text: str, route: dict, model: str) -> bool:
        """A model typed at a stopped job's retry: the view's route answer, filled with it (#2005).

        The core issues a new retry token for that model; the retry on it is offered next, as usual.
        Before this, the typed model was read as a decline and the chat left with the run paused."""
        command = actions.parse(text, *actions.own(view, self.workspace, self.run_dir))
        filled = actions.fill(view, command, {route["question_id"]: model})
        self.say(f"Next it runs: autocode {shlex.join(filled.argv)}")
        fresh = self.status()
        if fresh.get("needs") != view.get("needs"):
            self.say("The run changed meanwhile; here is where it stands now.")
            return True
        self.take(filled)
        self.say(f"\nAutoCode: {route.get('job') or route.get('role')} continues on {model}; the retry on it is next.")
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

    def confirm_spend(self, confirmation) -> bool:
        """The core's spend confirmation before paid planning, as the extension's modal shows it:
        its title and detail, then its own button label as the question. None: nothing to confirm."""
        if not isinstance(confirmation, dict):
            return True
        self.say(f"\n{confirmation.get('title') or 'Continue?'}\n{confirmation.get('detail') or ''}".rstrip())
        return self.ask(f"{confirmation.get('label') or 'Continue'}? [y/N]: ").lower() in YES

    def send(self, text: str, context: str) -> bool:
        """A message through the view's chat input, then on with the run, as the extension does."""
        self.take(actions.message(self.status(), text, context, self.workspace, self.run_dir))
        self._replied_context = context
        return self.continue_after_input()

    def continue_after_input(self) -> bool:
        view = self.status()
        if not view.get("done") and (view.get("needs") or {}).get("kind") == "continue" and not actions.working(view):
            self.take(actions.named(view, self.workspace, self.run_dir))
        return True


def title_of(view: dict) -> str:
    """A chat's title: the first line of its task, without a Markdown heading's marks, bounded."""
    first = next(
        (line.strip().lstrip("#").strip() for line in str(view.get("task") or "").splitlines() if line.strip()), ""
    )
    return first[:39] + "…" if len(first) > 40 else first


def progress_identity(progress: dict) -> tuple:
    """What a progress line tells a person that is new: the stage, the counts and what it waits on,
    not whether that stage is working, finishing or next. A stage that flips between those words
    without changing a count is the same news (#1844)."""
    tasks = progress.get("tasks") or {}
    requirements = progress.get("requirements") or {}
    problems = progress.get("problems") or {}
    return (
        progress.get("stage"),
        progress.get("needs_you"),
        progress.get("for_earlier_request"),
        tasks.get("label"),
        requirements.get("label"),
        problems.get("open"),
    )


def wait_identity(view: dict) -> tuple | None:
    """What a run waits on a person for, as the extension's notifications tell one wait from the next:
    ("done",) once finished, the need and its tokens while it waits, None while it works on its own."""
    current = (view.get("attention") or {}).get("item") or {}
    if current.get("id"):
        return ("done" if current.get("kind") == "completion" else current.get("kind"), current["id"])
    if view.get("done"):
        return ("done",)
    need = view.get("needs") or {}
    if not need or need.get("kind") == "continue":
        return None
    keys = ("request_id", "resolver_token", "resolver_request_id", "token", "job_retry_token")
    return (need.get("kind"), *(str(need.get(key) or "") for key in keys))


def render_plan(plan: dict) -> str:
    """The plan a person approves, in the extension's sections: what is checked, the limits, what it may do."""
    lines = ["Plan to approve:"]
    for row in plan["acceptance_criteria"]:
        lines.append(f"  [{row['id']}] {row['criterion']}")
        if row.get("current_behavior"):
            lines.append(f"      Today: {row['current_behavior']}")
        lines.append(f"      Checked by: {row['verification_method']}")
        if row["human_review"]:
            lines.append("      You check this one yourself.")
    for title, rows in (
        ("Constraints", plan["constraints"]),
        ("What it may do", plan["permission_boundaries"]),
        ("Not covered", plan.get("scope_exclusions") or []),
    ):
        if rows:
            lines.append(f"{title}:")
            lines.extend(f"  - {text}" for text in rows)
    delivery = plan.get("git_delivery")
    if isinstance(delivery, dict) and delivery.get("kind") == "atomic_commit":
        lines.append("Create one local atomic commit: " + delivery["message"])
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
    try:
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
    except BaseException:
        client.close()
        raise
    return client


def same_answer(reply: str, review: dict) -> bool:
    """A typed reply that is the review's own answer, matched as the core matches it (``trim_lower``)."""
    confirmation = review.get("spend_confirmation") or {}
    if confirmation.get("match") != "trim_lower":
        return reply == review.get("answer")
    strip = " \t\n\r\x0b\x0c\x1c\x1d\x1e\x1f\x85"
    return reply.strip(strip).lower() == str(review.get("answer") or "").strip(strip).lower()


LAUNCH_WARNING = "Start a live AutoCode run? It spends your model allowance and can run for a long time."


def confirm_launch(ask: Callable[[str], str], say: Callable[[str], None], mode: dict) -> bool:
    """A mode that does not confirm before planning (``spend_confirmation`` other than "planning",
    or none from an older core) is confirmed before the run starts, as the extension does."""
    if mode.get("spend_confirmation") == "planning":
        return True
    say(f"\n{LAUNCH_WARNING}")
    try:
        return ask("Start it? [y/N]: ").strip().lower() in YES
    except (EOFError, KeyboardInterrupt):
        return False


def mode_row(mode: str | None, *, command=AUTOCODE, env=None) -> dict:
    """The mode `autocode modes list` names, or the core's default without one."""
    proc = subprocess.run([*command, "modes", "list", "--json"], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise actions.ClientGap(f"could not list run modes: {tail(proc)}")
    listed = json.loads(proc.stdout)
    wanted = mode or listed.get("default")
    for row in listed.get("modes") or []:
        if row.get("id") == wanted:
            return row
    names = ", ".join(row.get("id", "") for row in listed.get("modes") or [])
    raise actions.ClientGap(f"no run mode {wanted!r}; choose one of: {names}")


def run_mode(mode: str | None, *, command=AUTOCODE, env=None) -> list[str]:
    """The start flags of a mode `autocode modes list` names; the core's default without one."""
    return list(mode_row(mode, command=command, env=env).get("start_args") or [])


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


def every_project_runs(*, command=AUTOCODE, env=None) -> list[dict]:
    """Every registered project's runs, as `autocode runs --all --json` lists them."""
    proc = subprocess.run([*command, "runs", "--all", "--json"], capture_output=True, text=True, env=env)
    if proc.returncode == 2 and not proc.stdout.strip() and proc.stderr.strip().startswith("No runs found"):
        return []
    if proc.returncode != 0:
        raise actions.ClientGap(f"could not list the projects' runs: {tail(proc)}")
    return list(json.loads(proc.stdout)["runs"])


def waiting(listed: list[dict]) -> list[dict]:
    """The runs that need a person or are still working."""
    return [row for row in listed if row.get("bucket") in ("needs you", "working")]


def unfinished(workspace: Path, *, command=AUTOCODE, env=None) -> list[dict]:
    """The project's runs that need a person or are still working, as `autocode runs --json` lists them."""
    return waiting(project_runs(workspace, command=command, env=env))


def picker_numbers(listed: list[dict]) -> dict[str, int]:
    """Each of the project's runs by the number /runs gives it (this project's chats come first there,
    newest first), so the startup picker and /runs call a run by the same number (#1754)."""
    return {str(row["run_dir"]): number for number, row in enumerate(panel.run_rows(listed), 1)}


def numbers_text(numbers: list[int]) -> str:
    """``1-3`` for a run of numbers, else ``1, 3, 4``."""
    if numbers == list(range(numbers[0], numbers[0] + len(numbers))):
        return f"{numbers[0]}-{numbers[-1]}"
    return ", ".join(str(number) for number in numbers)


def open_chat(
    workspace: Path,
    mode=None,
    options=(),
    *,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    hint: bool = False,
    intro: str | None = None,
    new: setup.NewChat | None = None,
    **kwargs,
):
    """`autocode chat` with nothing named: pick up the run left open here, or ask what to do.

    One unfinished run is picked up; with several the person chooses one or a new
    task. With none, the chat asks for a request and starts a run with the core's
    mode and options from the command line, if any. None: the person left without choosing.
    In the full-screen chat ``/runs`` and ``/open N`` work at that question too (``Opened``),
    as do ``/mode``, ``/engine`` and ``/connect NAME use``: they change ``new``, the chat's
    ``NewChat``, so it is read once the request is in, not before the question (#2009).
    """
    command, env = kwargs.get("command", AUTOCODE), kwargs.get("env")
    listed = project_runs(workspace, command=command, env=env)
    runs = waiting(listed)
    earlier = listed if hint and not runs else []
    try:
        if len(runs) == 1:
            say(f"Picking up {runs[0].get('title') or runs[0]['name']}.")
            return ChatClient(workspace, Path(runs[0]["run_dir"]), ask=ask, say=say, **kwargs)
        if runs:
            numbers = picker_numbers(listed)
            by_number = {numbers[str(row["run_dir"])]: row for row in runs}
            say("Runs waiting here:")
            for number, row in sorted(by_number.items()):
                state = row.get("pause_category_label") or row["bucket"]
                say(f"  {number}. {row.get('title') or row['name']} ({state})")
            offered = numbers_text(sorted(by_number))
            while (choice := ask(f"Pick one ({offered}), or type a new request: ").strip()).isdigit():
                if int(choice) in by_number:
                    return ChatClient(workspace, Path(by_number[int(choice)]["run_dir"]), ask=ask, say=say, **kwargs)
                say(f"There is no run {choice} waiting.")
            brief = choice
        else:
            if earlier:
                say(f"{len(earlier)} earlier chat{'s' if len(earlier) != 1 else ''} here. Type /runs to see them.")
            if intro:
                say(intro)
            brief = ask("What do you want done? ").strip()
    except Opened as opened:
        row = opened.row
        return ChatClient(Path(row.get("workspace") or workspace), Path(row["run_dir"]), ask=ask, say=say, **kwargs)
    except (EOFError, KeyboardInterrupt):
        brief = ""
    if not brief:
        say("Nothing started.")
        return None
    if new is not None:
        mode, options = new.mode, new.options  # what the person chose at the question, not before it
    chosen = mode_row(mode, command=command, env=env)
    if not confirm_launch(ask, say, chosen):
        say("Nothing started.")
        return None
    return start(workspace, brief, list(chosen.get("start_args") or []), options, ask=ask, say=say, **kwargs)


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
    result.add_argument(
        "--execution",
        choices=("owned", "durable_local"),
        default="owned",
        help="New run lifetime; durable_local requires existing persistent Linux user services",
    )
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
    if args.run_dir is not None and args.execution != "owned":
        arguments.error("--execution selects new runs; an existing run's status controls its lifetime mode")
    ui = None
    discovery = None
    client = None
    try:
        if args.brief is not None and not args.brief.strip():
            arguments.error("the brief is empty")
        ui = terminal_ui(force_line=args.line)
        ask, say = (ui.ask, ui.say) if ui is not None else (input, print)
        seen = (
            {
                "observe": ui.observe,
                "unavailable": ui.status_unavailable,
                "wait": ui.wait,
                "stream": ui.stream,
                "launched": ui.worker_launch,
            }
            if ui is not None
            else {}
        )
        workspace = (args.workspace or Path.cwd()).expanduser()
        # A new chat starts with the mode and options given here, else the project's saved choice.
        saved = setup.NewChat.load(workspace)
        new = setup.NewChat(args.mode or saved.mode, list(options) if options else saved.options)
        history = History(workspace.resolve(), new=new)
        if ui is not None:

            def open_attention(row):
                raise Opened(row)

            discovery = attention_discovery.Discovery(
                workspace, command=AUTOCODE, env=None, say=say, changed=ui.attention, open_run=open_attention
            )
            history.attention = discovery
            history.overview = discovery._overview
            ui.idle = discovery.poll
        if args.mode is None and saved.mode is not None:
            history.forget_unlisted_mode()
        if ui is not None:
            history.outside = ui.outside
        if ui is not None and args.run_dir is None:
            ui.set_context(workspace)
            ui.bind(history.slash, run=False)  # /runs, /open, /mode, /engine and /connect, before a chat
        mode, options = new.mode, new.options
        if args.execution == "durable_local":
            listing = history.read(["modes", "list", "--json"])
            offer: dict = next(
                (row for row in (listing.get("execution") or {}).get("modes", []) if row.get("id") == args.execution),
                {},
            )
            if offer.get("available") is not True:
                raise actions.ClientGap(offer.get("reason") or "Durable execution is unavailable on this host")
            seen["execution"] = offer
        if args.brief is not None:
            chosen = mode_row(mode)
            if not confirm_launch(ask, say, chosen):
                say("Nothing started.")
                return 2
            client = start(
                workspace, args.brief, list(chosen.get("start_args") or []), options, ask=ask, say=say, **seen
            )
        elif args.run_dir is not None:
            run_dir, workspace = events.resolve(args.workspace, args.run_dir)
            client = ChatClient(workspace, run_dir, ask=ask, say=say, **seen)
        else:
            client = open_chat(
                workspace.resolve(),
                mode,
                options,
                ask=ask,
                say=say,
                hint=ui is not None,
                intro=history.summary() if ui is not None else None,
                new=new,  # /mode, /engine and /connect at the first question change it (#2009)
                **seen,
            )
            if client is None:
                return 2
        client.history = history
        if discovery is not None:
            discovery.transfer(client)
        if ui is not None:
            ui.set_context(client.workspace, client.run_dir)
            ui.bind(client.slash)
            client.switched = ui.switch
            client.tabs_changed = ui.tabs
            client.attention_changed = ui.attention
            ui.idle = client.poll
            client.show_tabs()
        return client.converse()
    except (actions.ClientGap, OSError, ValueError) as error:
        if ui is not None:
            ui.close()  # first, so the message lands on the terminal and not on the closing screen
        print(f"autocode chat: {error}", file=sys.stderr)
        return 1
    except EOFError:
        print("\nLeft the viewer; the durable worker is unchanged.")
        return 2
    except KeyboardInterrupt:
        if ui is not None:
            ui.close()
        print("\nautocode chat: interrupted.", file=sys.stderr)
        return 130
    finally:
        if discovery is not None:
            discovery.close()
        if client is not None:
            client.close()
        if ui is not None:
            ui.close()


if __name__ == "__main__":
    raise SystemExit(cli())
