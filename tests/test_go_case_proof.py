"""Real Go proof through the public production proof entry point; no model shim."""
from pathlib import Path
import shutil
import unittest

import autocode_regression as regression
import autocode_test_cases as cases
from tests.test_verify import Project


class GoAliasTests(unittest.TestCase):
    def test_numeric_alias_is_complete_and_go_only(self):
        wanted = [{'id': 'AC3', 'test_name': 'test_ac3_defaults_to_30'}]
        good = 'policy::TestAc3DefaultsTo30'
        ids = [good, 'policy::TestAc30DefaultsTo30', 'policy::TestAc3DefaultsTo300',
               'policy::TestAc3DefaultsTo30Extra']
        self.assertEqual({'AC3': [good]}, cases.match_cases(wanted, ids, framework='go'))
        self.assertEqual({'AC3': []}, cases.match_cases(wanted, ids, framework='unittest'))
        version = [{'id': 'V', 'test_name': 'test_version2_1'}]
        self.assertEqual({'V': []}, cases.match_cases(version, ['pkg::TestVersion21'], framework='go'))


@unittest.skipUnless(shutil.which('go'), 'requires real Go compiler')
class GoProductionProofTests(unittest.TestCase):
    def test_framework_survives_projection_and_unrelated_name_cannot_prove_case(self):
        project = Project({'go.mod': 'module policy\n\ngo 1.16\n',
                           'policy.go': 'package policy\nfunc RetentionDays() int { return 0 }\n'})
        self.addCleanup(project.close)
        project.write({'policy.go': 'package policy\nfunc RetentionDays() int { return 30 }\n'})
        state = {'base_commit': project.base, 'settings': {}, 'iteration': 1, 'stages': [], 'history': [],
                 'goal_contract': {'body': {'task_kind': 'build', 'acceptance_criteria': [{
                     'id': 'AC3', 'criterion': 'The default retention is 30 days',
                     'verification_method': 'test: test_ac3_defaults_to_30 — native Go behavior'}],
                     'milestones': [{'id': 'M1'}]}}}
        run = project.root / '.autocode/runs/proof-fixture'
        run.mkdir(parents=True)
        for name, expected in [('TestAc3DefaultsTo30', 'PASS'), ('TestAc30DefaultsTo30', 'FAIL')]:
            with self.subTest(name=name):
                project.write({'policy_test.go': 'package policy\nimport "testing"\nfunc ' + name +
                    '(t *testing.T) { if RetentionDays()!=30 { t.Fatal("wrong retention") } }\n'})
                proof = regression.prove(state, project.root, run)
                self.assertEqual(expected, proof['verdict'], proof)
                self.assertEqual({'AC3': ['policy::' + name] if expected == 'PASS' else []}, proof['case_tests'])
