"""Explicit retry bounds retain ownership, batch binding and spent accounting."""

import copy
import tempfile
import unittest
from pathlib import Path

import autocode_util as util
import autocode_worker_limits as limits


class WorkerLimitPolicyTests(unittest.TestCase):
    def test_only_saved_operator_bounds_and_ownership_are_copied(self):
        child = {
            "roles": {"terra": {"model": "frozen", "reasoning_effort": "high"}},
            "limits": {"tool_timeout_seconds": 1800, "stage_timeout_seconds": 3600, "max_seconds": 100},
            "milestone_checkpoints": {"enabled": True, "max_seconds": 5400},
            "budget_origins": {"max_seconds": "runner_default"},
        }
        parent = {
            "roles": {"terra": {"model": "different", "reasoning_effort": "low"}},
            "limits": {"tool_timeout_seconds": 5400, "stage_timeout_seconds": 0, "max_seconds": 200},
            "milestone_checkpoints": {"enabled": False, "max_seconds": 21600},
            "budget_origins": {key: "user_explicit" for key in (*[row[2] for row in limits.BOUNDS], "max_seconds")},
        }
        originals = copy.deepcopy((child, parent))
        expected = copy.deepcopy(child)
        expected["limits"].update(tool_timeout_seconds=5400, stage_timeout_seconds=0)
        expected["milestone_checkpoints"]["max_seconds"] = 21600
        expected["budget_origins"].update({row[2]: "user_explicit" for row in limits.BOUNDS})
        self.assertEqual(expected, limits.updated_settings(child, parent))
        self.assertEqual(originals, (child, parent))

    def test_missing_default_unknown_and_delegated_bounds_are_unchanged(self):
        child = {"limits": {"tool_timeout_seconds": 1800}, "milestone_checkpoints": {"max_seconds": 5400}}
        for origin in (None, "runner_default", "resolver_delegated", "unknown", "user_explicit"):
            with self.subTest(origin=origin):
                parent = {"budget_origins": {"tool_timeout_seconds": origin}}
                if origin != "user_explicit":
                    parent["limits"] = {"tool_timeout_seconds": 0}
                self.assertEqual(child, limits.updated_settings(child, parent))

    def test_zero_milestone_budget_keeps_its_disabled_meaning(self):
        parent = {
            "milestone_checkpoints": {"max_seconds": 0},
            "budget_origins": {"milestone_max_seconds": "user_explicit"},
        }
        self.assertEqual(
            {"milestone_checkpoints": {"max_seconds": 0}, "budget_origins": parent["budget_origins"]},
            limits.updated_settings({"milestone_checkpoints": {"max_seconds": 5400}}, parent),
        )


class SelectedWorkerLimitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / ".autocode" / "runs" / "parent"
        self.run.mkdir(parents=True)
        goal = {"task_id": "goal", "revision": 1, "body": {}}
        goal.update(hash=util.digest(goal), approval_status="approved")
        self.parent = {
            "workspace": str(self.root),
            "goal_contract": goal,
            "settings": {
                "limits": {"tool_timeout_seconds": 5400, "stage_timeout_seconds": 7200},
                "milestone_checkpoints": {"max_seconds": 21600},
                "budget_origins": {row[2]: "user_explicit" for row in limits.BOUNDS},
            },
            "orchestration_batch": {"id": "batch", "status": "BUILDING", "contract_hash": goal["hash"], "workers": []},
        }
        self.children = {}
        for mid in ("M1", "M2", "M3"):
            workspace = self.root / "builders" / mid
            directory = workspace / ".autocode" / "runs" / "child"
            directory.mkdir(parents=True)
            row = {"milestone_id": mid, "workspace": str(workspace), "run_dir": str(directory), "task": {"id": mid}}
            self.parent["orchestration_batch"]["workers"].append(row)
            child = {
                "workspace": str(workspace),
                "parent_run": str(self.run),
                "parent_batch": "batch",
                "goal_contract": copy.deepcopy(goal),
                "current_task": copy.deepcopy(row["task"]),
                "status": "PAUSED_MILESTONE_BUDGET",
                "milestone_progress": {mid: {"seconds": 5400}},
                "automatic_recoveries_since_resume": 3,
                "stages": [{"tool_timeout_seconds": 1800, "stage_timeout_seconds": 3600}],
                "settings": {
                    "roles": {"terra": {"model": "frozen"}},
                    "limits": {"tool_timeout_seconds": 1800, "stage_timeout_seconds": 3600},
                    "milestone_checkpoints": {"max_seconds": 5400},
                },
            }
            path = directory / "state.json"
            util.atomic_json(path, child)
            self.children[mid] = path
        self.rows = self.parent["orchestration_batch"]["workers"]

    def test_only_selected_children_change_without_resetting_any_accounting_or_history(self):
        before = {mid: util.read(path) for mid, path in self.children.items()}
        limits.assign_selected(
            self.parent, self.run, [self.rows[0], self.rows[0], self.rows[1]], persist=util.atomic_json
        )
        for mid, path in self.children.items():
            expected = copy.deepcopy(before[mid])
            if mid != "M3":
                expected["settings"] = limits.updated_settings(expected["settings"], self.parent["settings"])
            self.assertEqual(expected, util.read(path))

    def test_a_stale_later_child_rejects_every_update(self):
        for key, value in (
            ("parent_batch", "other"),
            ("parent_run", "/other"),
            ("workspace", "/other"),
            ("current_task", {"id": "other"}),
        ):
            with self.subTest(key=key):
                original = util.read(self.children["M2"])
                changed = {**original, key: value}
                util.atomic_json(self.children["M2"], changed)
                before = {mid: path.read_bytes() for mid, path in self.children.items()}
                with self.assertRaisesRegex(ValueError, "current parent batch and task"):
                    limits.assign_selected(self.parent, self.run, self.rows[:2], persist=util.atomic_json)
                self.assertEqual(before, {mid: path.read_bytes() for mid, path in self.children.items()})
                util.atomic_json(self.children["M2"], original)

    def test_a_busy_later_child_rejects_every_update(self):
        before = {mid: path.read_bytes() for mid, path in self.children.items()}
        with util.workspace_lock(Path(self.rows[1]["workspace"])):
            with self.assertRaisesRegex(util.Paused, "workspace lock"):
                limits.assign_selected(self.parent, self.run, self.rows[:2], persist=util.atomic_json)
        self.assertEqual(before, {mid: path.read_bytes() for mid, path in self.children.items()})
