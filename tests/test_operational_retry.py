"""One fresh attempt at an operational hold (#301): the shared eligibility check and the launch guard.

The CLI behavior (advice, acceptance, one launch) is covered in test_recovery_advice_conformance.
"""
import unittest
from unittest.mock import patch

import autocode_operational_retry as retry
import autocode_recovery_limits as limits
import autocode_util as util

REVISION = "r" * 64


def denial(number, repeat, since=0, revision=REVISION):
    attempt = f"001/builder-{number:02d}"
    return {"attempt_id": attempt, "incident_id": "incident-1", "events": f"/run/{attempt}.jsonl",
            "denied_operation": {"capability": "external_directory", "path": "/tmp/diagnostic/*",
                                 "classification": "external_temporary_directory"},
            "diagnostic_directory": "/workspace/.autocode/recovery-evidence/scratch",
            "repeat_count": repeat, "denied_since_accepted": since, "source_revision": revision,
            "next_stage": "terra"}


def permission_held(*recoveries):
    return {"status": "PAUSED_REPEATED_FAILURE", "next_stage": "terra", "workspace": "/workspace",
            "settings": {"limits": {"no_progress_batches": 3}},
            "automatic_permission_recoveries": [dict(row) for row in recoveries],
            "recovery_context": dict(recoveries[-1]), "stages": []}


def exhausted():
    events = "/run/archived/completion-review-01.jsonl"
    return {"status": "PAUSED_RESOLVER_OPERATIONAL", "next_stage": "sol",
            "recovery_context": {"attempt_id": "001/completion-review-01", "events": events,
                                 "source_revision": REVISION},
            "stages": [{"stage": "astra_review", "events": events, "abandoned": True,
                        "source_revision": REVISION}]}


def target(state, cause=None, revision=REVISION, planning=()):
    return retry.target(state, cause=cause or state["status"], revision=lambda: revision,
                        is_planning=lambda _state, stage: stage in planning)


class PermissionHold(unittest.TestCase):
    def test_a_repeated_denial_at_its_frontier_is_retryable_once(self):
        state = permission_held(denial(1, 1), denial(2, 2, 1))
        kind, recovery = target(state)
        self.assertEqual(("permission_hold", "001/builder-02"), (kind, recovery["attempt_id"]))
        before = [dict(row) for row in state["automatic_permission_recoveries"]]
        authorization = retry.authorize(state, kind, recovery, now="now")
        self.assertEqual(("user_cli", "incident-1", 2), (authorization["actor"], authorization["incident_id"],
                                                         authorization["repeat_count"]))
        self.assertEqual("failure_retry_authorized", state["user_events"][-1]["kind"])
        self.assertEqual(before, state["automatic_permission_recoveries"], "nothing is reset")
        self.assertIsNone(target(state), "a denial is authorized at most once")

    def test_the_ceiling_hold_also_qualifies(self):
        self.assertIsNotNone(target(permission_held(denial(4, 1, 3))))

    def test_only_a_hold_at_its_unchanged_frontier_qualifies(self):
        def variant(change):
            state = permission_held(denial(1, 1), denial(2, 2, 1))
            change(state)
            return state
        cases = {
            "first denial retries automatically": lambda s: s.update(
                automatic_permission_recoveries=[denial(1, 1)], recovery_context=denial(1, 1)),
            "a later recovery replaced the context": lambda s: s.update(recovery_context={
                "attempt_id": "001/builder-03", "events": "/run/001/builder-03.jsonl", "timeout_kind": "idle"}),
            "next stage moved": lambda s: s.update(next_stage="sol"),
            "attempt still active": lambda s: s.update(active_stage={"stage": "terra"}),
            "report repair pending": lambda s: s.update(pending_report_repair={"attempts": 1}),
            "a requirements question is pending": lambda s: s.update(pending_questions=[{"id": "q1"}]),
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.assertIsNone(target(variant(change)))
        held = permission_held(denial(1, 1), denial(2, 2, 1))
        self.assertIsNone(target(held, revision="edited" * 8), "the source changed")
        self.assertIsNone(target(held, cause="PAUSED_TIME_LIMIT"))

        def unreadable():
            raise OSError("workspace gone")
        self.assertIsNone(retry.target(held, cause=held["status"], revision=unreadable,
                                       is_planning=lambda *_: False))

    def test_launch_guard_lifts_exactly_the_authorized_denial(self):
        state = permission_held(denial(1, 1), denial(2, 2, 1))
        with patch.object(limits, "snapshot", return_value={"revision": REVISION}):
            self.assertEqual("PAUSED_REPEATED_FAILURE", limits.stop_reason(state, 0, 3)[0])
            retry.authorize(state, *target(state), now="now")
            self.assertIsNone(limits.stop_reason(state, 0, 3))
            # The authorized attempt was denied again: a new denial holds again.
            state["automatic_permission_recoveries"].append(denial(3, 3, 2))
            state["recovery_context"] = denial(3, 3, 2)
            self.assertEqual("PAUSED_REPEATED_FAILURE", limits.stop_reason(state, 0, 3)[0])
            # The recovery budget still applies to an authorized attempt.
            state["recovery_context"] = denial(2, 2, 1)
            self.assertEqual("PAUSED_TIMEOUT_RECOVERY", limits.stop_reason(state, 3, 3)[0])


class OperationalExhaustion(unittest.TestCase):
    def test_a_stopped_attempt_at_its_source_is_retryable_once(self):
        state = exhausted()
        kind, recovery = target(state)
        self.assertEqual("operational_exhaustion", kind)
        retry.authorize(state, kind, recovery, now="now")
        self.assertEqual("sol", state["failure_retry_authorizations"][0]["stage"])
        self.assertIsNone(target(state))

    def test_planning_a_changed_source_and_unstopped_attempts_do_not_qualify(self):
        self.assertIsNone(target(exhausted(), planning=("sol",)), "planning has its own allowance")
        self.assertIsNone(target(exhausted(), revision="edited" * 8))
        accepted = exhausted()
        accepted["stages"][0].pop("abandoned")
        self.assertIsNone(target(accepted), "the attempt did not stop")
        unrelated = exhausted()
        unrelated["stages"][0]["runner_owned"] = True
        self.assertIsNone(target(unrelated))


class StopCause(unittest.TestCase):
    def published(self, status="pending", scope="operational_exhaustion", pause="PAUSED_RESOLVER_OPERATIONAL"):
        identity = {"issuer": "resolver", "proposal": {"scope": scope, "origin": {"pause_status": pause}}}
        key = util.digest(identity)
        state = {"status": "WAITING_FOR_USER",
                 "resolver": {"human_escalations": {key: {"status": status, "identity": identity}}}}
        return state, {"request_id": key}

    def test_a_paused_status_or_an_intact_pending_operational_request_names_the_pause(self):
        self.assertEqual("PAUSED_REPEATED_FAILURE", retry.stop_cause({"status": "PAUSED_REPEATED_FAILURE"}, None))
        state, public = self.published()
        self.assertEqual("PAUSED_RESOLVER_OPERATIONAL", retry.stop_cause(state, public))
        for variant in ({"status": "consumed"}, {"scope": "clarification"}):
            with self.subTest(variant):
                self.assertIsNone(retry.stop_cause(*self.published(**variant)))
        state, public = self.published()
        next(iter(state["resolver"]["human_escalations"].values()))["identity"]["issuer"] = "model"
        self.assertIsNone(retry.stop_cause(state, public), "a tampered request names nothing")


class Advice(unittest.TestCase):
    def test_retry_advice_names_only_the_retry(self):
        text = limits.advice(allow_grant=True, allow_retry=True)
        self.assertIn("--resume-paused --retry-failed-stage", text)
        self.assertNotIn("--grant-recovery", text)


if __name__ == "__main__":
    unittest.main()
