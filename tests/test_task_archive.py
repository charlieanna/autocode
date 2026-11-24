"""The one rule for moving a build task into the run's task_archive."""

import unittest

import autocode_task_archive as task_archive


class ArchiveCurrentTests(unittest.TestCase):
    def test_the_current_task_moves_to_the_archive(self):
        state = {"current_task": {"id": "t2"}, "task_archive": [{"id": "t1"}]}
        self.assertTrue(task_archive.archive_current(state))
        self.assertEqual([{"id": "t1"}, {"id": "t2"}], state["task_archive"])
        self.assertNotIn("current_task", state)

    def test_keep_archives_a_copy_and_leaves_the_task_current(self):
        state = {"current_task": {"id": "t1", "paths": ["a.py"]}}
        self.assertTrue(task_archive.archive_current(state, keep=True))
        state["current_task"]["paths"].append("b.py")
        self.assertEqual([{"id": "t1", "paths": ["a.py"]}], state["task_archive"])

    def test_no_task_or_an_empty_one_is_never_archived(self):
        for state in ({}, {"current_task": None}, {"current_task": {}}):
            with self.subTest(state=state):
                before = dict(state)
                self.assertFalse(task_archive.archive_current(state))
                self.assertEqual(before, state)


if __name__ == "__main__":
    unittest.main()
