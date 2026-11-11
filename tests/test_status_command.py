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


if __name__ == "__main__":
    unittest.main()
