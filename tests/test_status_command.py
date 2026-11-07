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
            path.write_text(json.dumps(state_at("terra")))
            before = json.loads(path.read_text())
            calls = []

            def inspect(state, run_dir, workspace, **_):
                calls.append(state["next_stage"])
                if len(calls) == 1:  # the controller finishes a stage and exits during the first inspection
                    path.write_text(json.dumps(state_at("sol")))
                return {"kind": "boundary", "reason": "fixture", "receipt": None, "checked": True, "alive": False}

            view = self.render(before, run, inspect)
        self.assertEqual(["terra", "sol"], calls)
        self.assertEqual("sol", view["next_stage"])
        self.assertEqual(util.digest(state_at("sol")), view["observation"]["checkpoint_token"])
        self.assertEqual("continue", view["needs"]["kind"])

    def test_an_unchanged_checkpoint_is_inspected_once(self):
        import autocode_util as util

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve() / "run"
            run.mkdir()
            (run / "state.json").write_text(json.dumps(state_at("terra")))
            calls = []

            def inspect(state, run_dir, workspace, **_):
                calls.append(state["next_stage"])
                return {"kind": "boundary", "reason": "fixture", "receipt": None, "checked": True, "alive": False}

            view = self.render(state_at("terra"), run, inspect)
        self.assertEqual(["terra"], calls)
        self.assertEqual(util.digest(state_at("terra")), view["observation"]["checkpoint_token"])

    def test_a_requirements_question_names_its_origin_instead_of_the_queued_planner(self):
        question = {"id": "Q1", "question": "Which history entries are affected?"}
        for origin, pending, status, expected in (
            ("requirements_gather", "pending", "WAITING_FOR_USER", "Requirements"),
            ("astra_discovery", "pending", "WAITING_FOR_USER", "Planner"),
            ("requirements_gather", "consumed", "WAITING_FOR_USER", "Planner"),
            ("requirements_gather", "pending", "PAUSED_INVALID_OUTPUT", "Planner"),
        ):
            with self.subTest(origin=origin, pending=pending, status=status), tempfile.TemporaryDirectory() as tmp:
                run = Path(tmp).resolve() / "run"
                run.mkdir()
                saved = {
                    **state_at("astra_discovery"),
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
                self.assertEqual("astra_discovery", shown["next_stage"])
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


if __name__ == "__main__":
    unittest.main()
