"""A settings flag at a parallel Builder member's stop, through the real CLI and worker processes (#542, #543).

A settings write withdraws the member's request and the run asks again (#542): the member's model question
is asked again while that member is still the batch's current stop, and route advice is never repeated
into a request that does not ask it. With --retry-builder the request stays for the retry to check (#543):
the retry runs under the new settings, or is refused with nothing saved.
"""
import hashlib
import json
import unittest

from . import test_subprocess
from . import test_quota_worker as quota_worker
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
    _fixture, paused, answer, resume, workers, attempts = (
        _flow.fixture, _flow.paused, _flow.answer, _flow.resume, _flow.workers, _flow.attempts)
    del _flow

    def fixture(self, *args, **kwargs):
        self._fixture(*args, **kwargs)
        # These cases exercise a batch's member stop and recovery, not the
        # fixture's concurrency rendezvous. Keep real CLI/worker processes but
        # remove its clock polling; the concurrency suite covers that barrier.
        executable = self.root / "fixture-bin" / "codex"
        code = executable.read_text()
        start = code.index("    deadline = time.monotonic() + 10\n")
        end = code.index("\n    if ", start) + 1
        executable.write_text(code[:start] + code[end:])
        hashes = json.loads(self.env["AUTOCODE_QUOTA_FIXTURE_HASHES"])
        hashes["codex"] = hashlib.sha256(executable.read_bytes()).hexdigest()
        self.env["AUTOCODE_QUOTA_FIXTURE_HASHES"] = json.dumps(hashes)

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

    def test_republished_member_question_survives_verifier_settings_toggled_back_and_again(self):
        run, state = self.paused(error=REFUSAL)
        self.asks_route(state, "PAUSED_CONTENT_FILTER")
        original_sol = state["settings"]["roles"]["sol"]["model"]
        original_completion = state["settings"]["roles"]["completion"]["model"]
        first = next(q for q in state["resolver_human_request"]["questions"] if q["id"] == "route-terra")

        # Moving both checkers makes the still-configured Plan Reviewer's model a valid Builder
        # candidate, changing the refusal's advice. Moving them back can republish the old receipt.
        self.resume_with(run, "--sol-model", LUNA, "--completion-model", LUNA)
        state = self.saved()[1]
        self.asks_route(state, "PAUSED_CONTENT_FILTER")
        second = next(q for q in state["resolver_human_request"]["questions"] if q["id"] == "route-terra")
        self.assertNotEqual(first["recommendation"], second["recommendation"])

        self.resume_with(run, "--sol-model", original_sol, "--completion-model", original_completion)
        state = self.saved()[1]
        self.asks_route(state, "PAUSED_CONTENT_FILTER")
        restored = next(q for q in state["resolver_human_request"]["questions"] if q["id"] == "route-terra")
        self.assertEqual(first["recommendation"], restored["recommendation"])

        # The next withdrawal must use the receipt actually shown last, even if its first
        # issuance predates the other settings' receipt. Its stated answer must still work.
        self.resume_with(run, "--sol-model", LUNA, "--completion-model", LUNA)
        state = self.saved()[1]
        self.asks_route(state, "PAUSED_CONTENT_FILTER")
        latest = next(q for q in state["resolver_human_request"]["questions"] if q["id"] == "route-terra")
        self.assertEqual(second["recommendation"], latest["recommendation"])
        self.assertEqual([[GLM], [GLM]], [self.attempts(self.workers(state)[mid]) for mid in ("M1", "M2")])
        self.answer(run, MIMO)
        state = self.resume(run)
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual([[GLM, MIMO], [GLM]], [self.attempts(self.workers(state)[mid]) for mid in ("M1", "M2")])

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

    # --- #543: a settings flag with --retry-builder ------------------------------------------------------

    def test_a_quota_stopped_member_retries_under_the_new_settings(self):
        run, _ = self.paused()
        self.resume_with(run, "--retry-builder", "M1", "--max-parallel-builders", "3", expected=0)
        state = self.saved()[1]
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual(3, state["settings"]["orchestration"]["max_parallel"])
        self.assertEqual([GLM, GLM], self.attempts(self.workers(state)["M1"]))

    def test_a_quota_stopped_members_retry_with_a_builder_model_is_refused_and_saves_nothing(self):
        # The retry reruns the member on the route its batch started it with; a Builder model given with it
        # would be saved for the parent alone. The member's model is named by answering its question.
        run, _ = self.paused()
        checkpoint = run / "state.json"
        before = checkpoint.read_bytes()
        for flag, value in (("--terra-model", MIMO), ("--terra-reasoning-effort", "low")):
            with self.subTest(flag=flag):
                result = self.resume_with(run, "--retry-builder", "M1", flag, value)
                self.assertIn(f"Builder M1's retry runs on the Builder route its batch started it with ({GLM})", result.stderr)
                self.assertIn("--answer route-terra=MODEL", result.stderr)
                self.assertIn("Nothing was saved", result.stderr)
                self.assertEqual(before, checkpoint.read_bytes())
        self.answer(run, MIMO)  # the accepted form
        state = self.resume(run)
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual([GLM, MIMO], self.attempts(self.workers(state)["M1"]))

    def test_a_consumed_refusal_reasks_its_model_question_with_new_settings_without_launching(self):
        run, state = self.paused(error=REFUSAL)
        self.asks_route(state, "PAUSED_CONTENT_FILTER")
        # Corrective information consumes the request without choosing a model.
        issued = state["resolver_human_request"]
        self.launch(["--run-dir", str(run), "--resolver-request", issued["request_id"],
                     "--resolver-token", issued["request_token"], "--resolver-response", "provide_information",
                     "--resolver-message", "The refusal was inspected; keep the approved scope", "--no-chat"], 0)
        before = self.saved()[1]
        attempts = {mid: self.attempts(row) for mid, row in self.workers(before).items()}
        self.resume_with(run, "--retry-builder", "M1", "--sol-model", LUNA)
        state = self.saved()[1]
        self.assertEqual(LUNA, state["settings"]["roles"]["sol"]["model"])
        self.asks_route(state, "PAUSED_CONTENT_FILTER")
        self.assertNotEqual(issued["request_id"], state["resolver_human_request"]["request_id"])
        self.assertEqual(attempts, {mid: self.attempts(row) for mid, row in self.workers(state).items()})
        self.answer(run, MIMO)
        state = self.resume(run)
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual([GLM, MIMO], self.attempts(self.workers(state)["M1"]))

    def test_a_failed_member_gets_its_second_attempt_under_the_new_settings(self):
        run, state = self.paused("AUTOCODE_BUILDER_FAIL")
        assert_operational_wait(self, state, "PAUSED_ORCHESTRATOR_WORKER")
        del self.env["AUTOCODE_BUILDER_FAIL"]  # the cause was fixed
        # A Builder model would not reach the member's retry, and a failed member has no model question.
        before = (run / "state.json").read_bytes()
        result = self.resume_with(run, "--retry-builder", "M1", "--terra-model", MIMO)
        self.assertIn(f"Builder M1's retry runs on the Builder route its batch started it with ({GLM})", result.stderr)
        self.assertNotIn("route-terra", result.stderr)
        self.assertIn("Nothing was saved", result.stderr)
        self.assertEqual(before, (run / "state.json").read_bytes())
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
