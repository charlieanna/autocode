import unittest

from autocode_request_usage import accounting, measurement, view


class RequestUsageTests(unittest.TestCase):
    def step(self, identity, tokens):
        return {"type": "step_finish", "sessionID": "s", "part": {"id": identity, "tokens": tokens}}

    def test_individual_context_includes_cache_and_is_not_cumulative(self):
        rows = [
            self.step("1", {"input": 10, "output": 5, "cache": {"read": 100, "write": 20}}),
            self.step("2", {"input": 8, "output": 2, "cache": {"read": 150, "write": 0}}),
        ]
        result = measurement(rows + [rows[-1]])
        self.assertEqual(result["peak_input_tokens"], 158)
        self.assertEqual(result["last"]["input_tokens"], 158)
        self.assertEqual(result["observed_requests"], 2)
        self.assertTrue(result["complete"])

    def test_missing_counters_and_native_turn_totals_are_unavailable(self):
        for rows in ([], [{"type": "turn.completed", "usage": {"input_tokens": 900}}], [self.step("x", {})]):
            result = measurement(rows)
            self.assertIsNone(result["peak_input_tokens"])
            self.assertIsNone(result["last"])
            self.assertFalse(result["complete"])
        result = measurement([self.step("a", {"input": 2, "cache": {"read": 3, "write": 0}}), self.step("b", {})])
        self.assertIsNone(result["last"])
        self.assertEqual(result["missing_usage_requests"], 1)
        self.assertEqual(view({"stages": [{"metrics": {"request_context": result}}]})["unavailable_stages"], 1)

    def test_started_request_without_final_usage_keeps_measurement_incomplete(self):
        result = measurement([self.step("a", {"input": 2, "cache": {"read": 3, "write": 0}}), {"type": "step_start"}])
        self.assertEqual(result["peak_input_tokens"], 5)
        self.assertFalse(result["complete"])
        self.assertTrue(result["unfinished_request"])

    def test_bad_identity_or_conflicting_replay_is_unavailable(self):
        good = self.step("a", {"input": 2, "cache": {"read": 3, "write": 0}})
        for bad in (self.step({"invalid": "id"}, {}), self.step("a", {"input": 100, "cache": {"read": 3, "write": 0}})):
            result = measurement([good, bad])
            self.assertFalse(result["complete"])
            self.assertEqual(result["missing_usage_requests"], 1)


class MessageTimingTests(unittest.TestCase):
    def event(self, kind, part_id, timestamp, message=None):
        part = {"id": part_id, "sessionID": "s"}
        if message is not None:
            part["messageID"] = message
        if kind == "step_finish":
            part["tokens"] = {"input": 10, "output": 4, "reasoning": 2, "cache": {"read": 20, "write": 0}}
        return {"type": kind, "sessionID": "s", "timestamp": timestamp, "part": part}

    def test_messages_pair_independently_and_replayed_finish_cannot_close_new_start(self):
        starts = [self.event("step_start", "sa", 100, "a"), self.event("step_start", "sb", 200, "b")]
        first, second = self.event("step_finish", "fb", 300, "b"), self.event("step_finish", "fa", 400, "a")
        result = accounting([*starts, first, second])
        self.assertEqual([200, 100], [row["started_at_ms"] for row in result["requests"]])
        self.assertTrue(result["complete"])
        replay = accounting([*starts, first, second, self.event("step_start", "sc", 500, "a"), second])
        self.assertEqual(2, replay["observed_requests"])
        self.assertEqual(1, replay["unfinished_requests"])
        self.assertFalse(replay["complete"])

    def test_extra_middle_start_cannot_shift_final_message_timing(self):
        rows = [
            self.event("step_start", "s1", 100, "first"),
            self.event("step_finish", "f1", 200, "first"),
            self.event("step_start", "s2", 300, "middle"),
            self.event("step_start", "s3", 400, "middle"),
            self.event("step_finish", "f2", 500, "middle"),
            self.event("step_start", "s4", 600, "final"),
            self.event("step_finish", "f3", 800, "final"),
        ]
        result = accounting(rows)
        self.assertEqual([100, None, 600], [row["started_at_ms"] for row in result["requests"]])
        self.assertEqual(1, result["unfinished_requests"])
        self.assertFalse(result["complete"])
        self.assertEqual(90, sum(row["tokens"]["input_tokens"] for row in result["requests"]))
        self.assertTrue(any("ambiguous request timing" in issue for issue in result["issues"]))

    def test_same_message_ambiguity_stays_unknown_for_later_finishes(self):
        result = accounting(
            [
                self.event("step_start", "s1", 100, "a"),
                self.event("step_start", "s2", 200, "a"),
                self.event("step_finish", "f1", 300, "a"),
                self.event("step_finish", "f2", 400, "a"),
            ]
        )
        self.assertEqual([None, None], [row["started_at_ms"] for row in result["requests"]])
        self.assertEqual(0, result["unfinished_requests"])
        self.assertFalse(result["complete"])
        self.assertEqual(2, result["observed_requests"])

    def test_missing_foreign_and_malformed_messages_cannot_drain_known_starts(self):
        early = accounting(
            [
                self.event("step_start", "anonymous", 50),
                self.event("step_finish", "anonymous-finish", 80),
                self.event("step_start", "known", 100, "a"),
                self.event("step_finish", "matched", 300, "a"),
            ]
        )
        self.assertEqual([None, 100], [row["started_at_ms"] for row in early["requests"]])
        self.assertEqual(1, early["unfinished_requests"])
        for message in (None, "other", [], False):
            with self.subTest(message=message):
                result = accounting(
                    [
                        self.event("step_start", "anonymous", 50),
                        self.event("step_start", "known", 100, "a"),
                        self.event("step_finish", "foreign", 200, message),
                        self.event("step_finish", "matched", 300, "a"),
                    ]
                )
                self.assertEqual([None, 100], [row["started_at_ms"] for row in result["requests"]])
                self.assertEqual(1, result["unfinished_requests"])
                self.assertFalse(result["complete"])
                self.assertEqual(60, sum(row["tokens"]["input_tokens"] for row in result["requests"]))
        result = accounting([self.event("step_start", "anonymous", 50), self.event("step_finish", "known", 200, "a")])
        self.assertIsNone(result["requests"][0]["started_at_ms"])
        self.assertEqual(1, result["unfinished_requests"])

    def test_legacy_fifo_and_finish_only_usage_remain_supported(self):
        result = accounting(
            [
                self.event("step_start", "s1", 100),
                self.event("step_start", "s2", 200),
                self.event("step_finish", "f1", 300),
                self.event("step_finish", "f2", 400),
            ]
        )
        self.assertEqual([100, 200], [row["started_at_ms"] for row in result["requests"]])
        self.assertTrue(result["complete"])
        only = accounting([self.event("step_finish", "f1", 300, "a")])
        self.assertTrue(only["complete"])
        self.assertIsNone(only["requests"][0]["started_at_ms"])

    def test_conflicting_replays_keep_usage_but_cannot_prove_timing(self):
        start = self.event("step_start", "s1", 100, "a")
        finish = self.event("step_finish", "f1", 200, "a")
        changed_start = self.event("step_start", "s1", 100, "b")
        changed_finish = self.event("step_finish", "f1", 200, "b")
        for rows in ([start, changed_start, finish], [start, finish, changed_start], [start, finish, changed_finish]):
            with self.subTest(rows=rows):
                result = accounting(rows)
                self.assertEqual(1, result["observed_requests"])
                self.assertEqual(30, result["requests"][0]["tokens"]["input_tokens"])
                self.assertIsNone(result["requests"][0]["started_at_ms"])
                self.assertFalse(result["complete"])
                self.assertTrue(any("conflicting request message" in issue for issue in result["issues"]))
        pending = accounting([start, finish, self.event("step_start", "s2", 300, "b"), changed_finish])
        self.assertEqual(1, pending["unfinished_requests"])
        for changed in (changed_start, changed_finish):
            result = accounting(
                [
                    start,
                    self.event("step_start", "s2", 150, "b"),
                    finish,
                    changed,
                    self.event("step_finish", "f2", 300, "b"),
                    self.event("step_start", "s3", 400, "c"),
                    self.event("step_finish", "f3", 500, "c"),
                ]
            )
            self.assertEqual([None, None, 400], [row["started_at_ms"] for row in result["requests"]])
            self.assertEqual(0, result["unfinished_requests"])
            self.assertEqual(90, sum(row["tokens"]["input_tokens"] for row in result["requests"]))
