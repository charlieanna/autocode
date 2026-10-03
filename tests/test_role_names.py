"""Names follow the work being performed, independent of a selected model."""
import ast
import copy
import inspect
import unittest
import autopilot
from units.autoplanner import V2_STAGE_ROLES
from autocode_jobs import STAGES
from autocode_role_names import CATALOGUE, ROLES, role_name
import autocode_status


class RoleNamesTests(unittest.TestCase):
    def test_every_dispatched_stage_and_report_repair_has_a_display_name(self):
        tree = ast.parse(inspect.getsource(autopilot.unit_for))
        declared = set(STAGES) | set(V2_STAGE_ROLES) | {'recognize_workflow'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == 'stage':
                for value in node.comparators:
                    if isinstance(value, (ast.Tuple, ast.List)):
                        declared.update(item.value for item in value.elts if isinstance(item, ast.Constant) and isinstance(item.value, str))
        self.assertFalse(declared - set(ROLES), 'A dispatched job must never leak an internal name')
        for stage in declared:
            with self.subTest(stage=stage):
                self.assertTrue(role_name(stage))
                self.assertEqual(role_name(stage), role_name(stage + '_report_repair'))

    def test_legacy_and_current_names_describe_the_same_job(self):
        for names, expected in [(('requirements_gather', 'requirements'), 'Requirements'),
                                (('astra_discovery', 'plan', 'astra_plan'), 'Planner'),
                                (('astra_challenge', 'plan_review'), 'Plan Reviewer'),
                                (('terra', 'builder'), 'Builder'), (('sol', 'validator'), 'Validator')]:
            for stage in names:
                self.assertEqual(expected, role_name(stage))

    def test_alternate_reviewer_route_names_the_validation_and_completion_jobs(self):
        for mode in ('glm_first_v1', 'glm_final_audit_v2'):
            state = {'task': 'Fix routing', 'workspace': '/fixture', 'status': 'RUNNING',
                     'settings': {'workflow': {'mode': mode}},
                     'active_stage': {'stage': 'astra_checkpoint', 'role': 'astra',
                                      'launch_route': {'model': 'provider/plan-reviewer-model'}}}
            before = copy.deepcopy(state['settings'])
            row = autocode_status.record(state, timestamp=1)
            self.assertEqual('Validator / Completion Owner', row['speaker'])
            self.assertIn('provider/plan-reviewer-model', row['text'])
            self.assertEqual(before, state['settings'])
            self.assertEqual('Validator / Completion Owner', role_name('astra_checkpoint_report_repair', mode))
        self.assertEqual('Completion Owner', role_name('astra_checkpoint'))

    def test_registry_is_presentation_data_only(self):
        self.assertEqual({'roles', 'aliases', 'stages', 'modes'}, set(CATALOGUE))
        for entry in CATALOGUE['stages'].values():
            self.assertEqual({'role', 'activity'}, set(entry))
