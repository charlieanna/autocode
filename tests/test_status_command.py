"""`--status` samples one checkpoint: its token, needs and controller all describe the same saved state."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def state_at(stage):
    return {
        "status": "RUNNING",
        "iteration": 1,
        "sessions": {},
        "next_stage": stage,
        "stages": [],
        "active_stage": None,
    }


class ConsistentSampleTests(unittest.TestCase):
    """A controller that writes and exits between the state read and the receipt read (the
    durable-worker flake of 2026-10-26 and 2026-10-27) must not produce a view whose continue offer
    carries the token of a checkpoint that no longer exists."""

    def render(self, state, run, inspect):
        import autocode as runner
        import autocode_status_command as command

        args = SimpleNamespace(inspect_evidence=False, run_dir="fixture", engine=None, explain=False)
        output = io.StringIO()
        with (
            patch.object(command.controller_owner, "inspect", side_effect=inspect),
            patch.object(runner.milestones, "summary", return_value={"accepted_milestones": []}),
            patch.object(runner.dependency, "export", return_value={}),
            patch.object(runner, "intervention_metadata", return_value={}),
            patch.object(runner.resolver_human, "projection", return_value={}),
            redirect_stdout(output),
            redirect_stderr(io.StringIO()),
        ):
            command.render(runner, state, args, run.parent, run)
        return json.loads(output.getvalue())["view"]

    def test_a_checkpoint_replaced_during_inspection_is_read_again(self):
        import autocode_util as util

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve() / "run"
            run.mkdir()
            path = run / "state.json"
            path.write_text(json.dumps(state_at("builder")))
            before = json.loads(path.read_text())
            calls = []

            def inspect(state, run_dir, workspace, **_):
                calls.append(state["next_stage"])
                if len(calls) == 1:  # the controller finishes a stage and exits during the first inspection
                    path.write_text(json.dumps(state_at("tester")))
                return {"kind": "boundary", "reason": "fixture", "receipt": None, "checked": True, "alive": False}

            view = self.render(before, run, inspect)
        self.assertEqual(["builder", "tester"], calls)
        self.assertEqual("tester", view["next_stage"])
        self.assertEqual(util.digest(state_at("tester")), view["observation"]["checkpoint_token"])
        self.assertEqual("continue", view["needs"]["kind"])

    def test_an_unchanged_checkpoint_is_inspected_once(self):
        import autocode_util as util

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve() / "run"
            run.mkdir()
            (run / "state.json").write_text(json.dumps(state_at("builder")))
            calls = []

            def inspect(state, run_dir, workspace, **_):
                calls.append(state["next_stage"])
                return {"kind": "boundary", "reason": "fixture", "receipt": None, "checked": True, "alive": False}

            view = self.render(state_at("builder"), run, inspect)
        self.assertEqual(["builder"], calls)
        self.assertEqual(util.digest(state_at("builder")), view["observation"]["checkpoint_token"])

    def test_a_requirements_question_names_its_origin_instead_of_the_queued_planner(self):
        question = {"id": "Q1", "question": "Which history entries are affected?"}
        for origin, pending, status, expected in (
            ("requirements_gather", "pending", "WAITING_FOR_USER", "Requirements"),
            ("draft_plan", "pending", "WAITING_FOR_USER", "Planner"),
            ("requirements_gather", "consumed", "WAITING_FOR_USER", "Planner"),
            ("requirements_gather", "pending", "PAUSED_INVALID_OUTPUT", "Planner"),
        ):
            with self.subTest(origin=origin, pending=pending, status=status), tempfile.TemporaryDirectory() as tmp:
                run = Path(tmp).resolve() / "run"
                run.mkdir()
                saved = {
                    **state_at("draft_plan"),
                    "status": status,
                    "settings": {"spec_first": True},
                    "pending_questions": [question],
                    "resolver_human_request": {
                        "request_id": "request-1",
                        "request_token": "token-1",
                        "scope": "clarification",
                    },
                    "resolver": {
                        "human_escalations": {
                            "request-1": {
                                "status": pending,
                                "identity": {"proposal": {"origin": {"stage": origin}}},
                            }
                        }
                    },
                }
                (run / "state.json").write_text(json.dumps(saved))
                shown = self.render(
                    saved,
                    run,
                    lambda *_args, **_kwargs: {
                        "kind": "boundary",
                        "reason": "fixture",
                        "receipt": None,
                        "checked": True,
                        "alive": False,
                    },
                )
                self.assertEqual(expected, shown["progress"]["stage"])
                self.assertEqual("Waiting for you", shown["progress"]["headline"])
                self.assertEqual("draft_plan", shown["next_stage"])
                self.assertEqual("Q1", shown["needs"]["questions"][0]["id"])

    def test_a_checkpoint_that_keeps_changing_is_bounded(self):
        import autocode_status_command as command

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve() / "run"
            run.mkdir()
            path = run / "state.json"
            path.write_text(json.dumps(state_at("stage-0")))
            calls = []

            def inspect(state, run_dir, workspace, **_):
                calls.append(state["next_stage"])
                path.write_text(json.dumps(state_at(f"stage-{len(calls)}")))
                return {"kind": "boundary", "reason": "fixture", "receipt": None, "checked": True, "alive": False}

            view = self.render(state_at("stage-0"), run, inspect)
        self.assertEqual(command.SAMPLE_RETRIES + 1, len(calls))
        self.assertEqual(f"stage-{command.SAMPLE_RETRIES}", view["next_stage"])


def supervision():
    return {
        "schema": 1,
        "nonce": "a" * 32,
        "receipt": "/run/attempt-supervision.json",
        "owner": {"pid": 101, "birth_identity": 123.25},
        "keeper": {"pid": 102, "birth_identity": 124.25},
        "provider": {"pid": 103, "birth_identity": 125.25},
        "started_at": "2026-11-09T12:00:00Z",
    }


def observed(saved, *, owner=True, keeper=False, provider=False, phase="stopped", cause="provider_stopped", error=None):
    """``autocode_supervision.observe`` for one attempt: each role's fresh liveness and its authenticated receipt."""
    return {
        "owner": {"checked": True, "alive": owner},
        "keeper": {"checked": True, "alive": keeper},
        "provider": {"checked": True, "alive": provider},
        "receipt": {
            **saved,
            "phase": phase,
            "cause": cause,
            "cleanup_error": error,
            "observed_at": "2026-11-09T12:09:20Z",
            "processes": [dict(saved["provider"])],
        },
    }


class FinalizingHandoffTests(unittest.TestCase):
    """#3031: a Builder whose provider stopped normally, with a finished report, while its birth-bound
    controller is alive and saving it, is being finalized, not abandoned. Status must not call it
    unsupervised or send the Resolver; a genuinely lost controller or keeper still must."""

    CONTROLLER_RUNNING = {
        "kind": "running",
        "reason": "Birth-bound controller is alive",
        "checked": True,
        "alive": True,
    }
    CONTROLLER_GONE = {
        "kind": "unknown",
        "reason": "Saved owner has not finished returning",
        "checked": True,
        "alive": False,
    }

    def render(self, *, controller, finished=True, liveness=None, workers=None):
        import autocode as runner
        import autocode_status_command as command

        saved = supervision()
        state = {
            **state_at("builder"),
            "active_stage": {
                "stage": "builder",
                "iteration": 1,
                "output": "iterations/builder-04.jsonl",
                "started_at": "2026-11-09T12:00:00Z",
                "supervision": saved,
            },
        }
        args = SimpleNamespace(inspect_evidence=False, run_dir="fixture", engine=None, explain=False)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve() / "run"
            run.mkdir()
            (run / "state.json").write_text(json.dumps(state))
            with (
                patch.object(command.controller_owner, "inspect", return_value={**controller, "receipt": None}),
                patch.object(
                    runner.processes, "recorded_worker_state", return_value=workers or {"checked": True, "alive": False}
                ),
                patch.object(runner.supervision, "observe", return_value=liveness or observed(saved)),
                patch.object(runner, "stage_completed", return_value=finished),
                patch.object(runner.milestones, "summary", return_value={"accepted_milestones": []}),
                patch.object(runner.dependency, "export", return_value={}),
                patch.object(runner, "intervention_metadata", return_value={}),
                patch.object(runner.resolver_human, "projection", return_value={}),
                redirect_stdout(output),
                redirect_stderr(io.StringIO()),
            ):
                command.render(runner, state, args, run.parent, run)
        return json.loads(output.getvalue())

    def assert_abandoned(self, payload):
        self.assertTrue(payload["stale"])
        self.assertIn("The Resolver must settle the interrupted attempt 001/builder-04", payload["next_action"])
        self.assertEqual("stopped", payload["view"]["summary"]["state"])
        self.assertIn("nothing supervises its retained attempt", payload["view"]["summary"]["sentence"])

    def test_a_finished_stage_its_live_controller_is_saving_is_not_unsupervised(self):
        payload = self.render(controller=self.CONTROLLER_RUNNING)
        self.assertEqual("stopped", payload["view"]["liveness"]["kind"])  # the payload the issue reported
        self.assertTrue(payload["active_stage_finished"])
        self.assertFalse(payload["stale"])
        self.assertIsNone(payload["next_action"])
        self.assertEqual("working", payload["view"]["summary"]["state"])
        self.assertNotIn("nothing supervises", payload["view"]["summary"]["sentence"])

    def test_a_finished_stage_whose_controller_is_gone_is_still_abandoned(self):
        self.assert_abandoned(self.render(controller=self.CONTROLLER_GONE))

    def test_incomplete_output_with_a_stopped_keeper_is_still_abandoned(self):
        # Unchecked recorded workers leave only the supervision receipt to judge the attempt by.
        for workers in ({"checked": True, "alive": False}, {"checked": False, "alive": None}):
            with self.subTest(workers=workers):
                self.assert_abandoned(self.render(controller=self.CONTROLLER_RUNNING, finished=False, workers=workers))

    def test_a_lost_supervision_owner_or_abnormal_cleanup_is_still_abandoned(self):
        saved = supervision()
        for name, liveness in (
            ("owner lost", observed(saved, owner=False)),
            ("cleanup error", observed(saved, error="provider group survived SIGKILL")),
            ("keeper failure", observed(saved, cause="keeper_failure")),
            ("interrupted", observed(saved, cause="owner_lost")),
        ):
            with self.subTest(name):
                self.assert_abandoned(self.render(controller=self.CONTROLLER_RUNNING, liveness=liveness))


class ProjectedControllerPauseTests(unittest.TestCase):
    """#3287: `runs` and the run's own --status display one controller-exit projection, read-only."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="projected-pause-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        (self.workspace / ".git").mkdir()
        self.harness = ConsistentSampleTests("test_an_unchanged_checkpoint_is_inspected_once")

    def persist(self, path, state):
        import autocode_controller_owner as owners
        import autocode_util as util

        util.atomic_json(path, state)
        owners.checkpoint(path, state)

    def make_run(self, name):
        import autocode_util as util

        run_dir = self.workspace / ".autocode" / "runs" / name
        run_dir.mkdir(parents=True)
        state = {
            "version": 3,
            "task": f"build {name}",
            "workspace": str(self.workspace),
            "status": "RUNNING",
            "iteration": 1,
            "sessions": {},
            "next_stage": "builder",
            "stages": [],
            "phase": "BUILDING",
        }
        util.atomic_json(run_dir / "state.json", state)
        return run_dir

    def exit_ready(self, run_dir):
        """The controller exits deliberately at this exact checkpoint: a ready receipt (#2332)."""
        import autocode_controller_owner as owners
        import autocode_util as util

        state = util.read(run_dir / "state.json")
        with util.run_lock(run_dir), owners.Owner(state, run_dir, self.persist) as owner:
            owner.result(0, ready=True)

    def exit_gone(self, run_dir):
        """A receipt whose recorded controller matches no live process-table row (#3287)."""
        import autocode_controller_owner as owners
        import autocode_util as util

        self.exit_ready(run_dir)
        receipt = util.read(run_dir / owners.FILE)
        receipt.update(disposition="running", exit_code=None, exited_at=None)
        util.atomic_json(run_dir / owners.FILE, receipt)

    def render_view(self, run_dir):
        """The run's own --status view, rendered through this module's sample harness."""
        import autocode_controller_owner as owners
        import autocode_process as processes
        import autocode_util as util

        real_inspect = owners.inspect

        def inspect(state, run_dir_arg, workspace, **_):
            with patch.object(processes, "process_table", return_value={}):
                return real_inspect(state, run_dir_arg, self.workspace)

        return self.harness.render(util.read(run_dir / "state.json"), run_dir, inspect)

    def render_envelope(self, run_dir):
        import autocode as runner
        import autocode_process as processes
        import autocode_status_command as command
        import autocode_util as util

        output = io.StringIO()
        args = SimpleNamespace(inspect_evidence=False, run_dir=str(run_dir), engine=None, explain=False)
        with (
            patch.object(processes, "process_table", return_value={}),
            patch.object(runner.milestones, "summary", return_value={"accepted_milestones": []}),
            patch.object(runner.dependency, "export", return_value={}),
            patch.object(runner, "intervention_metadata", return_value={}),
            redirect_stdout(output),
            redirect_stderr(io.StringIO()),
        ):
            command.render(runner, util.read(run_dir / "state.json"), args, self.workspace, run_dir)
        return json.loads(output.getvalue())

    def test_ready_display_survives_the_real_human_authority_envelope(self):
        run = self.make_run("envelope-ready")
        self.exit_ready(run)
        before = (run / "state.json").read_bytes()
        payload = self.render_envelope(run)
        self.assertEqual("PAUSED_CONTROLLER_EXIT", payload["status"])
        self.assertEqual(payload["status"], payload["view"]["status"])
        self.assertEqual("RUNNING", payload["saved_status"])
        self.assertFalse(payload["human_request_authorized"])
        self.assertEqual(before, (run / "state.json").read_bytes())

    def test_other_human_authority_status_projections_are_preserved(self):
        import autocode_util as util

        run = self.make_run("envelope-unissued")
        saved = util.read(run / "state.json")
        saved["status"] = "WAITING_FOR_USER"
        util.atomic_json(run / "state.json", saved)
        payload = self.render_envelope(run)
        self.assertEqual("RESOLVER_PENDING", payload["status"])
        self.assertEqual("WAITING_FOR_USER", payload["saved_status"])
        self.assertFalse(payload["human_request_authorized"])

    def test_ac3_runs_and_status_agree_on_the_projected_pause(self):
        import autocode_process as processes
        import autocode_run_view as run_view
        import autocode_runs_command as runs
        import autocode_status_summary as status_summary
        import autocode_util as util

        dead = self.make_run("dead")
        self.exit_gone(dead)
        ready = self.make_run("ready")
        self.exit_ready(ready)

        dead_view = self.render_view(dead)
        self.assertEqual("PAUSED_CONTROLLER_EXIT", dead_view["status"])
        self.assertEqual("resume", dead_view["needs"]["kind"])
        self.assertIn("--resume-paused", dead_view["next_command"])
        with patch.object(processes, "process_table", return_value={}):
            dead_row = next(item for item in runs.rows(self.workspace) if item["name"] == "dead")
        self.assertEqual(dead_view["status"], dead_row["status"])
        self.assertIn("--resume-paused", dead_row["next_command"])

        ready_saved = util.read(ready / "state.json")
        ready_view = self.render_view(ready)
        self.assertEqual("PAUSED_CONTROLLER_EXIT", ready_view["status"])
        self.assertEqual("continue", ready_view["needs"]["kind"])
        self.assertEqual("ready", ready_view["controller"]["kind"])
        self.assertEqual(
            run_view.action(ready_saved, run_dir=ready, workspace=ready.parent)["next_command"],
            ready_view["next_command"],
        )
        self.assertEqual(
            "Needs you: Saved and ready to continue; nothing runs it until you do.",
            status_summary.text(ready_view["summary"]).splitlines()[0],
        )
        ready_row = next(item for item in runs.rows(self.workspace) if item["name"] == "ready")
        self.assertEqual(ready_view["status"], ready_row["status"])

    def test_ac4_inventory_and_status_write_nothing(self):
        import contextlib

        import autocode_runs_command as runs

        dead = self.make_run("dead")
        self.exit_gone(dead)
        ready = self.make_run("ready")
        self.exit_ready(ready)

        def snapshot():
            return {
                str(path): path.read_bytes()
                for run in (dead, ready)
                for path in sorted(run.rglob("*"))
                if path.is_file()
            }

        before = snapshot()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = runs.cli(["--workspace", str(self.workspace), "--json"])
            self.render_view(dead)
            self.render_view(ready)
        self.assertEqual(0, code)
        self.assertEqual(before, snapshot(), "the inventory and --status are display-only")

    def test_ac9_action_payloads_render_the_projected_ready_status(self):
        import autocode_run_view as run_view
        import autocode_util as util

        ready = self.make_run("ready")
        self.exit_ready(ready)
        saved = util.read(ready / "state.json")
        view = self.render_view(ready)
        self.assertEqual("PAUSED_CONTROLLER_EXIT", view["status"])
        self.assertEqual("continue", view["needs"]["kind"])
        self.assertEqual("ready", view["controller"]["kind"])
        self.assertEqual(0, view["controller"]["receipt"]["exit_code"])
        self.assertEqual(
            run_view.action(saved, run_dir=ready, workspace=ready.parent)["next_command"],
            view["next_command"],
        )


if __name__ == "__main__":
    unittest.main()
