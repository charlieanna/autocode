"""Offline harness kit for the AutoPilot test catalogue (T00).

Everything a catalogue scenario needs to run safely and leave evidence:

* ``Bundle`` writes the per-case evidence bundle from HARNESS_PROTOCOL.md
  (state snapshots, trace, assertions, operations ledger, environment,
  result) under a dedicated artifact root that never enters candidate
  source hashing.  The root defaults to the gitignored ``.tmp-*`` tree and
  can be redirected with ``AUTOCODE_TEST_ARTIFACTS``.
* ``FindingsOracle`` is an independent reference model of the finding
  ledger contract.  It computes expected ledger rows from scripted reports
  without importing or calling the production decision functions.
* ``CommandOracle`` decides expected command equivalence from the fixture's
  construction (program text plus a strictly verified single login-shell
  wrapper) rather than from ``autocode_support.same_command``.
* ``FakeProvider`` records would-be launches in the operations ledger; a
  scenario that tries to launch a real provider records the attempt and
  fails the bundle.
* ``offline`` blocks socket creation for the guarded block, proving a
  scenario needs no network.

Assertion style: ``bundle.check``/``bundle.expect_raises`` record rows and
never raise, so a failing scenario still writes a complete, replayable
bundle; ``bundle.finish`` writes ``result.json`` and then raises once if
any recorded row failed.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import platform
import re
import shlex
import socket
import subprocess
import sys
import traceback
import unittest
from unittest import mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PASS, FAIL, BLOCKED_ENV, ERROR = "PASS", "FAIL", "BLOCKED_ENV", "ERROR"


def artifacts_root() -> Path:
    configured = os.environ.get("AUTOCODE_TEST_ARTIFACTS")
    root = Path(configured) if configured else REPO_ROOT / ".tmp-autopilot-testkit" / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def source_revision() -> dict:
    def git(*args):
        return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain")),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD")}


class Bundle:
    """One scenario attempt's evidence bundle."""

    def __init__(self, scenario_id: str, attempt: int | None = None):
        self.scenario_id = scenario_id
        base = artifacts_root() / scenario_id
        base.mkdir(parents=True, exist_ok=True)
        if attempt is None:
            used = [int(p.name) for p in base.iterdir() if p.is_dir() and p.name.isdigit()]
            attempt = max(used, default=0) + 1
        self.attempt = attempt
        self.dir = base / f"{attempt:02d}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.operations: list[dict] = []
        self.rows: list[dict] = []
        self.finished = False
        self.log("bundle_opened")
        (self.dir / "environment.json").write_text(json.dumps({
            "scenario": scenario_id, "python": sys.version, "platform": platform.platform(),
            "machine": platform.machine(), "git": source_revision(),
            "interpreter": sys.executable, "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }, indent=2))

    # -- observation ------------------------------------------------------
    def log(self, event: str, **detail):
        row = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "event": event, **detail}
        with (self.dir / "trace.jsonl").open("a") as handle:
            handle.write(json.dumps(row) + "\n")

    def operation(self, kind: str, **payload):
        """Record one fake launch/effect in the independent operations ledger."""
        entry = {"op_id": len(self.operations) + 1, "kind": kind,
                 "at": dt.datetime.now(dt.timezone.utc).isoformat(), **payload}
        self.operations.append(entry)
        (self.dir / "operations.json").write_text(json.dumps(self.operations, indent=2))
        return entry

    def state(self, which: str, value):
        (self.dir / f"state.{which}.json").write_text(json.dumps(value, indent=2, default=str))

    # -- assertions -------------------------------------------------------
    def check(self, name: str, expected, observed) -> bool:
        ok = expected == observed
        self.rows.append({"name": name, "expected": expected, "observed": observed, "ok": ok})
        self.log("assertion", name=name, ok=ok)
        return ok

    def check_true(self, name: str, observed: bool) -> bool:
        return self.check(name, True, bool(observed))

    def check_false(self, name: str, observed: bool) -> bool:
        return self.check(name, False, bool(observed))

    def expect_raises(self, name: str, exc_type, fn, *args, **kwargs) -> bool:
        try:
            fn(*args, **kwargs)
        except exc_type as error:
            self.rows.append({"name": name, "expected": f"raises {exc_type.__name__}",
                              "observed": f"{type(error).__name__}: {error}", "ok": True})
            self.log("assertion", name=name, ok=True)
            return True
        except Exception as error:  # wrong failure class
            self.rows.append({"name": name, "expected": f"raises {exc_type.__name__}",
                              "observed": f"wrong class {type(error).__name__}: {error}", "ok": False})
            self.log("assertion", name=name, ok=False)
            return False
        self.rows.append({"name": name, "expected": f"raises {exc_type.__name__}", "observed": "no exception", "ok": False})
        self.log("assertion", name=name, ok=False)
        return False

    # -- completion -------------------------------------------------------
    @property
    def failures(self):
        return [row for row in self.rows if not row["ok"]]

    def finish(self, status: str = PASS, summary: str = "") -> bool:
        assert not self.finished
        self.finished = True
        if status == PASS and self.failures:
            status = FAIL
        if status == FAIL and not summary:
            summary = "; ".join(f"{row['name']}: expected {row['expected']!r} got {row['observed']!r}"
                                for row in self.failures)
        (self.dir / "assertions.json").write_text(json.dumps(self.rows, indent=2, default=str))
        (self.dir / "result.json").write_text(json.dumps({
            "scenario": self.scenario_id, "attempt": self.attempt, "status": status,
            "summary": summary or status, "checks": len(self.rows), "failed": len(self.failures),
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }, indent=2))
        self.log("bundle_finished", status=status)
        if status == FAIL:
            raise AssertionError(f"{self.scenario_id} failed: {summary}")
        return True


class CatalogueCase(unittest.TestCase):
    """Base class opening a bundle that always leaves a result, even on error.

    The scenario id comes from the class attribute when set, otherwise it is
    derived from the test method name (``test_fnd01_two_defects`` -> FND-01).
    """

    scenario_id: str = ""

    def resolve_scenario_id(self) -> str:
        if self.scenario_id:
            return self.scenario_id
        match = re.search(r"_([a-z]{3})(\d+)", self._testMethodName)
        if match:
            return f"{match.group(1).upper()}-{int(match.group(2))}"
        return self._testMethodName

    def setUp(self):
        assert self.scenario_id or re.search(r"_([a-z]{3})(\d+)", self._testMethodName), \
            "set scenario_id or name the test test_<id>_<case>"
        self.bundle = Bundle(self.resolve_scenario_id())
        self.addCleanup(self._close_bundle)

    def _close_bundle(self):
        if not self.bundle.finished:
            error = sys.exc_info()[1]
            if isinstance(error, self.failureException):
                self.bundle.rows.append({"name": "unittest_failure", "expected": "pass",
                                         "observed": str(error), "ok": False})
            self.bundle.finish(ERROR if not isinstance(error, self.failureException) else FAIL,
                               "".join(traceback.format_exception_only(type(error), error)) if error else "setUp/tearDown")

    @contextlib.contextmanager
    def forbid_real_launches(self, runtime):
        """Patch every role launch to a recording fake; any call is a failed check."""
        def fake_launch(**kwargs):
            self.bundle.operation("blocked_real_launch", stage=kwargs.get("state", {}).get("next_stage"))
            raise AssertionError("scenario must not launch a real provider")
        patcher = mock.patch.object(runtime, "run_role", side_effect=fake_launch)
        patcher.start()
        try:
            yield
        finally:
            patcher.stop()
            self.check("no_real_launches", [], [op for op in self.bundle.operations
                                                if op["kind"] == "blocked_real_launch"])

    # Assertion helpers delegate to the bundle so failures stay replayable.
    def check(self, name, expected, observed):
        return self.bundle.check(name, expected, observed)

    def check_true(self, name, observed):
        return self.bundle.check_true(name, observed)

    def check_false(self, name, observed):
        return self.bundle.check_false(name, observed)

    def expect_raises(self, name, exc_type, fn, *args, **kwargs):
        return self.bundle.expect_raises(name, exc_type, fn, *args, **kwargs)

    def finish(self, summary=""):
        return self.bundle.finish(summary=summary)


class FindingsOracle:
    """Reference model of the ledger contract, independent of autocode_findings.

    Identity is allocation-ordered (ORACLE-F1, -F2 ...), so two defects with
    identical wording stay separate.  Omission keeps a reviewer's open rows
    open and marks them not rechecked.  Dispositions close only that
    reviewer's open rows; a BLOCKED decision or a report-only repair closes
    nothing.  Scope: a disposition is honored only when the report reviewed
    a superset of the criteria the finding was raised against.
    """

    def __init__(self):
        self.rows: list[dict] = []
        self.counter = 0

    def _allocate(self):
        self.counter += 1
        return f"ORACLE-F{self.counter}"

    def apply(self, source: str, report: dict, *, scope=None, all_criteria=None,
              blocked=False, report_only=False) -> list[dict]:
        report = report or {}
        at = "oracle-now"
        open_source = [row for row in self.rows if row["source"] == source and row["status"] == "open"]
        seen: dict[str, dict] = {}
        for raw in report.get("findings", []):
            cited = str(raw.get("id") or "").strip()
            if cited:
                target = next((row for row in open_source if row["id"] == cited), None)
                if target is None or cited in seen:
                    raise ValueError(f"oracle: {source} cites unknown/duplicate open id {cited}")
                target.update(severity=raw.get("severity", target["severity"]),
                              evidence=str(raw.get("evidence", "")), times_reported=target["times_reported"] + 1,
                              last_reported_in=report.get("_output", ""))
                target.pop("not_rechecked", None)
                seen[cited] = target
            else:
                fid = self._allocate()
                self.rows.append({"id": fid, "source": source, "finding": raw.get("finding", ""),
                                  "severity": raw.get("severity", "medium"), "evidence": raw.get("evidence", ""),
                                  "status": "open", "opened_at": at, "times_reported": 1,
                                  "scope": scope, "assigned_task": None})
                seen[fid] = self.rows[-1]
        for row in open_source:
            if row["id"] not in seen:
                row["not_rechecked"] = True
        if not blocked and not report_only:
            for raw in report.get("finding_dispositions", []):
                target = next((row for row in self.rows
                               if row["source"] == source and row["status"] == "open"
                               and row["id"] == raw.get("id")), None)
                if target is None:
                    continue  # unknown or already closed: documented no-op
                if raw.get("disposition") not in ("resolved", "retracted") or not str(raw.get("evidence", "")).strip():
                    raise ValueError("oracle: invalid disposition")
                row_scope = target.get("scope")
                if row_scope is not None:
                    if scope is None or not set(row_scope["criteria"]) <= set(scope["criteria"]):
                        raise ValueError("oracle: disposition outside reviewed scope")
                elif scope is not None and all_criteria and not set(scope["criteria"]) >= set(all_criteria):
                    raise ValueError("oracle: scopeless finding needs a full review")
                target["status"] = raw["disposition"]
        return [dict(row) for row in self.rows]

    def open(self, source: str | None = None) -> list[dict]:
        return [row for row in self.rows if row["status"] == "open"
                and (source is None or row["source"] == source)]


class CommandOracle:
    """Independent equivalence decision for executed-command evidence.

    Two command lines are equivalent only when their program text is
    identical, or one side is exactly one login-shell invocation
    (``*/zsh -lc BODY`` / ``*/zsh -l -c BODY``) whose quoted BODY is the
    other side verbatim, with nothing else on the line.  Anything trailing
    after the wrapper body is executable content and must break equality.
    """

    @staticmethod
    def _unwrap(command: str):
        variants = {command}
        stripped = command.rstrip()
        if stripped and stripped[-1] in "\"'":
            variants.add(stripped[:-1])
        bodies = set()
        for variant in variants:
            try:
                parts = shlex.split(variant)
            except ValueError:
                continue
            if parts and parts[0].endswith("/zsh"):
                if parts[1:2] == ["-lc"] and len(parts) == 3:
                    bodies.add(parts[2])
                    continue
                if parts[1:3] == ["-l", "-c"] and len(parts) == 4:
                    bodies.add(parts[3])
                    continue
            bodies.add(variant)
        return bodies

    def equivalent(self, event_command: str, claimed_command: str) -> bool:
        if event_command == claimed_command:
            return True
        return bool(self._unwrap(event_command) & self._unwrap(claimed_command))


@contextlib.contextmanager
def offline():
    """Block socket construction for the guarded block: proves a scenario is offline."""
    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted during an offline scenario")

    real_socket, real_connect = socket.socket, socket.create_connection
    socket.socket = blocked
    socket.create_connection = blocked
    try:
        yield
    finally:
        socket.socket = real_socket
        socket.create_connection = real_connect


class FakeProvider:
    """Scripted provider stand-in: records the launch, returns the scripted report."""

    def __init__(self, bundle: Bundle, name: str = "fake-provider"):
        self.bundle, self.name = bundle, name
        self.script: list[dict] = []

    def queue(self, report: dict):
        self.script.append(report)

    def launch(self, **kwargs):
        self.bundle.operation("provider_launch", provider=self.name,
                              stage=kwargs.get("state", {}).get("next_stage") if isinstance(kwargs.get("state"), dict) else kwargs.get("stage"))
        if not self.script:
            raise AssertionError("scenario provider launched without a scripted report")
        report = self.script.pop(0)
        payload_digest = str(sorted(report.items()))[:64]
        self.bundle.operation("provider_result", provider=self.name, payload_digest=payload_digest,
                              remaining=len(self.script))
        return report


def read_result(scenario_id: str) -> dict:
    root = artifacts_root() / scenario_id
    results = sorted(root.glob("*/result.json"))
    if not results:
        raise FileNotFoundError(f"no executed result for {scenario_id}")
    return json.loads(results[-1].read_text())


def executed_statuses(scenario_ids):
    """Latest observed status per scenario id; missing scenarios are NOT_RUN."""
    statuses = {}
    for scenario_id in scenario_ids:
        try:
            statuses[scenario_id] = read_result(scenario_id)["status"]
        except FileNotFoundError:
            statuses[scenario_id] = "NOT_RUN"
    return statuses
