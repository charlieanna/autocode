"""The one reported-input bound behind role rotation (#1939) and report-repair fresh starts (#4094)."""

import unittest

import autocode_session_rotation as rotation


def record(role, tokens, *, stage="builder", thread=None, expected=None, sessions=None):
    row = {"role": role, "stage": stage, "metrics": {"provider_tokens": {"input_tokens": tokens}}}
    if thread is not None:
        row["thread_id"] = thread
    if expected is not None:
        row["expected_session"] = expected
    if sessions is not None:
        row["metrics"]["provider_sessions"] = sessions
    return row


class SessionRotationTests(unittest.TestCase):
    def state(self, *rows, limit=4000):
        return {"settings": {"rotation_after_input_tokens": limit}, "stages": list(rows)}

    def test_role_bound_reads_the_roles_latest_record(self):
        state = self.state(record("builder", 9000), record("tester", 1000), record("builder", 3000))
        self.assertFalse(rotation.reached(state, "builder"))
        self.assertFalse(rotation.reached(state, "tester"))
        self.assertEqual(3000, rotation.reported_input(state, "builder"))
        state["stages"].append(record("builder", 4000))
        self.assertTrue(rotation.reached(state, "builder"))

    def test_route_role_names_the_route_a_record_ran_on(self):
        state = self.state(
            {
                "role": "task_planner",
                "route_role": "tester",
                "stage": "review_checkpoint",
                "metrics": {"provider_tokens": {"input_tokens": 5000}},
            }
        )
        self.assertTrue(rotation.reached(state, "tester"))
        self.assertFalse(rotation.reached(state, "task_planner"))

    def test_a_record_without_a_role_reads_the_stages_route(self):
        state = self.state({"stage": "tester", "metrics": {"provider_tokens": {"input_tokens": 5000}}})
        self.assertTrue(rotation.reached(state, "tester"))
        repair = self.state({"stage": "builder_report_repair", "metrics": {"provider_tokens": {"input_tokens": 5000}}})
        self.assertTrue(rotation.reached(repair, "builder"))

    def test_thread_bound_reads_the_conversation_a_resume_carries(self):
        # The repair's own fresh request is small, but resuming the original thread would still carry
        # its whole conversation: the bound reads that thread, not the role's latest record (#4094).
        state = self.state(
            record("builder", 5000, thread="t-session"),
            record("builder", 3000, stage="builder_report_repair", expected=None, sessions=["fresh-thread"]),
        )
        self.assertFalse(rotation.reached(state, "builder"))
        self.assertTrue(rotation.session_reached(state, "t-session"))
        self.assertFalse(rotation.session_reached(state, "fresh-thread"))
        self.assertEqual(5000, rotation.session_input(state, "t-session"))

    def test_thread_identity_comes_from_the_records_own_evidence(self):
        for row in (
            record("builder", 5000, thread="t-session"),
            record("builder", 5000, expected="t-session"),
            record("builder", 5000, sessions=["t-session"]),
        ):
            with self.subTest(identity=sorted(set(row) - {"role", "stage", "metrics"})):
                self.assertEqual(5000, rotation.session_input(self.state(row), "t-session"))

    def test_the_largest_report_on_the_thread_is_its_conversation(self):
        state = self.state(
            record("builder", 2000, thread="t-session"),
            record("builder", 5000, stage="builder_report_repair", expected="t-session"),
            record("builder", 3000, stage="builder_report_repair", expected="t-session"),
        )
        self.assertEqual(5000, rotation.session_input(state, "t-session"))
        self.assertTrue(rotation.session_reached(state, "t-session"))

    def test_no_threshold_or_no_reported_input_never_reaches_it(self):
        rows = (record("builder", 9000, thread="t-session"),)
        for limit in (0, None):
            with self.subTest(limit=limit):
                self.assertFalse(rotation.reached(self.state(*rows, limit=limit), "builder"))
                self.assertFalse(rotation.session_reached(self.state(*rows, limit=limit), "t-session"))
        for metrics in (None, {}, {"provider_tokens": None}, {"provider_tokens": {"input_tokens": None}}):
            with self.subTest(metrics=metrics):
                state = self.state(
                    {"role": "builder", "stage": "builder", "thread_id": "t-session", "metrics": metrics}
                )
                self.assertFalse(rotation.reached(state, "builder"))
                self.assertFalse(rotation.session_reached(state, "t-session"))
        self.assertIsNone(rotation.session_input(self.state(*rows), "unknown-thread"))
        self.assertFalse(rotation.session_reached(self.state(*rows), "unknown-thread"))
        self.assertFalse(rotation.reached({"settings": {}, "stages": []}, "builder"))


if __name__ == "__main__":
    unittest.main()
