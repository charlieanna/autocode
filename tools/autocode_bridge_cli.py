"""``autocode bridge``: relay a run's human gates to GitHub issues and take the replies.

The channel bridge (docs/channels.md). One daemon per host polls one repository's
labeled issues: a newly labeled issue starts a durable run; a run that reaches a
human gate has it posted to its issue (questions with ids and options, the
complete plan a token approves, a pause's reason); an allowlisted reply is reduced
to the one action the fresh status view names. Control tokens never leave the
host, free text never approves, and between gates the worker runs independently.
With ``AUTOCODE_BRIDGE_MAX_ACTIVE`` set, at most that many bridged runs are in
flight at once; newer labeled issues wait, queued, and start oldest first as runs
settle (autocode_issue_admission). Configuration is environment-first, like
examples/github-issue.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from . import autocode_bridge_github as bridge_github
    from . import autocode_bridge_relay as bridge_relay
    from . import autocode_bridge_store as store
    from . import autocode_bridge_worker as worker
    from . import autocode_github as github
    from . import autocode_issue as issue_tool
    from . import autocode_issue_admission as admission
    from . import autocode_util as util
    from .autocode_taskrun import TaskRunError
except ImportError:
    import autocode_bridge_github as bridge_github
    import autocode_bridge_relay as bridge_relay
    import autocode_bridge_store as store
    import autocode_bridge_worker as worker
    import autocode_github as github
    import autocode_issue as issue_tool
    import autocode_issue_admission as admission
    import autocode_util as util
    from autocode_taskrun import TaskRunError

POLL_SECONDS = 10.0
LABEL = "autocode"


class ConfigError(RuntimeError):
    """The bridge is not configured for this host; ``bridge check`` names what is missing."""


@dataclass
class Config:
    repo: str
    project: Path
    label: str
    actors: tuple[str, ...]
    options: tuple[str, ...]
    poll_seconds: float
    open_pr: bool
    owned: bool = False  # the explicit no-systemd mode: runs live with the daemon
    max_active: int | None = None  # bridged runs in flight at once; None starts every labeled issue

    @classmethod
    def from_env(cls, environ=None, *, project=None, poll_seconds=None) -> Config:
        environ = os.environ if environ is None else environ

        def names(name):
            try:
                value = json.loads(environ.get(name, "[]"))
            except ValueError:
                raise ConfigError(f"{name} must be a JSON array of strings") from None
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ConfigError(f"{name} must be a JSON array of strings")
            return value

        repo = environ.get("AUTOCODE_BRIDGE_GITHUB_REPO", "").strip()
        if not repo:
            raise ConfigError("Set AUTOCODE_BRIDGE_GITHUB_REPO to OWNER/REPO")
        actors = names("AUTOCODE_BRIDGE_ACTORS")
        if not actors:
            raise ConfigError("Set AUTOCODE_BRIDGE_ACTORS to a JSON array of GitHub logins allowed to steer runs")
        selected = Path(project or environ.get("AUTOCODE_BRIDGE_PROJECT") or os.getcwd()).resolve()
        try:
            seconds = float(
                poll_seconds if poll_seconds is not None else environ.get("AUTOCODE_BRIDGE_POLL_SECONDS", POLL_SECONDS)
            )
        except ValueError:
            raise ConfigError("AUTOCODE_BRIDGE_POLL_SECONDS must be a number of seconds") from None
        if not (0.5 <= seconds <= 3600):
            raise ConfigError("AUTOCODE_BRIDGE_POLL_SECONDS must be between 0.5 and 3600")
        options = names("AUTOCODE_BRIDGE_OPTIONS")
        try:
            max_active = admission.parse_limit(environ.get("AUTOCODE_BRIDGE_MAX_ACTIVE"))
        except ValueError as error:
            raise ConfigError(f"AUTOCODE_BRIDGE_MAX_ACTIVE {error}") from None
        return cls(
            repo=repo,
            project=selected,
            label=environ.get("AUTOCODE_BRIDGE_LABEL", LABEL).strip() or LABEL,
            actors=tuple(actors),
            options=tuple(options),
            poll_seconds=seconds,
            open_pr=environ.get("AUTOCODE_BRIDGE_OPEN_PR", "").lower() in ("1", "true", "yes"),
            owned=environ.get("AUTOCODE_BRIDGE_OWNED", "").lower() in ("1", "true", "yes"),
            max_active=max_active,
        )


class Bridge:
    """One poll of the channel and of every bridged run; all decisions through the fresh view."""

    def __init__(
        self,
        config: Config,
        channel: bridge_github.GitHubChannel,
        *,
        launch=None,
        adopt=None,
        prepare=None,
        sleeper=time.sleep,
    ):
        self.config = config
        self.channel = channel
        self.launch = launch or worker.start_issue_run
        self.adopt = adopt or worker.adopt
        self.prepare = prepare or worker.prepare_pull_request
        self.sleep = sleeper

    # ---------------------------------------------------------------- discovery

    def discover(self) -> dict[int, dict]:
        """Take each newly labeled issue, or queue it under a limit; the open labeled issues by number."""
        labeled = {}
        for issue in self.channel.open_labeled_issues():
            number = issue.get("number")
            if not isinstance(number, int) or issue.get("state") != "open" or "pull_request" in issue:
                continue
            labeled[number] = issue
            existing = store.load(self.channel.owner, self.channel.repo, number)
            if existing is not None:
                continue
            if self.config.max_active is None:
                self.take(issue)
            else:
                self.queue(issue)
        return labeled

    def queue(self, issue: dict) -> None:
        """Remember a labeled issue that waits for a slot; admission starts it, oldest first."""
        number = issue["number"]
        store.save(
            {
                "owner": self.channel.owner,
                "repo": self.channel.repo,
                "number": number,
                "title": (issue.get("title") or f"Issue {number}").strip(),
                "url": issue.get("html_url"),
                "project": str(self.config.project),
                "gate": {"key": "queued"},
                "queued": True,
                "queued_at": util.now(),
                "failed": None,
            }
        )

    def start_queued(self, labeled: dict[int, dict]) -> None:
        """Start queued issues that are still open and labeled while a slot is free; tell the rest once."""
        waiting: tuple = ()
        for _ in range(len(labeled) + 1):  # a refused start frees its slot for the next issue in this poll
            rows = store.records()
            queued = [
                record
                for record in rows
                if record.get("queued")
                and record.get("owner") == self.channel.owner
                and record.get("repo") == self.channel.repo
                and record.get("number") in labeled
            ]
            decision = admission.admit(queued, sum(1 for record in rows if holds_slot(record)), self.config.max_active)
            waiting = decision.waiting
            if not decision.start:
                break
            for record in decision.start:
                self.take(labeled[record["number"]])
        limit = self.config.max_active
        for record in waiting:
            if record.get("queued_notice"):
                continue
            record["queued_notice"] = self.post(  # a notice that could not be posted is tried again next poll
                record["number"],
                f"**AutoCode queued this issue.** This bridge works on at most {limit} "
                f"issue{'s' if limit != 1 else ''} at a time, and every slot is in use. This one starts when "
                "an issue in progress finishes or stops, oldest issue first. Nothing is needed from you until then.",
            )
            store.save(record)

    def take(self, issue: dict) -> None:
        number = issue["number"]
        title = (issue.get("title") or f"Issue {number}").strip()
        record = {
            "owner": self.channel.owner,
            "repo": self.channel.repo,
            "number": number,
            "title": title,
            "url": issue.get("html_url"),
            "project": str(self.config.project),
            "gate": {"key": "starting"},
            "failed": None,
        }
        try:
            options = list(self.config.options)
            started = self.launch(
                self.config.project,
                github.IssueRef(self.channel.owner, self.channel.repo, number),
                issue,
                options,
                owned=self.config.owned,
            )
        except (worker.BridgeError, TaskRunError, OSError) as error:
            record["failed"] = f"{error}"[:500]
            record["gate"] = {"key": "failed"}
            store.save(record)
            self.post(
                number,
                f"**AutoCode could not start this run**: {record['failed']}\n\nThe bridge keeps this issue off the queue; remove its record to retry.",
            )
            return
        record.update(started)
        record["gate"] = {"key": "starting"}
        issue_now = self.safe_issue(number)
        if issue_now is not None:
            record["initial_comment_id"] = self.channel.floor(record, issue_now)
        store.save(record)
        # A run in the no-systemd mode lives with this process, so the notice must not call it durable.
        hosted = "a run that works while the bridge is running" if record.get("owned") else "a durable run"
        posted = self.post(
            number,
            f"**AutoCode took this issue** and started {hosted}.\n\n"
            f"- run: `{Path(record['run_dir']).name}` on branch `{record['branch']}`\n"
            f"- I will ask here when the run needs you: questions, the plan for approval, or a pause.\n"
            f"- Steering is limited to: {', '.join('@' + actor for actor in self.config.actors)}.",
        )
        if posted:
            record.update(store.seen_comments(record, [posted]))
            store.save(record)

    # ---------------------------------------------------------------- one record

    def tend(self, record: dict) -> None:
        if record.get("failed") or record.get("queued"):
            return
        if record.get("initial_comment_id") is None:  # a restart between provisioning and the floor: heal it
            issue_now = self.safe_issue(record["number"])
            if not issue_now:
                return
            record["initial_comment_id"] = self.channel.floor(record, issue_now)
            store.save(record)
        run = self.adopt(record)
        if not record.get("ready") and record.get("receipt"):
            try:
                observed = worker.inspect_launch(record, run)
                record["ready"] = bool(observed.get("ready"))
                store.save(record)
            except (worker.BridgeError, TaskRunError):
                return  # the launch receipt is still dispatching; the next tick inspects again
            if not record["ready"]:
                return
        view = run.status()
        self.note_settled(record, view)
        self.relay_gate(record, run, view)
        self.consume_replies(record, run)
        if record.get("owned"):  # an owned run only moves when the daemon drives it to its next boundary
            with contextlib.suppress(TaskRunError):
                # A no-progress stop keeps its saved boundary; the tick's caller reports it.
                run.advance_until_input()
        self.deliver(record, run)

    def note_settled(self, record: dict, view: dict) -> None:
        """Remember whether the run still holds its slot: a settled run frees it, a follow-up takes it again."""
        settled = admission.settled(view)
        if record.get("settled") is not settled:
            record["settled"] = settled
            store.save(record)

    def relay_gate(self, record: dict, run, view: dict) -> None:
        result = bridge_relay.relay(view, record, worker.changeset_summary(run, view))
        if result.publish_questions:
            worker.publish_questions(run, view)
            return
        if not result.messages:
            return  # an unchanged stop keeps the gate it was relayed with: its token and floor stand
        if result.gate.get("key") == "done":
            return  # the delivery step announces completion with the pull request, once
        number = record["number"]
        floor = self.channel.floor(record, self.safe_issue(number) or {})
        result.gate["floor"] = floor
        for message in result.messages:
            posted = self.post(number, message)
            if posted:
                floor = max(floor, posted)
                result.gate["floor"] = floor
        record["gate"] = result.gate
        store.save(record)

    def consume_replies(self, record: dict, run) -> None:
        number = record["number"]
        issue = self.safe_issue(number)
        if not issue:
            return
        consumed = []
        for reply in self.channel.new_comments(record, issue):
            consumed.append(reply.id)
            try:
                view = run.status()  # a decision is always taken against a fresh view
                action = bridge_relay.interpret(record, view, reply.text)
                if action.kind == "none":
                    noted = self.post(number, f"@{reply.actor} {action.text}")
                else:
                    outcome = worker.take(run, view, action)
                    noted = self.post(number, f"@{reply.actor} {outcome}")
                if noted:
                    consumed.append(noted)  # the bridge's own words are never replies
            except (worker.BridgeError, TaskRunError) as error:
                self.post(number, f"@{reply.actor} AutoCode refused that: {error}"[:2000])
            except OSError as error:
                self.post(number, f"@{reply.actor} the bridge could not reach the run: {error}"[:2000])
            record.update(store.seen_comments(record, consumed))
            store.save(record)

    def deliver(self, record: dict, run) -> None:
        if record.get("pr_prepared"):
            return
        view = run.status()
        self.note_settled(record, view)  # an owned run the daemon just drove to its end frees its slot in this poll
        if not view.get("done"):
            return
        number = record["number"]
        try:
            output = self.prepare(record, open_=self.config.open_pr)
        except (worker.BridgeError, TaskRunError, OSError) as error:
            self.post(number, f"**The run completed, but preparing the pull request failed**: {error}"[:2000])
            record["pr_prepared"] = "failed"
            record["gate"] = {"key": "done"}
            store.save(record)
            return
        record["pr_prepared"] = True
        record["gate"] = {"key": "done"}
        store.save(record)
        outcome = (view.get("evidence") or {}).get("outcome") or view.get("status") or "complete"
        summary = f"**Task complete** ({outcome}). The change is committed on `{record['branch']}` with the run's evidence.\n\n```\n{output[-800:]}\n```"
        self.post(number, summary)

    # ---------------------------------------------------------------- helpers

    def safe_issue(self, number: int) -> dict | None:
        try:
            return self.channel.issue(number)
        except github.GitHubError as error:
            print(f"autocode bridge: reading issue {number} failed: {error}", file=sys.stderr)
            return None

    def post(self, number: int, body: str) -> int | None:
        try:
            return self.channel.post(number, body)
        except github.GitHubError as error:
            print(f"autocode bridge: posting to issue {number} failed: {error}", file=sys.stderr)
            return None

    def tick(self) -> None:
        labeled = None
        try:
            labeled = self.discover()
        except github.GitHubError as error:
            print(f"autocode bridge: discovery failed: {error}", file=sys.stderr)
        for record in store.records():
            try:
                self.tend(record)
            except (worker.BridgeError, TaskRunError, OSError, ValueError) as error:
                print(
                    f"autocode bridge: tending {record.get('owner')}/{record.get('repo')}#{record.get('number')} failed: {error}",
                    file=sys.stderr,
                )
        if labeled is not None:  # only issues seen open and labeled this poll may start
            self.start_queued(labeled)

    def run(self, *, once: bool = False) -> int:
        # The lock file stays open (and the lock held) for the daemon's whole life.
        with open(store.root() / "bridge.lock", "a+") as lock:
            try:
                util.lock_file(lock)
            except OSError:
                raise ConfigError("another bridge daemon already holds " + str(store.root() / "bridge.lock")) from None
            while True:
                self.tick()
                if once:
                    return 0
                self.sleep(self.config.poll_seconds)


def holds_slot(record: dict) -> bool:
    """A bridged issue whose run is in flight: started, not refused at start, and not settled.

    ``settled`` is what the run's last status view said. A record that never had its view read
    (one saved before the limit existed, or whose run cannot be read) is free only once delivered.
    """
    if record.get("queued") or record.get("failed"):
        return False
    settled = record.get("settled")
    return not (bool(record.get("pr_prepared")) if settled is None else settled)


def cmd_run(args) -> int:
    config = Config.from_env(project=args.project, poll_seconds=args.poll_seconds)
    channel = bridge_github.GitHubChannel(github.Client(), config.repo, config.label, config.actors)
    bridge = Bridge(config, channel)
    if not config.owned and not worker.durable_availability().get("available"):
        print(
            "autocode bridge: warning: durable execution is unavailable on this host; "
            "new runs will be refused until it is (see modes list --json, or set AUTOCODE_BRIDGE_OWNED=1 "
            "for runs that live with the daemon)",
            file=sys.stderr,
        )
    return bridge.run(once=args.once)


def cmd_check(args) -> int:
    failures = []
    try:
        config = Config.from_env(project=args.project)
        print(
            f"config     {config.repo} label {config.label!r}, {len(config.actors)} actor(s), options {list(config.options) or 'none'}"
        )
        print(
            f"limit      at most {config.max_active} bridged run(s) in flight at once"
            if config.max_active
            else "limit      none: every labeled issue starts at once (AUTOCODE_BRIDGE_MAX_ACTIVE sets one)"
        )
    except ConfigError as error:
        print(f"config     ✖ {error}")
        failures.append("config")
        config = None
    if config is not None:
        try:
            issue_tool.git(config.project, "rev-parse", "--verify", "HEAD^{commit}")
            print(f"project    {config.project}")
        except (issue_tool.IssueError, OSError) as error:
            print(f"project    ✖ {error}")
            failures.append("project")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    print(f"github    {'token present' if token else '✖ set GITHUB_TOKEN for the authorized bridge'}")
    if not token:
        failures.append("github")
    availability = worker.durable_availability()
    if config is not None and config.owned:
        print("durable    not used (AUTOCODE_BRIDGE_OWNED=1: runs live with the daemon process)")
    elif availability.get("available"):
        print("durable    available")
    else:
        print(f"durable    ✖ {availability.get('reason') or 'unavailable'}; runs cannot start here")
        failures.append("durable")
    print(f"storage    {store.root()}")
    if failures:
        print("not ready: " + ", ".join(failures))
        return 1
    print("ready for bridged runs")
    return 0


def cmd_status(args) -> int:
    rows = store.records()
    if args.json:
        print(json.dumps({"version": store.VERSION, "records": rows}, indent=2))
        return 0
    if not rows:
        print("no bridged issues")
        return 0
    for record in rows:
        where = f"{record['owner']}/{record['repo']}#{record['number']}"
        state = record.get("failed") or (
            f"gate {record.get('gate', {}).get('key')}" + (", pr prepared" if record.get("pr_prepared") else "")
        )
        print(f"{where}: {state}")
        print(f"  {record.get('title') or ''}")
        print(f"  run {record.get('run_dir') or '-'}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="autocode bridge", description=__doc__.splitlines()[0])
    commands = result.add_subparsers(dest="bridge_command", required=True)
    run = commands.add_parser("run", help="the daemon: take labeled issues, relay gates, take replies")
    run.add_argument("--once", action="store_true", help="one poll, then exit (for tests and cron-style operation)")
    run.add_argument("--project", help="the persistent checkout to build in (default: AUTOCODE_BRIDGE_PROJECT or cwd)")
    run.add_argument(
        "--poll-seconds",
        type=float,
        help="seconds between polls (default: 10, or AUTOCODE_BRIDGE_POLL_SECONDS)",
        default=None,
    )
    run.set_defaults(handler=cmd_run)
    check = commands.add_parser("check", help="report configuration, GitHub access, durable availability")
    check.add_argument("--project", help="persistent checkout to build in (default: AUTOCODE_BRIDGE_PROJECT or here)")
    check.set_defaults(handler=cmd_check)
    status = commands.add_parser("status", help="list the bridged issues and their gates")
    status.add_argument("--json", action="store_true", help="print the listing as JSON")
    status.set_defaults(handler=cmd_status)
    return result


def cli(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
