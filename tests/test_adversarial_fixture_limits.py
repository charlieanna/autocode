"""Only the heavy CSV planning fixture receives the longer outer driver bound."""

import importlib
import os
import tempfile
import types
import unittest
from unittest.mock import patch

from scenarios import test_adversarial_planning as planning

harness = importlib.import_module(planning.AdversarialCase.__module__)


class AdversarialFixtureLimits(unittest.TestCase):
    def probe(self, case_type):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(os.environ, {"AUTOCODE_ADVERSARIAL_OUT": directory}),
                patch.object(
                    harness, "Driver", side_effect=lambda *args, **kwargs: types.SimpleNamespace(env=args[3])
                ) as driver,
            ):
                case = case_type("runTest")
                case.setUp()
                self.addCleanup(case.doCleanups)
                flags = driver.call_args.args[2]
                limits = {
                    name: flags[flags.index(name) + 1]
                    for name in ("--max-iterations", "--max-seconds", "--max-stage-seconds")
                }
                return driver.call_args.kwargs["timeout_seconds"], limits

    def test_other_adversarial_cases_keep_the_150_second_outer_bound(self):
        for case_type in (planning.AdversarialCase, planning.PlanningAttacks, planning.DraftProofPlanning):
            with self.subTest(case=case_type.__name__):
                timeout, limits = self.probe(case_type)
                self.assertEqual(150, timeout)
                self.assertEqual({"--max-iterations": "2", "--max-seconds": "300", "--max-stage-seconds": "30"}, limits)

    def test_csv_outer_bound_matches_its_existing_task_budget_without_changing_provider_limits(self):
        timeout, limits = self.probe(planning.DraftExamplePlanning)
        self.assertEqual(300, timeout)
        self.assertEqual({"--max-iterations": "2", "--max-seconds": "300", "--max-stage-seconds": "30"}, limits)
