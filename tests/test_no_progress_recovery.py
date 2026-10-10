"""Raising an exhausted no-progress bound through the CLI.

A run held at PAUSED_NO_PROGRESS publishes an AutoResolver request. These tests
drive the public commands that answer it: raising the bound on resume, saving it
first, and reasserting it after an informational response. A raised bound, or 0
(no cap), must reach the Builder without resetting the retained count or granting
more timeout recoveries; a bound that still does not admit the count must never
launch one, and a no-progress bound never acknowledges another cause's request.

The held run has the shape of the 2026-10-03 AWS design trial: three planning
timeout recoveries counted as no-progress batches after the plan was approved, and
AutoResolver already evaluating the blocker (docs/bugs/2026-10-05-no-progress-bound-reassertion.md).
Two tests instead reach the count with no automatic recovery recorded at all.
"""

import copy
import json
import unittest

import autocode_resolver_human as human
import autocode_support as support

from . import test_subprocess


class NoProgressBoundTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def stop_before_builder(self, *options):
        """Approve a plan and stop at the boundary before the Builder."""
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch(["Build greeting", "--chat", *options], 2, answers="CLI\nno\n")
        self.run, state = self.saved()
        self.launch(["--run-dir", str(self.run), "--approve-goal", state["displayed_goal"]], 0)
        self.launch(["--run-dir", str(self.run), "--no-chat", "--pause-after-stage"], 2)
        _, state = self.saved()
        self.assertEqual("terra", state["next_stage"])
        self.args = ["--run-dir", str(self.run), "--no-chat"]
        return state

    def hold_at(self, pause, state):
        """Resume the edited checkpoint into a published request for this pause."""
        support.atomic_json(self.run / "state.json", state)
        self.launch([*self.args, "--resume-paused"], 2)
        _, held = self.saved()
        # As in the trial, AutoResolver has already evaluated this blocker in the current epoch.
        held["resolver"]["attempts"] = {"retained": 2}
        support.atomic_json(self.run / "state.json", held)
        request = human.current(held)
        self.assertEqual("operational_exhaustion", request["scope"])
        self.assertEqual(pause, self.origin(held, request)["pause_status"])
        self.assertFalse((self.project / "greet.py").exists())
        return held, request

    def hold(self):
        """Approve a plan, stop before the Builder, then hit the no-progress bound of 3."""
        state = self.stop_before_builder()
        state.update(
            no_progress_batches=3,
            automatic_recoveries_since_resume=2,
            consecutive_timeout_recoveries=0,
            automatic_timeout_recoveries=[{"stage": "glm_revise", "attempt_id": str(i)} for i in range(3)],
        )
        return self.hold_at("PAUSED_NO_PROGRESS", state)

    @staticmethod
    def origin(state, request):
        return state["resolver"]["human_escalations"][request["request_id"]]["identity"]["proposal"]["origin"]

    def view(self):
        return json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["view"]

    def inform(self, request):
        self.launch(
            [
                *self.args,
                "--resolver-request",
                request["request_id"],
                "--resolver-token",
                request["request_token"],
                "--resolver-response",
                "provide_information",
                "--resolver-message",
                "The finite no-progress bound will be corrected",
            ],
            0,
        )
        return self.saved()[1]

    def assert_admitted_without_new_allowance(self, held, limit=4):
        """The Builder is next and runs; the count, approval and recovery history are those of the hold.

        `--unit autoplanner` stops at the Builder handoff whether or not the bound admits the
        count, so only the next launch shows that it does: the task completes under the held
        approval and recovery history, with no grant.
        """
        _, resumed = self.saved()
        view = self.view()
        self.assertEqual(("RUNNING", "terra"), (view["status"], view["next_stage"]))
        self.assertIsNone(human.current(resumed))
        self.assertEqual(3, resumed["no_progress_batches"])
        self.assertEqual(limit, resumed["settings"]["limits"]["no_progress_batches"])
        self.assertEqual(held["goal_contract"], resumed["goal_contract"])
        for key in ("automatic_timeout_recoveries", "automatic_recoveries_since_resume"):
            self.assertEqual(held.get(key), resumed.get(key), key)
        self.assertFalse(resumed.get("recovery_grants"))
        self.assertFalse((self.project / "greet.py").exists())
        self.launch(self.args, 0)
        _, finished = self.saved()
        self.assertEqual("TASK_COMPLETE", finished["status"])
        self.assertTrue((self.project / "greet.py").is_file())
        self.assertEqual(held["goal_contract"], finished["goal_contract"])
        self.assertEqual(held.get("automatic_timeout_recoveries"), finished.get("automatic_timeout_recoveries"))
        self.assertFalse(finished.get("recovery_grants"))
        return resumed

    def resume_with_bound_and_build(self, limit):
        held, request = self.hold()
        self.launch([*self.args, "--resume-paused", "--no-progress-limit", str(limit), "--unit", "autoplanner"], 0)
        resumed = self.assert_admitted_without_new_allowance(held, limit)
        self.assertEqual("superseded", resumed["resolver"]["human_escalations"][request["request_id"]]["status"])

    def hold_without_a_recovery(self, *options):
        """The unchanged-batch count at 3 with no automatic recovery recorded since the run began."""
        state = self.stop_before_builder(*options)
        self.assertNotIn("automatic_recoveries_since_resume", state)
        self.assertFalse(state.get("automatic_timeout_recoveries"))
        state["no_progress_batches"] = 3
        return state

    def test_raising_the_bound_on_resume_retires_the_request_and_builds(self):
        self.resume_with_bound_and_build(4)

    def test_disabling_the_bound_on_resume_retires_the_request_and_builds(self):
        """0 disables the cap, so the retained count of 3 no longer holds the Builder."""
        self.resume_with_bound_and_build(0)

    def test_a_saved_raise_is_acknowledged_by_a_plain_resume(self):
        held, request = self.hold()
        self.launch([*self.args, "--no-progress-limit", "4"], 2)
        self.assertFalse((self.project / "greet.py").exists())
        self.launch([*self.args, "--resume-paused", "--unit", "autoplanner"], 0)
        resumed = self.assert_admitted_without_new_allowance(held)
        self.assertEqual("superseded", resumed["resolver"]["human_escalations"][request["request_id"]]["status"])

    def test_after_an_informational_response_reasserting_the_saved_bound_admits(self):
        held, request = self.hold()
        answered = self.inform(request)
        self.assertEqual("PAUSED_NO_PROGRESS", answered["status"])
        # Information alone is not an allowance: AutoResolver re-evaluates it once (#486) and holds,
        # naming the bound; a further plain resume repeats that decision and writes nothing.
        result = self.launch([*self.args, "--resume-paused"], 2)
        self.assertIn("--resume-paused --no-progress-limit N", result.stdout)
        self.assertEqual("--resume-paused --no-progress-limit N", self.view()["needs"]["action"])
        before = (self.run / "state.json").read_bytes()
        self.launch([*self.args, "--resume-paused"], 2)
        self.assertEqual(before, (self.run / "state.json").read_bytes())
        self.launch([*self.args, "--no-progress-limit", "4"], 2)
        _, corrected = self.saved()
        self.assertEqual("PAUSED_NO_PROGRESS", corrected["status"])
        self.assertIsNone(human.current(corrected))
        self.launch([*self.args, "--resume-paused", "--no-progress-limit", "4", "--unit", "autoplanner"], 0)
        resumed = self.assert_admitted_without_new_allowance(held)
        self.assertEqual(answered["resolver"]["human_responses"], resumed["resolver"]["human_responses"])

    def reassert_after_a_plain_resume_asked_again(self, limit):
        """The trial's stranded run: information, a saved bound, then a plain resume asked again.

        A plain resume does not acknowledge the saved bound, so it republishes the
        no-progress request. Reasserting the bound used to crash with "Role result belongs
        to another implementation task" and leave the run waiting on that request.
        """
        held, request = self.hold()
        answered = self.inform(request)
        self.launch([*self.args, "--no-progress-limit", str(limit)], 2)
        self.launch([*self.args, "--resume-paused"], 2)
        _, republished = self.saved()
        again = human.current(republished)
        self.assertNotEqual(request["request_id"], again["request_id"])
        self.assertEqual("PAUSED_NO_PROGRESS", self.origin(republished, again)["pause_status"])
        # #448: the limit still caused this pause, so the republished advice names it.
        decision = republished["user_request"]["decision_needed"]
        self.assertIn("autocode resume --no-progress-limit N", decision)
        self.assertNotIn("--resolver-response", decision)
        self.assertFalse((self.project / "greet.py").exists())
        self.launch([*self.args, "--resume-paused", "--no-progress-limit", str(limit), "--unit", "autoplanner"], 0)
        resumed = self.assert_admitted_without_new_allowance(held, limit)
        self.assertEqual("superseded", resumed["resolver"]["human_escalations"][again["request_id"]]["status"])
        self.assertEqual(answered["resolver"]["human_responses"], resumed["resolver"]["human_responses"])

    def test_reasserting_the_bound_after_a_plain_resume_asked_again_admits(self):
        self.reassert_after_a_plain_resume_asked_again(4)

    def test_reasserting_a_disabled_bound_after_a_plain_resume_asked_again_admits(self):
        self.reassert_after_a_plain_resume_asked_again(0)

    def test_the_published_advice_is_the_command_that_admits_the_builder(self):
        """#448: the advice said to send information and then resume, and that resume holds.

        The request, its question in the status view and the stop reason name the bound
        instead; following them literally, in the documented `autocode resume` form, reaches
        the Builder.
        """
        held, request = self.hold()
        view = self.view()
        self.assertEqual("operational_exhaustion", view["needs"]["resolver_scope"])
        for text in (
            held["user_request"]["decision_needed"],
            view["needs"]["questions"][0]["question"],
            view["stop_reason"],
        ):
            self.assertIn("autocode resume --no-progress-limit N with N above the retained count of 3 ", text)
            self.assertNotIn("--resolver-response", text)
            self.assertNotIn("--grant-recovery", text)
            # The saved limit is the count, so reasserting it is refused; the advice does not offer it.
            self.assertNotIn("saved limit", text)
        self.assertIn(
            "Acknowledge the pause with autocode resume --no-progress-limit N", view["needs"]["questions"][0]["options"]
        )
        self.launch(["resume", *self.args, "--no-progress-limit", "4", "--unit", "autoplanner"], 0)
        resumed = self.assert_admitted_without_new_allowance(held)
        self.assertEqual("superseded", resumed["resolver"]["human_escalations"][request["request_id"]]["status"])

    def test_after_an_informational_response_the_view_names_the_command_that_admits(self):
        """#448: once a response consumed the request, its advice is gone and a plain resume holds.

        The status view's resume need carries the one command that continues and the retained
        count its N must exceed; running it reaches the Builder.
        """
        held, request = self.hold()
        answered = self.inform(request)
        need = self.view()["needs"]
        self.assertEqual(
            ("resume", "--resume-paused --no-progress-limit N", 3),
            (need["kind"], need.get("action"), need.get("no_progress_batches")),
        )
        action = need["action"].replace(" N", f" {need['no_progress_batches'] + 1}").split()
        self.launch([*self.args, *action, "--unit", "autoplanner"], 0)
        resumed = self.assert_admitted_without_new_allowance(held)
        self.assertEqual(answered["resolver"]["human_responses"], resumed["resolver"]["human_responses"])

    def test_the_trial_checkpoint_names_the_command_that_admits(self):
        """#448's retained checkpoint: a request republished at limit 4/count 3 after consumed guidance.

        The guidance was consumed, limit 4 saved in its own invocation, and a plain resume asked
        again. The limit still caused the pause though the count is now below it, so the request
        and, once answered, the status view name the bound; reasserting the saved 4 admits.
        """
        held, request = self.hold()
        self.inform(request)
        self.launch([*self.args, "--no-progress-limit", "4"], 2)
        self.launch([*self.args, "--resume-paused"], 2)
        _, republished = self.saved()
        again = human.current(republished)
        decision = republished["user_request"]["decision_needed"]
        self.assertIn("autocode resume --no-progress-limit N with N above the retained count of 3 ", decision)
        self.assertIn("Reasserting the saved limit, 4, also acknowledges it.", decision)
        self.assertNotIn("--resolver-response", decision)
        self.inform(again)
        need = self.view()["needs"]
        self.assertEqual(
            ("resume", "--resume-paused --no-progress-limit N", 3),
            (need["kind"], need.get("action"), need.get("no_progress_batches")),
        )
        self.launch([*self.args, *need["action"].replace(" N", " 4").split(), "--unit", "autoplanner"], 0)
        self.assert_admitted_without_new_allowance(held)

    def test_unchanged_batches_alone_pause_for_no_progress_not_for_recoveries(self):
        """#448: with no automatic recovery recorded, the count was also read as spent recoveries.

        The run paused as PAUSED_TIMEOUT_RECOVERY and asked for --grant-recovery, a real grant,
        before the no-progress pause and its advice could even appear.
        """
        held, request = self.hold_at("PAUSED_NO_PROGRESS", self.hold_without_a_recovery())
        decision = held["user_request"]["decision_needed"]
        self.assertIn("autocode resume --no-progress-limit N", decision)
        self.assertNotIn("--grant-recovery", decision)
        self.launch(["resume", *self.args, "--no-progress-limit", "4", "--unit", "autoplanner"], 0)
        self.assert_admitted_without_new_allowance(held)

    def test_unchanged_batches_below_an_explicit_limit_spend_no_recoveries(self):
        """#448: below the operator's limit of 5, three unchanged batches stopped the run as recovery-exhausted."""
        state = self.hold_without_a_recovery("--no-progress-limit", "5")
        support.atomic_json(self.run / "state.json", state)
        self.launch([*self.args, "--resume-paused"], 0)
        _, finished = self.saved()
        self.assertEqual("TASK_COMPLETE", finished["status"])
        self.assertTrue((self.project / "greet.py").is_file())
        self.assertFalse(finished.get("recovery_grants"))
        self.assertNotIn("automatic_recoveries_since_resume", finished)

    def test_a_bound_that_does_not_admit_the_count_never_launches_the_builder(self):
        held, _ = self.hold()
        for limit, count in ((3, 3), (4, 4), (4, 5), (2, 3)):
            with self.subTest(limit=limit, count=count):
                state = copy.deepcopy(held)
                state["no_progress_batches"] = count
                support.atomic_json(self.run / "state.json", state)
                self.launch([*self.args, "--resume-paused", "--no-progress-limit", str(limit)], 2)
                _, blocked = self.saved()
                request = human.current(blocked)
                self.assertEqual("operational_exhaustion", request["scope"])
                self.assertEqual("PAUSED_NO_PROGRESS", self.origin(blocked, request)["pause_status"])
                self.assertEqual(count, blocked["no_progress_batches"])
                self.assertEqual(held["goal_contract"], blocked["goal_contract"])
                self.assertEqual("terra", blocked["next_stage"])
                self.assertFalse((self.project / "greet.py").exists())

    def test_a_no_progress_bound_leaves_an_active_time_request_alone(self):
        """A no-progress bound acknowledges only a no-progress request.

        The run was started with an explicit --no-progress-limit 3, so reasserting 3 at an
        active-time request changes no setting and must leave that request as published.
        0 is a settings change: the stale binding is retired and the same hold republished.
        """
        state = self.stop_before_builder("--no-progress-limit", "3")
        state["active_seconds"] = state["settings"]["limits"]["max_seconds"]
        held, request = self.hold_at("PAUSED_TIME_LIMIT", state)
        for limit, kept in ((3, True), (0, False)):
            with self.subTest(limit=limit):
                support.atomic_json(self.run / "state.json", held)
                self.launch([*self.args, "--resume-paused", "--no-progress-limit", str(limit)], 2)
                _, blocked = self.saved()
                current = human.current(blocked)
                self.assertEqual("PAUSED_TIME_LIMIT", self.origin(blocked, current)["pause_status"])
                self.assertEqual(kept, current["request_id"] == request["request_id"])
                original = blocked["resolver"]["human_escalations"][request["request_id"]]
                self.assertEqual(
                    ("pending", None)
                    if kept
                    else ("superseded", "Settings changed without changing the exhausted bound"),
                    (original["status"], original.get("superseded_reason")),
                )
                self.assertEqual(limit, blocked["settings"]["limits"]["no_progress_batches"])
                self.assertEqual(held["active_seconds"], blocked["active_seconds"])
                self.assertEqual(held["goal_contract"], blocked["goal_contract"])
                self.assertEqual("terra", blocked["next_stage"])
                self.assertFalse((self.project / "greet.py").exists())


if __name__ == "__main__":
    unittest.main()
