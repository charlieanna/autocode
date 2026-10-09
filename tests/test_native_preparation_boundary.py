"""Verification caller ownership boundaries after the PR776/native merge.

These are controlled caller checks, not native execution or model qualification.
The original scratch_run and real Supply codec run; copy/worker/command seams
are controlled so refusal and exception custody can be checked without targets.
"""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import autocode_launch_inputs as launch_inputs
import autocode_native_check_execution as native_checks
import autocode_util as util
import autocode_verify as verify


class NativePreparationBoundaryTests(unittest.TestCase):
    def forbidden_setup(self, stack):
        """Observe both copy owners without allowing Git, workers or removal."""
        names = ('_git', 'make_tree', 'prepare_scratch_tree', 'remove_tree')
        observed = {name: stack.enter_context(patch.object(verify, name)) for name in names}
        observed['native_copy'] = stack.enter_context(patch.object(native_checks.NativeCheckExecution, 'copy'))
        observed['worker'] = stack.enter_context(patch.object(verify.preparation, 'launch'))
        return observed

    def test_admitted_preparation_and_concrete_native_owner_refuse_before_any_copy(self):
        execution = object.__new__(native_checks.NativeCheckExecution)
        with ExitStack() as stack:
            observed = self.forbidden_setup(stack)
            stack.enter_context(patch.object(verify.preparation, 'active', return_value=True))
            with self.assertRaisesRegex(util.Paused, 'cannot share scheduled preparation ownership'):
                verify.scratch_run('/controlled/workspace', '/controlled/output',
                                   command='printf checked', native_execution=execution)
            for call in observed.values():
                call.assert_not_called()

    def test_wrong_native_owner_never_falls_through_to_admitted_worker(self):
        with ExitStack() as stack:
            observed = self.forbidden_setup(stack)
            active = stack.enter_context(patch.object(verify.preparation, 'active', return_value=True))
            with self.assertRaisesRegex(util.Paused, 'Original concrete native check owner required'):
                verify.scratch_run('/controlled/workspace', '/controlled/output',
                                   command='printf checked', native_execution=object())
            active.assert_not_called()
            for call in observed.values():
                call.assert_not_called()

    def test_ordinary_admitted_worker_receives_exact_nonempty_supply_transport(self):
        workspace, output = Path('/controlled/workspace'), Path('/controlled/output')
        inputs = launch_inputs.Supply(workspace, output / 'launch-inputs',
            {'_version.py': ['a' * 64, 0o644]}, {'vendor/library.py': ['b' * 64, 0o644]},
            [], ['controlled input inventory'], recorded=True)
        receipt = {'error': 'controlled worker result; no actual command receipt'}
        with ExitStack() as stack:
            observed = self.forbidden_setup(stack)
            stack.enter_context(patch.object(verify.preparation, 'active', return_value=True))
            observed['worker'].return_value = receipt
            discovery = stack.enter_context(patch.object(verify, 'test_environment'))
            returned = verify.scratch_run(workspace, output, command='python3 -m unittest -v test_app',
                                          timeout=23, ignored_inputs=inputs)
            self.assertIs(returned, receipt)
            observed['worker'].assert_called_once()
            args, options = observed['worker'].call_args
            self.assertEqual(args[:3], ('execute', workspace, output))
            parameters = args[3]
            self.assertEqual(parameters['ignored_inputs'], inputs.to_transport())
            self.assertEqual(parameters['command'], 'python3 -m unittest -v test_app')
            self.assertEqual(parameters['timeout'], 23)
            self.assertEqual(options['timeout'], 23)
            self.assertEqual(options['env']['PYTHONDONTWRITEBYTECODE'], '1')
            discovery.assert_not_called()
            for name, call in observed.items():
                if name != 'worker':
                    call.assert_not_called()

    def test_native_uncertain_command_exits_original_scope_with_exception_and_retains_copy(self):
        execution = object.__new__(native_checks.NativeCheckExecution)
        error = verify.command_receipt.OwnershipUncertain('controlled uncertain command custody')
        with tempfile.TemporaryDirectory(prefix='native-preparation-boundary-') as directory:
            root = Path(directory)
            tree = root / 'retained-copy'; tree.mkdir()
            retained = tree / 'source.txt'; retained.write_bytes(b'original retained copy\n')
            native_scope = MagicMock()
            native_scope.__enter__.return_value = tree
            native_scope.__exit__.return_value = False
            with ExitStack() as stack:
                observed = self.forbidden_setup(stack)
                observed['native_copy'].return_value = native_scope
                stack.enter_context(patch.object(verify.preparation, 'active', return_value=False))
                stack.enter_context(patch.object(verify, 'command_framework', return_value=None))
                command = stack.enter_context(patch.object(verify, 'run_command', side_effect=error))
                with self.assertRaises(verify.command_receipt.OwnershipUncertain) as raised:
                    verify.scratch_run(root / 'workspace', root / 'output',
                                       command='printf checked', native_execution=execution)
                self.assertIs(raised.exception, error)
                command.assert_called_once()
                self.assertIs(command.call_args.kwargs['native_execution'], execution)
                native_scope.__enter__.assert_called_once()
                native_scope.__exit__.assert_called_once()
                kind, actual, traceback = native_scope.__exit__.call_args.args
                self.assertIs(kind, type(error)); self.assertIs(actual, error)
                self.assertIsNotNone(traceback)
                for name, call in observed.items():
                    if name != 'native_copy':
                        call.assert_not_called()
            self.assertEqual(retained.read_bytes(), b'original retained copy\n')


if __name__ == '__main__':
    unittest.main()
