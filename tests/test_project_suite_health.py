"""Cross-run and cross-worktree project suite-health persistence (#2942)."""

from __future__ import annotations

import shlex
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import autocode_project_suite_health as project_suite_health  # noqa: E402
import autocode_regression as regression  # noqa: E402
import autocode_util as util  # noqa: E402
import autocode_verify as verify  # noqa: E402
import autocode_workspaces as workspaces  # noqa: E402

from tests.test_verify import Project, git, isolated_python  # noqa: E402


class ProjectSuiteHealthTests(unittest.TestCase):
    def setUp(self):
        self.python = isolated_python(self)
        self.command = f"{shlex.quote(self.python)} -m unittest -v"
        self.framework = verify.command_framework(self.command)

    def _baseline_result(
        self,
        base: str,
        directory: Path,
        *,
        health: str = "passing",
        passed: list[str] | None = None,
        failed: list[str] | None = None,
        timed_out: bool = False,
        exit_code: int = 0,
        results_expected: bool = True,
        results_format: str | None = None,
    ) -> dict:
        log = Path(directory).resolve() / "baseline" / "suite-on-base.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("base suite log\n", encoding="utf-8")
        p_list = ["tests.test_app.AppTests.test_ok"] if passed is None else list(passed)
        f_list = [] if failed is None else list(failed)
        return {
            "base": base,
            "command": self.command,
            "health": health,
            "receipt": {
                "command": self.command,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "error": "",
                "output": str(log),
                "output_sha256": util.file_hash(log),
                "results_expected": results_expected,
                **({"results_format": results_format} if results_format else {}),
                "results": {
                    "complete": not timed_out and results_expected,
                    "passed": p_list,
                    "failed": f_list,
                    "skipped": [],
                    "uncollected": [],
                    "total": len(p_list) + len(f_list),
                },
            },
        }

    def test_cross_run_and_cross_worktree_baseline_save_and_restore_and_git_cleanliness(self):
        for health, failed in (("passing", []), ("failing_tests", ["tests.test_app.AppTests.test_pre"])):
            with self.subTest(health=health):
                project = Project(
                    {
                        "app.py": "def val():\n    return 1\n",
                        "test_app.py": (
                            "import unittest\n\nclass AppTests(unittest.TestCase):\n    def test_ok(self): pass\n"
                        ),
                    }
                )
                self.addCleanup(project.close)
                wt_a = Path(workspaces.create(project.root, "task-a")["workspace"])
                wt_b = Path(workspaces.create(project.root, "task-b")["workspace"])
                run_a = project.root / ".autocode" / "runs" / "20261115-run-a"
                run_b = project.root / ".autocode" / "runs" / "20261115-run-b"

                def fake_baseline(workspace, base, directory, **kwargs):
                    _ = (workspace, kwargs)
                    return self._baseline_result(
                        base,
                        directory,
                        health=health,
                        failed=failed,
                        exit_code=1 if failed else 0,
                    )

                state_a: dict = {}
                state_b: dict = {}
                with mock.patch.object(verify, "baseline", side_effect=fake_baseline) as launcher:
                    first = regression._baseline(state_a, wt_a, run_a, project.base, self.framework, self.command, None)
                    self.assertEqual(1, launcher.call_count)
                    # Even after Run A's run_dir is completely deleted, Run B in worktree B restores
                    shutil.rmtree(run_a)
                    second = regression._baseline(
                        state_b, wt_b, run_b, project.base, self.framework, self.command, None
                    )
                    self.assertEqual(1, launcher.call_count, "worktree B must restore project baseline without rerun")

                self.assertEqual(health, first["health"])
                self.assertEqual(health, second["health"])
                self.assertEqual("project_baseline", state_b["regression_baseline"].get("source"))
                restored_log = Path(second["receipt"]["output"]).resolve()
                self.assertTrue(restored_log.is_relative_to(run_b.resolve()))
                self.assertEqual(first["receipt"]["output_sha256"], second["receipt"]["output_sha256"])
                self.assertEqual("", git(project.root, "status", "--porcelain", "--untracked-files=all"))
                self.assertEqual("", git(wt_a, "status", "--porcelain", "--untracked-files=all"))
                self.assertEqual("", git(wt_b, "status", "--porcelain", "--untracked-files=all"))

    def test_refreshes_on_base_command_or_runtime_change(self):
        project = Project(
            {
                "app.py": "x = 1\n",
                ".gitignore": "node_modules\n",
                "test_app.py": "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n",
            }
        )
        self.addCleanup(project.close)
        (project.root / "app.py").write_text("x = 2\n")
        git(project.root, "add", "app.py")
        git(project.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "second")
        base_b = git(project.root, "rev-parse", "HEAD")

        run_dir = project.root / ".autocode" / "runs" / "20261115-run-1"
        calls = []

        def fake_baseline(workspace, base, directory, **kwargs):
            cmd = kwargs.get("suite_command") or self.command
            calls.append((base, cmd))
            res = self._baseline_result(base, directory)
            res["command"] = cmd
            res["receipt"]["command"] = cmd
            return res

        with mock.patch.object(verify, "baseline", side_effect=fake_baseline):
            regression._baseline({}, project.root, run_dir, project.base, self.framework, self.command, project.root)
            # Changed base commit -> rerun
            regression._baseline({}, project.root, run_dir, base_b, self.framework, self.command, project.root)
            # Changed suite command -> rerun
            alt_cmd = f"{shlex.quote(self.python)} -m unittest discover -v"
            regression._baseline({}, project.root, run_dir, base_b, self.framework, alt_cmd, project.root)
            # Changed runtime dependencies -> rerun
            (project.root / "node_modules" / "dep").mkdir(parents=True)
            (project.root / "node_modules" / "dep" / "index.js").write_text("1\n")
            regression._baseline({}, project.root, run_dir, base_b, self.framework, alt_cmd, project.root)

        self.assertEqual(4, len(calls), calls)

    def test_ineligible_and_broken_baselines_are_never_shared(self):
        project = Project({"app.py": "x = 1\n", "test_app.py": "import unittest\n"})
        self.addCleanup(project.close)
        run_dir = project.root / ".autocode" / "runs" / "20261115-run-bad"
        bad_cases = [
            ("timeout", {"health": "timeout", "timed_out": True, "exit_code": 1}),
            ("broken", {"health": "broken", "exit_code": 2}),
            ("empty_only", {"health": "passing", "passed": [], "failed": []}),
            ("junit", {"results_format": "junit"}),
        ]
        for label, opts in bad_cases:
            with self.subTest(case=label):
                fake = self._baseline_result(project.base, run_dir / label, **opts)
                saved = project_suite_health.save_baseline(
                    project.root, "a" * 64, fake, (run_dir / label).resolve(), framework_name="unittest"
                )
                self.assertIsNone(saved, label)
        self.assertEqual([], project_suite_health.load(project.root)["document"]["baselines"])

        # base_patch, ignored_inputs, junit framework, or reuse_supported=False disable project cache eligibility
        self.assertFalse(
            project_suite_health.baseline_eligible(
                base_patch="/tmp/patch.diff",
                ignored_inputs=None,
                framework=self.framework,
                runtime_identity={"reuse_supported": True},
            )
        )
        self.assertFalse(
            project_suite_health.baseline_eligible(
                base_patch=None,
                ignored_inputs=SimpleNamespace(generated=["gen.py"], vendor=[], unverified=[]),
                framework=self.framework,
                runtime_identity={"reuse_supported": True},
            )
        )
        self.assertFalse(
            project_suite_health.baseline_eligible(
                base_patch=None,
                ignored_inputs=None,
                framework=SimpleNamespace(name="junit"),
                runtime_identity={"reuse_supported": True},
            )
        )
        self.assertFalse(
            project_suite_health.baseline_eligible(
                base_patch=None,
                ignored_inputs=None,
                framework=self.framework,
                runtime_identity={"reuse_supported": False},
            )
        )

    def test_tampered_bytes_or_symlink_fails_closed(self):
        project = Project({"app.py": "x = 1\n"})
        self.addCleanup(project.close)
        src = project.root / ".autocode" / "tmp-src"
        src.mkdir(parents=True, exist_ok=True)
        res = self._baseline_result(project.base, src)
        digest = "b" * 64
        row = project_suite_health.save_baseline(project.root, digest, res, src.resolve(), framework_name="unittest")
        self.assertIsNotNone(row)
        assert row is not None
        target = project.root / ".autocode" / "runs" / "restore-check"
        self.assertIsNotNone(project_suite_health.restore_baseline(project.root, digest, target))

        # Corrupt stored log bytes -> restore_baseline fails closed to None
        stored_log = (
            project.root / ".autocode" / "suite-health" / "baselines" / row["id"] / "baseline" / "suite-on-base.log"
        )
        stored_log.write_text("tampered\n", encoding="utf-8")
        self.assertIsNone(project_suite_health.restore_baseline(project.root, digest, target / "t1"))

        # Re-save cleanly then swap baseline dir with symlink -> fails closed to None
        row2 = project_suite_health.save_baseline(project.root, digest, res, src.resolve(), framework_name="unittest")
        assert row2 is not None
        entry_dir = project.root / ".autocode" / "suite-health" / "baselines" / row2["id"]
        decoy = project.root / ".autocode" / "decoy-baseline"
        shutil.move(str(entry_dir), str(decoy))
        entry_dir.symlink_to(decoy, target_is_directory=True)
        self.assertIsNone(project_suite_health.restore_baseline(project.root, digest, target / "t2"))

    def test_active_quarantine_invalidation_on_test_edit_config_edit_and_max_clean_bases(self):
        project = Project({"app.py": "x = 1\n"})
        self.addCleanup(project.close)
        test_id = "tests.test_flaky.FlakyTests.test_sometimes"
        project_suite_health.record_observations(
            project.root,
            base="base-0",
            command=self.command,
            framework_name="unittest",
            quarantine_rows=[
                {
                    "test": test_id,
                    "base_runs": ["pass", "fail", "pass"],
                    "candidate_runs": ["fail", "pass", "pass"],
                    "reason": "flaky on base too",
                }
            ],
        )

        # Unrelated product edit keeps quarantine active
        active = project_suite_health.active_quarantine(
            project.root, "unittest", self.command, "base-1", git_changed_files_fn=lambda a, b: {"app.py"}
        )
        self.assertIn(test_id, active)

        # Editing the test file, ancestor __init__, conftest (under pytest), or pyproject.toml invalidates it
        for path, fw in (
            ("tests/test_flaky.py", "unittest"),
            ("tests/__init__.py", "unittest"),
            ("pyproject.toml", "unittest"),
            ("sub/conftest.py", "pytest"),
        ):
            with self.subTest(changed_path=path, fw=fw):
                if fw == "pytest":
                    project_suite_health.record_observations(
                        project.root,
                        base="base-0",
                        command=f"{self.python} -m pytest",
                        framework_name="pytest",
                        quarantine_rows=[
                            {
                                "test": "tests/test_flaky.py::test_sometimes",
                                "base_runs": ["pass", "fail", "pass"],
                                "candidate_runs": ["fail", "pass", "pass"],
                                "reason": "flaky",
                            }
                        ],
                    )
                res = project_suite_health.active_quarantine(
                    project.root,
                    fw,
                    self.command if fw == "unittest" else f"{self.python} -m pytest",
                    "base-2",
                    git_changed_files_fn=lambda a, b, p=path: {p},
                )
                self.assertEqual({}, res)

        # Advancing MAX_QUARANTINE_BASES (10) clean base commits expires the entry on a new base
        for i in range(1, project_suite_health.MAX_QUARANTINE_BASES + 1):
            project_suite_health.record_observations(
                project.root,
                base=f"clean-base-{i}",
                command=self.command,
                framework_name="unittest",
                clean_passed_tests=[test_id],
            )
        expired = project_suite_health.active_quarantine(
            project.root, "unittest", self.command, "base-after-10", git_changed_files_fn=lambda a, b: {"app.py"}
        )
        self.assertEqual({}, expired)


if __name__ == "__main__":
    unittest.main()
