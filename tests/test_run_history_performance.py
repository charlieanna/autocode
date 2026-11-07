"""Bounded saved-history workloads, with work budgets rather than timing gates.

Reproduce timings without providers or private history:
    .venv/bin/python -m tests.test_run_history_performance --benchmark
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

import autocode_registry as registry
import autocode_run_finder as finder
import autocode_run_view as run_view
import autocode_runs_command as runs

NOW = 1794000000


class History:
    """Three projects, issue/task lanes, mixed saved statuses and two bad pointers.

    Assemble the documented registry pointers in one write: registering each of
    500 fixtures separately would measure quadratic fixture setup, not listing.
    All checkpoint timestamps differ, as they do in a history grown over time.
    """

    def __init__(self, root: Path, size: int):
        self.root = root.resolve()
        self.projects = [self.root / f"project-{i}" for i in range(3)]
        self.home = self.root / "registry-home"
        self.states: dict[Path, dict] = {}
        document = {"version": 1, "workspaces": {}, "runs": {}}

        def identity(prefix, path):
            return f"{prefix}-{hashlib.sha256(str(path).encode()).hexdigest()[:24]}"

        def save(workspace, name, state):
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / ".git").touch()
            run_dir = workspace / ".autocode" / "runs" / name
            run_dir.mkdir(parents=True)
            state = {"version": 3, "workspace": str(workspace), "task": f"Fix {name}", **state}
            path = run_dir / "state.json"
            path.write_text(json.dumps(state))
            os.utime(path, (NOW - len(self.states) * 60, NOW - len(self.states) * 60))
            self.states[run_dir] = state
            workspace_id, run_id = identity("workspace", workspace), identity("run", run_dir)
            document["workspaces"][workspace_id] = {"id": workspace_id, "workspace": str(workspace)}
            document["runs"][run_id] = {
                "id": run_id,
                "workspace_id": workspace_id,
                "workspace": str(workspace),
                "run_dir": str(run_dir),
                "task_id": None,
            }
            return run_dir

        for i in range(size):
            project = self.projects[i % 3]
            lane = (i // 3) % 3
            workspace = project
            if lane == 1:
                workspace = project / runs.ISSUE_WORKTREES / "example-7"
            elif lane == 2:
                workspace = project / ".autocode" / "worktrees" / "fix-regression"
                (workspace / ".autocode").mkdir(parents=True, exist_ok=True)
                (workspace / ".autocode" / "task-workspace.json").write_text(
                    json.dumps({"workspace": str(workspace), "project_workspace": str(project)})
                )
            save(
                workspace,
                f"20261101-120000-{i:04}",
                {
                    "status": ("RUNNING", "PAUSED_REQUESTED", "TASK_COMPLETE")[(i // 9) % 3],
                    "created_at": "2026-11-01T12:00:00Z",
                    "task": f"Fix regression {i}\nPreserve behavior and add a regression test.",
                    "settings": {},
                    "iteration": 1,
                    "phase": "BUILDING",
                    "next_stage": "terra",
                    "stop_reason": "paused on request",
                    "stages": [],
                    "turns": [],
                    "user_events": [],
                },
            )
        self.corrupt = save(self.projects[0], "corrupt", {"status": "RUNNING"})
        (self.corrupt / "state.json").write_text("{not JSON")
        self.deleted = save(self.projects[0], "deleted", {"status": "RUNNING"})
        (self.deleted / "state.json").unlink()
        self.deleted.rmdir()
        self.home.mkdir()
        (self.home / "registry.json").write_text(json.dumps(document))
        self.environment = {
            "AUTOCODE_HOME": str(self.home),
            "AUTOCODE_ATTENTION_HOME": str(self.root / "attention-home"),
        }

    def local(self):
        return {path for path in self.states if path != self.deleted and path.is_relative_to(self.projects[0])}


def snapshot(root):
    """Notice new/deleted paths, changed bytes, permissions and mtimes; ignore access time."""
    return {
        str(path.relative_to(root)): (path.stat().st_mode, path.stat().st_mtime_ns, path.read_bytes())
        if path.is_file()
        else None
        for path in root.rglob("*")
    }


class RunHistoryWorkTests(unittest.TestCase):
    def test_bounded_history_reads_are_linear_and_discovery_is_once_per_listing(self):
        for size in (10, 100, 500):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as temporary:
                history = History(Path(temporary), size)
                with mock.patch.dict(os.environ, history.environment):
                    for global_scope in (False, True):
                        with self.subTest(all_workspaces=global_scope):
                            reads = Counter()
                            original = Path.read_text

                            def read(path, *args, **kwargs):
                                reads[path] += 1
                                return original(path, *args, **kwargs)

                            with (
                                mock.patch.object(Path, "read_text", read),
                                mock.patch.object(runs, "_pairs", wraps=runs._pairs) as discovery,
                                mock.patch.object(finder, "task_worktrees", wraps=finder.task_worktrees) as trees,
                                mock.patch.object(registry, "listing", wraps=registry.listing) as registered,
                                mock.patch.object(runs.changeset_store, "memberships", return_value={}) as sets,
                                mock.patch.object(
                                    run_view, "view", side_effect=AssertionError("full status projection")
                                ),
                            ):
                                rows = runs.rows(history.projects[0], all_workspaces=global_scope, now=NOW)
                            expected = set(history.states) if global_scope else history.local()
                            self.assertEqual(expected, {Path(row["run_dir"]) for row in rows})
                            self.assertEqual(1, discovery.call_count)
                            self.assertEqual(1, sets.call_count)
                            self.assertEqual(int(global_scope), registered.call_count)
                            self.assertEqual(int(not global_scope), trees.call_count)
                            checkpoint_reads = {
                                path: count for path, count in reads.items() if path.name == "state.json"
                            }
                            self.assertEqual(expected - {history.deleted}, {path.parent for path in checkpoint_reads})
                            # Global discovery currently validates before projection. Permit
                            # eliminating its duplicate read without weakening the local budget.
                            self.assertLessEqual(max(checkpoint_reads.values()), 2 if global_scope else 1)
                            self.assertTrue(
                                all(count == 1 for path, count in reads.items() if path.name != "state.json")
                            )

    def test_public_cli_keeps_actions_order_bad_entries_and_every_fixture_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            history = History(Path(temporary), 100)
            source, member = list(history.states)[:2]
            (history.home / "changesets.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "changesets": {
                            "example": {
                                "id": "example",
                                "source": {"run_dir": str(source), "workspace": history.states[source]["workspace"]},
                                "members": [
                                    {
                                        "target": "other",
                                        "run_dir": str(member),
                                        "workspace": history.states[member]["workspace"],
                                    }
                                ],
                            }
                        },
                    }
                )
            )
            before = snapshot(history.root)
            with mock.patch.dict(os.environ, history.environment):
                import autocode

                out = io.StringIO()
                with (
                    mock.patch.object(sys, "argv", ["autocode", "runs", "--all", "--json"]),
                    mock.patch.object(subprocess, "Popen", side_effect=AssertionError("listing launched a process")),
                    contextlib.redirect_stdout(out),
                ):
                    self.assertEqual(0, autocode.main())
                document = json.loads(out.getvalue())
                self.assertEqual("registry", document["scope"])
                self.assertEqual(102, document["counts"]["total"])
                rows = document["runs"]
                self.assertEqual(
                    sorted(rows, key=lambda row: (runs.BUCKETS.index(row["bucket"]), row["run_dir"])), rows
                )
                for row in rows:
                    path = Path(row["run_dir"])
                    if path in (history.corrupt, history.deleted):
                        self.assertEqual(runs.UNREADABLE, row["bucket"])
                        self.assertNotIn("next_command", row)
                        continue
                    state = history.states[path]
                    self.assertEqual("example" if path in (source, member) else None, row["changeset"])
                    action = run_view.action(state, run_dir=path, workspace=Path(state["workspace"]))
                    self.assertEqual(action["next_command"], row["next_command"])
                    self.assertEqual((action["needs"] or {}).get("kind"), row["needs"])
                    if state["status"] == "TASK_COMPLETE":
                        self.assertEqual("completion", row["attention"]["item"]["kind"])
                deleted = next(row for row in rows if row["run_dir"] == str(history.deleted))
                self.assertIn("registry forget-deleted", deleted["reason"])
                self.assertEqual(before, snapshot(history.root))

    def test_repeated_listing_reads_changed_and_deleted_checkpoints_and_replay_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            history = History(Path(temporary), 10)
            run_dir = next(iter(history.states))
            path = run_dir / "state.json"
            with mock.patch.dict(os.environ, history.environment):

                def current():
                    return next(row for row in runs.rows(history.projects[0]) if row["run_dir"] == str(run_dir))

                self.assertEqual(runs.WORKING, current()["bucket"])
                state = {**history.states[run_dir], "status": "PAUSED_REQUESTED"}
                path.write_text(json.dumps(state))
                self.assertIn("--resume-paused", current()["next_command"])
                for settings, completed in (
                    ({}, True),
                    ({"replay_recording": {}}, False),
                    ({"replay_recording": {"dispatch_ids": [], "sha256": "0" * 64, "checks": "recorded"}}, False),
                ):
                    path.write_text(json.dumps({**state, "status": "TASK_COMPLETE", "settings": settings}))
                    item = current()["attention"]["item"]
                    self.assertEqual(completed, bool(item and item["kind"] == "completion"))
                path.unlink()
                self.assertEqual("checkpoint_missing", current()["availability"])


def benchmark():
    """Warm row projection and fresh CLI processes; setup/instrumentation are untimed."""
    print(json.dumps({"platform": platform.platform(), "python": sys.version, "warm_samples": 5, "cli_samples": 3}))
    for size in (10, 100, 500):
        with tempfile.TemporaryDirectory() as temporary:
            history = History(Path(temporary), size)
            with mock.patch.dict(os.environ, history.environment):
                for global_scope in (False, True):

                    def listing():
                        return runs.rows(history.projects[0], all_workspaces=global_scope, now=NOW)

                    listing()
                    samples = []
                    for _ in range(5):
                        started = time.perf_counter()
                        rows = listing()
                        samples.append(time.perf_counter() - started)
                    reads = Counter()
                    original = Path.read_text

                    def read(path, *args, **kwargs):
                        reads[path.name] += 1
                        return original(path, *args, **kwargs)

                    with mock.patch.object(Path, "read_text", read):
                        listing()
                    command = [sys.executable, str(Path(runs.__file__).with_name("autocode.py")), "runs", "--json"]
                    command += ["--all"] if global_scope else ["--workspace", str(history.projects[0])]
                    cli_samples = []
                    for _ in range(3):
                        started = time.perf_counter()
                        result = subprocess.run(
                            command, cwd=history.projects[0], capture_output=True, text=True, check=True, timeout=30
                        )
                        cli_samples.append(time.perf_counter() - started)
                        if len(json.loads(result.stdout)["runs"]) != len(rows):
                            raise AssertionError("CLI and row projection disagree")
                    print(
                        json.dumps(
                            {
                                "checkpoints": size,
                                "all": global_scope,
                                "rows": len(rows),
                                "median_s": statistics.median(samples),
                                "samples_s": samples,
                                "reads": reads,
                                "cli_median_s": statistics.median(cli_samples),
                                "cli_samples_s": cli_samples,
                            }
                        ),
                        flush=True,
                    )


if __name__ == "__main__":
    if sys.argv[1:] == ["--benchmark"]:
        benchmark()
    else:
        unittest.main()
