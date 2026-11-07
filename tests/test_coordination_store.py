"""Cheap public coordination observations never dispatch participant work."""

import copy
import unittest
from unittest.mock import patch

import autocode_coordination_store as store


class IntegrationOfferTests(unittest.TestCase):
    def test_offer_publishes_only_current_joint_identity_before_its_start_returns(self):
        document = {
            "phase": "joint_regression",
            "revision": 12,
            "active": "joint plan start",
            "pending": {"operation": "joint_plan_start"},
            "joint": {"workspace": "/joint", "run_dir": "/joint/run", "base": "private-detail"},
            "members": [],
        }
        before = copy.deepcopy(document)
        with patch.object(store, "read", return_value=document):
            offer = store.offer("/anchor")
        self.assertEqual({"workspace": "/joint", "run_dir": "/joint/run"}, offer["integration"])
        self.assertEqual(before, document)

    def test_absent_or_retired_joint_is_not_reconstructed_from_attempt_history(self):
        for document in (None, {"joint_attempts": [{"workspace": "/old", "run_dir": "/old/run"}]}):
            with self.subTest(document=document), patch.object(store, "read", return_value=document):
                self.assertIsNone(store.offer("/anchor", eligible=True)["integration"])
