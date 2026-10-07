"""A settings flag at a parallel Builder member's stop, through the real CLI and worker processes (#542, #543).

A settings write withdraws the member's request and the run asks again (#542): the member's model question
is asked again while that member is still the batch's current stop, and route advice is never repeated
into a request that does not ask it. With --retry-builder the request stays for the retry to check (#543):
the retry runs under the new settings, or is refused with nothing saved.
"""
import unittest

from . import test_subprocess
from . import test_quota_worker as quota_worker
import autocode as runner
import autocode_resolver_human as resolver_human
import autocode_support as support
from goal_fixtures import assert_operational_wait

QUOTA, REFUSAL, GLM, MIMO = quota_worker.QUOTA, quota_worker.REFUSAL, quota_worker.GLM, quota_worker.MIMO
LUNA = "openai/gpt-6-luna"
ADVICE = "--answer route-terra=MODEL"
RESOLVER_ADVICE = "AutoResolver could not resolve"
GUARD = "belongs to another implementation task"


class MemberStopSettingsTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ()
    # Borrowed, not inherited, so ParallelQuotaTests' own tests do not run again here.
    _flow = quota_worker.ParallelQuotaTests
    fixture, paused, answer, resume, workers, attempts = (
        _flow.fixture, _flow.paused, _flow.answer, _flow.resume, _flow.workers, _flow.attempts)
    del _flow

    def resume_with(self, run, *flags, expected=2):
        result = self.launch(["--run-dir", str(run), "--resume-paused", *flags, "--no-chat"], expected)
        self.assertNotIn(GUARD, result.stdout + result.stderr)
        return result

    def asks_route(self, state, status):
        """The member's own model question, advised once."""
        request = assert_operational_wait(self, state, status)
        asked = [q for q in request["questions"] if q["id"] == "route-terra"]
        self.assertEqual(["Builder (milestone M1)"], [q["job"] for q in asked], state["stop_reason"])
        self.assertEqual(1, state["stop_reason"].count(ADVICE), state["stop_reason"])

    # --- #542: a settings flag alone ---------------------------------------------------------------------

    def settings_flag_asks_the_member_again(self, error, status):
        run, state = self.paused(error=error)
        self.asks_route(state, status)
        self.resume_with(run, "--sol-model", LUNA)
        state = self.saved()[1]
        self.assertEqual(LUNA, state["settings"]["roles"]["sol"]["model"])
        self.asks_route(state, status)
        self.answer(run, MIMO)  # the command the run advises is accepted
        state = self.resume(run)
        self.assertEqual("TASK_COMPLETE", state["status"])
        rows = self.workers(state)
        self.assertEqual([[GLM, MIMO], [GLM]], [self.attempts(rows["M1"]), self.attempts(rows["M2"])])

    def test_a_settings_flag_asks_a_quota_stopped_members_model_question_again(self):
        self.settings_flag_asks_the_member_again(QUOTA, "PAUSED_BUDGET")

    def test_a_settings_flag_asks_a_refused_members_model_question_again(self):
        self.settings_flag_asks_the_member_again(REFUSAL, "PAUSED_CONTENT_FILTER")

    def test_a_settings_flag_asks_a_failed_member_again_with_its_advice_once(self):
        # A generic Builder failure asks no model question. Asked again after the settings write, its
        # request starts from the member's own cause, not from the stop reason the withdrawn request
        # composed, so AutoResolver's advice is given once.
        run, state = self.paused("AUTOCODE_BUILDER_FAIL")
        assert_operational_wait(self, state, "PAUSED_ORCHESTRATOR_WORKER")
        self.assertEqual(1, state["stop_reason"].count(RESOLVER_ADVICE), state["stop_reason"])
        self.resume_with(run, "--sol-model", LUNA)
        state = self.saved()[1]
        self.assertEqual(LUNA, state["settings"]["roles"]["sol"]["model"])
        request = assert_operational_wait(self, state, "PAUSED_ORCHESTRATOR_WORKER")
        self.assertFalse([q for q in request["questions"] if q["id"] == "route-terra"])
        self.assertEqual(1, state["stop_reason"].count(RESOLVER_ADVICE), state["stop_reason"])
        del self.env["AUTOCODE_BUILDER_FAIL"]  # the cause was fixed
        self.resume_with(run, "--retry-builder", "M1", expected=0)
        self.assertEqual("TASK_COMPLETE", self.saved()[1]["status"])

    def test_a_request_that_asks_no_model_question_repeats_no_route_advice(self):
        # The member's stop is withdrawn and, by the time the run asks again, it is no longer the batch's
        # current stop (here: its retry built it). The new request asks no model question, so it must not
        # repeat the withdrawn request's route advice from the saved stop reason.
        run, state = self.paused()
        self.assertIn(ADVICE, state["stop_reason"])
        state["run_dir"] = str(run)
        self.assertTrue(resolver_human.supersede_operational(state, "Settings changed"))
        self.workers(state)["M1"]["status"] = "BUILT"
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(
            runner, state, run, support.Paused(state["status"], state["stop_reason"])))
        request = state[resolver_human.PRIVATE]
        self.assertFalse([q for q in request["questions"] if q["id"] == "route-terra"])
        self.assertNotIn("route-terra", state["stop_reason"])
        self.assertNotIn("route-terra", request["request"]["discovered"])
        self.assertEqual(1, state["stop_reason"].count("Builder M1 stopped on quota"), state["stop_reason"])

    # --- #543: a settings flag with --retry-builder ------------------------------------------------------

    def test_a_quota_stopped_member_retries_under_the_new_settings(self):
        run, _ = self.paused()
        self.resume_with(run, "--retry-builder", "M1", "--max-parallel-builders", "3", expected=0)
        state = self.saved()[1]
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual(3, state["settings"]["orchestration"]["max_parallel"])
        self.assertEqual([GLM, GLM], self.attempts(self.workers(state)["M1"]))

    def test_a_failed_member_gets_its_second_attempt_under_the_new_settings(self):
        run, state = self.paused("AUTOCODE_BUILDER_FAIL")
        assert_operational_wait(self, state, "PAUSED_ORCHESTRATOR_WORKER")
        del self.env["AUTOCODE_BUILDER_FAIL"]  # the cause was fixed
        self.resume_with(run, "--retry-builder", "M1", "--sol-model", LUNA, expected=0)
        state = self.saved()[1]
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual(LUNA, state["settings"]["roles"]["sol"]["model"])
        self.assertEqual([GLM, GLM], self.attempts(self.workers(state)["M1"]))
        self.assertEqual(["M1", "M2", "M3"], [
            (self.project / name).read_text().strip() for name in ("a.txt", "b.txt", "combined.txt")])

    def test_a_refused_members_retry_with_a_settings_flag_is_refused_and_saves_nothing(self):
        run, _ = self.paused(error=REFUSAL)
        checkpoint = run / "state.json"
        before = checkpoint.read_bytes()
        result = self.resume_with(run, "--retry-builder", "M1", "--sol-model", LUNA)
        self.assertIn(f"Builder M1 was refused by its provider's content filter on {GLM}", result.stderr)
        self.assertIn("route-terra question", result.stderr)
        self.assertIn("Nothing was saved", result.stderr)
        self.assertEqual(before, checkpoint.read_bytes())
        # Every member refusal is followed by what was not saved, in a sentence of its own.
        result = self.resume_with(run, "--retry-builder", "M2", "--sol-model", LUNA)
        self.assertIn("Builder M2 already completed; its work will be retained. Nothing was saved", result.stderr)
        self.assertEqual(before, checkpoint.read_bytes())
        # The accepted form: the settings alone, then the member's model answer.
        self.resume_with(run, "--sol-model", LUNA)
        self.answer(run, MIMO)
        state = self.resume(run)
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual([GLM, MIMO], self.attempts(self.workers(state)["M1"]))


if __name__ == "__main__":
    unittest.main()

