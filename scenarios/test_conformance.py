"""The headless conformance client (#1012): its decisions, and the lifecycle scenario end to end.

The decision tests build status views by hand and launch nothing. The end-to-end test drives
conformance-lifecycle through AutoCode's real CLI with the scripted provider.
"""

from __future__ import annotations

import argparse
import copy
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

    def time_offer(self):
        public = view(
            "resume",
            "AUTOCODE_EXPECTED_RECOVERY_TOKEN=current autocode --resume-paused --max-seconds N --expected-recovery-token -",
        )
        public["status"] = "PAUSED_TIME_LIMIT"
        public["recovery"] = {"token": "current"}
        public["needs"]["command_inputs"] = [
            {
                "id": "max_seconds",
                "kind": "integer",
                "flag": "--max-seconds",
                "placeholder": "N",
                "arg_index": 2,
                "label": "Total active time (seconds)",
                "current": 2700,
                "elapsed": 3010.885,
                "minimum": 3011,
                "maximum": 9007199254740991,
            }
        ]
        return public

    def test_finite_total_requires_persons_number_and_preserves_exact_bound_command(self):
        public = self.time_offer()
        words, env, _ = self.client.named_command(public)
        with self.assertRaisesRegex(ConformanceGap, "explicit whole number"):
            self.client.decide(public["needs"], words, view=public, env=env)
        result = self.client.decide(
            public["needs"], words, command_inputs={"max_seconds": "5400"}, view=public, env=env
        )
        self.assertEqual(["--resume-paused", "--max-seconds", "5400", "--expected-recovery-token", "-"], result)
        self.assertEqual({"AUTOCODE_EXPECTED_RECOVERY_TOKEN": "current"}, env)
        for invalid in (
            "",
            " ",
            "0",
            "3010",
            "-1",
            "1.5",
            "5e3",
            "Infinity",
            "5400 --approve-goal -",
            "9007199254740992",
            "9" * 5000,
        ):
            with self.subTest(invalid=invalid[:30]), self.assertRaises(ConformanceGap):
                self.client.decide(
                    public["needs"], words, command_inputs={"max_seconds": invalid}, view=public, env=env
                )

    def test_finite_review_allowance_preserves_resume_and_save_only_commands(self):
        for save_only in (False, True):
            public = view(
                "planning_budget",
                "AUTOCODE_EXPECTED_RECOVERY_TOKEN=current autocode "
                + ("" if save_only else "--resume-paused ")
                + "--planning-review-call-limit N --expected-recovery-token -",
            )
            public.update(status="PAUSED_PLANNING_BUDGET", recovery={"token": "current"})
            public["needs"]["command_inputs"] = [
                {
                    "id": "planning_review_call_limit",
                    "kind": "integer",
                    "flag": "--planning-review-call-limit",
                    "placeholder": "N",
                    "arg_index": 1 if save_only else 2,
                    "label": "Total plan-review calls",
                    "current": 2,
                    "used": 2,
                    "minimum": 3,
                    "maximum": 9007199254740991,
                }
            ]
            words, env, _ = self.client.named_command(public)
            for invalid in (None, "", "0", "2", "1.5", "4e2", "9" * 5000):
                with self.subTest(save_only=save_only, invalid=str(invalid)[:20]), self.assertRaises(ConformanceGap):
                    self.client.decide(
                        public["needs"],
                        words,
                        command_inputs={"planning_review_call_limit": invalid},
                        view=public,
                        env=env,
                    )
            chosen = self.client.decide(
                public["needs"], words, command_inputs={"planning_review_call_limit": "4"}, view=public, env=env
            )
            self.assertEqual(
                ([] if save_only else ["--resume-paused"])
                + ["--planning-review-call-limit", "4", "--expected-recovery-token", "-"],
                chosen,
            )
            for key, value in (
                ("used", True),
                ("used", 10**1000),
                ("current", 2.5),
                ("minimum", 2),
                ("arg_index", 2 if save_only else 1),
                ("flag", "--max-seconds"),
            ):
                bad = copy.deepcopy(public)
                bad["needs"]["command_inputs"][0][key] = value
                with self.subTest(key=key), self.assertRaises(ConformanceGap):
                    self.client.decide(
                        bad["needs"], words, command_inputs={"planning_review_call_limit": "4"}, view=bad, env=env
                    )
            with self.assertRaises(ConformanceGap):
                self.client.decide(
                    public["needs"], words, command_inputs={"planning_review_call_limit": "4"}, view=public, env={}
                )

    def test_finite_recovery_grant_binds_only_explicit_replenishment(self):
        public = view(
            "resume",
            "AUTOCODE_EXPECTED_RECOVERY_TOKEN=current autocode --resume-paused --grant-recovery N --expected-recovery-token -",
        )
        public.update(status="PAUSED_TIMEOUT_RECOVERY", recovery={"token": "current"})
        public["needs"]["command_inputs"] = [
            {
                "id": "grant_recovery",
                "kind": "integer",
                "flag": "--grant-recovery",
                "placeholder": "N",
                "arg_index": 2,
                "label": "Additional recoveries",
                "current": 3,
                "used": 3,
                "minimum": 1,
                "maximum": 3,
            }
        ]
        words, env, _ = self.client.named_command(public)
        for invalid in (None, "", "0", "4", "1.5", "1e2", "9" * 5000):
            with self.subTest(invalid=str(invalid)[:20]), self.assertRaises(ConformanceGap):
                self.client.decide(
                    public["needs"], words, command_inputs={"grant_recovery": invalid}, view=public, env=env
                )
        result = self.client.decide(
            public["needs"], words, command_inputs={"grant_recovery": "1"}, view=public, env=env
        )
        self.assertEqual(["--resume-paused", "--grant-recovery", "1", "--expected-recovery-token", "-"], result)
        for key, value in (("used", True), ("used", 10**1000), ("maximum", 4), ("minimum", 0), ("current", 3.5)):
            bad = copy.deepcopy(public)
            bad["needs"]["command_inputs"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ConformanceGap):
                self.client.decide(bad["needs"], words, command_inputs={"grant_recovery": "1"}, view=bad, env=env)

    def test_typed_total_rejects_changed_metadata_token_and_other_commands(self):
        public = self.time_offer()
        words, env, _ = self.client.named_command(public)
        for key, value in (
            ("arg_index", 1),
            ("flag", "--max-cost-usd"),
            ("minimum", 1),
            ("maximum", 0),
            ("elapsed", float("nan")),
            ("elapsed", 10**1000),
            ("current", True),
            ("current", 2700.5),
        ):
            bad = copy.deepcopy(public)
            bad["needs"]["command_inputs"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ConformanceGap):
                self.client.decide(bad["needs"], words, command_inputs={"max_seconds": "5400"}, view=bad, env=env)
        for changed_words, changed_env in (
            (words + ["--approve-goal", "-"], env),
            (words, {"AUTOCODE_EXPECTED_RECOVERY_TOKEN": "old"}),
            (words, {}),
        ):
            with self.assertRaises(ConformanceGap):
                self.client.decide(
                    public["needs"], changed_words, command_inputs={"max_seconds": "5400"}, view=public, env=changed_env
                )
        with self.assertRaisesRegex(ConformanceGap, "leaves 'N' open"):
            self.client.decide({"kind": "resume"}, words, command_inputs={"max_seconds": "5400"})

    def test_the_named_command_carries_its_token_and_this_run(self):
        words, env, source = self.named(
            f"AUTOCODE_APPROVE_GOAL_TOKEN=r2:abc autocode --run-dir {self.client.run_dir} "
            f"--workspace {self.client.project} --approve-goal -"
        )
        self.assertEqual((["--approve-goal", "-"], {"AUTOCODE_APPROVE_GOAL_TOKEN": "r2:abc"}), (words, env))
        self.assertEqual("next_command", source)

    def test_operational_message_fills_only_bound_person_reply(self):
        need = {
            "kind": "answer",
            "resolver_scope": "blocker",
            "resolver_request_id": "req-1",
            "resolver_token": "current-token",
        }
        command = "AUTOCODE_RESOLVER_TOKEN=current-token autocode --resolver-request req-1 --resolver-token - --resolver-response provide_information --resolver-message MESSAGE"
        public = view(**need)
        public["chat"] = {"input": {"kind": "resolver_response", "next_command": command}}
        args, env, source = self.client.message_command(public, "I corrected the fixture")
        self.assertEqual(
            [
                "--resolver-request",
                "req-1",
                "--resolver-token",
                "-",
                "--resolver-response",
                "provide_information",
                "--resolver-message",
                "I corrected the fixture",
            ],
            args,
        )
        self.assertEqual({"AUTOCODE_RESOLVER_TOKEN": "current-token"}, env)
        self.assertEqual("chat.input.next_command", source)
        for bad in (
            command.replace("req-1", "req-old"),
            command.replace("current-token", "old-token"),
            command + " --resume-paused",
            command.replace("provide_information", "retry"),
        ):
            public["chat"]["input"]["next_command"] = bad
            with self.assertRaises(ConformanceGap):
                self.client.message_command(public, "reply")

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

    def test_each_approval_it_takes_is_a_gate_passed_for_the_person(self):
        """Recorded as the ordinary driver records its own (result.json "approvals"), so the approval
        provenance check (#722) matches each one to a row of the final view."""
        review = ["--approve-review", "C1", "--approve-review", "C2", "--review-token", "-"]
        with mock.patch.object(self.client, "call") as call:
            self.client.take(view(token="r2:abc"), ["--approve-goal", "-"], {}, "next_command")
            self.client.take(view("review", token="rt", criteria=["C1", "C2"]), review, {}, "next_command")
            self.client.take(view("answer"), ["--answer", "Q1=Use UTC"], {}, "next_command")
        self.assertEqual(3, call.call_count)
        self.assertEqual(
            [
                {"gate": "approve_plan", "token": "r2:abc"},
                {"gate": "review", "criterion": "C1", "token": "rt"},
                {"gate": "review", "criterion": "C2", "token": "rt"},
            ],
            self.client.approvals,
        )

    def test_a_catalog_run_exports_each_plan_before_the_client_approves_it(self):
        """#720: as Driver.serve does, the plan is exported and read back first (harness.plan_round_trip), and
        what happened is in that approval's row. Only `autocode` is stood in for: the client's own ``call``
        (which also runs `autocode intervention submit`) carries the plan commands to the CLI."""
        self.client.round_trip_plans = True
        self.client.run_dir.mkdir(parents=True)
        (self.client.run_dir / "state.json").write_text("{}")
        goal, commands = "f" * 64, []

        def cli(command, **_):
            args = list(command[1:])
            commands.append(args)
            if args[:2] == ["plan", "--export"]:
                Path(args[2]).write_text("---\n")
                payload = {"revision": 2, "goal_hash": goal, "written_goal_hash": goal, "masked": False}
            elif args[:2] == ["plan", "--check"]:
                payload = {"goal_hash": goal, "exported_hash": goal, "revision": 2, "matches": True}
            else:
                payload = {}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        with mock.patch("harness.driver.run_cli", cli):
            self.client.take(view(token="r2:abc"), ["--approve-goal", "-"], {}, "next_command")
            self.client.take(view("answer"), ["--answer", "Q1=Use UTC"], {}, "next_command")
        path = self.client.root / "plan-files" / "01.md"
        where = ["--workspace", str(self.client.project), "--run-dir", str(self.client.run_dir)]
        self.assertEqual(
            [
                ["plan", "--export", str(path), *where, "--json"],
                ["plan", "--check", str(path), "--json"],
                [*where, "--approve-goal", "-"],
                [*where, "--answer", "Q1=Use UTC"],
            ],
            commands,
        )
        (gate,) = self.client.approvals
        self.assertEqual(("approve_plan", "r2:abc"), (gate["gate"], gate["token"]))
        self.assertEqual(
            (str(path), 0, goal, 0, goal, []),
            (
                gate["plan_file"]["file"],
                gate["plan_file"]["export"]["exit"],
                gate["plan_file"]["export"]["goal_hash"],
                gate["plan_file"]["check"]["exit"],
                gate["plan_file"]["check"]["goal_hash"],
                gate["plan_file"]["run_changed"],
            ),
        )
        # The export and the check are no steps; the two decisions are.
        self.assertEqual(["approve_plan", "answer"], [step["kind"] for step in self.client.steps])

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

    def test_a_named_recovery_after_the_persons_interrupt_keeps_the_pause_theirs(self):
        # The core names setting aside the interrupted attempt as a continue; the abandoned stop
        # that follows is still the pause the person caused, so the client resumes it.
        run_flags = f"autocode --run-dir {self.client.run_dir} --workspace {self.client.project}"
        recovery = view("continue", f"{run_flags} --abandon-stage 002/builder-01", abandon_stage="002/builder-01")
        abandoned = view("resume", f"{run_flags} --resume-paused")
        relaunch = view("continue")
        done = {**view("continue"), "done": True}
        taken = []
        self.client.person_paused = True
        with (
            mock.patch.object(self.client, "view", side_effect=[recovery, abandoned, relaunch, done]),
            mock.patch.object(self.client, "take", side_effect=lambda _view, words, _env, _source: taken.append(words)),
        ):
            self.client.until_stopped()
        self.assertEqual([["--abandon-stage", "002/builder-01"], ["--resume-paused"], ["--no-chat"]], taken)
        self.assertFalse(self.client.person_paused, "a plain relaunch ends the person's pause")

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
        # #722: each plan approval the client took is listed in the final view with the person's provenance.
        taken = [row for row in journal if row["did"] == "took" and "--approve-goal" in row["what"].split()]
        self.assertEqual(len(taken), sum(gate["gate"] == "approve_plan" for gate in result["approvals"]))
        self.assertGreaterEqual(len(taken), 2)
        (provenance,) = [check for check in result["checks"] if check["name"] == "approval provenance: task"]
        self.assertTrue(provenance["ok"], provenance["detail"])
        # #720: the client exported each of those plans and read the file back before approving it.
        (trip,) = [check for check in result["checks"] if check["name"] == "plan round trip"]
        self.assertTrue(trip["ok"], trip["detail"])
        self.assertIn(f"{len(taken)} plan(s) exported at the approval stop", trip["detail"])


if __name__ == "__main__":
    unittest.main()
