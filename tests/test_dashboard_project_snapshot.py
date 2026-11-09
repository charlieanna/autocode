"""A read after a project mutation must not inherit an older inventory."""

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dashboard.dashboard_project_controls import ProjectRemovalMixin


class ReleaseOnWait(threading.Condition):
    def __init__(self, release, spurious=False):
        super().__init__()
        self.release = release
        self.spurious = spurious

    def wait(self, timeout=None):
        if self.spurious:
            self.spurious = False
            return True
        # Release the first read only once the second has joined it. This
        # forces the race without sleeps or relying on thread scheduling.
        self.release.set()
        return super().wait(timeout)


class InventorySource:
    def __init__(self, workspace):
        self.workspaces = [workspace]

    def discover(self):
        return []


class ControlledDashboard(ProjectRemovalMixin, InventorySource):
    def __init__(self, workspace, store, spurious=False):
        super().__init__(workspace, project_store_root=store)
        self.captured = threading.Event()
        self.release = threading.Event()
        self._dashboard_snapshot_condition = ReleaseOnWait(self.release, spurious)
        self.reads = 0

    def _build_dashboard_snapshot(self):
        # Only inventory enumeration is controlled. The public removal and
        # snapshot methods and the on-disk project preference store are real.
        removed = self.removed_projects()
        snapshot = {"removed_projects": removed, "workspaces": [] if removed else self.workspaces}
        self.reads += 1
        if self.reads == 1:
            self.captured.set()
            if not self.release.wait(5):
                raise AssertionError("The second reader did not join the first")
        else:
            self.release.set()
        return snapshot


class DashboardProjectSnapshotTests(unittest.TestCase):
    def overlapping_reads(self, initial_removed, action, *, spurious=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / "project"
            workspace.mkdir()
            console = ControlledDashboard(workspace, root / "preferences", spurious)
            if initial_removed:
                console.project_action({"workspace": str(workspace), "action": "remove"})
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(console.dashboard_snapshot)
                try:
                    self.assertTrue(console.captured.wait(5), "First read must capture the old inventory")
                    if action:
                        console.project_action({"workspace": str(workspace), "action": action})
                    second = pool.submit(console.dashboard_snapshot)
                    current = second.result(timeout=5)
                    previous = first.result(timeout=5)
                finally:
                    console.release.set()
            return previous, current, console.reads

    def test_read_after_restore_does_not_join_a_removed_inventory(self):
        previous, current, reads = self.overlapping_reads(True, "restore")
        self.assertEqual(1, len(previous["removed_projects"]))
        self.assertEqual([], current["removed_projects"])
        self.assertEqual(1, len(current["workspaces"]))
        self.assertEqual(2, reads)

    def test_read_after_remove_does_not_join_a_visible_inventory(self):
        previous, current, reads = self.overlapping_reads(False, "remove")
        self.assertEqual([], previous["removed_projects"])
        self.assertEqual(1, len(current["removed_projects"]))
        self.assertEqual([], current["workspaces"])
        self.assertEqual(2, reads)

    def test_overlapping_reads_without_a_mutation_still_share_the_inventory(self):
        for removed in (False, True):
            with self.subTest(removed=removed):
                previous, current, reads = self.overlapping_reads(removed, None)
                self.assertEqual(previous, current)
                self.assertEqual(1, reads)

    def test_a_spurious_wakeup_does_not_start_a_competing_inventory_read(self):
        previous, current, reads = self.overlapping_reads(False, None, spurious=True)
        self.assertEqual(previous, current)
        self.assertEqual(1, reads)


if __name__ == "__main__":
    unittest.main()
