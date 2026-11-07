"""Checking a change set's runs together and delivering them (#2066, step 6).

Each run of a change set passes its own completion gate unchanged. This module adds what no single
run can check, all driven by a person through ``autocode changeset`` (autocode_changeset):

- an **integration command** the person names (``check-command``): an argument array, run with no
  shell in one run's workspace, with ``AUTOCODE_CHANGESET_WORKSPACES`` holding a JSON object from
  each run's repository name to its workspace, so for example the consumer's tests can run against
  the producer's checkout. It is the place for the shared interface check: the producer's tests
  emit the contract and the consumer's tests read it;
- ``verify`` runs it once every run is TASK_COMPLETE. Results and waivers identify the named check,
  member runs and every workspace's code. A later change withdraws that evidence; a concurrent
  record change refuses the write instead of assigning an old result to a new check;
- ``waive`` records the person's reason to accept the set without a passing check, bound the same
  way; ``reopen`` sends the run that diverged a follow-up carrying the failed check's output;
- ``order`` sets the merge order, and ``pr_body`` writes the section each pull request carries:
  the change set id, every run, the merge order, the check or waiver, and the parts not covered.

Never reads ``state.json``: runs are read through their status views (the caller's summary) and
TaskRun, workspaces only through Git. Imports autocode_changeset_store and autocode_taskrun.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

try:
    from . import autocode_changeset_store as store
    from . import autocode_taskrun as taskrun
    from . import autocode_util as util
except ImportError:
    import autocode_changeset_store as store
    import autocode_taskrun as taskrun
    import autocode_util as util

WORKSPACES_ENV = "AUTOCODE_CHANGESET_WORKSPACES"
CHECK_TIMEOUT_SECONDS = 1800
OUTPUT_LIMIT = 4000  # the tail of the check's output kept in the record
COMPLETE = "TASK_COMPLETE"


class DeliveryRefused(Exception):
    """A verification or delivery action the change set's current state does not allow."""


def _git(workspace, *args) -> bytes:
    return subprocess.run(["git", "-C", str(workspace), *args], capture_output=True, check=False).stdout


def workspace_fingerprint(workspace) -> str:
    """A digest of the workspace's code: its commit, its diff and its untracked files, AutoCode's own excluded."""
    digest = hashlib.sha256()
    digest.update(_git(workspace, "rev-parse", "HEAD"))
    digest.update(_git(workspace, "diff", "HEAD", "--binary", "--", ".", ":(exclude).autocode"))
    for name in sorted(_git(workspace, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")):
        if not name or name.startswith(b".autocode/"):
            continue
        digest.update(name + b"\0")
        try:
            digest.update(hashlib.sha256((Path(workspace) / name.decode()).read_bytes()).digest())
        except OSError:
            digest.update(b"unreadable")
    return digest.hexdigest()


def fingerprint(summary: dict) -> str:
    """Bind evidence to the member runs, their statuses and their workspace code."""
    rows = sorted(
        (
            row["target"],
            row["workspace"],
            row.get("run_dir"),
            row.get("role"),
            row.get("status"),
            workspace_fingerprint(row["workspace"]),
        )
        for row in summary["runs"]
    )
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()[:32]


def check_identity(record: dict) -> str | None:
    """The exact named command, execution target and definition, never inferred for old evidence."""
    check = record.get("check")
    if not check:
        return None
    return hashlib.sha256(json.dumps(check, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _expected_revision(summary: dict, record: dict) -> int:
    """A separately read record must still be the one whose run views the caller inspected."""
    revision = record.get("revision", 0)
    if summary.get("changeset") != record.get("id") or summary.get("record_revision") != revision:
        raise DeliveryRefused("the change set changed since it was inspected; read its status again")
    return revision


def targets(summary: dict) -> list[str]:
    return [row["target"] for row in summary["runs"]]


def _row(summary: dict, target: str) -> dict:
    for row in summary["runs"]:
        if row["target"] == target:
            return row
    raise DeliveryRefused(f"{target!r} is not a run of this change set (runs: {', '.join(targets(summary))})")


def merge_order(summary: dict, record: dict) -> list[str]:
    """The person's merge order, else the source run first and then each linked run as linked."""
    order = record.get("merge_order") or []
    return order if sorted(order) == sorted(targets(summary)) else targets(summary)


def view(summary: dict, record: dict) -> dict:
    """The change set's ``verification``: whether it is done, and why not."""
    waiting = [row["target"] for row in summary["runs"] if row.get("status") != COMPLETE]
    check = record.get("check")
    result, waiver = record.get("check_result"), record.get("waiver")
    current = fingerprint(summary) if not waiting else None
    identity = check_identity(record)
    result_current = bool(
        result
        and current
        and identity
        and result.get("fingerprint") == current
        and result.get("check_identity") == identity
    )
    waiver_current = bool(
        waiver
        and current
        and identity
        and waiver.get("fingerprint") == current
        and waiver.get("check_identity") == identity
    )
    passed = result_current and bool(result and result.get("passed"))
    done = not waiting and (check is None or passed or waiver_current)
    next_command = None
    if not waiting and check and not result_current:
        next_command = ["autocode", "changeset", "verify", "--run-dir", summary["runs"][0]["run_dir"]]
    return {
        # The runs not yet TASK_COMPLETE; nothing is checked together before each passes its own gate.
        "waiting_on": waiting,
        "check": check,
        "result": {**result, "current": result_current} if result else None,
        "waiver": {**waiver, "current": waiver_current} if waiver else None,
        "done": done,
        "merge_order": merge_order(summary, record),
        "next_command": next_command,
    }


def set_check(summary: dict, record: dict, target: str, command: list[str]) -> dict:
    """Name the integration command; its previous result and waiver authorize no replacement check."""
    revision = _expected_revision(summary, record)
    _row(summary, target)
    if not command:
        raise DeliveryRefused("name the integration command after --, as an argument array")
    return store.set_fields(
        record["id"],
        {"check": {"command": list(command), "in": target, "set_at": util.now()}, "check_result": None, "waiver": None},
        expected_revision=revision,
    )


def verify(summary: dict, record: dict, *, timeout=CHECK_TIMEOUT_SECONDS) -> dict:
    """Run the integration command once every run is complete; save the result bound to their code."""
    revision = _expected_revision(summary, record)
    verification = view(summary, record)
    if verification["waiting_on"]:
        raise DeliveryRefused(f"every run must be complete first: waiting on {', '.join(verification['waiting_on'])}")
    check = record.get("check")
    if not check:
        raise DeliveryRefused("no integration command is named; set one with `changeset check-command`")
    identity = check_identity(record)
    workspaces = {row["target"]: row["workspace"] for row in summary["runs"]}
    before = fingerprint(summary)
    try:
        proc = subprocess.run(
            check["command"],
            cwd=_row(summary, check["in"])["workspace"],
            env={**os.environ, WORKSPACES_ENV: json.dumps(workspaces)},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        exit_code, output = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        exit_code, output = None, f"the integration command did not finish within {timeout} s"
    except OSError as error:
        exit_code, output = None, f"the integration command could not run: {error}"
    after = fingerprint(summary)
    result = {
        "passed": exit_code == 0 and before == after,
        "exit_code": exit_code,
        "at": util.now(),
        # A check that changed a workspace proves nothing about the code the runs delivered.
        "fingerprint": after,
        "check_identity": identity,
        "changed_workspaces": before != after,
        "output": output[-OUTPUT_LIMIT:],
    }
    return store.set_fields(record["id"], {"check_result": result}, expected_revision=revision)


def waive(summary: dict, record: dict, reason: str) -> dict:
    """Accept the set without a passing check, for the stated reason, for the code as it is now."""
    revision = _expected_revision(summary, record)
    reason = " ".join(str(reason or "").split())
    if not reason:
        raise DeliveryRefused("a waiver needs a reason")
    verification = view(summary, record)
    if verification["waiting_on"]:
        raise DeliveryRefused(f"every run must be complete first: waiting on {', '.join(verification['waiting_on'])}")
    if verification["done"] and not (verification["waiver"] or {}).get("current"):
        raise DeliveryRefused("the change set is already done; there is nothing to waive")
    return store.set_fields(
        record["id"],
        {
            "waiver": {
                "reason": reason,
                "at": util.now(),
                "fingerprint": fingerprint(summary),
                "check_identity": check_identity(record),
            }
        },
        expected_revision=revision,
    )


def reopen(summary: dict, record: dict, target: str, message: str | None = None, *, command=taskrun.AUTOCODE):
    """Send the run that diverged a follow-up; by default it carries the failed check's output."""
    row = _row(summary, target)
    if not row.get("run_dir") or row.get("status") != COMPLETE:
        raise DeliveryRefused(f"{target} is not complete; only a finished run takes a follow-up")
    result = record.get("check_result") or {}
    text = " ".join(str(message or "").split())
    if not text:
        if not result or result.get("passed"):
            raise DeliveryRefused("no failed integration check to send; give --message")
        check = record.get("check") or {}
        text = (
            f"The change set's integration check failed: {' '.join(check.get('command') or [])} "
            f"(in {check.get('in')}) exited {result.get('exit_code')}. Make this repository's side agree with "
            f"the other runs of the change set. Its output ends:\n{result.get('output', '')[-1500:]}"
        )
    return taskrun.TaskRun(Path(row["workspace"]), Path(row["run_dir"]), command=tuple(command)).follow_up(text)


def set_order(summary: dict, record: dict, order: list[str]) -> dict:
    if sorted(order) != sorted(targets(summary)):
        raise DeliveryRefused(f"name every run of the change set once: {', '.join(targets(summary))}")
    return store.set_fields(record["id"], {"merge_order": list(order)})


def pr_body(summary: dict, record: dict, target: str, uncovered: list[str]) -> str:
    """The Markdown section ``target``'s pull request carries, so the requests cross-link."""
    _row(summary, target)
    verification = view(summary, record)
    lines = [f"## Change set {summary['changeset']}", ""]
    lines.append(f"This pull request is the {target} side of a change that spans {len(summary['runs'])} repositories.")
    lines += ["", "| Repository | Role | Status | Run |", "| --- | --- | --- | --- |"]
    for row in summary["runs"]:
        here = " (this pull request)" if row["target"] == target else ""
        lines.append(
            f"| {row['target']}{here} | {row['role']} | {row.get('status')} | `{Path(row['run_dir'] or '').name}` |"
        )
    lines += ["", "Merge order: " + ", then ".join(verification["merge_order"]) + "."]
    check, result, waiver = verification["check"], verification["result"], verification["waiver"]
    if check is None:
        lines.append("Integration check: none was named.")
    elif result and result["current"] and result["passed"]:
        lines.append(f"Integration check passed: `{' '.join(check['command'])}` in {check['in']}.")
    elif waiver and waiver["current"]:
        lines.append(f"Integration check waived by the person: {waiver['reason']}")
    else:
        lines.append(f"Integration check not passed yet: `{' '.join(check['command'])}` in {check['in']}.")
    if uncovered:
        lines += ["", "Not covered by this change set: " + "; ".join(uncovered) + "."]
    return "\n".join(lines) + "\n"
