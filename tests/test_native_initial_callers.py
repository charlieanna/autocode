"""Caller ordering controls; controlled provider seams are not native image proof."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
import autocode as runner
import autocode_native_check_execution as checks
import autocode_util as util
from tests.test_native_check_execution import _ControlledNativeCheckFixture


class OriginalCheckFailureTests(_ControlledNativeCheckFixture, unittest.TestCase):
    def setUp(self):
        self.prepare()
        self.approve()

    def assert_failed_result(self, actual, reason):
        original = {'command': self.command, 'output': 'retained-original-output.log', **actual}
        with patch.object(checks.supervision, 'run', return_value=original), \
                patch.object(self.owner, 'finish_check', wraps=self.owner.finish_check) as finish:
            with self.assertRaisesRegex(util.Paused, reason):
                self.run_check(self.command)
            finish.assert_not_called()
        self.assertFalse(any(row['kind']=='check-finished' for row in self.owner._rows()))
        self.assertEqual({key:original[key] for key in actual}, actual)

    def test_unreleased_admission_error_is_retained_without_fabricated_finish(self):
        self.assert_failed_result({'exit_code': None, 'timed_out': False,
            'error': 'original keeper admission refused before release'},
            'original keeper admission refused before release')

    def test_zero_exit_with_ownership_error_cannot_finish(self):
        self.assert_failed_result({'exit_code': 0, 'timed_out': False,
            'error': 'original ownership collection failed'}, 'original ownership collection failed')

    def test_original_timeout_cannot_finish(self):
        self.assert_failed_result({'exit_code': None, 'timed_out': True}, 'original deadline expired')

    def test_missing_collected_exit_cannot_finish(self):
        self.assert_failed_result({'exit_code': None, 'timed_out': False}, 'no collected target exit')

    def test_boolean_is_not_a_collected_integer_exit(self):
        self.assert_failed_result({'exit_code': False, 'timed_out': False}, 'no collected target exit')

    def test_foreign_review_cannot_enable_checks_from_approval_alone(self):
        with self.assertRaisesRegex(util.Paused,'missing original independent review launch'):
            checks.NativeCheckExecution(self.context,self.state,persist=self.sink.write,
                review_record={'stage':'sol','role':'sol','source_revision':self.raw['revision'],'exit_code':0})


class OriginalStageCallerTests(unittest.TestCase):
    def test_actual_commit_threads_native_and_observes_only_after_publication(self):
        state, record, value = {'marker':'original'}, {'stage':'sol'}, {}
        order=[]
        native=SimpleNamespace(review_progress=Mock(side_effect=lambda *args: order.append('observe') or {'marker':'observed'}))
        def commit(current,run_dir,stage,apply,publish,**kw):
            order.append('apply');apply({'marker':'applied'});order.append('publish');publish({'marker':'saved'});order.append('durable-return')
        def boundary(*args,**kw): order.append('parent-'+args[1]['marker'])
        with patch.object(runner.result_application,'commit',side_effect=commit), \
             patch.object(runner,'apply_result') as apply, \
             patch.object(runner,'commit_boundary_candidate',side_effect=boundary):
            runner.commit_stage_result(state,'sol',value,record,Path('/controlled'),Path('/controlled/run'),native=native)
        self.assertIs(apply.call_args.kwargs['native'],native)
        self.assertEqual(order,['apply','publish','parent-saved','durable-return','observe','parent-observed'])
        self.assertIs(native.review_progress.call_args.args[2],runner.milestones.observe_validation)

    def test_failed_parent_commit_never_observes_milestone(self):
        native=SimpleNamespace(review_progress=Mock())
        with patch.object(runner.result_application,'commit',side_effect=util.Paused('HELD','original publication failed')):
            with self.assertRaisesRegex(util.Paused,'original publication failed'):
                runner.commit_stage_result({},'sol',{}, {},Path('/controlled'),Path('/controlled/run'),native=native)
        native.review_progress.assert_not_called()

    def test_actual_apply_result_uses_invocation_local_runtime(self):
        calls=SimpleNamespace(sink=SimpleNamespace(write=lambda *a,**k: None))
        with patch.object(runner.autopilot,'apply_result',return_value='applied') as apply, \
             patch.object(runner.job_failure,'completed'):
            self.assertEqual(runner.apply_result({},'sol',{}, {},Path('/controlled'),Path('/controlled/run'),native=calls),'applied')
        self.assertIs(apply.call_args.args[0].native_calls,calls)
        self.assertFalse(hasattr(runner,'native_calls'))


class CompletionResumeControls(unittest.TestCase):
    """Decision admission permits correction; terminal preparation stays strict."""
    def caller(self):
        import autocode_native_calls as calls
        native = object.__new__(calls.PlanningCalls)
        native.sink = SimpleNamespace(require_ready=Mock())
        native.require_initial_input = Mock()
        native.snapshot = Mock(return_value={'revision': 'current'})
        native.context = SimpleNamespace(factory=SimpleNamespace(
            require_final_integration=Mock(), decision_review_proof=Mock(), require_resolver_request=Mock(),
            protection=SimpleNamespace(initial_validate_authority=Mock())))
        native.current_review = Mock(return_value={'kind': 'rejections', 'sha256': 'original'})
        return native

    def test_completion_decision_resume_replays_actual_current_review_or_rejection(self):
        native = self.caller()
        state = {'current_task': {'kind': 'implement'}, 'next_stage': 'astra_review'}
        native.require_supported_action(SimpleNamespace(run_dir='original'), state)
        native.current_review.assert_called_once_with(state, {'revision': 'current'})
        native.context.factory.decision_review_proof.assert_called_once_with(state,
            snapshot={'revision': 'current'}, selector={'kind': 'rejections', 'sha256': 'original'})
        native.context.factory.require_final_integration.assert_not_called()

    def test_resolver_requires_genuine_ordinary_request_after_current_evidence(self):
        native = self.caller()
        state = {'current_task': {'kind': 'implement'}, 'next_stage': 'astra_resolve'}
        native.context.factory.require_resolver_request.side_effect = util.Paused('HELD', 'missing genuine request')
        with self.assertRaisesRegex(util.Paused, 'missing genuine request'):
            native.require_supported_action(SimpleNamespace(run_dir='original'), state)
        native.context.factory.decision_review_proof.assert_called_once()
        native.context.factory.require_resolver_request.assert_called_once_with(state, snapshot={'revision': 'current'})

    def test_approval_alone_or_uncommitted_evidence_cannot_resume_completion(self):
        native = self.caller()
        native.current_review.side_effect = util.Paused('HELD', 'missing current committed review')
        with self.assertRaisesRegex(util.Paused, 'missing current committed review'):
            native.require_supported_action(SimpleNamespace(run_dir='original'),
                {'current_task': {'kind': 'implement'}, 'next_stage': 'astra_review'})
        native.context.factory.decision_review_proof.assert_not_called()

    def test_terminal_completion_requires_fresh_all_case_integration_even_after_decision(self):
        native = self.caller()
        state = {'current_task': {'kind': 'implement'}, 'next_stage': 'astra_review'}
        native.context.factory.require_final_integration.side_effect = util.Paused('HELD', 'missing all-case integration')
        with self.assertRaisesRegex(util.Paused, 'missing all-case integration'):
            native.completion(state, {'revision': 'current'}, {}, {'status': 'COMPLETE'})
        native.context.factory.require_final_integration.assert_called_once_with(state,
            snapshot={'revision': 'current'}, review_selector={'kind': 'rejections', 'sha256': 'original'})

    def test_readonly_writer_and_later_routes_remain_held_before_proof(self):
        for kind, stage in (('validate', 'terra'), ('validate', 'astra_checkpoint')):
            native = self.caller()
            with self.subTest(kind=kind, stage=stage), self.assertRaises(util.Paused):
                native.require_supported_action(SimpleNamespace(run_dir='original'),
                    {'current_task': {'kind': kind}, 'next_stage': stage})
            native.current_review.assert_not_called()
            native.context.factory.protection.initial_validate_authority.assert_not_called()
