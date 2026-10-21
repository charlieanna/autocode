"""A paraphrase/usage change cannot renew the material-permission handoff allowance."""

import copy
import unittest

import autocode_permission_progress as progress
from autocode_util import digest


class PermissionProgressTests(unittest.TestCase):
    def setUp(self):
        self.binding = {
            "task_id": "task",
            "task": "Fix listing",
            "current_task": "task-hash",
            "contract": {"hash": "contract"},
            "observed_source": {"revision": "source"},
            "validation": "failed-observation",
            "accounting": {"active_seconds": 10},
        }
        self.resolver = {"human_escalations": {}}
        for i in range(3):
            binding = {**copy.deepcopy(self.binding), "answers": str(i), "accounting": {"active_seconds": i * 60}}
            identity = {
                "issuer": "resolver",
                "action": "escalate",
                "binding": binding,
                "proposal": {
                    "scope": "permission",
                    "request": {"decision_needed": f"Paraphrase {i}"},
                    "origin": {"stage": "astra_review", "output": f"review-{i}"},
                },
            }
            key = digest(identity)
            self.resolver["human_escalations"][key] = {
                "identity": identity,
                "receipt_hash": key,
                "status": "consumed",
                "response": {
                    "actor": "user_cli",
                    "action": "material_decision",
                    "request_id": key,
                    "request_token": digest({"request_id": key, "binding": binding}),
                },
            }

    def test_three_answered_permissions_stop_even_with_new_wording_usage_or_answers(self):
        self.assertEqual(3, progress.exhausted(self.resolver, self.binding)["count"])
        for key in ("answers", "user_events", "accounting", "failure_history", "settings"):
            self.assertIsNotNone(progress.exhausted(self.resolver, {**self.binding, key: "changed"}))

    def test_real_work_progress_allows_new_decisions(self):
        for key in progress.FIELDS:
            self.assertIsNone(progress.exhausted(self.resolver, {**self.binding, key: "changed"}))

    def test_pending_forged_or_nonmaterial_receipts_do_not_spend_this_allowance(self):
        for change in (
            {"status": "pending"},
            {"receipt_hash": "forged"},
            {"response": {"action": "provide_information"}},
        ):
            rows = copy.deepcopy(self.resolver)
            next(iter(rows["human_escalations"].values())).update(change)
            self.assertIsNone(progress.exhausted(rows, self.binding))
