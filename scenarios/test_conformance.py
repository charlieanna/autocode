"""The headless conformance client (#1012): its decisions, and the lifecycle scenario end to end.

The decision tests build status views by hand and launch nothing. The end-to-end test drives
conformance-lifecycle through AutoCode's real CLI with the scripted provider.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402
from harness import catalog, conformance, verdict  # noqa: E402
from harness.conformance import ConformanceDriver, ConformanceGap, Moment  # noqa: E402


def view(kind="approve_plan", next_command=None, **need):
    return {"status": "WAITING", "needs": {"kind": kind, **need}, "next_command": next_command, "done": False}


class ClientDecisionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.client = ConformanceDriver(
            root / "project",
            root,
            [],
            {},
            autocode=["autocode"],
            max_steps=5,
            timeout_seconds=5,
            explicit_answers={"route-sol": "gpt-6-luna"},
            conformance={},
            scripted=True,
        )
        self.client.run_dir = root / "project/.autocode/runs/r1"

    def named(self, text, kind="approve_plan"):
        return self.client.named_command(view(kind, text))

    def test_the_named_command_carries_its_token_and_this_run(self):
        words, env, source = self.named(
            f"AUTOCODE_APPROVE_GOAL_TOKEN=r2:abc autocode --run-dir {self.client.run_dir} "
            f"--workspace {self.client.project} --approve-goal -"
        )
        self.assertEqual((["--approve-goal", "-"], {"AUTOCODE_APPROVE_GOAL_TOKEN": "r2:abc"}), (words, env))
        self.assertEqual("next_command", source)

    def test_a_command_for_another_run_is_a_gap(self):
        with self.assertRaisesRegex(ConformanceGap, "not this run"):
            self.named("autocode --run-dir /elsewhere/run --approve-goal -")

    def test_only_continue_may_go_without_a_named_command(self):
        self.assertEqual((["--no-chat"], {}, "needs.kind"), self.named(None, "continue"))
        with self.assertRaisesRegex(ConformanceGap, "names no next_command"):
            self.named(None, "resume")

    def test_a_person_fills_only_the_decisions_the_view_leaves_open(self):
        need = {"kind": "answer", "questions": [{"id": "Q1", "proposed_default": "Use UTC"}]}
        self.assertEqual(["--answer", "Q1=Use UTC"], self.client.decide(need, ["--answer", "Q1=ANSWER"]))
        self.assertEqual(
            ["--answer", "route-sol=gpt-6-luna"], self.client.decide(need, ["--answer", "route-sol=MODEL"])
        )
        # Who made each decision is kept for the campaign's counts (#455): the scenario's own answer is the
        # person's, a proposed default is the harness's.
        self.assertEqual(
            [("Q1", "proposed_default", False), ("route-sol", "person", True)],
            [(row["id"], row["source"], row["explicit"]) for row in self.client.answers],
        )
        for word in ("GOAL_FILE", "WHAT CHANGED", "--explain"):
            with self.subTest(word=word), self.assertRaises(ConformanceGap):
                self.client.decide(need, ["--edit-goal", word])
        with self.assertRaisesRegex(ConformanceGap, "name a model"):
            self.client.decide(need, ["--answer", "route-terra=MODEL"])

    def test_an_operational_request_continues_only_through_a_named_retry(self):
        operational = view(
            "answer", "autocode --resolver-message 'WHAT CHANGED'", resolver_scope="operational_exhaustion"
        )
        self.assertIsNone(self.client.person_command(operational))
        operational["needs"]["retry_action"] = "--resume-paused --retry-failed-stage"
        self.assertEqual(
            (["--resume-paused", "--retry-failed-stage"], {}, "needs.retry_action"),
            self.client.person_command(operational),
        )

    def test_a_moment_waits_for_the_stage_the_view_shows_working(self):
        moment = Moment("interrupt", "terra", "Builder", after_failed=True)
        working = {"progress": {"stage": "Builder", "headline": "Builder working", "requirements": {"failed": 0}}}
        self.assertFalse(moment.ready(working))
        working["progress"]["requirements"]["failed"] = 1
        self.assertTrue(moment.ready(working))
        working["progress"]["headline"] = "Next: Builder"
        self.assertFalse(moment.ready(working))

    def boundary_fixture(self):
        self.client.pending = [Moment("interrupt", "terra", "Builder", after_failed=True)]
        self.client.journal = []
        self.client.person_paused = False
        alive = {"value": True}
        worker = mock.Mock()
        worker.is_alive.side_effect = lambda: alive["value"]
        barrier = mock.Mock(environment={"LIFECYCLE_HOLD_STAGE": "terra"})
        barrier.wait.return_value = {"stage": "terra", "pid": 202}
        process = mock.Mock()
        process.send_signal.side_effect = lambda _signal: alive.update(value=False)
        return worker, barrier, process

    @staticmethod
    def working_view(*, failed=1, controller=None, headline="Builder working"):
        return {
            "progress": {"stage": "Builder", "headline": headline, "requirements": {"failed": failed}},
            "controller": controller
            if controller is not None
            else {"kind": "running", "receipt": {"identity": {"pid": 101, "birth_identity": 20.0}}},
        }

    def test_one_held_announcement_waits_for_publication_and_a_running_controller(self):
        worker, barrier, process = self.boundary_fixture()
        snapshots = [
            self.working_view(headline="Next: Builder"),
            self.working_view(controller={"kind": "unknown", "reason": "Receipt is being inspected"}),
            self.working_view(),
        ]

        def status():
            barrier.wait.assert_called_once_with(timeout=0.5)
            barrier.release.assert_not_called()
            process.send_signal.assert_not_called()
            self.assertEqual(1, len(self.client.pending))
            self.assertEqual([], self.client.journal)
            return snapshots.pop(0)

        with (
            mock.patch.object(conformance, "ProviderBarrier", return_value=barrier),
            mock.patch.object(conformance.threading, "Thread", return_value=worker),
            mock.patch.object(self.client, "view", side_effect=status),
            mock.patch("psutil.Process", return_value=process),
            mock.patch("harness.adversarial.birth_identity", return_value=20.0),
        ):
            self.client.watched("continue", [])
        self.assertEqual([], snapshots)
        barrier.release.assert_not_called()
        barrier.close.assert_called_once()
        process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual([], self.client.pending)
        self.assertEqual(["interrupted"], [row["did"] for row in self.client.journal])

    def test_first_builder_is_released_before_interrupting_the_failed_rework_builder(self):
        worker, barrier, process = self.boundary_fixture()
        snapshots = [self.working_view(failed=0), self.working_view()]

        def status():
            process.send_signal.assert_not_called()
            self.assertEqual(1, len(self.client.pending))
            if len(snapshots) == 1:
                barrier.release.assert_called_once()
                self.assertEqual(2, barrier.wait.call_count)
            return snapshots.pop(0)

        with (
            mock.patch.object(conformance, "ProviderBarrier", return_value=barrier),
            mock.patch.object(conformance.threading, "Thread", return_value=worker),
            mock.patch.object(self.client, "view", side_effect=status),
            mock.patch("psutil.Process", return_value=process),
            mock.patch("harness.adversarial.birth_identity", return_value=20.0),
        ):
            self.client.watched("continue", [])
        barrier.release.assert_called_once()
        process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(["interrupted"], [row["did"] for row in self.client.journal])

    def test_unknown_controller_exhausts_the_existing_driver_deadline_without_signalling(self):
        worker, barrier, process = self.boundary_fixture()
        self.client.deadline = 5
        snapshot = self.working_view(controller={"kind": "unknown", "reason": "Ownership inspection denied"})
        output = subprocess.CompletedProcess([], 0, json.dumps({"view": snapshot}), "")
        with (
            mock.patch.object(conformance, "ProviderBarrier", return_value=barrier),
            mock.patch.object(conformance.threading, "Thread", return_value=worker),
            mock.patch("harness.driver.time.monotonic", side_effect=[3, 3, 4, 4, 5]),
            mock.patch("harness.driver.run_cli", return_value=output) as status,
            mock.patch("harness.driver.attempts.observe"),
            mock.patch("psutil.Process", return_value=process),
            self.assertRaisesRegex(ConformanceGap, "time budget used up.*Ownership inspection denied"),
        ):
            self.client.watched("continue", [])
        self.assertEqual(2, status.call_count)
        barrier.wait.assert_called_once_with(timeout=0.5)
        barrier.release.assert_not_called()
        barrier.close.assert_called_once()
        process.send_signal.assert_not_called()
        self.assertEqual(1, len(self.client.pending))
        self.assertEqual([], self.client.journal)
        self.assertFalse(self.client.person_paused)

    def test_interrupt_refusals_never_consume_the_moment_or_record_a_signal(self):
        for controller, observed_birth, reason in (
            ({"kind": "busy", "reason": "Another controller holds the run lock"}, 20.0, "Another controller"),
            ({}, 20.0, "no usable running controller"),
            ({"kind": "running", "receipt": {"identity": {"pid": 101}}}, 20.0, "invalid controller birth"),
            (
                {"kind": "running", "receipt": {"identity": {"pid": 101, "birth_identity": 20.0}}},
                21.0,
                "birth identity does not match",
            ),
        ):
            with self.subTest(controller=controller):
                worker, barrier, process = self.boundary_fixture()
                with (
                    mock.patch.object(conformance, "ProviderBarrier", return_value=barrier),
                    mock.patch.object(conformance.threading, "Thread", return_value=worker),
                    mock.patch.object(self.client, "view", return_value=self.working_view(controller=controller)),
                    mock.patch("psutil.Process", return_value=process),
                    mock.patch("harness.adversarial.birth_identity", return_value=observed_birth),
                    self.assertRaisesRegex(ConformanceGap, reason),
                ):
                    self.client.watched("continue", [])
                process.send_signal.assert_not_called()
                barrier.release.assert_not_called()
                self.assertEqual(1, len(self.client.pending))
                self.assertEqual([], self.client.journal)
                self.assertFalse(self.client.person_paused)


class LifecycleScenarioTests(unittest.TestCase):
    def test_the_whole_lifecycle_completes_through_the_view_only(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                fake=True,
                profile=None,
                provider=None,
                hybrid=False,
                fake_solution="reference",
                fake_fault=None,
                out=Path(directory),
                autocode=None,
                max_steps=None,
                timeout_minutes=10,
                local_docker=False,
                i_authorize_live_model_spend=False,
            )
            result = run.run_one(catalog.load("conformance-lifecycle"), args)
            journal = [
                json.loads(line) for line in (Path(result["evidence"]) / "conformance.jsonl").read_text().splitlines()
            ]
        self.assertEqual(verdict.PASS, result["verdict"], result.get("summary"))
        self.assertEqual(
            ["interrupted", "feedback"], [row["did"] for row in journal if row["did"] in ("interrupted", "feedback")]
        )
        self.assertIn("needs.retry_action", [row["source"] for row in journal])


if __name__ == "__main__":
    unittest.main()
