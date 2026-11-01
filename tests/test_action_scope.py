"""Exact linked assignment recovery preserves human gates."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import autocode_action_scope as action


class AssignmentActionTests(unittest.TestCase):
    def test_runtime_stale_scope_receipt_is_refreshed_before_retirement(self):
        state = {"pending_questions": [{"id": "workspace-scope-current"}]}
        runner = Mock()
        published = {"scope": "clarification"}
        with patch.object(action.assignment, "current", return_value={"identity": "current"}), \
             patch.object(action.human, "current", return_value=None), \
             patch.object(action.human, "refresh_assigned_scope", return_value=published) as refresh, \
             patch.object(action.human, "supersede_assigned_scope") as supersede:
            self.assertEqual(0, action.apply(runner, SimpleNamespace(assign_linked_scope="current"), None,
                                            state, Path("state.json"), Path("run"), Path("workspace"), None))
        refresh.assert_called_once_with(state)
        supersede.assert_called_once_with(state, published, [], {"identity": "current"})
        runner.write_json.assert_called_once()

    def test_unrefreshable_or_other_human_gate_is_not_released(self):
        for published in (None, {"scope": "goal_approval"}, {"scope": "permission"}):
            with self.subTest(published=published):
                state = {"pending_questions": [{"id": "workspace-scope-current"}]}
                runner = Mock()
                with patch.object(action.assignment, "current", return_value={"identity": "current"}), \
                     patch.object(action.human, "current", return_value=None), \
                     patch.object(action.human, "refresh_assigned_scope", return_value=published), \
                     patch.object(action.human, "supersede_assigned_scope") as supersede:
                    with self.assertRaisesRegex(ValueError, "cannot release"):
                        action.apply(runner, SimpleNamespace(assign_linked_scope="current"), None,
                                     state, Path("state.json"), Path("run"), Path("workspace"), None)
                supersede.assert_not_called()
                runner.write_json.assert_not_called()

    def test_exact_assignment_reaches_its_handler_without_generic_pause_cleanup(self):
        import copy
        import autocode as runner
        import autocode_action_authority as authority
        import autocode_args
        from autocode_run_actions import Decided

        parser = autocode_args.build_parser(None, runner.DEFAULT_ROLE_MODELS)
        args = parser.parse_args(["--assign-linked-scope", "current"])
        state = {"status": "PAUSED_INVALID_OUTPUT", "settings": {},
                 "pending_questions": [{"id": "workspace-scope-current"}]}
        before = copy.deepcopy(state)
        decided = Decided()
        with patch.object(authority.resolver_human, "current", return_value=None), \
             patch.object(authority.resolver_human, "response_holds_current_frontier", return_value=False), \
             patch.object(authority.operational_information, "reevaluate", return_value=None), \
             patch.object(authority.resolver_runtime, "record_operational_exhaustion", return_value=False) as investigate:
            result = authority.settle(Mock(), args, parser, state, Path("state.json"), Path("run"),
                                      Path("workspace"), decided)
        self.assertIsNone(result)
        self.assertEqual(before, state)
        self.assertTrue(decided.decision_action)
        investigate.assert_not_called()
