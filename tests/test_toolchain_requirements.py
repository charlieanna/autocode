"""Executable approval survives prose-only rework of the same criteria."""
import unittest

import autocode_verification_plan as plan


class RetainedValidationTests(unittest.TestCase):
    def state(self):
        return {'goal_contract': {'body': {
            'initial_task': {'acceptance_criteria': ['AC1'],
                             'validation_plan': ['node --test greet.test.cjs']},
            'acceptance_criteria': [{'id': 'AC1', 'verification_method': 'test: greeting'}]}},
            'current_task': {'acceptance_criteria': ['AC1'],
                             'validation_plan': ['Run node --test greet.test.cjs and inspect its results.']}}

    def test_rework_keeps_the_exact_approved_check_for_replay_and_tool_selection(self):
        state = self.state()
        self.assertEqual(['node --test greet.test.cjs'], plan.approved_commands(state))
        self.assertEqual(['node --test greet.test.cjs'], plan.launch_commands(state))

    def test_prose_without_an_approved_executable_check_is_not_a_command(self):
        state = self.state()
        state['goal_contract']['body']['initial_task']['validation_plan'] = [
            'Run node --test greet.test.cjs and inspect its results.']
        self.assertEqual([], plan.launch_commands(state))

    def test_an_unrelated_criterion_slice_does_not_inherit_the_old_check(self):
        state = self.state()
        state['current_task'] = {'acceptance_criteria': ['AC2'], 'validation_plan': ['go test ./...']}
        self.assertEqual(['go test ./...'], plan.launch_commands(state))

    def test_completion_without_a_task_slice_retains_approved_checks(self):
        state = self.state()
        state['current_task'] = None
        self.assertEqual(['node --test greet.test.cjs'], plan.approved_commands(state))

    def test_current_checks_and_cli_commands_stay_included_without_duplicates(self):
        state = self.state()
        state['current_task']['validation_plan'] = ['node --test greet.test.cjs', 'node --test extra.test.cjs']
        state['settings'] = {'regression': {'test_command': 'node --test greet.test.cjs'}}
        self.assertEqual(['node --test greet.test.cjs', 'node --test extra.test.cjs'], plan.launch_commands(state))

    def test_progressive_required_checks_remain_the_authoritative_checklist(self):
        state = self.state()
        context = {'required_checks': [{'method': 'node --test cumulative.test.cjs'}]}
        self.assertEqual(['node --test cumulative.test.cjs'],
                         plan.launch_commands(state, progressive_context=context))

    def test_retained_command_preserves_repetition_and_expected_nonzero_exit(self):
        state = self.state()
        state['goal_contract']['body']['initial_task']['validation_plan'] = [
            'Run `node --test greet.test.cjs` twice.',
            'Run `node invalid.cjs` and confirm exit code 2.']
        self.assertEqual(2, plan.repetitions(state)['node --test greet.test.cjs'])
        commands = plan.approved_commands(state)
        self.assertEqual(2, len(commands))
        self.assertIn('test "$autocode_plan_exit" -eq 2', commands[1])

    def test_current_contract_replacement_does_not_recover_historical_commands(self):
        state = self.state()
        old = state['goal_contract']['body']['initial_task']
        state['goal_contract']['body']['initial_task'] = {
            'acceptance_criteria': ['AC1'], 'validation_plan': ['node --test replacement.cjs']}
        state['completed_tasks'] = [old]
        self.assertEqual(['node --test replacement.cjs'], plan.launch_commands(state))


if __name__ == '__main__':
    unittest.main()
