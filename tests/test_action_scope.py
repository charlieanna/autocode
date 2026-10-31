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
