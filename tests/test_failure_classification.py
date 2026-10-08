import unittest

from autocode_failure_classification import classify


class ClassificationTests(unittest.TestCase):
    def test_operational_statuses_do_not_become_code_failures(self):
        for status in ("PAUSED_TOOL_CONTAINMENT", "PAUSED_PROVIDER_QUOTA", "PAUSED_AUTH",
                       "PAUSED_VERIFICATION_UNCERTAIN", "PAUSED_INVALID_OUTPUT",
                       "PAUSED_PROCESS_CLEANUP", "PAUSED_STALE_VALIDATION"):
            with self.subTest(status=status):
                self.assertEqual("operational", classify({"error_class": status,
                    "checks_verified": True, "checks": [{"exit_code": 1}]}))

    def test_recorded_interruption_timeout_and_cleanup_outrank_diagnosis(self):
        for record in ({"timed_out": True}, {"interrupted": True}, {"exit_code": -9},
                       {"cleanup_error": "owned process remains"},
                       {"supervision_errors": ["inspection denied"]}):
            with self.subTest(record=record):
                self.assertEqual("operational", classify({"record": record, "failure_id": "f1",
                    "evidence_refs": ["attempt.json"], "diagnosis": {"failure_id": "f1",
                    "failure_class": "execution", "evidence_refs": ["attempt.json"]}}))

    def test_saved_schema_rejection_is_operational_but_size_limit_is_uncertain(self):
        for parse, expected in (("invalid_or_unreadable", "operational"),
                                ("json_other", "operational"),
                                ("skipped_size_limit", "unknown"), ("json_object", "unknown")):
            with self.subTest(parse=parse):
                self.assertEqual(expected, classify({"output_probe": {
                    "attempts": 1, "result": {"final_output": {"present": True, "parse": parse}}}}))

    def test_only_verified_current_checkpoint_establishes_plan_failure(self):
        for verified in (True, False, None):
            with self.subTest(verified=verified):
                self.assertEqual("plan" if verified is True else "unknown", classify({
                    "checkpoint_verified": verified, "checkpoint": {"decision": "needs_replan"}}))

    def test_captured_genuine_failure_can_use_execution_retry(self):
        self.assertEqual("execution", classify({"checks_verified": True,
            "checks": [{"exit_code": 1, "evidence_ref": "event:unit-test", "timed_out": False}]}))
        self.assertEqual("unknown", classify({"checks": [{"exit_code": 1}]}))
        for exit_code in (0, True, "1", None):
            with self.subTest(exit_code=exit_code):
                self.assertEqual("unknown", classify({"checks_verified": True,
                    "checks": [{"exit_code": exit_code}]}))

    def test_signalled_or_uncertain_check_is_operational_not_an_ordinary_failure(self):
        for check in ({"exit_code": -9}, {"exit_code": 1, "timed_out": True},
                      {"exit_code": None, "error": "command not collected"},
                      {"exit_code": 1, "supervision_errors": ["keeper unavailable"]}):
            with self.subTest(check=check):
                self.assertEqual("operational", classify({"checks_verified": True, "checks": [check]}))

    def test_operational_check_outranks_checkpoint_and_bound_diagnosis(self):
        for check in ({"exit_code": -9}, {"exit_code": 1, "timed_out": True},
                      {"exit_code": 1, "interrupted": True},
                      {"exit_code": None, "error": "command not collected"},
                      {"exit_code": 1, "supervision_errors": ["keeper unavailable"]}):
            for stronger in ({"checkpoint_verified": True, "checkpoint": {"decision": "needs_replan"}},
                             {"failure_id": "f1", "evidence_refs": ["attempt.json"], "diagnosis": {
                              "failure_id": "f1", "failure_class": "execution",
                              "evidence_refs": ["attempt.json"]}}):
                with self.subTest(check=check, stronger=stronger):
                    self.assertEqual("operational", classify({"checks_verified": True,
                        "checks": [check], **stronger}))

    def test_unavailable_record_does_not_hide_independent_operational_status(self):
        for record in ([1], "unreadable", 123):
            for key in ("error_class", "provider_error_class"):
                with self.subTest(record=record, key=key):
                    self.assertEqual("operational", classify({"record": record,
                        key: "PAUSED_PROVIDER_QUOTA"}))

    def test_empty_diff_and_provider_words_do_not_establish_execution_or_replan(self):
        for evidence in ({"record": {"changed_files": [], "exit_code": 0}},
                         {"reason": "the environment failed"},
                         {"reason": "the plan is wrong"},
                         {"checks": [{"output": "ERROR: 429 Too Many Requests", "exit_code": 1}]}):
            with self.subTest(evidence=evidence):
                self.assertEqual("unknown", classify(evidence))

    def test_current_evidence_bound_investigator_can_settle_each_class(self):
        for kind in ("plan", "execution", "operational", "unknown"):
            with self.subTest(kind=kind):
                self.assertEqual(kind, classify({"failure_id": "current-source-task-contract",
                    "evidence_refs": ["failure.json", "provider.jsonl"], "diagnosis": {
                    "failure_id": "current-source-task-contract", "failure_class": kind,
                    "evidence_refs": ["failure.json"]}}))

    def test_stale_uncited_or_invalid_diagnosis_cannot_authorize_a_route(self):
        for changes in ({"failure_id": "old"}, {"evidence_refs": []},
                        {"evidence_refs": ["unowned.json"]}, {"failure_class": "retry"},
                        {"failure_class": []}):
            with self.subTest(changes=changes):
                diagnosis = {"failure_id": "f1", "failure_class": "execution",
                             "evidence_refs": ["failure.json"], **changes}
                self.assertEqual("unknown", classify({"failure_id": "f1",
                    "evidence_refs": ["failure.json"], "diagnosis": diagnosis}))

    def test_malformed_facts_fail_closed(self):
        for evidence in (None, [], {"record": []}, {"diagnosis": []},
                         {"checks_verified": True, "checks": [None]}):
            with self.subTest(evidence=evidence):
                self.assertEqual("unknown", classify(evidence))
