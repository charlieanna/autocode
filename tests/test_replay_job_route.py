"""A stopped workflow job's recorded route and retry replay through public actions."""

import copy
import dataclasses
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_replay_cli as replay_cli
import autocode_replay_job_action as replay_job_action
import autocode_replay_recording as recording
from autocode_taskrun import TaskRun

from tests import test_job_route


class ReplayStoppedJobTests(unittest.TestCase):
    def test_replays_the_recorded_stopped_job_route_and_retry(self):
        test_job_route.JobModelRouteTests.setUpClass()
        self.addCleanup(test_job_route.JobModelRouteTests.tearDownClass)
        original = test_job_route.JobModelRouteTests("runTest")
        original.setUp()
        self.addCleanup(original.doCleanups)
        with tempfile.TemporaryDirectory(prefix="replay-job-route-") as directory:
            root = Path(directory).resolve()
            task = original.refused("quota")
            original.stopped(task)
            assigned = task.assign_model("architect", "gpt-6-luna@medium")
            final = task.retry_job(assigned["needs"]["job_retry_token"])
            self.assertEqual("PAUSED_DESIGN_CONFLICT", final["status"])
            projection = task.status(inspect_evidence=True)["replay_source"]
            tape = recording.load(recording.export(projection, root / "recording"))
            self.assertEqual(["route_job", "retry_job"], [action["kind"] for action in tape.document["actions"]])
            for key, value in (("role", "foreign"), ("model", ""), ("effort", "invented")):
                with self.subTest(malformed=key):
                    damaged = copy.deepcopy(tape.document)
                    damaged["actions"][0]["payload"][key] = value
                    with self.assertRaises(recording.RecordingError):
                        recording._validate(damaged)
            workspace = root / "replay"
            recording.prepare_workspace(tape, workspace)
            tape = recording.copy_into(tape, workspace / ".autocode/replay/recording")
            replay = TaskRun.start(
                workspace,
                tape.document["launch"]["task"],
                command=task.command,
                options=(
                    "--replay-recording",
                    str(tape.manifest_path),
                    "--replay-checks",
                    "fresh",
                    "--evidence-provenance",
                    "fake",
                ),
                env=task.env,
                timeout=90,
            )
            checkpoint = replay.run_dir / "state.json"
            state = json.loads(checkpoint.read_text())
            state["settings"]["roles"]["architect"]["reasoning_effort"] = "high"
            state["job_failure"]["route"].pop("candidates", None)
            checkpoint.write_text(json.dumps(state))
            _, _, offered = replay_job_action.next_action(state, workspace)
            self.assertEqual("route_job", offered["kind"])
            for actions in ([], tape.document["actions"][:1], tape.document["actions"][1:]):
                with self.subTest(incomplete_actions=[action["kind"] for action in actions]):
                    incomplete = copy.deepcopy(tape.document)
                    incomplete["actions"] = actions
                    with mock.patch.object(
                        replay_job_action.transport,
                        "selected",
                        return_value=dataclasses.replace(tape, document=incomplete),
                    ):
                        with self.assertRaises(recording.RecordingError):
                            replay_job_action.next_action(state, workspace)
            legacy = copy.deepcopy(tape.document)
            legacy["actions"][0]["payload"].update(effort=None, effort_known=False)
            # A synthetic legacy projection checks the decision only; it is not a captured tape.
            with (
                mock.patch.object(
                    replay_job_action.transport, "selected", return_value=dataclasses.replace(tape, document=legacy)
                ),
                mock.patch.object(replay_job_action.recording, "verify_source"),
            ):
                with self.assertRaisesRegex(recording.RecordingError, "does not preserve the original effort"):
                    replay_job_action.answer(state, state, workspace, "architect", "gpt-6-luna", None)
            # An intermediate model is not the final dispatch; this unsupported
            # synthetic chain must hold rather than applying an inferred route.
            multi = copy.deepcopy(tape.document)
            intermediate = copy.deepcopy(multi["actions"][0])
            intermediate["payload"]["model"] = "gpt-6-astra"
            multi["actions"][0]["match"]["route"]["current_model"] = "gpt-6-astra"
            multi["actions"].insert(0, intermediate)
            candidate = copy.deepcopy(state)
            candidate["settings"]["roles"]["architect"].update(model="gpt-6-astra", reasoning_effort="medium")
            with (
                mock.patch.object(
                    replay_job_action.transport, "selected", return_value=dataclasses.replace(tape, document=multi)
                ),
                mock.patch.object(replay_job_action.recording, "verify_source"),
            ):
                with self.assertRaisesRegex(recording.RecordingError, "model differs"):
                    replay_job_action.answer(state, candidate, workspace, "architect", "gpt-6-astra", "medium")
            for key in ("route_role", "engine", "current_model", "cause", "stopped_model"):
                with self.subTest(saved_route=key):
                    changed_state = copy.deepcopy(state)
                    changed_state["job_failure"]["route"][key] = "foreign"
                    with self.assertRaises(recording.RecordingError):
                        replay_job_action.next_action(changed_state, workspace)
            next_dispatch = tape.document["dispatches"][replay_job_action.transport.consumed(state)]
            tracked = tape.document["snapshots"][next_dispatch["before"]]["files"]
            source = next(
                workspace / name
                for name in tracked
                if (workspace / name).is_file() and not (workspace / name).is_symlink()
            )
            source_bytes = source.read_bytes()
            try:
                source.write_bytes(source_bytes + b"\n# changed after the recorded stop\n")
                with self.assertRaises(recording.RecordingError):
                    replay_job_action.next_action(state, workspace)
                held = replay.status()
                self.assertEqual("replay_stopped", held["needs"]["kind"])
                self.assertIsNone(held["next_command"])
            finally:
                source.write_bytes(source_bytes)
            # Fixture-only damaged checkpoint: a missing source pin cannot offer
            # a retry or ask for a new model either.
            missing_source = copy.deepcopy(state)
            missing_source["job_failure"]["source_identity"] = None
            checkpoint.write_text(json.dumps(missing_source))
            try:
                held = replay.status()
                self.assertEqual("continue", held["needs"]["kind"])
                self.assertNotIn("--answer", held["next_command"])
                self.assertNotIn("--retry-failed-stage", held["next_command"])
            finally:
                checkpoint.write_text(json.dumps(state))
            changed_state = copy.deepcopy(state)
            changed_state["job_failure"]["stage"] = "foreign"
            with self.assertRaises(recording.RecordingError):
                replay_job_action.next_action(changed_state, workspace)
            changed_state = copy.deepcopy(state)
            changed_state["user_events"].append(
                {"kind": "route_assignment", "attempt_id": state["job_failure"]["attempt_id"]}
            )
            with self.assertRaises(recording.RecordingError):
                replay_job_action.next_action(changed_state, workspace)
            after_route = copy.deepcopy(state)
            route_match, route_payload = offered["match"]["route"], offered["payload"]
            after_route["user_events"].append(
                {
                    "kind": "route_assignment",
                    "attempt_id": state["job_failure"]["attempt_id"],
                    "role": route_payload["role"],
                    "from": route_match["current_model"],
                    "to": route_payload["model"],
                    "requested_effort": route_payload["effort"],
                    "actor": "user_cli",
                    "via": "answer",
                    "stage": offered["match"]["stage"],
                    "engine": route_match["engine"],
                    "pause_status": state["job_failure"]["pause_status"],
                }
            )
            after_route["job_failure"]["route"]["current_model"] = route_payload["model"]
            _, _, retry = replay_job_action.next_action(after_route, workspace)
            self.assertEqual("retry_job", retry["kind"])
            for key in ("route_role", "engine", "current_model", "cause", "stopped_model"):
                with self.subTest(retry_route=key):
                    changed_state = copy.deepcopy(after_route)
                    changed_state["job_failure"]["route"][key] = "foreign"
                    with self.assertRaises(recording.RecordingError):
                        replay_job_action.next_action(changed_state, workspace)
            before = checkpoint.read_bytes()
            current = replay.status()
            self.assertEqual("continue", current["needs"]["kind"], current["needs"])
            self.assertTrue(current["needs"]["replay_job"])
            self.assertIn("--answer route-architect=gpt-6-luna@medium", current["next_command"])
            wrong = subprocess.run(
                [
                    *replay.command,
                    "--workspace",
                    str(workspace),
                    "--run-dir",
                    str(replay.run_dir),
                    "--answer",
                    "route-architect=gpt-6-astra@medium",
                    "--job-retry-token",
                    current["needs"]["job_retry_token"],
                ],
                env={**os.environ, **(replay.env or {})},
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(2, wrong.returncode, wrong.stdout + wrong.stderr)
            self.assertEqual(before, checkpoint.read_bytes(), "an unrecorded answer changes no state")
            route_action = tape.document["actions"][0]
            recording.match_action(tape, route_action, current)
            for key in ("role", "engine", "current_model", "cause", "stopped_model"):
                with self.subTest(key=key):
                    changed = copy.deepcopy(current)
                    changed["needs"]["route"][key] = "foreign"
                    with self.assertRaises(recording.RecordingError):
                        recording.match_action(tape, route_action, changed)
            changed = copy.deepcopy(current)
            changed["needs"]["stage"] = "foreign"
            with self.assertRaises(recording.RecordingError):
                recording.match_action(tape, route_action, changed)
            result = replay_cli.drive(tape, replay)
            self.assertEqual("PAUSED_DESIGN_CONFLICT", result["status"])
            self.assertEqual("end", result["replay_progress"]["next_dispatch"])
