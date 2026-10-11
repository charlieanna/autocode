"""First interaction latency is historical public fact, never a status-read effect."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import autocode_goal_lifecycle as lifecycle
import autocode_interaction_timing as timing
import autocode_run_view as run_view

LAUNCH = "2026-10-10T08:00:00+00:00"
QUESTION = "2026-10-10T08:00:12.5+00:00"
PLAN = "2026-10-10T08:01:00+00:00"
BUILDER = "2026-10-10T08:05:00+00:00"


class InteractionTimingTests(unittest.TestCase):
    def test_first_events_survive_repeat_displays_and_include_human_wait(self):
        state = {"status": "RUNNING"}
        timing.begin(state, LAUNCH)
        timing.mark(state, "question", QUESTION)
        timing.mark(state, "plan", PLAN)
        timing.mark(state, "builder", BUILDER)
        saved = copy.deepcopy(state)
        for event in timing.EVENTS:
            self.assertFalse(timing.mark(state, event, "2026-10-11T08:00:00+00:00"))
        shown = run_view.view(state)["interaction_timing"]
        self.assertEqual((12.5, 60, 300), tuple(shown[f"first_{event}_seconds"] for event in timing.EVENTS))
        self.assertEqual(saved, state)
        shown["first_builder_at"] = "changed by reader"
        self.assertEqual(BUILDER, run_view.view(state)["interaction_timing"]["first_builder_at"])

    def test_legacy_and_unreached_events_remain_unknown_without_mutation(self):
        state = {"created_at": LAUNCH, "status": "AWAITING_GOAL_APPROVAL"}
        before = copy.deepcopy(state)
        self.assertFalse(timing.mark(state, "plan", PLAN))
        self.assertTrue(all(value is None for value in run_view.view(state)["interaction_timing"].values()))
        self.assertEqual(before, state)
        timing.begin(state, LAUNCH)
        self.assertIsNone(run_view.view(state)["interaction_timing"]["first_builder_at"])
        self.assertIsNone(run_view.view(state)["interaction_timing"]["first_builder_seconds"])

    def test_only_published_questions_and_approval_ready_plans_count(self):
        state = {"status": "WAITING_FOR_USER"}
        timing.begin(state, LAUNCH)
        timing.presented(state, None, at=QUESTION)
        timing.presented(state, {"scope": "goal_approval"}, at=QUESTION)
        self.assertIsNone(timing.project(state)["first_question_at"])
        timing.presented(state, {"scope": "clarification", "questions": [{"id": "Q"}]}, at=QUESTION)
        self.assertEqual(QUESTION, timing.project(state)["first_question_at"])
        state["status"] = "AWAITING_GOAL_APPROVAL"
        timing.presented(state, {"scope": "clarification", "questions": [{"id": "Q"}]}, at=PLAN)
        self.assertIsNone(timing.project(state)["first_plan_at"])
        timing.presented(state, {"scope": "goal_approval"}, at=PLAN)
        self.assertEqual(PLAN, timing.project(state)["first_plan_at"])

    def test_failed_plan_render_does_not_invent_a_display(self):
        state = {"status": "AWAITING_GOAL_APPROVAL"}
        timing.begin(state, LAUNCH)
        with patch.object(lifecycle.human, "current", return_value={"scope": "goal_approval"}):
            with patch.object(lifecycle, "render", side_effect=ValueError("unrenderable")):
                with self.assertRaisesRegex(ValueError, "unrenderable"):
                    lifecycle.present(state)
        self.assertIsNone(timing.project(state)["first_plan_at"])
        with patch.object(lifecycle.human, "current", return_value={"scope": "goal_approval"}):
            with (
                patch.object(lifecycle, "render", return_value="Plan"),
                patch.object(lifecycle.s, "now", return_value=PLAN),
            ):
                self.assertEqual("Plan", lifecycle.present(state))
        self.assertEqual(PLAN, timing.project(state)["first_plan_at"])

    def test_bad_or_backwards_clocks_are_unknown_not_negative_latency(self):
        for launched_at, at in (
            (LAUNCH, "bad"),
            ("bad", PLAN),
            (LAUNCH, "2026-10-09T08:00:00+00:00"),
            (LAUNCH, "2026-10-10T08:00:15"),
        ):
            with self.subTest(launch=launched_at, at=at):
                self.assertIsNone(timing.elapsed(launched_at, at))
        self.assertEqual(60, timing.elapsed(LAUNCH, "2026-10-10T09:01:00+01:00"))


class BuilderTimingBoundaryTests(unittest.TestCase):
    def test_parallel_worker_success_and_failed_spawn_are_distinct(self):
        import autocode_dispatch as dispatch

        for succeeds in (False, True):
            with self.subTest(spawn_succeeds=succeeds), tempfile.TemporaryDirectory() as directory:
                run_dir = Path(directory)
                worker_dir = run_dir / "worker"
                worker_dir.mkdir()
                (worker_dir / "result.json").write_text(json.dumps({"status": "BUILT"}))
                state = {"status": "RUNNING"}
                timing.begin(state, LAUNCH)
                batch = {"workers": [{"status": "PENDING", "run_dir": str(worker_dir), "workspace": str(worker_dir)}]}
                child = MagicMock(pid=123, returncode=0)
                child.poll.return_value = 0
                tree = MagicMock()
                tree.sample.return_value = False
                with (
                    patch.object(dispatch.processes, "process_table", return_value={}),
                    patch.object(dispatch.s, "assert_no_legacy_process"),
                    patch.object(dispatch.processes, "ProcessTree", return_value=tree),
                    patch.object(dispatch.s, "now", return_value=BUILDER),
                    patch.object(
                        dispatch.subprocess,
                        "Popen",
                        return_value=child,
                        side_effect=None if succeeds else OSError("spawn failed"),
                    ) as spawn,
                ):
                    if succeeds:
                        dispatch.run_workers(state, run_dir, batch)
                        self.assertEqual(BUILDER, run_view.view(state)["interaction_timing"]["first_builder_at"])
                    else:
                        with self.assertRaisesRegex(OSError, "spawn failed"):
                            dispatch.run_workers(state, run_dir, batch)
                        self.assertIsNone(run_view.view(state)["interaction_timing"]["first_builder_at"])
                    spawn.assert_called_once()

    def test_post_spawn_timing_write_failure_still_collects_the_registered_worker(self):
        import autocode_dispatch as dispatch

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            worker_dir = run_dir / "worker"
            worker_dir.mkdir()
            state = {"status": "RUNNING"}
            timing.begin(state, LAUNCH)
            batch = {"workers": [{"status": "PENDING", "run_dir": str(worker_dir), "workspace": str(worker_dir)}]}
            child = MagicMock(pid=123)
            child.poll.return_value = None
            tree = MagicMock()
            tree.sample.return_value = False
            with (
                patch.object(dispatch.processes, "process_table", return_value={}),
                patch.object(dispatch.s, "assert_no_legacy_process"),
                patch.object(dispatch.processes, "ProcessTree", return_value=tree),
                patch.object(dispatch.s, "now", return_value=BUILDER),
                patch.object(dispatch.subprocess, "Popen", return_value=child),
                patch.object(
                    dispatch.autocode_status, "persist", side_effect=[None, OSError("timing checkpoint failed"), None]
                ),
            ):
                with self.assertRaisesRegex(OSError, "timing checkpoint failed"):
                    dispatch.run_workers(state, run_dir, batch)
            tree.sample.assert_called_once_with(initial=True)
            tree.stop.assert_called_once_with(child)

    def test_serial_dry_run_and_failed_provider_spawn_do_not_count(self):
        import autocode as runner

        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory) / "project"
                workspace.mkdir()
                subprocess.run(["git", "init", "-q", str(workspace)], check=True)
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(workspace),
                        "-c",
                        "user.name=Fixture",
                        "-c",
                        "user.email=fixture@example.test",
                        "commit",
                        "--allow-empty",
                        "-qm",
                        "base",
                    ],
                    check=True,
                )
                run_dir = workspace / ".autocode/runs/fixture"
                run_dir.mkdir(parents=True)
                state = {
                    "status": "RUNNING",
                    "iteration": 1,
                    "next_stage": "terra",
                    "settings": {"engine": "codex", "roles": {"terra": {"model": "builder"}}, "limits": {}},
                }
                timing.begin(state, LAUNCH)
                prepared = (["unused-provider"], {}, {}, {"engine": "codex"})
                with (
                    patch.object(runner.processes, "preflight"),
                    patch.object(runner.provider_launch, "prepare", return_value=prepared),
                    patch.object(runner.supervision, "launch", side_effect=OSError("provider spawn failed")) as spawn,
                ):
                    arguments = {
                        "role": "terra",
                        "prompt": "Fixture",
                        "sandbox": "workspace-write",
                        "workspace": workspace,
                        "run_dir": run_dir,
                        "state": state,
                        "schema": run_dir / "unused.schema.json",
                        "model": "builder",
                        "allow_write": True,
                        "dry_run": dry_run,
                    }
                    if dry_run:
                        report, _ = runner.run_role(**arguments)
                        self.assertEqual("DRY_RUN", report["status"])
                        spawn.assert_not_called()
                    else:
                        with self.assertRaisesRegex(OSError, "provider spawn failed"):
                            runner.run_role(**arguments)
                        spawn.assert_called_once()
                self.assertIsNone(run_view.view(state)["interaction_timing"]["first_builder_at"])


if __name__ == "__main__":
    unittest.main()
