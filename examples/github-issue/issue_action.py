#!/usr/bin/env python3
"""Persistent self-hosted issue Action; run decisions remain in autocode-issue/TaskRun."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from autocode_cli.autocode_taskrun import TaskRun, TaskRunError


class ActionError(RuntimeError):
    pass


class GitHub:
    def __init__(self):
        self.token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not self.token:
            raise ActionError("Set GITHUB_TOKEN for the authorized issue workflow")
        self.api = (os.environ.get("AUTOCODE_GITHUB_API") or
                    os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
        address = urlsplit(self.api)
        if address.scheme != "https" and not (address.scheme == "http" and
                                               address.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ActionError("The GitHub API must use HTTPS (loopback HTTP is for the offline smoke)")

    def call(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self.token}",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "autocode-issue-action"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(Request(self.api + path, data=data, headers=headers, method=method), timeout=30) as response:
                return json.loads(response.read() or b"null")
        except HTTPError as error:
            raise ActionError(f"GitHub {method} {path} returned HTTP {error.code}") from None
        except (URLError, ValueError) as error:
            raise ActionError(f"GitHub {method} {path} failed: {error}") from None


def string_list(name):
    try:
        value = json.loads(os.environ.get(name, "[]"))
    except ValueError:
        raise ActionError(f"{name} must be a JSON array of strings") from None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ActionError(f"{name} must be a JSON array of strings")
    return value


def provider_options(value):
    # These are saved for future invocations. A human action must never be saved
    # as a provider option and replayed by a label or continuation.
    forbidden = {"--answer", "--delegate", "--delegate-all", "--reject-assumption",
                 "--feedback", "--follow-up", "--edit-goal", "--reconcile-review", "--review-token",
                 "--close-finding", "--close-reason", "--accept-completion", "--resume-paused",
                 "--resolver-response", "--resolver-request", "--resolver-token", "--resolver-message",
                 "--retry-failed-stage", "--retry-builder", "--diagnose-failed-stage", "--retry-report",
                 "--grant-recovery", "--accept-source-edit", "--accept-transport-change",
                 "--abandon-stage", "--recover-job-report", "--request-milestone-checkpoints",
                 "--planning-review-call-limit", "--bind-dependency", "--receive-dependency",
                 "--show-goal", "--status", "--dry-run", "--migrate-only", "--no-requirements",
                 "--workspace", "--run-dir", "--in-place", "--ui-run", "--adaptive-planning"}
    for word in value:
        option = word.split("=", 1)[0]
        if option.startswith("--approve-") or option in forbidden:
            raise ActionError(f"{option} is a human/run action, not an Action provider option")
    return value


def selected_issue(event, name, label):
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository):
        raise ActionError("GITHUB_REPOSITORY must name OWNER/REPO")
    if (event.get("repository") or {}).get("full_name", "").lower() != repository.lower():
        raise ActionError("Event repository does not match GITHUB_REPOSITORY")
    if name == "issues":
        issue = event.get("issue") or {}
        if (event.get("action") != "labeled" or (event.get("label") or {}).get("name") != label or
                "pull_request" in issue or issue.get("state") != "open"):
            return None
        number = issue.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ActionError("Issue event has no positive issue number")
    elif name == "workflow_dispatch":
        text = (event.get("inputs") or {}).get("issue", "")
        if not isinstance(text, str) or not re.fullmatch(r"[1-9][0-9]*", text):
            raise ActionError("Dispatch input issue must be a positive issue number")
        number = int(text)
    else:
        raise ActionError("Only issues:labeled and workflow_dispatch are supported")
    return repository, number


def authorize(api, repository):
    actor = os.environ.get("GITHUB_TRIGGERING_ACTOR") or os.environ.get("GITHUB_ACTOR")
    actors = string_list("AUTOCODE_ISSUE_ACTORS")
    if not actor or not actors or actor.lower() not in {item.lower() for item in actors}:
        raise ActionError("The triggering actor is not in AUTOCODE_ISSUE_ACTORS")
    permission = api.call("GET", f"/repos/{repository}/collaborators/{quote(actor, safe='')}/permission")
    # The API maps maintain to write and triage to read. Fail closed for read,
    # missing permissions, API failures, and unknown custom role names.
    if not isinstance(permission, dict) or permission.get("permission") not in {"write", "admin"}:
        raise ActionError("The triggering actor needs repository write or admin permission")


def read_regular(path):
    if path.is_symlink() or not path.is_file():
        raise ActionError(f"Expected a regular owned attachment: {path}")
    try:
        value = json.loads(path.read_text())
    except ValueError:
        raise ActionError(f"Invalid JSON attachment: {path}") from None
    if not isinstance(value, dict):
        raise ActionError(f"Expected an object attachment: {path}")
    return value


def attachment(project, slug, repository, number):
    record = read_regular(project / ".autocode" / "issues" / (slug + ".json"))
    owner, repo = repository.split("/")
    if (record.get("version"), record.get("owner"), record.get("repo"), record.get("number")) != (1, owner, repo, number):
        raise ActionError("The saved issue attachment has a different identity/version")
    workspace = Path(record.get("worktree") or "").resolve()
    expected = project / ".autocode" / "issues" / slug
    if expected.parent.is_symlink() or expected.is_symlink():
        raise ActionError("The owned issue worktree must not be a symlink")
    if workspace != expected.resolve() or not record.get("run_dir"):
        raise ActionError("The issue start is incomplete; inspect its retained attachment before retrying")
    run_dir = Path(record["run_dir"]).resolve()
    if not run_dir.is_relative_to(workspace / ".autocode" / "runs"):
        raise ActionError("The issue run is outside its owned worktree")
    options = record.get("options") or []
    if not isinstance(options, list) or any(not isinstance(word, str) for word in options):
        raise ActionError("The issue attachment has invalid provider options")
    provider_options(options)
    if "--no-adaptive-planning" not in options:
        raise ActionError("The retained issue run lacks --no-adaptive-planning; inspect its approval policy before continuing")
    return record, TaskRun(workspace, run_dir, options=tuple(options))


@contextmanager
def issue_lock(project, slug):
    directory = project / ".autocode" / "issue-action"
    for path in (project / ".autocode", directory):
        if path.is_symlink():
            raise ActionError(f"Persistent Action state must not be a symlink: {path}")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (slug + ".lock")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ActionError("Another Action owns this issue; retry after it finishes") from None
        yield directory


def issue_command(project, repository, number, command, *arguments, options=()):
    argv = [sys.executable, "-m", "autocode_cli.autocode_issue", command, f"{repository}#{number}",
            "--project", str(project), *arguments]
    if options:
        argv += ["--", *options]
    # argv/file-backed data only: issue text and dispatch inputs never enter a shell.
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode:
        raise ActionError(result.stderr.strip() or result.stdout.strip() or f"{command} failed")
    return result.stdout


def status_comment(api, repository, number, slug, directory, text):
    marker = f"<!-- autocode-issue-action:{repository}#{number} -->"
    author = os.environ.get("AUTOCODE_ISSUE_COMMENT_AUTHOR", "github-actions[bot]")
    fence = "`" * max(3, max((len(item) + 1 for item in re.findall(r"`+", text)), default=3))
    body = (marker + "\nAutoCode retains this issue's worktree and run on the persistent runner.\n\n" +
            fence + "text\n" + text.rstrip() + "\n" + fence + "\n\n" +
            "Read the plan and execute the displayed approval/answer command on that runner. "
            "Then run this workflow manually with issue `" + str(number) + "`. "
            "Dispatching the workflow does not approve anything. Pull requests remain drafts; nothing merges.")
    receipt_path = directory / (slug + ".json")
    comment = None
    if receipt_path.exists() or receipt_path.is_symlink():
        receipt = read_regular(receipt_path)
        comment_id = receipt.get("comment_id")
        if isinstance(comment_id, bool) or not isinstance(comment_id, int) or comment_id < 1:
            raise ActionError("Invalid Action comment receipt")
        comment = api.call("GET", f"/repos/{repository}/issues/comments/{comment_id}")
    else:
        # Recover a comment accepted immediately before the receipt was saved.
        for page in range(1, 11):
            comments = api.call("GET", f"/repos/{repository}/issues/{number}/comments?per_page=100&page={page}")
            if not isinstance(comments, list):
                raise ActionError("GitHub returned an invalid comment list")
            matches = [item for item in comments if (item.get("body") or "").startswith(marker) and
                       (item.get("user") or {}).get("login") == author]
            if matches:
                comment = matches[0]
                break
            if len(comments) < 100:
                break
        else:
            raise ActionError("Locate the existing Action comment before retrying; refusing a duplicate")
    if comment is not None:
        if (comment.get("user") or {}).get("login") != author or not (comment.get("body") or "").startswith(marker):
            raise ActionError("The saved comment is not this Action's owned issue comment")
        if comment.get("body") != body:
            comment = api.call("PATCH", f"/repos/{repository}/issues/comments/{comment['id']}", {"body": body})
    else:
        comment = api.call("POST", f"/repos/{repository}/issues/{number}/comments", {"body": body})
    if not isinstance(comment.get("id"), int) or comment["id"] < 1:
        raise ActionError("GitHub did not return a comment identity")
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=slug + "-", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump({"version": 1, "comment_id": comment["id"]}, stream)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(receipt_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--project", default=os.environ.get("AUTOCODE_ISSUE_PROJECT"))
    parser.add_argument("--label", default=os.environ.get("AUTOCODE_ISSUE_LABEL", "autocode"))
    args = parser.parse_args(argv)
    try:
        if not args.event or not args.project:
            raise ActionError("Set GITHUB_EVENT_PATH and the persistent AUTOCODE_ISSUE_PROJECT")
        event = read_regular(Path(args.event))
        name = os.environ.get("GITHUB_EVENT_NAME")
        selected = selected_issue(event, name, args.label)
        if selected is None:
            print("Ignored event: not an open issue with the configured label")
            return 0
        repository, number = selected
        api = GitHub()
        authorize(api, repository)
        # The issue CLI uses this variable, including on GitHub Enterprise.
        os.environ["AUTOCODE_GITHUB_API"] = api.api
        options = provider_options(string_list("AUTOCODE_ISSUE_OPTIONS"))
        # Normal joint planning may let a Plan Reviewer approve automatically.
        # This example requires the operator's exact-token approval every time.
        if "--no-adaptive-planning" not in options:
            options.append("--no-adaptive-planning")
        project = Path(args.project).resolve()
        checked = subprocess.run(["git", "-C", str(project), "rev-parse", "--show-toplevel"],
                                 capture_output=True, text=True)
        if checked.returncode or Path(checked.stdout.strip()).resolve() != project:
            raise ActionError("AUTOCODE_ISSUE_PROJECT must be the persistent Git checkout root")
        slug = repository.replace("/", "-") + "-" + str(number)
        with issue_lock(project, slug) as directory:
            path = project / ".autocode" / "issues" / (slug + ".json")
            if name == "issues" and not (path.exists() or path.is_symlink()):
                issue_command(project, repository, number, "start", options=options)
            elif name == "workflow_dispatch" and not path.exists():
                raise ActionError("No retained issue run; apply the configured label first")
            record, run = attachment(project, slug, repository, number)
            if name == "workflow_dispatch" and not record.get("pr_url"):
                issue_command(project, repository, number, "continue")
                if run.status()["done"]:
                    # The issue CLI independently checks current canonical evidence
                    # before committing or opening. The Action never rebuilds it.
                    issue_command(project, repository, number, "pr", "--open")
            text = issue_command(project, repository, number, "status")
            print(text, end="")
            status_comment(api, repository, number, slug, directory, text)
        return 0
    except (ActionError, TaskRunError, OSError) as error:
        print(f"issue-action: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
