"""Model-free fix verification (autocode_verify).

These tests execute real test suites in scratch Git worktrees; they never launch
a provider. Each negative control is a way a candidate could look fixed without
being fixed, and each must be rejected by execution, not by reading a report.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import autocode_launch_inputs as launch_inputs  # noqa: E402
import autocode_regression as regression  # noqa: E402
import autocode_verification_schedule as schedule  # noqa: E402
import autocode_verify as verify  # noqa: E402
import autocode_workspaces as workspaces  # noqa: E402
import scenario_references as references  # noqa: E402
import task_scenarios as scenarios  # noqa: E402

REFERENCE = references.BUGFIX_REFERENCE
SEED = scenarios.BUGFIX_SEED
# Existing behavior a project keeps out of Git (an ignore rule hides it from the pinned tree).
LEGACY_PROGRAM = "def answer():\n    return 42\n"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def project_file(project, path):
    return (project.root / path).read_text()


def isolated_python(test_case, *, directory=None):
    """A real no-pip stdlib runtime without the mutable controller editable install."""
    import venv

    temporary = tempfile.TemporaryDirectory(prefix="verification-command-runtime-", dir=directory)
    test_case.addCleanup(temporary.cleanup)
    root = Path(temporary.name)
    venv.EnvBuilder(with_pip=False).create(root)
    return str(root / "bin" / "python")


def isolated_python_env(test_case, env, *, directory):
    """Select the stdlib test runtime on PATH, not the absolute controller command."""
    python = isolated_python(test_case, directory=directory)
    return {**env, "PATH": f"{Path(python).parent}{os.pathsep}{env['PATH']}"}


class Project:
    """A committed BUGFIX-01 seed; tests overlay candidate files on the working tree."""

    def __init__(self, files=SEED):
        self.temp = tempfile.TemporaryDirectory(prefix="fix-verify-")
        self.root = Path(self.temp.name).resolve() / "project"
        self.root.mkdir()
        references.write(files, self.root)
        git(self.root, "init", "-q")
        # A commit starts `git maintenance run --auto --detach`. On CI's Git 2.55 it repacks once two
        # loose objects share the objects/17 shard, writing packs under .git while cleanup removes it
        # ("Directory not empty"). docs/bugs/git-background-repack-cleanup-race.md
        git(self.root, "config", "maintenance.auto", "false")
        git(self.root, "config", "gc.auto", "0")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.evidence = Path(self.temp.name) / "evidence"

    def write(self, files):
        references.write(files, self.root)

    def verify(self, **options):
        framework = verify.detect_framework(self.root)
        suite = options.pop("suite_command", None) or framework.suite
        base_suite = verify.baseline(
            self.root, self.base, self.evidence, framework=framework, suite_command=suite, timeout=120
        )
        return verify.verify(
            self.root, self.base, self.evidence, framework=framework, base_suite=base_suite, timeout=120, **options
        )

    def close(self):
        self.temp.cleanup()


class ProjectFixtureTests(unittest.TestCase):
    def test_a_commit_starts_no_background_maintenance(self):
        project = Project()
        self.addCleanup(project.close)
        trace = Path(project.temp.name) / "trace2.json"
        (project.root / "extra.txt").write_text("extra\n")
        git(project.root, "add", "extra.txt")
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "extra"],
            cwd=project.root,
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_TRACE2_EVENT": str(trace)},
        )
        children = [
            event["argv"]
            for event in map(json.loads, trace.read_text().splitlines())
            if event.get("event") == "child_start"
        ]
        self.assertEqual([], [argv for argv in children if {"maintenance", "gc"} & set(argv)], children)


class ComponentCollectionTests(unittest.TestCase):
    def test_package_markers_and_comment_mentions_do_not_hide_go_or_node_suites(self):
        for marker in ("", "# load_tests and unittest.TestCase are not declarations\n", "VALUE = 'load_tests'\n"):
            for files, expected in (
                ({"go.mod": "module sample\ngo 1.22\n", "lib_test.go": "package sample\n"}, "go"),
                ({"package.json": json.dumps({"scripts": {"test": "node --test"}}), "app.test.js": ""}, "node"),
            ):
                with self.subTest(marker=marker, framework=expected):
                    project = Project({"components/lib/__init__.py": marker, **files})
                    self.addCleanup(project.close)
                    framework = verify.detect_framework(project.root, python=sys.executable)
                    self.assertEqual(expected, framework.name)
                    self.assertNotIn("autocode_component_tests.py", framework.suite)

    def collect(self, files):
        project = Project(files)
        self.addCleanup(project.close)
        framework = verify.detect_framework(project.root, python=sys.executable)
        self.assertIsNotNone(framework)
        receipt = verify.run_suite(framework, framework.suite, project.root, project.evidence, "components", timeout=30)
        return framework, receipt, Path(receipt["output"]).read_text()

    def test_root_namespace_relative_import_hyphen_and_owner_hook_are_all_collected(self):
        framework, receipt, text = self.collect(
            {
                "test_root.py": "import unittest\nclass Root(unittest.TestCase):\n def test_root(self): pass\n",
                "components/api/value.py": "VALUE = 17\n",
                "components/api/tests/test_value.py": "import unittest\nfrom ..value import VALUE\n"
                "class Value(unittest.TestCase):\n def test_relative(self): self.assertEqual(17, VALUE)\n",
                "components/store/__init__.py": "import unittest\nclass Hook(unittest.TestCase):\n"
                " def test_hook(self): self.fail('package hook reached')\n"
                "def load_tests(loader, tests, pattern): return loader.loadTestsFromTestCase(Hook)\n",
                "components/store/tests/test_skipped.py": "raise AssertionError('hook must own collection')\n",
                "components/link-service/tests/value_test.py": "import unittest\nclass Hyphen(unittest.TestCase):\n"
                " def test_hyphen(self): self.fail('named failure')\n",
            }
        )
        self.assertEqual(1, receipt["exit_code"], text)
        self.assertIn("test_relative", text)
        self.assertIn("test_root", text)
        self.assertIn("package hook reached", text)
        self.assertNotIn("hook must own collection", text)
        self.assertEqual(4, receipt["results"]["total"])
        self.assertTrue(receipt["results"]["complete"])
        self.assertTrue(any("components.link-service" in name for name in receipt["results"]["failed"]))
        self.assertEqual(framework.suite, framework.targeted(["components/store/tests/test_skipped.py"]))
        self.assertIsNone(framework.targeted([]))

    def test_hook_only_repository_and_root_delegation_cannot_be_silently_skipped(self):
        hook = (
            "import unittest\nclass Hook(unittest.TestCase):\n def test_hook(self): self.fail('hook reached')\n"
            "def load_tests(loader, tests, pattern): return loader.loadTestsFromTestCase(Hook)\n"
        )
        for owner in ("components", "components/store"):
            with self.subTest(owner=owner):
                _, receipt, text = self.collect({"components/__init__.py": "", owner + "/__init__.py": hook})
                self.assertEqual(1, receipt["exit_code"], text)
                self.assertIn("hook reached", text)
                self.assertTrue(receipt["results"]["failed"])
        _, receipt, text = self.collect(
            {
                "components/__init__.py": "from pathlib import Path\ndef load_tests(loader, tests, pattern):\n"
                " return loader.discover(str(Path(__file__).parent), pattern=pattern, top_level_dir=str(Path.cwd()))\n",
                "components/api/test_ok.py": "import unittest\nclass Fine(unittest.TestCase):\n def test_ok(self): pass\n",
                "components/store/__init__.py": hook,
            }
        )
        self.assertEqual(1, receipt["exit_code"], text)
        self.assertEqual(2, receipt["results"]["total"])
        self.assertEqual(1, text.count("test_ok ("))

    def test_components_preserve_configured_pytest(self):
        project = Project(
            {
                "pytest.ini": "[pytest]\naddopts = --strict-markers\n",
                "components/api/tests/test_value.py": "def test_value(): assert True\n",
            }
        )
        self.addCleanup(project.close)
        with mock.patch.object(verify, "_python_can_import", return_value=True):
            framework = verify.detect_framework(project.root, python=sys.executable)
        self.assertEqual("pytest", framework.name)
        self.assertNotIn(" -c ", framework.suite)


class PreservationEvidenceCase(unittest.TestCase):
    """Suite preservation needs actual base coverage, not matching runner errors."""

    def check_suite(self, legacy, *, new_behavior=False):
        project = Project({"calc.py": "VALUE = 'old'\n", **legacy})
        self.addCleanup(project.close)
        project.write(
            {
                "calc.py": "VALUE = 'new'\n",
                "regression/test_change.py": "import unittest\nfrom calc import VALUE\n\n"
                "class Change(unittest.TestCase):\n"
                "    def test_new_value(self):\n"
                "        self.assertEqual('new', VALUE)\n",
            }
        )
        suite = f"{sys.executable} -m unittest discover -s legacy -v"
        regression = f"{sys.executable} -m unittest discover -s regression -v"
        framework = verify.Framework("unittest", suite, python=sys.executable)
        base = verify.baseline(
            project.root, project.base, project.evidence, framework=framework, suite_command=suite, timeout=30
        )
        result = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=suite,
            regression_command=regression,
            base_suite=base,
            timeout=30,
            new_behavior=new_behavior,
        )
        self.assertEqual(["test_change.Change.test_new_value"], result["fail_to_pass"], result)
        return base, result

    @staticmethod
    def broken_module():
        return {"legacy/test_broken.py": "import missing_autocode_preservation_dependency\n"}

    @staticmethod
    def healthy_module():
        return {
            "legacy/test_ok.py": "import unittest\n\nclass Existing(unittest.TestCase):\n"
            "    def test_existing(self):\n        self.assertEqual(2, 1 + 1)\n"
        }

    @staticmethod
    def failing_module():
        return {
            "legacy/test_env.py": "import unittest\n\nclass Environment(unittest.TestCase):\n"
            "    def test_pre_existing_failure(self):\n"
            "        self.fail('unavailable environment')\n"
        }

    def test_matching_import_errors_are_not_preservation_evidence(self):
        base, result = self.check_suite(self.broken_module())
        candidate = result["checks"]["suite_on_candidate"]
        self.assertEqual("broken", base["health"])
        self.assertEqual(1, base["receipt"]["exit_code"])
        self.assertEqual(1, candidate["exit_code"])
        self.assertEqual(base["receipt"]["results"]["collection_errors"], candidate["results"]["collection_errors"])
        self.assertTrue(candidate["results"]["collection_errors"])
        self.assertEqual([], candidate["results"]["passed"])
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertEqual([], result["failures"])
        self.assertTrue(any("preservation" in reason for reason in result["unverified"]), result)

    def test_partial_collection_errors_do_not_establish_preservation(self):
        base, result = self.check_suite({**self.broken_module(), **self.healthy_module()})
        self.assertEqual("broken", base["health"])
        self.assertEqual(["test_ok.Existing.test_existing"], base["receipt"]["results"]["passed"])
        self.assertTrue(base["receipt"]["results"]["collection_errors"])
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertEqual([], result["failures"])

    def test_a_base_with_no_passing_tests_does_not_establish_preservation(self):
        base, result = self.check_suite(self.failing_module())
        self.assertEqual("broken", base["health"])
        self.assertEqual([], base["receipt"]["results"]["passed"])
        self.assertEqual([], base["receipt"]["results"]["collection_errors"])
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertEqual([], result["failures"])

    def test_new_behavior_does_not_bypass_import_errors_in_an_existing_suite(self):
        base, result = self.check_suite(self.broken_module(), new_behavior=True)
        self.assertEqual("broken", base["health"])
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)

    def check_first_suite(
        self,
        project,
        *,
        test_path="test_feature.py",
        verdict=verify.PASS,
        run="worktree",
        independent_dependencies=None,
    ):
        """Prove a first feature on ``project.base`` with dependencies_from as autocode_regression passes it.

        ``run`` "worktree": a default run, in a task worktree (autocode_workspaces.create) whose
        dependencies_from is the original checkout. "in_place": the candidate works in the
        project checkout, which is also dependencies_from. "in_place_via_symlink": the same, with
        dependencies_from a symlink to the checkout. "no_dependencies": no dependencies_from.
        ``independent_dependencies`` is passed as autocode_regression.proof_dependencies returns it.
        Returns the candidate workspace."""
        self.addCleanup(project.close)
        if run == "worktree":
            workspace = Path(workspaces.create(project.root, "first feature")["workspace"])
        else:
            workspace = project.root
        dependencies = None if run == "no_dependencies" else project.root
        if run == "in_place_via_symlink":
            dependencies = Path(project.temp.name) / "project-link"
            dependencies.symlink_to(project.root, target_is_directory=True)
        references.write(
            {
                "calc.py": "VALUE = 'new'\n",
                test_path: "import unittest\nfrom calc import VALUE\n\n"
                "class Feature(unittest.TestCase):\n"
                "    def test_new_value(self):\n"
                "        self.assertEqual('new', VALUE)\n",
            },
            workspace,
        )
        framework = verify.detect_framework(workspace, python=sys.executable)
        base = verify.baseline(
            workspace,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=framework.suite,
            timeout=30,
            dependencies_from=dependencies,
        )
        result = verify.verify(
            workspace,
            project.base,
            project.evidence,
            framework=framework,
            base_suite=base,
            new_behavior=True,
            timeout=30,
            dependencies_from=dependencies,
            independent_dependencies=independent_dependencies,
        )
        self.assertIn(base["receipt"]["exit_code"], (0, 5))
        self.assertEqual(0, base["receipt"]["results"]["total"])
        self.assertEqual([], base["receipt"]["results"]["collection_errors"])
        test_name = test_path.removesuffix(".py").replace("/", ".") + ".Feature.test_new_value"
        self.assertEqual([test_name], result["fail_to_pass"], result)
        self.assertEqual(verdict, result["verdict"], result)
        if verdict == verify.UNVERIFIED:
            self.assertEqual([], result["failures"], result)
            self.assertTrue(any("preservation" in reason for reason in result["unverified"]), result)
        return workspace

    def test_new_project_with_no_existing_test_inventory_can_prove_a_feature(self):
        self.check_first_suite(Project({"README.md": "A new project.\n"}))

    def test_in_place_a_readme_only_project_can_still_prove_a_feature(self):
        self.check_first_suite(Project({"README.md": "A new project.\n"}), run="in_place")

    def test_empty_pinned_project_can_prove_its_first_feature(self):
        project = Project({"README.md": "A new project.\n"})
        git(project.root, "rm", "README.md")
        git(project.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "empty project")
        project.base = git(project.root, "rev-parse", "HEAD")
        self.check_first_suite(project)

    @staticmethod
    def recommit(project):
        git(project.root, "add", "-A")
        git(project.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "scaffold")
        project.base = git(project.root, "rev-parse", "HEAD")

    # A project scaffold: what a walking skeleton found in a live run (2026-10-06),
    # plus a nested .gitignore. docs/bugs/2026-10-06-regression-proof-scaffold-base.md
    SCAFFOLD = {
        "README.md": "A new project.\n",
        ".gitignore": "__pycache__/\n*.pyc\n",
        "docs/.gitignore": "build/\n",
        "tests/__init__.py": "",
    }

    def test_scaffold_with_gitignores_and_an_empty_package_can_prove_its_first_feature(self):
        self.check_first_suite(Project(self.SCAFFOLD), test_path="tests/test_feature.py")

    def test_an_executable_empty_file_is_not_a_first_suite_base(self):
        project = Project(self.SCAFFOLD)
        (project.root / "tests" / "__init__.py").chmod(0o755)
        self.recommit(project)
        self.check_first_suite(project, test_path="tests/test_feature.py", verdict=verify.UNVERIFIED)

    def test_a_non_empty_package_init_is_not_a_first_suite_base(self):
        project = Project({**self.SCAFFOLD, "tests/__init__.py": "SETTING = 1\n"})
        self.check_first_suite(project, test_path="tests/test_feature.py", verdict=verify.UNVERIFIED)

    def test_a_gitignore_symlink_is_not_a_first_suite_base(self):
        project = Project({"README.md": "A new project.\n", "tests/__init__.py": ""})
        os.symlink("README.md", project.root / ".gitignore")
        self.recommit(project)
        self.check_first_suite(project, test_path="tests/test_feature.py", verdict=verify.UNVERIFIED)

    def test_another_non_empty_dotfile_is_not_a_first_suite_base(self):
        project = Project({**self.SCAFFOLD, ".gitattributes": "* text=auto\n"})
        self.check_first_suite(project, test_path="tests/test_feature.py", verdict=verify.UNVERIFIED)

    # Ignored code next to a tracked file is copied into both proof trees
    # (copy_generated_sources), so it is existing behavior the pinned tree does not list.
    # In a default run it is in the original checkout only, never in the task worktree:
    # the check must read dependencies_from, the checkout make_tree copies from.
    def check_ignored_program(self, project, path, *, run="worktree"):
        (project.root / path).write_text(LEGACY_PROGRAM)
        self.assertIn(path, verify.generated_sources(project.root))
        workspace = self.check_first_suite(project, verdict=verify.UNVERIFIED, run=run)
        if run == "worktree":
            self.assertFalse((workspace / path).exists())
            self.assertEqual([], verify.generated_sources(workspace))

    def test_ignored_code_beside_a_tracked_file_is_not_a_first_suite_base(self):
        self.check_ignored_program(Project({"README.md": "A project.\n", ".gitignore": "legacy.py\n"}), "legacy.py")

    def test_ignored_code_in_an_empty_package_is_not_a_first_suite_base(self):
        project = Project({"README.md": "A project.\n", ".gitignore": "core.py\n", "src_pkg/__init__.py": ""})
        self.check_ignored_program(project, "src_pkg/core.py")

    def test_code_excluded_outside_the_tree_is_not_a_first_suite_base(self):
        # The exclude hides code from a README-only base, which the pinned-tree inventory alone accepts.
        project = Project({"README.md": "A project.\n"})
        (project.root / ".git" / "info").mkdir(exist_ok=True)
        (project.root / ".git" / "info" / "exclude").write_text("legacy.py\n")
        self.check_ignored_program(project, "legacy.py")

    def test_in_place_code_excluded_outside_the_tree_is_not_a_first_suite_base(self):
        project = Project({"README.md": "A project.\n"})
        (project.root / ".git" / "info").mkdir(exist_ok=True)
        (project.root / ".git" / "info" / "exclude").write_text("legacy.py\n")
        self.check_ignored_program(project, "legacy.py", run="in_place")

    # In place, the candidate edits the checkout the ignored-code check reads, before the
    # proof first runs (autocode_regression.before_review, after the Builder). With nothing
    # left to see, only a README.md-only base may skip preservation, as before .gitignore
    # and empty files were accepted.
    def test_in_place_candidate_that_stops_ignoring_code_cannot_prove_a_first_suite(self):
        project = Project({"README.md": "A project.\n", ".gitignore": "legacy.py\n"})
        (project.root / "legacy.py").write_text(LEGACY_PROGRAM)  # the user's ignored program
        (project.root / ".gitignore").write_text("__pycache__/\n")  # the candidate stops ignoring it
        (project.root / "legacy.py").write_text("def answer():\n    raise SystemExit('broken')\n")
        self.assertEqual([], verify.generated_sources(project.root))
        self.check_first_suite(project, run="in_place", verdict=verify.UNVERIFIED)

    def test_in_place_candidate_that_deletes_ignored_code_cannot_prove_a_first_suite(self):
        project = Project({"README.md": "A project.\n", ".gitignore": "legacy.py\n"})
        (project.root / "legacy.py").write_text(LEGACY_PROGRAM)  # the user's ignored program
        (project.root / "legacy.py").unlink()  # the candidate deletes it
        self.check_first_suite(project, run="in_place", verdict=verify.UNVERIFIED)

    def test_in_place_candidate_that_deletes_excluded_code_beside_an_empty_file_cannot_prove_a_first_suite(self):
        project = Project({"README.md": "A project.\n", "src_pkg/__init__.py": ""})
        (project.root / ".git" / "info").mkdir(exist_ok=True)
        (project.root / ".git" / "info" / "exclude").write_text("src_pkg/core.py\n")
        (project.root / "src_pkg" / "core.py").write_text(LEGACY_PROGRAM)  # the user's excluded program
        self.assertIn("src_pkg/core.py", verify.generated_sources(project.root))
        (project.root / "src_pkg" / "core.py").unlink()  # the candidate deletes it
        self.check_first_suite(project, run="in_place", verdict=verify.UNVERIFIED)

    def test_in_place_through_a_symlink_a_gitignore_is_not_a_first_suite_base(self):
        # A symlink to the workspace names the checkout the candidate edits: both paths are resolved.
        self.check_first_suite(
            Project({"README.md": "A project.\n", ".gitignore": "legacy.py\n"}),
            run="in_place_via_symlink",
            verdict=verify.UNVERIFIED,
        )

    # A continuation restored from a checkpoint of an --in-place run works in a new worktree, but
    # its dependencies_from is the checkout the earlier Builder worked in, where it could hide
    # ignored code before the proof read it. autocode_regression.proof_dependencies then passes
    # independent_dependencies=False (project_worked_in_place, autocode_checkpoint_continuation).
    def test_a_checkout_an_earlier_builder_worked_in_does_not_admit_a_scaffold_base(self):
        self.check_first_suite(
            Project(self.SCAFFOLD),
            test_path="tests/test_feature.py",
            verdict=verify.UNVERIFIED,
            independent_dependencies=False,
        )

    def test_a_continuation_of_an_in_place_run_that_hid_ignored_code_cannot_prove_a_first_suite(self):
        def delete(root):
            (root / "legacy.py").unlink()

        def stop_ignoring(root):
            (root / ".gitignore").write_text("__pycache__/\n")
            (root / "legacy.py").write_text("def answer():\n    raise SystemExit('broken')\n")

        for hide in (delete, stop_ignoring):
            with self.subTest(hide.__name__):
                project = Project({"README.md": "A project.\n", ".gitignore": "legacy.py\n"})
                (project.root / "legacy.py").write_text(LEGACY_PROGRAM)  # the user's ignored program
                hide(project.root)  # the earlier Builder, in place, before the checkpoint
                self.assertEqual([], verify.generated_sources(project.root))
                self.check_first_suite(project, verdict=verify.UNVERIFIED, independent_dependencies=False)

    def test_without_dependencies_from_a_gitignore_is_not_a_first_suite_base(self):
        # No proof tree receives ignored code, so nothing records what the .gitignore hides.
        project = Project({"README.md": "A project.\n", ".gitignore": "legacy.py\n"})
        (project.root / "legacy.py").write_text(LEGACY_PROGRAM)
        self.check_first_suite(project, run="no_dependencies", verdict=verify.UNVERIFIED)

    # A build of an approved design: the design workflow's docs/design/ document and an empty
    # package, the base of implement-locked-design (five live runs stopped UNVERIFIED on it,
    # 2026-10-07). docs/bugs/2026-10-07-regression-proof-design-document-base.md
    DESIGNED = {
        "README.md": "A new project.\n",
        "tests/__init__.py": "",
        "docs/design/feature.md": "# Feature\n\ncalc.VALUE becomes 'new'.\n",
    }

    def test_a_design_document_and_an_empty_package_can_prove_a_first_feature(self):
        self.check_first_suite(Project(self.DESIGNED), test_path="tests/test_feature.py")

    def test_an_executable_design_document_is_not_a_first_suite_base(self):
        project = Project(self.DESIGNED)
        (project.root / "docs" / "design" / "feature.md").chmod(0o755)
        self.recommit(project)
        self.check_first_suite(project, test_path="tests/test_feature.py", verdict=verify.UNVERIFIED)

    def test_a_document_that_is_not_markdown_is_not_a_first_suite_base(self):
        project = Project({**self.DESIGNED, "docs/design/feature.rst": "Feature\n=======\n"})
        self.check_first_suite(project, test_path="tests/test_feature.py", verdict=verify.UNVERIFIED)

    def test_in_place_without_a_launch_record_a_design_document_is_not_a_first_suite_base(self):
        # Nothing recorded the ignored code the checkout held before the candidate edited it.
        project = Project({"README.md": "A new project.\n", "docs/design/feature.md": "# Feature\n"})
        self.check_first_suite(project, run="in_place", verdict=verify.UNVERIFIED)

    def test_a_launch_record_taken_before_its_base_was_pinned_does_not_bind_it(self):
        # The checkout was busy at launch: the record was taken first and the base pinned later, so
        # ignored code left in between is in neither (review of this fix, 2026-10-07).
        project = Project(self.DESIGNED)
        self.addCleanup(project.close)
        run_dir = Path(project.temp.name) / "run"
        run_dir.mkdir()
        state = {
            "base_commit": None,
            "goal_contract": {"body": {"task_kind": "build"}},
            "settings": {"regression": {"python": sys.executable, "test_timeout": 60}},
        }
        launch_inputs.record(state, project.root, run_dir)
        state["base_commit"] = project.base
        self.assertFalse(launch_inputs.supply(state, project.root, run_dir).recorded)
        references.write(
            {
                "calc.py": "VALUE = 'new'\n",
                "tests/test_feature.py": "import unittest\nfrom calc import VALUE\n\n"
                "class Feature(unittest.TestCase):\n"
                "    def test_new_value(self):\n"
                "        self.assertEqual('new', VALUE)\n",
            },
            project.root,
        )
        self.assertEqual(verify.UNVERIFIED, regression.prove(state, project.root, run_dir)["verdict"])

    def prove_in_place(self, project, change=None, *, record=True):
        """autocode_regression.prove for an --in-place run whose ignored inputs were recorded at launch
        (autocode_launch_inputs.record, before any provider), as autocode_run_setup records them.
        ``record=False``: a run saved before launch records existed."""
        self.addCleanup(project.close)
        run_dir = Path(project.temp.name) / "run"
        run_dir.mkdir()
        state = {
            "base_commit": project.base,
            "goal_contract": {"body": {"task_kind": "build"}},
            "settings": {"regression": {"python": sys.executable, "test_timeout": 60}},
        }
        if record:
            launch_inputs.record(state, project.root, run_dir)
        if change:
            change(project.root)  # the candidate, after launch
        references.write(
            {
                "calc.py": "VALUE = 'new'\n",
                "tests/test_feature.py": "import unittest\nfrom calc import VALUE\n\n"
                "class Feature(unittest.TestCase):\n"
                "    def test_new_value(self):\n"
                "        self.assertEqual('new', VALUE)\n",
            },
            project.root,
        )
        return regression.prove(state, project.root, run_dir)

    def test_in_place_with_a_launch_record_a_design_document_and_an_empty_package_can_prove_a_first_feature(self):
        proof = self.prove_in_place(Project(self.DESIGNED))
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["tests.test_feature.Feature.test_new_value"], proof["fail_to_pass"], proof)

    def test_in_place_without_a_launch_record_prove_does_not_admit_a_design_document(self):
        proof = self.prove_in_place(Project(self.DESIGNED), record=False)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)

    def test_in_place_ignored_code_the_launch_record_holds_is_existing_behavior(self):
        # The user's program, ignored beside the design: kept, un-ignored as it was, deleted, or
        # un-ignored and broken by the candidate. The launch record still lists it, or supply refuses.
        def keep(root):
            pass

        def stop_ignoring(root):
            (root / ".gitignore").write_text("__pycache__/\n")

        def delete(root):
            (root / "docs" / "design" / "legacy.py").unlink()

        def stop_ignoring_and_break(root):
            stop_ignoring(root)
            (root / "docs" / "design" / "legacy.py").write_text("def answer():\n    raise SystemExit('broken')\n")

        for change in (keep, stop_ignoring, delete, stop_ignoring_and_break):
            with self.subTest(change.__name__):
                project = Project({**self.DESIGNED, ".gitignore": "legacy.py\n"})
                (project.root / "docs" / "design" / "legacy.py").write_text(LEGACY_PROGRAM)
                self.assertIn("docs/design/legacy.py", verify.generated_sources(project.root))
                proof = self.prove_in_place(project, change)
                self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
                self.assertEqual([], proof["failures"], proof)

    def check_existing_program(self, path, *, executable=False):
        legacy = (
            "#!/usr/bin/env python3\nimport unittest\nVALUE = 'old'\n"
            "class Existing(unittest.TestCase):\n"
            "    def test_existing_value(self):\n"
            "        self.assertEqual('old', VALUE)\n"
            "if __name__ == '__main__': unittest.main()\n"
        )
        project = Project({path: legacy})
        self.addCleanup(project.close)
        if executable:
            (project.root / path).chmod(0o755)
            git(project.root, "add", path)
            git(
                project.root,
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-qm",
                "executable existing program",
            )
            project.base = git(project.root, "rev-parse", "HEAD")
        legacy_command = f"{sys.executable} {path} -v"
        original = verify.scratch_run(
            project.root, project.evidence / "original-program", command=legacy_command, timeout=30
        )
        self.assertEqual(0, original["exit_code"], original)
        project.write(
            {
                path: legacy.replace("VALUE = 'old'", "VALUE = 'new'"),
                "calc.py": "VALUE = 'new'\n",
                "test_new.py": "import unittest\nfrom calc import VALUE\n\n"
                "class Feature(unittest.TestCase):\n"
                "    def test_new_value(self):\n"
                "        self.assertEqual('new', VALUE)\n",
            }
        )
        if executable:
            # Inventory belongs to the pinned base, including its executable
            # mode; changing the candidate's mode must not erase that evidence.
            (project.root / path).chmod(0o644)
        broken = verify.scratch_run(
            project.root, project.evidence / "candidate-program", command=legacy_command, timeout=30
        )
        self.assertEqual(1, broken["exit_code"], broken)
        self.assertIn("test_existing_value", Path(broken["output"]).read_text())
        self.assertIn("AssertionError", Path(broken["output"]).read_text())
        suite = f"{sys.executable} -m unittest discover -p test_new.py -v"
        framework = verify.Framework("unittest", suite, python=sys.executable)
        base = verify.baseline(
            project.root, project.base, project.evidence, framework=framework, suite_command=suite, timeout=30
        )
        result = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=suite,
            regression_command=suite,
            base_suite=base,
            new_behavior=True,
            timeout=30,
        )
        self.assertIn(base["receipt"]["exit_code"], (0, 5))
        self.assertEqual(0, base["receipt"]["results"]["total"])
        self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"])
        self.assertEqual(["test_new.Feature.test_new_value"], result["fail_to_pass"], result)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)

    def test_extensionless_existing_program_is_not_a_first_suite(self):
        self.check_existing_program("legacy")

    def test_executable_document_is_not_a_first_suite(self):
        self.check_existing_program("README.md", executable=True)

    def test_empty_collection_does_not_bypass_the_existing_base_test_inventory(self):
        legacy = self.healthy_module()
        project = Project({"calc.py": "VALUE = 'old'\n", **legacy})
        self.addCleanup(project.close)
        project.write(
            {
                "calc.py": "VALUE = 'new'\n",
                "legacy/test_new.py": "import unittest\nfrom calc import VALUE\n\n"
                "class Feature(unittest.TestCase):\n"
                "    def test_new_value(self):\n"
                "        self.assertEqual('new', VALUE)\n",
            }
        )
        suite = f"{sys.executable} -m unittest discover -s legacy -p test_new.py -v"
        framework = verify.Framework("unittest", suite, python=sys.executable)
        base = verify.baseline(
            project.root, project.base, project.evidence, framework=framework, suite_command=suite, timeout=30
        )
        result = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=suite,
            regression_command=suite,
            base_suite=base,
            new_behavior=True,
            timeout=30,
        )
        self.assertIn(base["receipt"]["exit_code"], (0, 5))
        self.assertEqual(0, base["receipt"]["results"]["total"])
        self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"])
        self.assertEqual(["test_new.Feature.test_new_value"], result["fail_to_pass"], result)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)

    def test_existing_source_with_unconventional_tests_is_not_a_first_suite(self):
        project = Project(
            {
                "calc.py": "VALUE = 'old'\n",
                "legacy.py": "import unittest\nfrom calc import VALUE\n\n"
                "class Existing(unittest.TestCase):\n"
                "    def test_existing_value(self):\n"
                "        self.assertEqual('old', VALUE)\n",
            }
        )
        self.addCleanup(project.close)
        legacy_command = f"{sys.executable} -m unittest -v legacy"
        original = verify.scratch_run(
            project.root, project.evidence / "original-legacy", command=legacy_command, timeout=30
        )
        self.assertEqual(["legacy.Existing.test_existing_value"], original["results"]["passed"])
        project.write(
            {
                "calc.py": "VALUE = 'new'\n",
                "test_new.py": "import unittest\nfrom calc import VALUE\n\n"
                "class Feature(unittest.TestCase):\n"
                "    def test_new_value(self):\n"
                "        self.assertEqual('new', VALUE)\n",
            }
        )
        # This explicit suite filters out the existing module, whose filename
        # does not follow is_test_path's conventions. Empty collection is not
        # independent evidence that existing product behavior is preserved.
        suite = f"{sys.executable} -m unittest discover -p test_new.py -v"
        framework = verify.Framework("unittest", suite, python=sys.executable)
        base = verify.baseline(
            project.root, project.base, project.evidence, framework=framework, suite_command=suite, timeout=30
        )
        result = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=suite,
            regression_command=suite,
            base_suite=base,
            new_behavior=True,
            timeout=30,
        )
        self.assertIn(base["receipt"]["exit_code"], (0, 5))
        self.assertEqual(0, base["receipt"]["results"]["total"])
        self.assertEqual(["test_new.Feature.test_new_value"], result["fail_to_pass"], result)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        old_behavior = verify.scratch_run(
            project.root, project.evidence / "candidate-legacy", command=legacy_command, timeout=30
        )
        self.assertEqual(1, old_behavior["exit_code"])
        self.assertEqual(["legacy.Existing.test_existing_value"], old_behavior["results"]["failed"])

    def test_a_healthy_suite_establishes_preservation(self):
        base, result = self.check_suite(self.healthy_module())
        self.assertEqual("passing", base["health"])
        self.assertEqual(verify.PASS, result["verdict"], result)

    def test_pre_existing_assertion_failures_with_passing_tests_stay_supported(self):
        base, result = self.check_suite({**self.healthy_module(), **self.failing_module()})
        self.assertEqual("failing_tests", base["health"])
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertTrue(any("already failed on base" in note for note in result["notes"]), result)

    def test_observed_named_regression_still_fails_with_collection_errors(self):
        module = {
            "legacy/test_preserved.py": "import unittest\nfrom calc import VALUE\n\n"
            "class Existing(unittest.TestCase):\n"
            "    def test_existing_value(self):\n"
            "        self.assertEqual('old', VALUE)\n"
        }
        base, result = self.check_suite({**self.broken_module(), **module})
        self.assertEqual("broken", base["health"])
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertTrue(
            any("test_preserved.Existing.test_existing_value" in reason for reason in result["failures"]), result
        )


class VerifyCase(unittest.TestCase):
    def project(self, files=SEED):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def proof_python(self, *, pytest=False):
        """A real isolated runtime without this controller's editable install."""
        import importlib.metadata
        import importlib.util

        python = isolated_python(self)
        if pytest:
            site = Path(
                subprocess.check_output(
                    [python, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True
                ).strip()
            )
            # Copy only already-installed pytest runtime packages. No install,
            # external plugin or editable controller is needed by these tests.
            packages = ("pytest", "_pytest", "pluggy", "packaging", "iniconfig", "pygments", "py")
            for package in packages:
                spec = importlib.util.find_spec(package)
                if spec is None and package == "pygments":
                    continue  # optional on older installed pytest versions
                source = Path(spec.origin)
                if spec.submodule_search_locations:
                    shutil.copytree(source.parent, site / package, ignore=shutil.ignore_patterns("__pycache__"))
                else:
                    shutil.copy2(source, site / source.name)
            for package in ("pytest", "pluggy", "packaging", "iniconfig", "pygments", "py"):
                if not (site / package).is_dir() and not (site / (package + ".py")).is_file():
                    continue
                try:
                    distribution = importlib.metadata.distribution(package)
                except importlib.metadata.PackageNotFoundError:
                    continue  # pytest may supply py.py without a py distribution
                metadata = next(file for file in distribution.files if file.name == "METADATA")
                source = Path(distribution.locate_file(metadata)).parent
                shutil.copytree(source, site / source.name)
            probe = subprocess.run(
                [python, "-m", "pytest", "--version"],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
            )
            self.assertEqual(0, probe.returncode, probe.stdout + probe.stderr)
        return python

    @unittest.skipUnless(verify._python_can_import(sys.executable, "pytest"), "pytest is not installed")
    def test_explicit_pytest_proves_modified_tests_module_in_a_unittest_repository(self):
        import autocode_regression as regression

        python = self.proof_python(pytest=True)
        existing = (
            "import unittest\nfrom app import value\n"
            "class Existing(unittest.TestCase):\n"
            "    def test_one(self):\n        self.assertEqual(2, value(1))\n"
        )
        lifecycle = (
            "import unittest\nfrom app import value\n"
            "class Lifecycle(unittest.TestCase):\n"
            "    def test_one(self):\n        self.assertEqual(2, value(1))\n"
        )
        project = self.project(
            {
                "app.py": "def value(n):\n    return n + 1\n",
                "tests/__init__.py": "",
                "tests/lifecycle/__init__.py": "",
                "tests/test_existing.py": existing,
                "tests/lifecycle/tests.py": lifecycle,
            }
        )
        self.assertEqual("unittest", verify.detect_framework(project.root, python=python).name)
        project.write(
            {
                "app.py": "def value(n):\n    return 4 if n == 2 else n + 1\n",
                "tests/lifecycle/tests.py": lifecycle
                + "    def test_t1_two_is_fixed(self):\n        self.assertEqual(4, value(2))\n",
            }
        )
        suite = (
            f"{shlex.quote(python)} -m pytest -q -p no:cacheprovider tests/test_existing.py tests/lifecycle/tests.py"
        )
        state = {
            "goal_contract": {"body": {"task_kind": "bugfix"}},
            "base_commit": project.base,
            "settings": {
                "regression": {"test_command": suite, "test_timeout": 30, "python": "/missing/detected-python"}
            },
        }
        proof = regression.prove(state, project.root, project.evidence)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual("pytest", proof["framework"]["name"])
        self.assertEqual(python, proof["framework"]["python"])
        self.assertEqual("derived:pytest", proof["commands"]["regression_source"])
        self.assertEqual(["tests.lifecycle.tests.Lifecycle::test_t1_two_is_fixed"], proof["fail_to_pass"])
        self.assertEqual(1, proof["checks"]["regression_on_base"]["exit_code"])
        self.assertEqual(0, proof["checks"]["regression_on_candidate"]["exit_code"])
        receipt = json.loads(Path(proof["path"]).read_text())
        for label in ("regression_on_base", "regression_on_candidate", "suite_on_candidate"):
            self.assertTrue(receipt["checks"][label]["results"]["complete"], label)
        self.assertTrue(regression.complete(state, proof["source_revision"]))

        project.write({"app.py": "def value(n):\n    return n + 1  # still broken\n"})
        broken = regression.prove(state, project.root, project.evidence)
        self.assertEqual(verify.FAIL, broken["verdict"], broken)
        self.assertEqual(1, broken["checks"]["regression_on_candidate"]["exit_code"])
        self.assertFalse(regression.complete(state, broken["source_revision"]))

    @unittest.skipUnless(verify._python_can_import(sys.executable, "pytest"), "pytest is not installed")
    def test_env_pytest_proof_preserves_bootstrap_and_collection_config(self):
        python = self.proof_python(pytest=True)
        project = self.project(
            {
                "app.py": "VALUE = 1\n",
                "pytest.ini": "[pytest]\nminversion = 999\n",
                "tests/test_existing.py": "def test_existing():\n    assert 2 + 2 == 4\n",
            }
        )
        support = Path(project.temp.name) / "support with spaces"
        support.mkdir()
        (support / "bootstrap.py").write_text(
            "import os\nimport pytest\n@pytest.fixture\ndef bootstrapped():\n    return os.environ['PROOF_MODE']\n"
        )
        project.write(
            {
                "app.py": "VALUE = 2\n",
                "tests/test_fix.py": "from app import VALUE\ndef test_t1_fix(bootstrapped):\n"
                "    assert bootstrapped == 'ready'\n    assert VALUE == 2\n",
            }
        )
        suite = shlex.join(
            [
                "env",
                f"PYTHONPATH={support}",
                "PROOF_MODE=ready",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
                python,
                "-m",
                "pytest",
                "-q",
                "-o",
                "minversion=",
                "-p",
                "bootstrap",
                "-p",
                "no:cacheprovider",
                "tests/",
            ]
        )
        state = {
            "goal_contract": {"body": {"task_kind": "bugfix"}},
            "base_commit": project.base,
            "settings": {
                "regression": {"test_command": suite, "test_timeout": 30, "python": "/missing/detected-python"}
            },
        }
        identity = verify.execution_identity(project.root, command=suite)
        self.assertEqual(str(Path(python).resolve()), identity["interpreter"]["path"])
        self.assertFalse(identity["reuse_supported"])
        changed_env = verify.execution_identity(
            project.root, command=suite.replace("PROOF_MODE=ready", "PROOF_MODE=other")
        )
        self.assertNotEqual(identity["environment_hash"], changed_env["environment_hash"])
        proof = regression.prove(state, project.root, project.evidence)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(python, proof["framework"]["python"])
        self.assertEqual(["tests.test_fix::test_t1_fix"], proof["fail_to_pass"])
        self.assertEqual(1, proof["checks"]["regression_on_base"]["exit_code"])
        self.assertEqual(0, proof["checks"]["regression_on_candidate"]["exit_code"])
        project.write({"app.py": "VALUE = 1  # still broken\n"})
        broken = regression.prove(state, project.root, project.evidence)
        self.assertEqual(verify.FAIL, broken["verdict"], broken)
        self.assertFalse(regression.complete(state, broken["source_revision"]))

    def test_proof_framework_detection_survives_absent_or_unknown_explicit_command(self):
        import autocode_regression as regression

        python = self.proof_python()
        for suite in (None, f"{shlex.quote(python)} -m unittest discover -v && true"):
            with self.subTest(suite=suite):
                project = self.project()
                project.write(REFERENCE)
                options = {"python": python, "test_timeout": 30}
                if suite:
                    options["test_command"] = suite
                    self.assertIsNone(verify.command_framework(suite))
                state = {
                    "goal_contract": {"body": {"task_kind": "bugfix"}},
                    "base_commit": project.base,
                    "settings": {"regression": options},
                }
                proof = regression.prove(state, project.root, project.evidence)
                self.assertEqual(verify.PASS, proof["verdict"], proof)
                self.assertEqual("unittest", proof["framework"]["name"])
                self.assertEqual("derived:unittest", proof["commands"]["regression_source"])
                self.assertTrue(proof["fail_to_pass"])
                self.assertEqual(1, proof["checks"]["regression_on_base"]["exit_code"])
                self.assertEqual(0, proof["checks"]["regression_on_candidate"]["exit_code"])
                self.assertTrue(regression.complete(state, proof["source_revision"]))

    @unittest.skipUnless(shutil.which("node"), "Node is required for named Node proof")
    def test_node_named_case_flip_preserves_original_custom_suite(self):
        import autocode_regression as regression

        seed = {
            "package.json": '{"scripts":{"test":"node tests/check.cjs"}}',
            "app.cjs": "module.exports = n => n + 1;\n",
            "tests/check.cjs": "require('node:assert/strict').equal(require('../app.cjs')(1),2);\n",
        }
        project = self.project(seed)
        project.write(
            {
                "app.cjs": "module.exports = n => n === 2 ? 4 : n + 1;\n",
                "tests/cases.cjs": "const {test}=require('node:test');"
                "const assert=require('node:assert/strict'); const app=require('../app.cjs');"
                "test('test_c1_two',()=>assert.equal(app(2),4));"
                "test('test_c2_one',()=>assert.equal(app(1),2));\n",
            }
        )
        result = project.verify()
        regression.check_cases(
            result,
            [
                {"id": "C1", "text": "two gives four", "test_name": "test_c1_two"},
                {"id": "C2", "text": "one stays two", "test_name": "test_c2_one", "kind": "preserve"},
            ],
        )
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual("derived:node", result["commands"]["regression_source"])
        self.assertEqual("npm test --silent", result["commands"]["suite"])
        self.assertEqual(["tests/cases.cjs::test_c1_two"], result["fail_to_pass"])
        self.assertEqual(["tests/cases.cjs::test_c2_one"], result["pass_to_pass"])
        self.assertEqual(seed["tests/check.cjs"], project_file(project, "tests/check.cjs"))
        for source in (
            "module.exports = n => n + 1; // still broken\n",
            "module.exports = n => n === 2 ? 4 : 0; // breaks the protected suite\n",
        ):
            with self.subTest(source=source):
                project.write({"app.cjs": source})
                rejected = project.verify()
                self.assertEqual(verify.FAIL, rejected["verdict"], rejected)

    @staticmethod
    def _narrowed_package_suite():
        # Existing Node project whose suite is `npm test --silent`. The candidate
        # breaks add() and narrows scripts.test so the old test never runs (#528).
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node --test"}}),
            "calc.js": "module.exports = {add: (a, b) => a + b};\n",
            "test/calc.test.js": (
                "const {test}=require('node:test');\n"
                "const assert=require('node:assert/strict');\n"
                "const {add}=require('../calc.js');\n"
                "test('add',()=>assert.equal(add(2,3),5));\n"
            ),
        }
        candidate = {
            "calc.js": "module.exports = {add: (a, b) => a - b, mul: (a, b) => a * b};\n",
            "package.json": json.dumps({"scripts": {"test": "node --test test/feature.test.js"}}),
            "test/feature.test.js": (
                "const {test}=require('node:test');\n"
                "const assert=require('node:assert/strict');\n"
                "const {mul}=require('../calc.js');\n"
                "test('mul',()=>assert.equal(mul(2,3),6));\n"
            ),
        }
        return seed, candidate

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_narrowing_the_package_test_script_cannot_hide_a_break_from_the_suite(self):
        seed, candidate = self._narrowed_package_suite()
        project = self.project(seed)
        project.write(candidate)
        result = project.verify(new_behavior=True)
        self.assertEqual("npm test --silent", result["commands"]["suite"], result)
        self.assertEqual(["test/feature.test.js::mul"], result["fail_to_pass"], result)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertEqual(
            [
                "The base suite definition fails against the candidate code: the candidate "
                "changed which tests the suite runs, so tests the base ran no longer pass"
            ],
            result["failures"],
            result,
        )
        self.assertEqual(1, result["checks"]["suite_base_definition_on_candidate"]["exit_code"], result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_the_same_break_without_narrowing_the_script_still_fails(self):
        seed, candidate = self._narrowed_package_suite()
        candidate = {**candidate, "package.json": seed["package.json"]}
        project = self.project(seed)
        project.write(candidate)
        result = project.verify(new_behavior=True)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertTrue(
            any("passes on base but fails on the candidate" in reason for reason in result["failures"]), result
        )

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_a_package_json_change_that_leaves_scripts_alone_still_passes(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node --test"}}),
            "calc.js": "module.exports = {add: (a, b) => a + b};\n",
            "test/calc.test.js": (
                "const {test}=require('node:test');\n"
                "const assert=require('node:assert/strict');\n"
                "const {add}=require('../calc.js');\n"
                "test('add',()=>assert.equal(add(2,3),5));\n"
            ),
        }
        project = self.project(seed)
        project.write(
            {
                "calc.js": "module.exports = {add: (a, b) => a + b, mul: (a, b) => a * b};\n",
                "package.json": json.dumps(
                    {
                        "scripts": {"test": "node --test"},
                        "dependencies": {"left-pad": "1.3.0"},
                    }
                ),
                "test/feature.test.js": (
                    "const {test}=require('node:test');\n"
                    "const assert=require('node:assert/strict');\n"
                    "const {mul}=require('../calc.js');\n"
                    "test('mul',()=>assert.equal(mul(2,3),6));\n"
                ),
            }
        )
        result = project.verify(new_behavior=True)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertFalse(any("redefined" in reason for reason in result["unverified"]), result)
        self.assertIn("test/feature.test.js::mul", result["fail_to_pass"], result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_a_first_node_project_defining_its_test_script_still_passes(self):
        project = self.project({"README.md": "A new project.\n"})
        project.write(
            {
                "package.json": json.dumps({"scripts": {"test": "node --test"}}),
                "calc.js": "module.exports = {add: (a, b) => a + b};\n",
                "test/calc.test.js": (
                    "const {test}=require('node:test');\n"
                    "const assert=require('node:assert/strict');\n"
                    "const {add}=require('../calc.js');\n"
                    "test('add',()=>assert.equal(add(2,3),5));\n"
                ),
            }
        )
        result = project.verify(new_behavior=True)
        self.assertEqual("npm test --silent", result["commands"]["suite"], result)
        self.assertEqual(verify.PASS, result["verdict"], result)

    def test_suite_package_script_names_the_script_an_npm_command_runs(self):
        self.assertEqual("test", verify._suite_package_script("npm test --silent"))
        self.assertEqual("test", verify._suite_package_script("yarn test"))
        self.assertEqual("test", verify._suite_package_script("pnpm run test"))
        self.assertEqual("test:unit", verify._suite_package_script("npm run test:unit"))
        self.assertIsNone(verify._suite_package_script("node --test a.test.js"))
        self.assertIsNone(verify._suite_package_script("npm test && echo done"))
        self.assertIsNone(verify._suite_package_script("npm run"))

    # --- the base suite definition executed over candidate code (#587) -----------
    ADD_TEST = (
        "const {test}=require('node:test');\n"
        "const assert=require('node:assert/strict');\n"
        "const {add}=require('../calc.js');\n"
        "test('add',()=>{assert.equal(add(2,3),5);assert.equal(add(42,58),100);"
        "assert.equal(add(-4,9),5);});\n"
    )
    MUL_TEST = (
        "const {test}=require('node:test');\n"
        "const assert=require('node:assert/strict');\n"
        "const {mul}=require('../calc.js');\n"
        "test('mul',()=>assert.equal(mul(2,3),6));\n"
    )
    ADDITION_TEST = ADD_TEST.replace("test('add'", "test('addition'")
    MULTIPLICATION_TEST = MUL_TEST.replace("test('mul'", "test('multiplication'")
    ADD_ONLY = "module.exports = {add: (a, b) => a + b};\n"
    ADD_BROKEN = "module.exports = {add: (a, b) => a - b, mul: (a, b) => a * b};\n"
    ADD_AND_MUL = "module.exports = {add: (a, b) => a + b, mul: (a, b) => a * b};\n"

    @staticmethod
    def _runner_suite_fixture():
        """A script-driven suite whose runner file names the old test file (#587 T1)."""
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
            "calc.js": VerifyCase.ADD_ONLY,
            "run-tests.js": 'require("node:child_process").execSync("node --test test/calc.test.js",'
            ' {stdio: "inherit"});\n',
            "test/calc.test.js": VerifyCase.ADD_TEST,
        }
        candidate = {
            "calc.js": VerifyCase.ADD_BROKEN,
            "run-tests.js": 'require("node:child_process").execSync("node --test test/feature.test.js",'
            ' {stdio: "inherit"});\n',
            "test/feature.test.js": VerifyCase.MUL_TEST,
        }
        return seed, candidate

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_quoted_shell_runner_cannot_hide_a_break(self):
        old_tests = (
            "const {test}=require('node:test');\n"
            "const assert=require('node:assert/strict');\n"
            "const {add}=require('../calc.js');\n"
            + "".join(f"test('addition {index}',()=>assert.equal(add(2,3),5));\n" for index in range(32))
        )
        old_runner = 'require("node:child_process").execSync("node --test test/calc.test.js", {stdio: "inherit"});\n'
        alternate_runner = (
            'require("node:child_process").execSync("node --test test/alternate.test.js", {stdio: "inherit"});\n'
        )
        for command, runner_path, package_script in (
            ("npm test --silent", "run-tests.js", "sh -c 'node run-tests.js'"),
            ("sh -c 'node run-tests.js'", "run-tests.js", "sh -c 'node run-tests.js'"),
            ("npm test --silent", "run tests.js", 'node "run tests.js"'),
        ):
            with self.subTest(suite_command=command, runner_path=runner_path):
                seed = {
                    "package.json": json.dumps({"scripts": {"test": package_script}}),
                    "calc.js": self.ADD_ONLY,
                    runner_path: old_runner,
                    "test/calc.test.js": old_tests,
                    "test/alternate.test.js": (
                        "const {test}=require('node:test');\n"
                        "const assert=require('node:assert/strict');\n"
                        "test('harmless',()=>assert.equal(1+1,2));\n"
                    ),
                }
                candidate = {
                    "calc.js": self.ADD_BROKEN,
                    runner_path: alternate_runner,
                    "test/feature.test.js": self.MUL_TEST,
                }
                project = self.project(seed)
                project.write(candidate)
                framework = verify.detect_framework(project.root)
                base_suite = verify.baseline(
                    project.root,
                    project.base,
                    project.evidence,
                    framework=framework,
                    suite_command=command,
                    timeout=120,
                )
                # Execute the original runner directly over the candidate product code, not
                # over a test-only reconstruction that could accidentally restore calc.js.
                project.write({runner_path: old_runner})
                try:
                    old_on_candidate = subprocess.run(
                        ["node", runner_path], cwd=project.root, capture_output=True, text=True
                    )
                finally:
                    project.write({runner_path: alternate_runner})
                result = verify.verify(
                    project.root,
                    project.base,
                    project.evidence,
                    framework=framework,
                    suite_command=command,
                    base_suite=base_suite,
                    timeout=120,
                    new_behavior=True,
                )
                self.assertEqual(0, base_suite["receipt"]["exit_code"], base_suite)
                self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"], result)
                self.assertEqual(1, old_on_candidate.returncode, old_on_candidate.stdout + old_on_candidate.stderr)
                self.assertEqual(1, result["checks"]["suite_base_definition_on_candidate"]["exit_code"], result)
                self.assertEqual(verify.FAIL, result["verdict"], result)
                self.assertTrue(any("base suite definition" in reason for reason in result["failures"]), result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_cd_prefixed_shell_runner_cannot_hide_a_break(self):
        """A literal `cd lib &&` must pin lib/run-tests.js, not a same-named root file."""
        old_runner = 'require("node:child_process").execSync("node --test test/calc.test.js", {stdio: "inherit"});\n'
        narrowed_runner = (
            'require("node:child_process").execSync("node --test test/feature.test.js", {stdio: "inherit"});\n'
        )
        calc_test = self.ADD_TEST.replace("require('../calc.js')", "require('../../calc.js')")
        feature_test = self.MUL_TEST.replace("require('../calc.js')", "require('../../calc.js')")
        alternate = (
            "const {test}=require('node:test');\n"
            "const assert=require('node:assert/strict');\n"
            "test('harmless',()=>assert.equal(1+1,2));\n"
        )
        script = "sh -c 'cd lib && node run-tests.js'"
        for command in ("npm test --silent", script):
            with self.subTest(suite_command=command):
                seed = {
                    "package.json": json.dumps({"scripts": {"test": script}}),
                    "calc.js": self.ADD_ONLY,
                    "lib/run-tests.js": old_runner,
                    "lib/test/calc.test.js": calc_test,
                    "lib/test/alternate.test.js": alternate,
                }
                candidate = {
                    "calc.js": self.ADD_BROKEN,
                    "lib/run-tests.js": narrowed_runner,
                    "lib/test/feature.test.js": feature_test,
                }
                project, base_suite, result = self.node_verify(seed, candidate, suite_command=command)
                self.assert_base_definition_fails(base_suite, result)

    def node_verify(self, seed, candidate, *, suite_command="npm test --silent"):
        """Baseline then verify over a Node fixture; returns (project, base_suite, result)."""
        project = self.project(seed)
        project.write(candidate)
        framework = verify.detect_framework(project.root)
        base_suite = verify.baseline(
            project.root, project.base, project.evidence, framework=framework, suite_command=suite_command, timeout=120
        )
        result = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=suite_command,
            base_suite=base_suite,
            timeout=120,
            new_behavior=True,
        )
        return project, base_suite, result

    def assert_base_definition_fails(self, base_suite, result):
        """A passing base and candidate suite whose base definition fails on candidate code.

        The verdict is asserted before the base-definition receipt: without the fix the
        verdict itself is wrong (the bug), which fails the test before any key the fix
        records is consulted."""
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertTrue(any("base suite definition" in reason for reason in result["failures"]), result)
        self.assertEqual(0, base_suite["receipt"]["exit_code"], base_suite)
        self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"], result)
        self.assertEqual(1, result["checks"]["suite_base_definition_on_candidate"]["exit_code"], result)

    def test_js_scan_masks_line_and_block_comments(self):
        # #652 G6: comment masking never fired — both slices were one character long, so
        # a require hidden in a comment looked live, and an apostrophe inside a comment
        # opened a phantom string that could hide the runner's require of its selector.
        _, strings = verify._js_scan(
            "let a = 1; // require(\"hidden-line\") don't\n/* require('hidden-block') */\nlet b = require('real');\n"
        )
        self.assertEqual(["real"], [value for _, _, value in strings])

    def test_definition_files_cover_yarn_pnpm_and_bun_config(self):
        # #652 G3: .yarnrc, .yarnrc.yml, bunfig.toml and the .pnpmfile.* / .yarn/releases
        # families are suite-definition inputs — a narrowed spec list behind any of them
        # is a definition change, not an invisible edit.
        for path in (
            ".yarnrc",
            "config/.yarnrc",
            ".yarnrc.yml",
            "bunfig.toml",
            ".pnpmfile.cjs",
            "scripts/.pnpmfile.js",
            ".yarn/releases/yarn-1.22.22.cjs",
        ):
            self.assertTrue(verify._definition_file(path), path)
        for path in ("README.md", "src/.pnpmfile-helper.js", "yarn/README.md"):
            self.assertFalse(verify._definition_file(path), path)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_a_green_candidate_suite_over_a_red_base_is_unverified_not_pass(self):
        # #652 G1: the base suite already fails, and the candidate narrows the script to a
        # new feature test so the broken function is never exercised. The base-definition
        # run used to start only when the base was green, so this case passed; a red base
        # cannot anchor preservation, and missing evidence is UNVERIFIED, never PASS.
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node --test test/calc.test.js"}}),
            "calc.js": self.ADD_BROKEN,
            "test/calc.test.js": self.ADD_TEST,
        }
        project = self.project(seed)
        project.write(
            {
                "package.json": json.dumps({"scripts": {"test": "node --test test/feature.test.js"}}),
                "test/feature.test.js": self.MUL_TEST,
            }
        )
        framework = verify.detect_framework(project.root)
        base_suite = verify.baseline(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command="npm test --silent",
            timeout=120,
        )
        self.assertNotEqual(0, base_suite["receipt"]["exit_code"], base_suite)
        result = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command="npm test --silent",
            base_suite=base_suite,
            timeout=120,
            new_behavior=True,
        )
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertTrue(any("base suite is not green" in reason for reason in result["unverified"]), result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_inv_base_definition_run_governs_pass_and_redefinition_verdicts(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node --test test/calc.test.js"}}),
            "calc.js": self.ADD_ONLY,
            "test/calc.test.js": self.ADD_TEST,
        }
        project = self.project(seed)
        project.write(
            {
                "calc.js": self.ADD_AND_MUL,
                "package.json": json.dumps({"scripts": {"test": "node --test"}}),
                "test/feature.test.js": self.MUL_TEST,
            }
        )
        framework = verify.detect_framework(project.root)
        base_suite = verify.baseline(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command="npm test --silent",
            timeout=120,
        )
        self.assertEqual(0, base_suite["receipt"]["exit_code"])
        broadened = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command="npm test --silent",
            base_suite=base_suite,
            timeout=120,
            new_behavior=True,
        )
        self.assertEqual(verify.PASS, broadened["verdict"], broadened)
        self.assertFalse(any("redefined" in reason for reason in broadened["unverified"]), broadened)
        self.assertEqual(0, broadened["checks"]["suite_on_candidate"]["exit_code"], broadened)
        self.assertEqual(0, broadened["checks"]["suite_base_definition_on_candidate"]["exit_code"], broadened)
        project.write(
            {
                "calc.js": self.ADD_BROKEN,
                "package.json": json.dumps({"scripts": {"test": "node --test test/feature.test.js"}}),
            }
        )
        narrowed = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command="npm test --silent",
            base_suite=base_suite,
            timeout=120,
            new_behavior=True,
        )
        self.assertEqual(verify.FAIL, narrowed["verdict"], narrowed)
        self.assertTrue(any("base suite definition" in reason for reason in narrowed["failures"]), narrowed)
        self.assertEqual(0, narrowed["checks"]["suite_on_candidate"]["exit_code"], narrowed)
        self.assertEqual(1, narrowed["checks"]["suite_base_definition_on_candidate"]["exit_code"], narrowed)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t1_edited_runner_file_cannot_hide_a_break_from_the_suite(self):
        seed, candidate = self._runner_suite_fixture()
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t2_narrowed_transitive_script_cannot_hide_a_break(self):
        seed = {
            "package.json": json.dumps(
                {"scripts": {"test": "npm run test:unit", "test:unit": "node --test test/calc.test.js"}}
            ),
            "calc.js": self.ADD_ONLY,
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "package.json": json.dumps(
                {"scripts": {"test": "npm run test:unit", "test:unit": "node --test test/feature.test.js"}}
            ),
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t3_workspace_script_narrowing_cannot_hide_a_break(self):
        seed = {
            "package.json": json.dumps({"private": True, "workspaces": ["web"]}),
            "web/package.json": json.dumps({"name": "web", "scripts": {"test": "node --test ../test/calc.test.js"}}),
            "calc.js": self.ADD_ONLY,
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "web/package.json": json.dumps({"name": "web", "scripts": {"test": "node --test ../test/feature.test.js"}}),
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate, suite_command="npm test --workspaces --silent")
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t4_package_field_narrowing_cannot_hide_a_break(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node run-tests.js"}, "suiteFile": "test/calc.test.js"}),
            "calc.js": self.ADD_ONLY,
            "run-tests.js": 'require("node:child_process").execSync("node --test "'
            '+ require("./package.json").suiteFile, {stdio: "inherit"});\n',
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "package.json": json.dumps({"scripts": {"test": "node run-tests.js"}, "suiteFile": "test/feature.test.js"}),
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t5_npmrc_script_shell_cannot_hide_a_break(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node --test test/calc.test.js"}}),
            "calc.js": self.ADD_ONLY,
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            ".npmrc": "script-shell=/usr/bin/true\n",
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t6_unrecognized_command_shape_cannot_hide_a_break(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node --test test/calc.test.js"}}),
            "calc.js": self.ADD_ONLY,
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "package.json": json.dumps({"scripts": {"test": "node --test test/feature.test.js"}}),
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate, suite_command="CI=1 npm test")
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t7_broadened_suite_definition_still_verifies(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node --test test/calc.test.js"}}),
            "calc.js": self.ADD_ONLY,
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_AND_MUL,
            "package.json": json.dumps({"scripts": {"test": "node --test"}}),
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(0, base_suite["receipt"]["exit_code"])
        self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"], result)
        self.assertEqual(0, result["checks"]["suite_base_definition_on_candidate"]["exit_code"], result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t8_break_without_definition_change_still_fails(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node --test test/calc.test.js"}}),
            "calc.js": self.ADD_ONLY,
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assertEqual(0, base_suite["receipt"]["exit_code"])
        self.assertEqual(1, result["checks"]["suite_on_candidate"]["exit_code"], result)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertTrue(
            any("passes on base but fails on the candidate" in reason for reason in result["failures"]), result
        )

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t9_indirect_runner_edit_cannot_hide_a_break(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
            "calc.js": self.ADD_ONLY,
            "run-tests.js": 'require("node:child_process").execSync("node --test "'
            '+ require("./scripts/select-tests.js").join(" "), {stdio: "inherit"});\n',
            "scripts/select-tests.js": "module.exports = ['test/calc.test.js'];\n",
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "scripts/select-tests.js": "module.exports = ['test/feature.test.js'];\n",
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t10_unrecognized_command_shapes_all_run_the_base_definition(self):
        commands = ["sh -c 'npm test'", "node --run test", "npm --prefix . test", "cd web && npm test"]
        if shutil.which("timeout"):
            commands = ["timeout 120 npm test"] + commands
        for suite_command in commands:
            with self.subTest(suite_command=suite_command):
                prefix = "web/" if suite_command == "cd web && npm test" else ""
                seed = {
                    prefix + "package.json": json.dumps({"scripts": {"test": "node --test test/calc.test.js"}}),
                    prefix + "calc.js": self.ADD_ONLY,
                    prefix + "test/calc.test.js": self.ADD_TEST,
                }
                candidate = {
                    prefix + "calc.js": self.ADD_BROKEN,
                    prefix + "package.json": json.dumps({"scripts": {"test": "node --test test/feature.test.js"}}),
                    prefix + "test/feature.test.js": self.MUL_TEST,
                }
                project, base_suite, result = self.node_verify(seed, candidate, suite_command=suite_command)
                self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t11_transitive_script_variants_cannot_hide_a_break(self):
        base_unit = json.dumps(
            {"scripts": {"test": "node --run test:unit", "test:unit": "node --test test/calc.test.js"}}
        )
        narrowed_unit = json.dumps(
            {"scripts": {"test": "node --run test:unit", "test:unit": "node --test test/feature.test.js"}}
        )
        base_runner = json.dumps(
            {"scripts": {"test": "node run-tests.js", "test:unit": "node --test test/calc.test.js"}}
        )
        narrowed_runner = json.dumps(
            {"scripts": {"test": "node run-tests.js", "test:unit": "node --test test/feature.test.js"}}
        )
        fixtures = {
            "A": (
                {"package.json": base_unit, "calc.js": self.ADD_ONLY, "test/calc.test.js": self.ADD_TEST},
                {"package.json": narrowed_unit},
            ),
            "B": (
                {
                    "package.json": base_runner,
                    "calc.js": self.ADD_ONLY,
                    "run-tests.js": 'require("node:child_process").execSync("npm run test:unit",'
                    ' {stdio: "inherit"});\n',
                    "test/calc.test.js": self.ADD_TEST,
                },
                {"package.json": narrowed_runner},
            ),
        }
        for name, (seed, package_change) in fixtures.items():
            with self.subTest(fixture=name):
                project, base_suite, result = self.node_verify(
                    seed, {"calc.js": self.ADD_BROKEN, "test/feature.test.js": self.MUL_TEST, **package_change}
                )
                self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(
        shutil.which("node") and shutil.which("npm") and shutil.which("pnpm"), "Node, npm and pnpm are required"
    )
    def test_t12_pnpm_workspace_script_narrowing_cannot_hide_a_break(self):
        seed = {
            "pnpm-workspace.yaml": "packages: ['web']\n",
            "web/package.json": json.dumps({"name": "web", "scripts": {"test": "node --test test/calc.test.js"}}),
            "web/calc.js": self.ADD_ONLY,
            "web/test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "web/calc.js": self.ADD_BROKEN,
            "web/package.json": json.dumps({"name": "web", "scripts": {"test": "node --test test/feature.test.js"}}),
            "web/test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate, suite_command="pnpm -r test")
        self.assert_base_definition_fails(base_suite, result)

    def base_definition_fault(self, receipt):
        """verify() over the T1 fixture with run_suite patched to return ``receipt`` for the
        base-definition run alone (the seam every suite run already goes through)."""
        seed, candidate = self._runner_suite_fixture()
        project = self.project(seed)
        project.write(candidate)
        real_run_suite = verify.run_suite

        def wrapped(framework, command, tree, evidence_dir, label, *, timeout):
            if Path(tree).name == "base-definition" and Path(tree).is_dir():
                return receipt
            return real_run_suite(framework, command, tree, evidence_dir, label, timeout=timeout)

        framework = verify.detect_framework(project.root)
        base_suite = verify.baseline(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command="npm test --silent",
            timeout=120,
        )
        with mock.patch.object(verify, "run_suite", wrapped):
            result = verify.verify(
                project.root,
                project.base,
                project.evidence,
                framework=framework,
                suite_command="npm test --silent",
                base_suite=base_suite,
                timeout=120,
                new_behavior=True,
            )
        return project, base_suite, result

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t13_incomplete_base_definition_run_stays_unverified_and_cleans_up(self):
        receipt = {
            "exit_code": None,
            "timed_out": False,
            "output": None,
            "tail": "injected incomplete base-definition run\n",
            "results": None,
            "results_expected": False,
            "supervision": {},
        }
        project, base_suite, result = self.base_definition_fault(receipt)
        self.assertEqual(0, base_suite["receipt"]["exit_code"])
        self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"], result)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertEqual([], result["failures"], result)
        self.assertTrue(any("base suite definition" in reason for reason in result["unverified"]), result)
        self.assertFalse((project.evidence / "scratch" / "base-definition").exists())

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t14_unstartable_base_definition_command_stays_unverified_and_cleans_up(self):
        receipt = {
            "exit_code": 127,
            "timed_out": False,
            "output": None,
            "tail": "sh: node: command not found\n",
            "results": None,
            "results_expected": False,
        }
        project, base_suite, result = self.base_definition_fault(receipt)
        self.assertEqual(0, base_suite["receipt"]["exit_code"])
        self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"], result)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertEqual([], result["failures"], result)
        self.assertTrue(any("base suite definition" in reason for reason in result["unverified"]), result)
        self.assertFalse((project.evidence / "scratch" / "base-definition").exists())

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t15_computed_selector_boundary_fails_closed_unverified(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
            "calc.js": self.ADD_ONLY,
            "run-tests.js": (
                'const fs = require("node:fs");\n'
                'const name = fs.readdirSync("scripts").filter(f => f.endsWith(".js"))[0];\n'
                'const list = require("./scripts/" + name);\n'
                'require("node:child_process").execSync("node --test " + list.join(" "),'
                ' {stdio: "inherit"});\n'
            ),
            "scripts/pick-list.js": "module.exports = ['test/calc.test.js'];\n",
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "scripts/pick-list.js": "module.exports = ['test/feature.test.js'];\n",
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assertEqual(0, base_suite["receipt"]["exit_code"])
        self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"], result)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertEqual([], result["failures"], result)
        self.assertTrue(any("base suite definition" in reason for reason in result["unverified"]), result)
        self.assertNotIn("suite_base_definition_on_candidate", result["checks"], result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t16_runner_imported_product_cannot_hide_a_break(self):
        seed, candidate = self._runner_suite_fixture()
        seed = {
            **seed,
            "run-tests.js": (
                'require("./calc.js");\n'
                'require("node:child_process").execSync("node --test test/calc.test.js",'
                ' {stdio: "inherit"});\n'
            ),
        }
        candidate = {
            **candidate,
            "run-tests.js": (
                'require("./calc.js");\n'
                'require("node:child_process").execSync('
                '"node --test test/feature.test.js", {stdio: "inherit"});\n'
            ),
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node"), "Node is required")
    def test_t17_shell_runner_command_runs_the_base_definition(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "sh test/run.sh"}}),
            "calc.js": self.ADD_ONLY,
            "test/run.sh": "node --test test/calc.test.js\n",
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "test/run.sh": "node --test test/feature.test.js\n",
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate, suite_command="sh test/run.sh")
        self.assert_base_definition_fails(base_suite, result)

    def test_selector_paths_follow_literals_and_dirname_joins(self):
        text = (
            'fs.readFileSync("tests.json", "utf8");\n'
            'fs.readFileSync(path.join(__dirname, "dir", "list.json"));\n'
            "fs.readFileSync(name);\n"
            'fs.readFileSync("a" + "b");\n'
        )
        self.assertEqual([("cwd", "tests.json"), ("file", "dir/list.json"), ("cwd", "ab")], verify._read_paths(text))
        self.assertEqual(
            ["sh test/run.sh", "inherit"],
            verify._exec_command_literals('execSync("sh test/run.sh", {stdio: "inherit"});\n'),
        )
        self.assertTrue(verify._shell_script("test/run.sh", "node list.js\n"))
        self.assertTrue(verify._shell_script("run", "#!/usr/bin/env bash\n"))
        self.assertFalse(verify._shell_script("run.js", "#!/usr/bin/env node\n"))

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_shell_script_selector_cannot_hide_a_break(self):
        """`node run-tests.js` → `sh test/run.sh` → `node list.js` stays on the base selector (#662)."""
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
            "calc.js": self.ADD_ONLY,
            "run-tests.js": 'require("node:child_process").execSync("sh test/run.sh", {stdio: "inherit"});\n',
            "test/run.sh": "node list.js\n",
            "list.js": 'require("node:child_process").execSync("node --test test/calc.test.js", {stdio: "inherit"});\n',
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "list.js": 'require("node:child_process").execSync("node --test test/feature.test.js",'
            ' {stdio: "inherit"});\n',
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_data_file_selector_cannot_hide_a_break(self):
        """A literal fs read of tests.json is part of the suite definition, not candidate product (#662)."""
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
            "calc.js": self.ADD_ONLY,
            "run-tests.js": (
                'const fs = require("node:fs");\n'
                'fs.readFileSync("./calc.js", "utf8");\n'
                'const list = JSON.parse(fs.readFileSync("tests.json", "utf8"));\n'
                'require("node:child_process").execSync("node --test " + list.join(" "),'
                ' {stdio: "inherit"});\n'
            ),
            "tests.json": '["test/calc.test.js"]\n',
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "tests.json": '["test/feature.test.js"]\n',
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_runner_config_selector_cannot_hide_a_break(self):
        """A mocha config the runner loads by convention stays on the base spec list (#662)."""
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node run-mocha.js"}}),
            "calc.js": self.ADD_ONLY,
            "run-mocha.js": (
                'const fs = require("node:fs");\n'
                'const name = "." + "mocharc.json";\n'
                'const spec = JSON.parse(fs.readFileSync(name, "utf8")).spec;\n'
                'require("node:child_process").execSync("node --test " + spec.join(" "),'
                ' {stdio: "inherit"});\n'
            ),
            ".mocharc.json": '{"spec": ["test/calc.test.js"]}\n',
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            ".mocharc.json": '{"spec": ["test/feature.test.js"]}\n',
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t18_pretest_deleting_the_old_test_cannot_hide_a_break(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node --test"}}),
            "calc.js": self.ADD_ONLY,
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "package.json": json.dumps({"scripts": {"test": "node --test", "pretest": "rm test/calc.test.js"}}),
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t19_npm_package_config_narrowing_cannot_hide_a_break(self):
        seed = {
            "package.json": json.dumps(
                {
                    "scripts": {"test": "node --test $npm_package_config_testfile"},
                    "config": {"testfile": "test/calc.test.js"},
                }
            ),
            "calc.js": self.ADD_ONLY,
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "package.json": json.dumps(
                {
                    "scripts": {"test": "node --test $npm_package_config_testfile"},
                    "config": {"testfile": "test/feature.test.js"},
                }
            ),
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t20_files_field_narrowing_cannot_hide_a_break(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node run-tests.js"}, "files": ["test/calc.test.js"]}),
            "calc.js": self.ADD_ONLY,
            "run-tests.js": 'require("node:child_process").execSync("node --test "'
            '+ require("./package.json").files.join(" "), {stdio: "inherit"});\n',
            "test/calc.test.js": self.ADD_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            "package.json": json.dumps({"scripts": {"test": "node run-tests.js"}, "files": ["test/feature.test.js"]}),
            "test/feature.test.js": self.MUL_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t21_npmrc_node_options_name_pattern_cannot_hide_a_break(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node --test"}}),
            "calc.js": self.ADD_ONLY,
            "test/calc.test.js": self.ADDITION_TEST,
        }
        candidate = {
            "calc.js": self.ADD_BROKEN,
            ".npmrc": "node-options=--test-name-pattern=multiplication\n",
            "test/feature.test.js": self.MULTIPLICATION_TEST,
        }
        project, base_suite, result = self.node_verify(seed, candidate)
        self.assert_base_definition_fails(base_suite, result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t22_harmless_suite_broadenings_still_verify(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node --test test/calc.test.js"}}),
            "calc.js": self.ADD_ONLY,
            "test/calc.test.js": self.ADD_TEST,
        }
        forms = {
            "A": {
                "calc.js": self.ADD_AND_MUL,
                "package.json": json.dumps({"scripts": {"test": "node --test test/calc.test.js test/feature.test.js"}}),
                "test/feature.test.js": self.MUL_TEST,
            },
            "B": {
                "calc.js": self.ADD_AND_MUL,
                "package.json": json.dumps({"scripts": {"test": "node --test --test-reporter=spec test/calc.test.js"}}),
                "test/feature.test.js": self.MUL_TEST,
            },
            "C": {
                "calc.js": self.ADD_AND_MUL,
                "app.js": "process.exitCode = 0;\n",
                "package.json": json.dumps({"scripts": {"test": "node --test test/calc.test.js && node app.js"}}),
                "test/feature.test.js": self.MUL_TEST,
            },
        }
        for form, candidate in forms.items():
            with self.subTest(form=form):
                project, base_suite, result = self.node_verify(seed, candidate)
                self.assertEqual(verify.PASS, result["verdict"], result)
                self.assertEqual(0, base_suite["receipt"]["exit_code"])
                self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"], result)
                self.assertEqual(0, result["checks"]["suite_base_definition_on_candidate"]["exit_code"], result)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_t23_patched_base_selector_edit_cannot_hide_a_break(self):
        seed = {
            "package.json": json.dumps({"scripts": {"test": "node run-tests.js"}}),
            "calc.js": self.ADD_ONLY,
            "run-tests.js": 'require("node:child_process").execSync("node --test test/calc.test.js",'
            ' {stdio: "inherit"});\n',
            "scripts/select-tests.js": "module.exports = ['test/calc.test.js'];\n",
            "test/calc.test.js": self.ADD_TEST,
        }
        patched_runner = (
            'require("node:child_process").execSync("node --test "'
            '+ require("./scripts/select-tests.js").join(" "), {stdio: "inherit"});\n'
        )
        project = self.project(seed)
        references.write({"run-tests.js": patched_runner}, project.root)
        patch = Path(project.temp.name) / "base.patch"
        patch.write_text(
            subprocess.run(
                ["git", "diff", "--", "run-tests.js"], cwd=project.root, check=True, capture_output=True, text=True
            ).stdout
        )
        self.assertTrue(patch.stat().st_size, "the base patch must rewrite run-tests.js")
        project.write(
            {
                "calc.js": self.ADD_BROKEN,
                "run-tests.js": patched_runner,
                "scripts/select-tests.js": "module.exports = ['test/feature.test.js'];\n",
                "test/feature.test.js": self.MUL_TEST,
            }
        )
        framework = verify.detect_framework(project.root)
        base_suite = verify.baseline(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command="npm test --silent",
            timeout=120,
            base_patch=patch,
        )
        result = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command="npm test --silent",
            base_suite=base_suite,
            timeout=120,
            new_behavior=True,
            base_patch=patch,
        )
        self.assert_base_definition_fails(base_suite, result)

    def test_ignored_vendor_reaches_scratch_probe_without_sharing_writes(self):
        project = self.project({**SEED, ".gitignore": "vendor/\n"})
        project.write({"vendor/example/resource.txt": "offline"})
        probe = (
            "python3 -c \"from pathlib import Path; p=Path('vendor/example/resource.txt'); "
            "assert p.read_text() == 'offline'; p.write_text('scratch edit')\""
        )
        result = verify.scratch_run(project.root, project.evidence, command=probe)
        self.assertEqual(0, result["exit_code"], result)
        self.assertEqual("offline", project_file(project, "vendor/example/resource.txt"))
        self.assertEqual({}, verify.changed_files(project.root, project.base))

    def test_new_vendor_source_is_not_copied_into_the_unfixed_baseline(self):
        seed = {
            "app.py": "from pathlib import Path\ndef value():\n    path = Path('vendor/value.txt')\n"
            "    return int(path.read_text()) if path.exists() else 0\n",
            "test_app.py": "import unittest\nfrom app import value\nclass ValueTests(unittest.TestCase):\n"
            "    def test_smoke(self):\n        self.assertIsInstance(value(), int)\n",
        }
        for ignored in (False, True):
            with self.subTest(force_tracked_under_ignore=ignored):
                project = self.project({**seed, ".gitignore": "vendor/\n" if ignored else ""})
                project.write(
                    {
                        "vendor/value.txt": "1\n",
                        "test_app.py": seed["test_app.py"]
                        + "    def test_restores_value(self):\n        self.assertEqual(1, value())\n",
                    }
                )
                if ignored:
                    git(project.root, "add", "-f", "vendor/value.txt")
                    project.write({"vendor/offline.txt": "ignored dependency\n"})
                result = project.verify(dependencies_from=project.root)
                self.assertEqual(verify.PASS, result["verdict"], result["failures"])
                self.assertIn("test_app.ValueTests.test_restores_value", result["fail_to_pass"])

    def test_reference_fix_is_proven_by_a_fail_to_pass_flip(self):
        project = self.project()
        project.write(REFERENCE)
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual("derived:unittest", result["commands"]["regression_source"])
        self.assertNotEqual(0, result["checks"]["regression_on_base"]["exit_code"])
        self.assertEqual(0, result["checks"]["regression_on_candidate"]["exit_code"])
        self.assertEqual(["test_greet.py"], result["test_files"])
        self.assertEqual(["greet.py"], result["source_files"])
        self.assertEqual(1, result["stats"]["source_files"])
        # Verification never runs in, or changes, the candidate workspace.
        self.assertEqual([], [line for line in git(project.root, "worktree", "list").splitlines()[1:]])
        self.assertEqual({"greet.py", "test_greet.py"}, set(result["changes"]))

    def test_linked_task_inherits_project_dependencies_for_parent_and_child_tests(self):
        project = self.project({**SEED, ".gitignore": ".venv/\n"})
        environment = project.root / ".venv"
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(environment)],
            check=True,
            capture_output=True,
            text=True,
        )
        python = environment / "bin" / "python"
        site = Path(
            subprocess.check_output(
                [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True
            ).strip()
        )
        (site / "worktree_test_dependency.py").write_text("value = 42\n")
        task = Path(project.temp.name) / "linked-task"
        git(project.root, "worktree", "add", "--detach", str(task), project.base)
        extra = (
            "\nclass Dependencies(unittest.TestCase):\n"
            "    def test_project_dependency_is_available(self):\n"
            "        import worktree_test_dependency\n"
            "        self.assertEqual(42, worktree_test_dependency.value)\n"
            "    def test_fixture_child_uses_the_same_environment(self):\n"
            "        import subprocess\n"
            "        child = subprocess.run(['python3', '-c', "
            "'import worktree_test_dependency; assert worktree_test_dependency.value == 42'], "
            "capture_output=True, text=True)\n"
            "        self.assertEqual(0, child.returncode, child.stderr)\n"
        )
        candidate = {**REFERENCE, "test_greet.py": REFERENCE["test_greet.py"] + extra}
        references.write(candidate, task)
        framework = verify.detect_framework(task)
        self.assertEqual(str(python), framework.python)
        result = verify.verify(
            task, project.base, project.evidence, framework=framework, dependencies_from=task, timeout=120
        )
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertIn("test_greet.Dependencies.test_fixture_child_uses_the_same_environment", result["pass_to_pass"])
        self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"])
        self.assertFalse((task / ".venv").exists(), "verification must not modify the task checkout")

    def test_a_task_local_environment_overrides_the_main_checkout(self):
        project = self.project()
        for root in (project.root, Path(project.temp.name) / "linked-task"):
            if root != project.root:
                git(project.root, "worktree", "add", "--detach", str(root), project.base)
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / ".venv" / "bin" / "python").symlink_to(sys.executable)
        task = Path(project.temp.name) / "linked-task"
        self.assertEqual(str(task / ".venv" / "bin" / "python"), verify.python_for(task))

    def test_fix_without_a_regression_test_fails(self):
        project = self.project()
        project.write({"greet.py": REFERENCE["greet.py"]})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("No regression test" in reason for reason in result["failures"]))

    def test_fix_without_a_test_is_only_unverified_when_allowed(self):
        project = self.project()
        project.write({"greet.py": REFERENCE["greet.py"]})
        result = project.verify(allow_no_test=True)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)

    def test_a_test_that_passes_on_the_unfixed_code_proves_nothing(self):
        project = self.project()
        vacuous = SEED["test_greet.py"].replace(
            "    def test_ada(self):",
            "    def test_blank(self):\n        self.assertTrue(True)\n\n    def test_ada(self):",
        )
        project.write({"greet.py": REFERENCE["greet.py"], "test_greet.py": vacuous})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("do not reproduce the bug" in reason for reason in result["failures"]), result)

    def test_only_tests_changed_is_not_a_fix(self):
        project = self.project()
        project.write({"test_greet.py": REFERENCE["test_greet.py"]})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("Only test files changed" in reason for reason in result["failures"]))
        self.assertTrue(any("fail on the candidate" in reason for reason in result["failures"]), result)

    def test_preserve_only_coverage_may_add_a_test_that_passes_on_the_base(self):
        project = self.project()
        extra = project_file(project, "test_greet.py").replace(
            '    def test_ada(self):\n        self.assertEqual(greet("Ada"), "Hello, Ada")\n',
            '    def test_ada(self):\n        self.assertEqual(greet("Ada"), "Hello, Ada")\n\n'
            '    def test_ada_still_greets(self):\n        self.assertEqual(greet("Ada"), "Hello, Ada")\n',
        )
        project.write({"test_greet.py": extra})
        result = project.verify(new_behavior=True, preserve_only=True)
        self.assertEqual(verify.PASS, result["verdict"], result["failures"] + result["unverified"])
        self.assertTrue(
            any(name.endswith("test_ada_still_greets") for name in result["pass_to_pass"]), result["pass_to_pass"]
        )

    def test_removing_an_existing_test_is_rejected(self):
        project = self.project()
        weakened = REFERENCE["test_greet.py"].replace("    def test_two_arg(self):", "    def two_arg_disabled(self):")
        project.write({"greet.py": REFERENCE["greet.py"], "test_greet.py": weakened})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("test_greet.py::test_two_arg" in reason for reason in result["failures"]), result)

    def test_deleting_a_test_file_is_rejected(self):
        files = {
            **SEED,
            "tests/test_extra.py": "import unittest\n\nclass T(unittest.TestCase):\n"
            "    def test_ok(self):\n        pass\n",
        }
        project = self.project(files)
        (project.root / "tests/test_extra.py").unlink()
        project.write(REFERENCE)
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("deleted" in reason for reason in result["failures"]), result)

    def test_a_fix_that_breaks_another_test_fails_the_suite(self):
        project = self.project()
        broken = REFERENCE["greet.py"].replace('return f"Hello, {name}"', 'return f"Hi, {name}"')
        project.write({"greet.py": broken, "test_greet.py": REFERENCE["test_greet.py"]})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(
            any(
                "fail on the candidate" in reason or "fails on the candidate" in reason for reason in result["failures"]
            ),
            result,
        )

    def test_pre_existing_failures_do_not_block_when_nothing_new_fails(self):
        flaky = (
            "import unittest\n\nclass Env(unittest.TestCase):\n"
            "    def test_needs_network(self):\n        self.fail('no network in CI')\n"
        )
        project = self.project({**SEED, "test_env.py": flaky})
        project.write(REFERENCE)
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual("failing_tests", result["baseline"]["health"])
        self.assertTrue(any("already failed on base" in note for note in result["notes"]), result)

    def test_new_failures_are_named_even_when_base_already_fails(self):
        flaky = (
            "import unittest\n\nclass Env(unittest.TestCase):\n"
            "    def test_needs_network(self):\n        self.fail('no network in CI')\n"
        )
        project = self.project({**SEED, "test_env.py": flaky})
        broken = REFERENCE["greet.py"].replace('return f"Hello, {name}"', 'return f"Hi, {name}"')
        project.write({"greet.py": broken, "test_greet.py": REFERENCE["test_greet.py"]})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("test_ada" in reason for reason in result["failures"]), result)

    def test_pre_existing_failure_in_the_changed_test_file_is_not_counted(self):
        """The regression test shares a file with an environment-dependent failure (common upstream)."""
        needs_env = (
            "\n    def test_needs_secret(self):\n"
            "        self.assertTrue(__import__('os').environ.get('NO_SUCH_SECRET_XYZ'))\n"
        )
        seed = {
            **SEED,
            "test_greet.py": SEED["test_greet.py"].replace("\n\nif __name__", needs_env + "\n\nif __name__"),
        }
        project = self.project(seed)
        project.write(
            {
                "greet.py": REFERENCE["greet.py"],
                "test_greet.py": REFERENCE["test_greet.py"].replace("\n\nif __name__", needs_env + "\n\nif __name__"),
            }
        )
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["test_greet.TestGreet.test_blank_name_rejected"], result["fail_to_pass"])
        self.assertTrue(any("already fail on base were not counted" in note for note in result["notes"]), result)

    def test_a_new_test_the_fix_does_not_fix_is_not_excused(self):
        still_broken = REFERENCE["test_greet.py"].replace(
            "\n\nif __name__",
            "\n    def test_tab_name(self):\n"
            "        proc = subprocess.run([sys.executable, 'greet.py', 'A\\tB'], capture_output=True)\n"
            "        self.assertEqual(3, proc.returncode)\n\n\nif __name__",
        )
        project = self.project()
        project.write({"greet.py": REFERENCE["greet.py"], "test_greet.py": still_broken})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(
            any("fail on the candidate: test_greet.TestGreet.test_tab_name" in reason for reason in result["failures"]),
            result,
        )

    @unittest.skipUnless(verify._python_can_import(sys.executable, "pytest"), "pytest is not installed")
    def test_pytest_projects_use_junit_results(self):
        files = {
            "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
            "src/calc/__init__.py": "def mean(values):\n    return sum(values) / len(values)\n",
            "tests/test_calc.py": "from calc import mean\n\n\ndef test_mean():\n    assert mean([1, 2, 3]) == 2\n",
        }
        project = self.project(files)
        project.write(
            {
                "src/calc/__init__.py": "def mean(values):\n    if not values:\n"
                "        raise ValueError('empty')\n    return sum(values) / len(values)\n",
                "tests/test_calc.py": files["tests/test_calc.py"] + "\n\ndef test_empty():\n"
                "    import pytest\n    with pytest.raises(ValueError):\n        mean([])\n",
            }
        )
        framework = verify.detect_framework(project.root, python=sys.executable)
        self.assertEqual("pytest", framework.name)
        base_suite = verify.baseline(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=framework.suite,
            timeout=120,
        )
        result = verify.verify(
            project.root, project.base, project.evidence, framework=framework, base_suite=base_suite, timeout=120
        )
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["tests.test_calc::test_empty"], result["fail_to_pass"])

    def test_scratch_tree_code_wins_over_an_editable_install(self):
        """Review r12: a .pth in a linked venv must not import the user's checkout instead."""
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            (tree / "src").mkdir()
            env = verify.test_environment(tree, {"PYTHONPATH": "/elsewhere"})
            self.assertEqual([str(tree / "src"), str(tree), "/elsewhere"], env["PYTHONPATH"].split(os.pathsep))
            self.assertEqual("1", env["CI"])

    def test_a_parent_tests_package_cannot_shadow_the_fixture_tests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tree = root / "tree"
            parent = root / "checkout"
            (tree / "tests").mkdir(parents=True)
            (parent / "tests").mkdir(parents=True)
            (parent / "tests" / "__init__.py").write_text("")
            other = root / "libs"
            other.mkdir()
            inherited = os.pathsep.join([str(parent), str(other)])
            env = verify.test_environment(tree, {"PYTHONPATH": inherited})
            self.assertEqual([str(tree), str(other)], env["PYTHONPATH"].split(os.pathsep))
            bare = root / "bare"
            bare.mkdir()
            env = verify.test_environment(bare, {"PYTHONPATH": inherited})
            self.assertEqual([str(bare), str(parent), str(other)], env["PYTHONPATH"].split(os.pathsep))
            (tree / "tests" / "test_local.py").write_text(
                "import unittest\n\nclass T(unittest.TestCase):\n"
                "    def test_here(self):\n        self.assertIn('tree', __file__)\n"
            )
            (parent / "tests" / "test_local.py").write_text("raise SystemExit('parent package')\n")
            log = root / "suite.log"
            receipt = verify.run_command(
                f"{sys.executable} -m unittest tests.test_local -q", tree, log, env={"PYTHONPATH": inherited}
            )
            self.assertEqual(0, receipt["exit_code"], Path(receipt["output"]).read_text()[-500:])

    @mock.patch.dict(os.environ, {"PYTHONPATH": ""})
    def test_generated_version_file_reaches_the_scratch_trees(self):
        """A setuptools-scm/hatch-vcs package imports a git-ignored _version.py that
        exists only where the project was installed. The fix is made in a separate task
        worktree, as in a real run; the proof must still import the package.
        Isolate the fixture's namespace tests from the controller checkout's tests
        package on inherited PYTHONPATH; patch.dict restores the outer environment."""
        project = self.project(
            {
                ".gitignore": "src/pkg/_version.py\nbuild/\n",
                "src/pkg/__init__.py": "from ._version import VERSION\n",
                "src/pkg/calc.py": "def mean(values):\n    return sum(values) / len(values)\n",
                "tests/test_calc.py": "import unittest\n\nfrom pkg.calc import mean\n\n\n"
                "class Mean(unittest.TestCase):\n    def test_mean(self):\n"
                "        self.assertEqual(2, mean([2]))\n",
            }
        )
        project.write(
            {
                "src/pkg/_version.py": "VERSION = '1.0'\n",
                "build/lib/pkg/calc.py": "raise SystemExit('stale build output')\n",
            }
        )
        task = Path(project.temp.name) / "task"
        git(project.root, "worktree", "add", "-q", "--detach", str(task), project.base)
        self.addCleanup(git, project.root, "worktree", "remove", "--force", str(task))
        references.write(
            {
                "src/pkg/calc.py": "def mean(values):\n    return sum(values) / len(values) if values else 0\n",
                "tests/test_calc.py": project_file(project, "tests/test_calc.py")
                + "\n    def test_empty(self):\n        self.assertEqual(0, mean([]))\n",
            },
            task,
        )
        framework = verify.detect_framework(task)
        base_suite = verify.baseline(
            task,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=framework.suite,
            timeout=120,
            dependencies_from=project.root,
        )
        result = verify.verify(
            task,
            project.base,
            project.evidence,
            framework=framework,
            base_suite=base_suite,
            timeout=120,
            dependencies_from=project.root,
        )
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["tests.test_calc.Mean.test_empty"], result["fail_to_pass"])
        # The generated file is shared context, not part of the fix; build output is not copied.
        self.assertEqual({"src/pkg/calc.py": "modified", "tests/test_calc.py": "modified"}, result["changes"])
        tree = Path(project.temp.name) / "tree"
        git(project.root, "worktree", "add", "-q", "--detach", str(tree), project.base)
        self.addCleanup(git, project.root, "worktree", "remove", "--force", str(tree))
        self.assertEqual(["src/pkg/_version.py"], verify.copy_generated_sources(project.root, tree))
        self.assertFalse((tree / "build").exists())

        # Receipt identity must bind the same dependency-owned input that the
        # scratch tree imports, without binding excluded stale build output.
        def generated_identity():
            return verify.execution_identity(task, dependencies_from=project.root, full=False)[
                "generated_dependency_sources"
            ]

        generated = generated_identity()
        self.assertEqual(["src/pkg/_version.py"], sorted(generated))
        project.write({"build/lib/pkg/calc.py": "raise SystemExit('different stale output')\n"})
        self.assertEqual(generated, generated_identity())
        project.write({"src/pkg/_version.py": "VERSION = '2.0'\n"})
        self.assertNotEqual(generated, generated_identity())
        (project.root / "src/pkg/_version.py").unlink()
        self.assertEqual({}, generated_identity())

    def test_an_in_place_proof_does_not_run_an_ignored_file_the_run_added(self):
        # #529: the ignored file fails test_add only in the base scratch tree, so
        # preservation would treat the candidate's break as already present.
        seed = {
            "calc.py": "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a + b\n",
            "test_calc.py": "import unittest\nimport calc\n\nclass Add(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(5, calc.add(2, 3))\n",
            "test_other.py": "import unittest\n\nclass Other(unittest.TestCase):\n"
            "    def test_ok(self):\n        self.assertEqual(1, 1)\n",
        }
        project = self.project(seed)
        record = verify.generated_source_record(project.root)
        self.assertEqual({}, record)
        # Capture before the Builder edits, through the launch-input policy.
        import autocode_launch_inputs as launch_inputs

        state = {
            "base_commit": project.base,
            "settings": {},
            "iteration": 1,
            "stages": [],
            "history": [],
            "goal_contract": {"body": {"task_kind": "bugfix"}},
        }
        project.evidence = project.evidence.resolve()
        project.evidence.mkdir()
        launch_inputs.record(state, project.root, project.evidence)
        project.write(
            {
                "calc.py": "def add(a, b):\n    return a * b\n\ndef sub(a, b):\n    return a - b\n",
                "test_feature.py": "import unittest\nimport calc\n\nclass Sub(unittest.TestCase):\n"
                "    def test_t1_sub_is_correct(self):\n"
                "        self.assertEqual(1, calc.sub(3, 2))\n",
                "test_aaa_env.py": "import os\nimport calc\n"
                "if '/baseline' in os.getcwd().replace('\\\\', '/'):\n"
                "    calc.add = lambda a, b: -999\n",
            }
        )
        exclude = project.root / ".git" / "info" / "exclude"
        exclude.write_text(exclude.read_text().rstrip() + "\ntest_aaa_env.py\n")
        self.assertEqual(["test_aaa_env.py"], sorted(verify.generated_source_record(project.root)))
        proof = regression.prove(state, project.root, project.evidence)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertTrue(any("test_calc.Add.test_add" in reason for reason in proof["failures"]), proof)
        self.assertTrue(any("test_aaa_env.py" in note and "added since" in note for note in proof["notes"]), proof)
        direct = subprocess.run(
            [sys.executable, "-m", "unittest", "test_calc"], cwd=project.root, capture_output=True, text=True
        )
        self.assertEqual(1, direct.returncode, direct.stdout + direct.stderr)

        old = {
            "base_commit": project.base,
            "settings": {},
            "iteration": 1,
            "stages": [],
            "history": [],
            "goal_contract": {"body": {"task_kind": "bugfix"}},
        }
        missing = regression.prove(old, project.root, project.evidence / "unrecorded")
        self.assertEqual(verify.UNVERIFIED, missing["verdict"], missing)
        self.assertTrue(
            any("record" in reason and "test_aaa_env.py" in reason for reason in missing["unverified"]), missing
        )

    def test_a_recorded_generated_file_is_copied_until_its_bytes_change(self):
        project = self.project(
            {
                ".gitignore": "src/pkg/_version.py\ntest_aaa_env.py\n",
                "src/pkg/__init__.py": "",
                "src/pkg/calc.py": "def mean(values):\n    return sum(values) / len(values)\n",
            }
        )
        project.write({"src/pkg/_version.py": "VERSION = '1.0'\n"})
        record = verify.generated_source_record(project.root)
        project.write({"test_aaa_env.py": "import calc\n"})
        tree = Path(project.temp.name) / "tree"
        git(project.root, "worktree", "add", "-q", "--detach", str(tree), project.base)
        self.addCleanup(git, project.root, "worktree", "remove", "--force", str(tree))
        self.assertEqual(["src/pkg/_version.py"], verify.copy_generated_sources(project.root, tree, record=record))
        self.assertFalse((tree / "test_aaa_env.py").exists())
        _trusted, notes, omitted = verify.classify_generated_sources(project.root, record)
        self.assertEqual(["test_aaa_env.py"], omitted)
        self.assertTrue(any("added during the run" in note for note in notes), notes)
        project.write({"src/pkg/_version.py": "VERSION = '2.0'\n"})
        trusted, notes, omitted = verify.classify_generated_sources(project.root, record)
        self.assertEqual([], trusted)
        self.assertEqual(["src/pkg/_version.py", "test_aaa_env.py"], omitted)
        self.assertTrue(any("changed during the run" in note and "_version.py" in note for note in notes), notes)

    def test_a_suite_that_cannot_start_is_broken_not_failing(self):
        """Review finding 15: command-not-found and no-results runs stop before any model call."""
        receipt = {"timed_out": False, "results": None, "results_expected": False}
        self.assertEqual("broken", verify.suite_health({**receipt, "exit_code": 127}))
        self.assertEqual("failing", verify.suite_health({**receipt, "exit_code": 1}))
        self.assertEqual("broken", verify.suite_health({**receipt, "exit_code": 0, "results_expected": True}))
        self.assertEqual("timeout", verify.suite_health({**receipt, "exit_code": None, "timed_out": True}))

    def test_partial_or_timed_out_baseline_is_not_a_fail_to_pass_proof(self):
        project = self.project()
        project.write(REFERENCE)
        framework = verify.detect_framework(project.root)
        for timed_out in (True, False):
            with self.subTest(timed_out=timed_out):

                def run(_framework, command, tree, out, label, **kwargs):
                    base = label == "regression-on-base"
                    return {
                        "command": command,
                        "exit_code": None if base and timed_out else 1 if base else 0,
                        "timed_out": base and timed_out,
                        "results_expected": True,
                        "output": "unused",
                        "tail": "",
                        "results": {
                            "passed": [] if base else ["test_greet.Case.test_empty"],
                            "failed": ["test_greet.Case.test_empty"] if base else [],
                            "skipped": [],
                            "collection_errors": [],
                            "total": 1,
                            "complete": not base,
                        },
                    }

                with mock.patch.object(verify, "run_suite", side_effect=run):
                    result = verify.verify(project.root, project.base, project.evidence, framework=framework)
                self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
                self.assertTrue(any("base" in reason for reason in result["unverified"]), result)

    @mock.patch.dict(os.environ, {"PYTHONPATH": ""})
    def test_untracked_new_test_file_counts_as_the_regression_test(self):
        # The nested fixture has a namespace tests directory; inherited controller
        # PYTHONPATH otherwise resolves tests.test_blank to the outer tests package.
        project = self.project()
        new_test = (
            "import subprocess, sys, unittest\n\nclass Blank(unittest.TestCase):\n"
            "    def test_blank(self):\n"
            "        proc = subprocess.run([sys.executable, 'greet.py', ''], capture_output=True)\n"
            "        self.assertEqual(2, proc.returncode)\n"
        )
        project.write({"greet.py": REFERENCE["greet.py"], "tests/test_blank.py": new_test})
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual({"greet.py": "modified", "tests/test_blank.py": "added"}, result["changes"])

    def test_candidate_workspace_changes_are_detected_after_commit_too(self):
        project = self.project()
        project.write(REFERENCE)
        git(project.root, "add", "-A")
        git(project.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "builder commit")
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)

    def test_builder_regression_command_must_name_a_changed_test(self):
        chosen = verify.select_commands(
            None, ["tests/test_blank.py"], reported={"regression_command": "grep -q strip greet.py"}
        )
        self.assertIsNone(chosen["regression"])
        self.assertTrue(chosen["notes"])
        chosen = verify.select_commands(
            None, ["tests/test_blank.py"], reported={"regression_command": "python -m pytest tests/test_blank.py"}
        )
        self.assertEqual("builder", chosen["regression_source"])

    def test_explicit_commands_win_over_detection(self):
        framework = verify.Framework("pytest", "python -m pytest -q", python="python")
        chosen = verify.select_commands(
            framework, ["tests/test_a.py"], suite_command="make check", regression_command="make one"
        )
        self.assertEqual(
            ("make check", "explicit", "make one", "explicit"),
            (chosen["suite"], chosen["suite_source"], chosen["regression"], chosen["regression_source"]),
        )

    def test_targeted_python_commands_include_django_tests_modules(self):
        for name in ("pytest", "unittest"):
            with self.subTest(framework=name):
                framework = verify.Framework(name, "unused suite", python=sys.executable)
                command = framework.targeted(
                    ["tests/constraints/tests.py", "tests/constraints/helpers.py", "tests/constraints/conftest.py"]
                )
                self.assertIn("tests/constraints/tests.py", command)
                self.assertNotIn("helpers.py", command)
                self.assertNotIn("conftest.py", command)

    def test_test_path_classification(self):
        for path in (
            "tests/test_x.py",
            "pkg/test_x.py",
            "x_test.go",
            "src/a.test.ts",
            "spec/a_spec.rb",
            "src/test/java/FooTest.java",
            "__tests__/a.js",
            "pkg/testdata/in.txt",
            "conftest.py",
            "src/__snapshots__/x.test.ts.snap",
            "test/unit/helpers.js",
            "pkg/core/tests/data.json",
            "TestParser.java",
        ):
            self.assertTrue(verify.is_test_path(path), path)
        for path in (
            "greet.py",
            "src/contest.py",
            "latest.py",
            "src/protest/x.go",
            "attestation.rs",
            "src/Latest.java",
            "src/Contest.kt",
            "numpy/testing/utils.py",
            "django/test/client.py",
            "api/spec/openapi.yaml",
        ):
            self.assertFalse(verify.is_test_path(path), path)

    def test_framework_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git(root, "init", "-q")
            (root / "go.mod").write_text("module x\n")
            git(root, "add", "-A")
            framework = verify.detect_framework(root)
            self.assertEqual(("go", "go test ./..."), (framework.name, framework.suite))
            self.assertEqual("go test ./pkg/a", framework.targeted(["pkg/a/a_test.go"]))
            package = {"scripts": {"test": "jest"}, "devDependencies": {"jest": "29"}}
            (root / "package.json").write_text(json.dumps(package))
            (root / "go.mod").unlink()
            git(root, "add", "-A")
            framework = verify.detect_framework(root)
            self.assertEqual(("jest", "npm test --silent"), (framework.name, framework.suite))
            self.assertIn("jest src/a.test.js", framework.targeted(["src/a.test.js"]))

    def test_a_new_projects_untracked_files_are_detected_and_ignored_ones_are_not(self):
        # AutoCode never commits: in a new project the Builder's code and tests are all untracked.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git(root, "init", "-q")
            (root / "README.md").write_text("seed\n")
            git(root, "add", "-A")
            git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-q", "-m", "seed")
            (root / ".autocode").mkdir()
            (root / ".autocode" / ".gitignore").write_text("*\n")
            (root / ".autocode" / "test_runner_owned.py").write_text("import unittest\n")
            self.assertIsNone(verify.detect_framework(root, python=sys.executable))
            (root / "greet.py").write_text("print('hi')\n")
            (root / "test_greet.py").write_text("import unittest\n")
            framework = verify.detect_framework(root, python=sys.executable)
            self.assertEqual("unittest", framework.name)
            self.assertIn("-m unittest discover", framework.suite)

    def test_unittest_results_are_parsed_per_test(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "out.log"
            log.write_text(
                "test_a (m.C.test_a) ... ok\n"
                "test_b (m.C.test_b)\nA docstring line ... FAIL\n"
                "test_d (m.C.test_d) ... noisy output from the test\nok\n"
                "test_e (m.C.test_e) ... skipped 'needs network'\n"
                "test_f (m.C.test_f) ... expected failure\n"
                "======================================================================\n"
                "FAIL: test_b (m.C.test_b)\nERROR: test_c (m.C)\n"
                "----------------------------------------------------------------------\n"
                "Ran 6 tests in 0.1s\n\nFAILED (failures=1, errors=1, skipped=1, expected failures=1)\n"
            )
            framework = verify.Framework("unittest", "python -m unittest", python="python")
            results = verify.per_test_results(framework, {"output": str(log)}, Path(temp) / "none.xml")
            self.assertEqual(
                {
                    "passed": ["m.C.test_a", "m.C.test_d"],
                    "failed": ["m.C.test_b", "m.C::test_c"],
                    "skipped": ["m.C.test_e", "m.C.test_f"],
                    "collection_errors": [],
                    "uncollected": [],
                    "total": 6,
                    "complete": True,
                },
                results,
            )

    def test_class_and_module_fixtures_outside_ran_count_keep_unittest_runs_complete(self):
        """#416: "Ran 2 tests ... OK (skipped=1)" from a setUpClass skip was an incomplete, broken run."""
        classes = "\nclass Runs(unittest.TestCase):\n    def test_one(self): pass\n    def test_two(self): pass\n"
        project = self.project({"test_x.py": "import unittest\n" + classes})
        command = f"{sys.executable} -m unittest -v"
        cases = {
            "setUpClass skip": (
                "class Skipped(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls):\n"
                "        raise unittest.SkipTest('needs a service')\n    def test_a(self): pass\n",
                0,
                [],
                "passing",
            ),
            "setUpModule skip in another module": ("", 0, [], "passing"),
            "setUpClass skip, class cleanup error": (
                "class Skipped(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls):\n"
                "        cls.addClassCleanup(int, 'x')\n        raise unittest.SkipTest('needs a service')\n"
                "    def test_a(self): pass\n",
                1,
                ["test_x.Skipped::setUpClass"],
                "failing_tests",
            ),
            "setUpClass error": (
                "class Broken(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls):\n"
                "        raise RuntimeError('down')\n    def test_a(self): pass\n",
                1,
                ["test_x.Broken::setUpClass"],
                "failing_tests",
            ),
            "tearDownModule error": (
                "def tearDownModule():\n    raise RuntimeError('down')\n",
                1,
                ["test_x::tearDownModule"],
                "failing_tests",
            ),
        }
        beside = {
            "setUpModule skip in another module": "import unittest\ndef setUpModule():\n"
            "    raise unittest.SkipTest('needs a service')\n"
            "class Db(unittest.TestCase):\n    def test_a(self): pass\n"
        }
        for name, (extra, exit_code, failed, health) in cases.items():
            with self.subTest(name):
                project.write({"test_x.py": "import unittest\n" + extra + classes, "test_y.py": beside.get(name, "")})
                result = verify.scratch_run(project.root, project.evidence / name.replace(" ", "-"), command=command)
                self.assertEqual((exit_code, ""), (result["exit_code"], result["error"]), result)
                self.assertEqual(["test_x.Runs.test_one", "test_x.Runs.test_two"], result["results"]["passed"])
                self.assertEqual((failed, []), (result["results"]["failed"], result["results"]["skipped"]))
                self.assertTrue(schedule.complete_results(result), result)
                self.assertEqual(health, verify.suite_health(result))
        # Fixtures never stand in for tests: zero tests, or a missing test line, stays incomplete.
        zero = {
            "module skip": (
                "def setUpModule():\n    raise unittest.SkipTest('no')\n" + classes,
                (0,),
                "Test command reported zero tests or incomplete per-test results",
            ),
            # Newer Python releases use NO_TESTS (5) even when a class fixture failed.
            "only a setUpClass error": (cases["setUpClass error"][0], (1, 5), ""),
        }
        for name, (source, exit_codes, error) in zero.items():
            with self.subTest(name):
                project.write({"test_x.py": "import unittest\n" + source})
                result = verify.scratch_run(project.root, project.evidence / name.replace(" ", "-"), command=command)
                self.assertIn(result["exit_code"], exit_codes, result)
                self.assertEqual((error, 0), (result["error"], result["results"]["total"]), result)
                self.assertFalse(schedule.complete_results(result))
                self.assertEqual("broken", verify.suite_health(result))
        log = project.evidence / "truncated.log"
        log.write_text(
            "setUpClass (m.S) ... skipped 'x'\ntest_a (m.C.test_a) ... ok\n"
            "tearDownClass (m.C) ... ERROR\n\nRan 2 tests in 0.1s\n\nFAILED (errors=1, skipped=1)\n"
        )
        framework = verify.Framework("unittest", command, python=sys.executable)
        results = verify.per_test_results(framework, {"output": str(log)}, project.evidence / "none.xml")
        self.assertEqual(
            (["m.C.test_a"], ["m.C::tearDownClass"], [], 3, False),
            (results["passed"], results["failed"], results["skipped"], results["total"], results["complete"]),
        )
        self.assertFalse(schedule.complete_results({"results": results}))

    def test_a_suite_of_only_fixture_errors_never_proves_existing_behavior_kept(self):
        """Review of #416: "Ran 0 tests" and one setUpModule ERROR on base and candidate is not a PASS."""
        project = self.project(
            {
                "calc.py": "def add(a, b):\n    return a - b\n",
                "test_db.py": "import unittest\ndef setUpModule():\n"
                "    raise RuntimeError('database is not reachable')\n"
                "class Db(unittest.TestCase):\n    def test_query(self): pass\n",
            }
        )
        project.write(
            {
                "calc.py": "def add(a, b):\n    return a + b\n",
                "test_calc.py": "import unittest\nfrom calc import add\nclass Add(unittest.TestCase):\n"
                "    def test_add(self):\n        self.assertEqual(3, add(1, 2))\n",
            }
        )
        framework = verify.detect_framework(project.root)
        suite = f"{sys.executable} -m unittest -v test_db"
        base_suite = verify.baseline(
            project.root, project.base, project.evidence, framework=framework, suite_command=suite, timeout=120
        )
        result = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            base_suite=base_suite,
            suite_command=suite,
            timeout=120,
        )
        self.assertEqual(
            (verify.UNVERIFIED, ["The project suite reported zero tests or incomplete per-test results"]),
            (result["verdict"], result["unverified"]),
            result,
        )
        self.assertEqual(["test_calc.Add.test_add"], result["fail_to_pass"], result)
        self.assertEqual(
            ("broken", ["test_db::setUpModule"], 0),
            (
                base_suite["health"],
                base_suite["receipt"]["results"]["failed"],
                base_suite["receipt"]["results"]["total"],
            ),
        )

    def test_pytest_failure_then_teardown_error_is_one_complete_failed_test(self):
        """pytest's JUnit XML reports a failing test whose teardown errors as two testcases of one id."""
        with tempfile.TemporaryDirectory() as temp:
            xml = Path(temp) / "out.junit.xml"
            case = '<testcase classname="t" name="test_a"><{0} message="m">x</{0}></testcase>'
            xml.write_text(
                '<testsuites><testsuite tests="2">'
                + case.format("failure")
                + case.format("error")
                + '<testcase classname="t" name="test_b" /></testsuite></testsuites>'
            )
            framework = verify.Framework("pytest", "python -m pytest", python="python")
            results = verify.per_test_results(framework, {}, xml)
            self.assertEqual(
                (["t::test_b"], ["t::test_a"], 2), (results["passed"], results["failed"], results["total"])
            )
            receipt = {"exit_code": 1, "timed_out": False, "results_expected": True, "results": results}
            self.assertTrue(schedule.complete_results(receipt))
            self.assertEqual("failing_tests", verify.suite_health(receipt))
            # One id reported with two outcomes is never a complete result.
            xml.write_text(
                "<testsuites><testsuite>"
                + case.format("failure")
                + '<testcase classname="t" name="test_a" /></testsuite></testsuites>'
            )
            self.assertFalse(schedule.complete_results({"results": verify.per_test_results(framework, {}, xml)}))

    def test_skipping_a_test_that_passed_on_base_is_a_regression(self):
        """Review r1: break greet(), skip the test that would catch it, add a real regression test."""
        project = self.project()
        broken = REFERENCE["greet.py"].replace('return f"Hello, {name}"', 'return f"Hi, {name}"')
        skipped = REFERENCE["test_greet.py"].replace(
            "    def test_ada(self):", "    @unittest.skip('flaky')\n    def test_ada(self):"
        )
        project.write({"greet.py": broken, "test_greet.py": skipped})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertTrue(
            any("did not pass on the candidate" in reason and "test_ada" in reason for reason in result["failures"]),
            result,
        )

    def test_an_import_error_on_base_is_not_a_reproduction(self):
        """Review r11: a no-op helper the test imports makes base fail only at import time."""
        project = self.project()
        noop = SEED["greet.py"].replace("def main(", "def normalize(name):\n    return name\n\n\ndef main(")
        tests = SEED["test_greet.py"].replace("from greet import greet", "from greet import greet, normalize")
        tests = tests.replace(
            "    def test_ada(self):",
            "    def test_normalize(self):\n        self.assertEqual('x', normalize('x'))\n\n    def test_ada(self):",
        )
        project.write({"greet.py": noop, "test_greet.py": tests})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertTrue(any("only fail to import or collect" in reason for reason in result["failures"]), result)
        self.assertEqual([], result["fail_to_pass"])

    def test_builder_chosen_commands_never_make_a_pass(self):
        """Review r3: with no detectable framework, the Builder's own commands prove nothing."""
        files = {"lib.sh": "echo old\n", "tests/test_lib.sh": "sh lib.sh | grep -q old\n"}
        project = self.project(files)
        project.write({"lib.sh": "echo new\n", "tests/test_bug.sh": "sh lib.sh | grep -q new\n"})
        reported = {"test_command": "sh tests/test_bug.sh", "regression_command": "sh tests/test_bug.sh"}
        result = verify.verify(project.root, project.base, project.evidence, reported=reported, timeout=60)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertEqual(
            ("builder", "builder"), (result["commands"]["regression_source"], result["commands"]["suite_source"])
        )
        self.assertFalse(verify._mentions_tests("grep -q FIXED lib.sh # tests/test_bug.sh", ["tests/test_bug.sh"]))
        self.assertFalse(verify._mentions_tests("true", ["t/t.sh"]))
        self.assertTrue(verify._mentions_tests("python -m pytest tests/test_x.py::test_y", ["tests/test_x.py"]))

    def test_a_run_that_reports_no_results_is_not_a_pass(self):
        """Review r4: product code that exits the test process early with status 0."""
        project = self.project()
        exits = REFERENCE["greet.py"].replace('    return f"Hello, {name}"', "    import os\n    os._exit(0)")
        project.write({"greet.py": exits, "test_greet.py": REFERENCE["test_greet.py"]})
        result = project.verify()
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertTrue(
            any("without reporting any test result" in reason for reason in result["failures"] + result["unverified"]),
            result,
        )

    def test_non_ascii_and_binary_changes_are_counted(self):
        """Review r8: quoted numstat paths made a 60-line change count as 0 lines."""
        project = self.project({**SEED, "café.py": "x = 1\n"})
        project.write({"café.py": "x = 1\n" + "y = 2\n" * 60, "logo.bin": "\0binary"})
        stats = verify.diff_stats(project.root, project.base, verify.changed_files(project.root, project.base))
        self.assertEqual(60 + verify.BINARY_LINES, stats["source_lines_changed"])
        self.assertEqual(["logo.bin"], stats["binary_files"])
        self.assertEqual(["logo.bin"], stats["non_code_files"])

    def test_scratch_tree_handles_a_directory_that_became_a_file(self):
        project = self.project({**SEED, "data/a.txt": "a\n"})
        (project.root / "data" / "a.txt").unlink()
        (project.root / "data").rmdir()
        (project.root / "data").write_text("now a file\n")
        changes = verify.changed_files(project.root, project.base)
        tree = verify.make_tree(project.root, project.base, project.evidence / "tree", project.root, changes)
        self.addCleanup(verify.remove_tree, project.root, tree)
        self.assertEqual("now a file\n", (tree / "data").read_text())

    @unittest.skipUnless(verify._python_can_import(sys.executable, "pytest"), "pytest is not installed")
    def test_deselecting_a_test_through_config_is_not_a_fix(self):
        """Review r2: no product change, only `addopts = --deselect`."""
        files = {
            "pytest.ini": "[pytest]\n",
            "calc.py": "def mean(v):\n    return sum(v) / len(v)\n",
            "test_calc.py": "from calc import mean\n\n\ndef test_mean():\n    assert mean([2]) == 2\n\n\n"
            "def test_empty():\n    assert mean([]) == 0\n",
        }
        project = self.project(files)
        project.write(
            {
                "pytest.ini": "[pytest]\naddopts = --deselect test_calc.py::test_empty\n",
                "test_calc.py": files["test_calc.py"] + "\n\ndef test_more():\n    assert mean([4]) == 4\n",
            }
        )
        framework = verify.detect_framework(project.root, python=sys.executable)
        base_suite = verify.baseline(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=framework.suite,
            timeout=120,
        )
        result = verify.verify(
            project.root, project.base, project.evidence, framework=framework, base_suite=base_suite, timeout=120
        )
        self.assertEqual(verify.FAIL, result["verdict"], result)


if __name__ == "__main__":
    unittest.main()


GO_SEED = {
    "go.mod": "module pager\n\ngo 1.21\n",
    "pager.go": "package pager\n\nfunc PageCount(total, size int) int {\n\treturn total / size\n}\n",
    "pager_test.go": 'package pager\n\nimport "testing"\n\n'
    'func TestExisting(t *testing.T) {\n\tif PageCount(10, 5) != 2 {\n\t\tt.Fatal("10/5")\n\t}\n}\n',
}
GO_FIX = {
    "pager.go": "package pager\n\nfunc PageCount(total, size int) int {\n\treturn (total + size - 1) / size\n}\n",
    "pager_test.go": GO_SEED["pager_test.go"]
    + '\nfunc Test_t1_partial_page_counts(t *testing.T) {\n\tif PageCount(11, 5) != 3 {\n\t\tt.Fatal("11/5")\n\t}\n}\n',
}


class SuitePreservationTests(unittest.TestCase):
    def test_suite_health_does_not_erase_real_regression_evidence(self):
        suite_test = (
            "import unittest\nfrom calc import keep\n"
            "class Existing(unittest.TestCase):\n"
            " def test_existing(self): self.assertEqual(9, keep())\n"
        )
        broken_import = "import no_such_autocode_479_driver\n" + suite_test
        failing_test = suite_test.replace("assertEqual(9", "assertEqual(8")
        skipped_test = suite_test.replace(
            " def test_existing", ' @unittest.skip("optional service")\n def test_existing'
        )
        cases = [
            ("collection_only", broken_import, None, 9, "UNVERIFIED"),
            ("passing_plus_collection", suite_test, broken_import, 9, "UNVERIFIED"),
            ("all_preexisting_failures", failing_test, None, 9, "UNVERIFIED"),
            ("all_skipped", skipped_test, None, 9, "UNVERIFIED"),
            ("healthy", suite_test, None, 9, "PASS"),
            ("passing_plus_preexisting_failure", suite_test, failing_test, 9, "PASS"),
            ("named_regression_with_collection", suite_test, broken_import, 0, "FAIL"),
        ]
        for name, primary, optional, kept, expected in cases:
            with self.subTest(name=name):
                files = {"calc.py": "def add(a,b): return a-b\ndef keep(): return 9\n", "test_db.py": primary}
                if optional:
                    files["test_optional.py"] = optional
                project = Project(files)
                try:
                    framework = verify.detect_framework(project.root, python=sys.executable)
                    suite = shlex.quote(sys.executable) + " -m unittest -v test_db"
                    if optional:
                        suite += " test_optional"
                    baseline = verify.baseline(
                        project.root,
                        project.base,
                        project.evidence,
                        framework=framework,
                        suite_command=suite,
                        timeout=30,
                    )
                    project.write(
                        {
                            "calc.py": f"def add(a,b): return a+b\ndef keep(): return {kept}\n",
                            "test_calc.py": "import unittest\nfrom calc import add\n"
                            "class Addition(unittest.TestCase):\n"
                            " def test_add(self): self.assertEqual(5,add(2,3))\n",
                        }
                    )
                    result = verify.verify(
                        project.root,
                        project.base,
                        project.evidence,
                        framework=framework,
                        suite_command=suite,
                        base_suite=baseline,
                        regression_command=shlex.quote(sys.executable) + " -m unittest -v test_calc",
                        timeout=30,
                    )
                    self.assertEqual(["test_calc.Addition.test_add"], result["fail_to_pass"])
                    self.assertEqual(expected, result["verdict"], result["failures"] + result["unverified"])
                    if expected == "UNVERIFIED":
                        self.assertTrue(result["unverified"])
                        self.assertFalse(result["failures"])
                    elif expected == "FAIL":
                        self.assertTrue(
                            any("test_db.Existing.test_existing" in reason for reason in result["failures"]), result
                        )
                    for path, original in files.items():
                        if path != "calc.py":
                            self.assertEqual(original, (project.root / path).read_text())
                finally:
                    project.close()

    @unittest.skipUnless(shutil.which("node"), "Node is required for a real hook-failure suite")
    def test_a_pre_existing_hook_failure_does_not_block_preservation(self):
        """#503: a failed beforeEach executed, so it is judged like a test that already failed on base."""
        suite_test = (
            "const {test, describe, beforeEach} = require('node:test');\n"
            "const assert = require('node:assert/strict');\n"
            "const {add} = require('./calc.cjs');\n"
            "test('add_works', () => assert.equal(3, add(1, 2)));\n"
            "describe('broken_fixture', () => {\n"
            "  beforeEach(() => { throw new Error('broken fixture'); });\n"
            "  test('sub_works', () => {});\n"
            "});\n"
        )
        regression_test = (
            "const {test} = require('node:test');\n"
            "const assert = require('node:assert/strict');\n"
            "const {sub} = require('./calc.cjs');\n"
            "test('sub_subtracts', () => assert.equal(1, sub(2, 1)));\n"
        )
        files = {
            "calc.cjs": "function add(a, b) { return a + b; }\n"
            "function sub(a, b) { return a + b; }\n"  # the bug: sub does not subtract
            "module.exports = {add, sub};\n",
            "a.test.cjs": suite_test,
        }
        project = Project(files)
        try:
            framework = verify.Framework("node", "node --test a.test.cjs")
            suite = "node --test a.test.cjs"
            regression = "node --test test_sub.test.cjs"
            base = verify.baseline(
                project.root, project.base, project.evidence, framework=framework, suite_command=suite, timeout=30
            )
            base_hook = base["receipt"]["results"]["collection_errors"]
            self.assertTrue(base_hook, base)
            self.assertEqual([], base["receipt"]["results"]["uncollected"], base)
            project.write(
                {
                    "calc.cjs": "function add(a, b) { return a + b; }\n"
                    "function sub(a, b) { return a - b; }\n"
                    "module.exports = {add, sub};\n",
                    "test_sub.test.cjs": regression_test,
                }
            )
            result = verify.verify(
                project.root,
                project.base,
                project.evidence,
                framework=framework,
                suite_command=suite,
                regression_command=regression,
                base_suite=base,
                timeout=30,
            )
            self.assertEqual(["test_sub.test.cjs::sub_subtracts"], result["fail_to_pass"], result)
            self.assertEqual("PASS", result["verdict"], result["failures"] + result["unverified"])
            self.assertTrue(any("already failed on base" in note for note in result["notes"]), result)
        finally:
            project.close()


class IncompleteEvidenceRegressionTests(unittest.TestCase):
    """#421/#503: incompleteness and hook failures neither hide nor invent a named regression."""

    @staticmethod
    def suite_results(*, passed=(), failed=(), skipped=(), collection=(), uncollected=None, total=None, complete=True):
        results = {
            "passed": list(passed),
            "failed": list(failed),
            "skipped": list(skipped),
            "collection_errors": list(collection),
            "total": total if total is not None else len(passed) + len(failed) + len(skipped),
            "complete": complete,
        }
        if uncollected is not None:
            results["uncollected"] = list(uncollected)
        return results

    def judge_suite(self, candidate, base_results, *, base_timed_out=False):
        fail, unverified, notes = [], [], []
        base_suite = {
            "command": "suite",
            "base": "b",
            "health": "failing_tests",
            "receipt": {"timed_out": base_timed_out, "exit_code": 1, "results_expected": True, "results": base_results},
        }
        verify._judge_suite(
            {"timed_out": False, "exit_code": 1, "results_expected": True, "results": candidate},
            base_suite,
            fail,
            unverified,
            notes,
        )
        return fail, unverified, notes

    def test_an_incomplete_base_suite_does_not_hide_a_named_regression(self):
        for base_timed_out in (False, True):
            with self.subTest(base_timed_out=base_timed_out):
                base = self.suite_results(passed=["t_keep", "t_break"], failed=["t_old"], complete=False)
                candidate = self.suite_results(passed=["t_keep"], failed=["t_break"])
                fail, unverified, _ = self.judge_suite(candidate, base, base_timed_out=base_timed_out)
                self.assertTrue(
                    any("t_break" in reason and "fail on the candidate" in reason for reason in fail),
                    (fail, unverified),
                )
                self.assertTrue(any("base suite was incomplete" in reason for reason in unverified))

    def test_a_test_that_never_ran_on_an_incomplete_base_is_not_a_regression(self):
        base = self.suite_results(passed=["t_keep"], complete=False)
        candidate = self.suite_results(passed=["t_keep"], failed=["t_never_ran"], total=2)
        fail, unverified, _ = self.judge_suite(candidate, base)
        self.assertEqual([], fail, unverified)  # the absence of a failure stays unproven
        self.assertTrue(any("base suite was incomplete" in reason for reason in unverified))

    def test_incomplete_candidate_results_still_judge_observed_failures(self):
        base = self.suite_results(passed=["t_keep", "t_break"])
        candidate = self.suite_results(passed=["t_keep"], failed=["t_break"], total=1, complete=False)
        fail, unverified, _ = self.judge_suite(candidate, base)
        self.assertTrue(
            any("t_break" in reason and "fail on the candidate" in reason for reason in fail), (fail, unverified)
        )
        self.assertTrue(any("incomplete" in reason for reason in unverified))

    def test_a_failed_hook_is_an_executed_failure_not_an_uncollected_module(self):
        # A Node/Vitest hook failure sits in collection_errors but never collected nothing;
        # only results saved before the ``uncollected`` split stay unproven (fail closed).
        base = self.suite_results(passed=["t_keep"], failed=["t_hooked"], collection=["t_hooked"])
        candidate = self.suite_results(passed=["t_keep"], failed=["t_hooked"], collection=["t_hooked"])
        fail, unverified, notes = self.judge_suite(candidate, base)
        self.assertEqual([], fail, (fail, unverified))
        self.assertTrue(any("already failed on base" in note for note in notes), notes)
        self.assertEqual(
            [
                "The base suite has collection errors; preservation is unproven: t_hooked",
                "The candidate suite has collection errors; preservation is unproven: t_hooked",
            ],
            [reason for reason in unverified if "collection errors" in reason],
        )
        for results in (base, candidate):
            results["uncollected"] = []
        fail, unverified, notes = self.judge_suite(candidate, base)
        self.assertEqual([], fail, (fail, unverified))
        self.assertEqual([], unverified, unverified)

    def test_an_uncollected_candidate_module_keeps_preservation_unproven(self):
        base = self.suite_results(passed=["t_keep"], uncollected=[])
        candidate = self.suite_results(
            passed=["t_keep"],
            failed=["m::[collection]"],
            collection=["m::[collection]"],
            uncollected=["m::[collection]"],
            total=2,
        )
        fail, unverified, _ = self.judge_suite(candidate, base)
        self.assertTrue(any("candidate suite has collection errors" in reason for reason in unverified))
        self.assertTrue(any("m::[collection]" in reason for reason in fail), (fail, unverified))

    def judge_regression(self, candidate_results, known):
        fail, unverified, notes, proof, review_reasons = [], [], [], {}, []
        on_candidate = {"timed_out": False, "exit_code": 1, "results_expected": True, "results": candidate_results}
        verify._judge_regression(
            on_candidate, None, fail, unverified, notes, proof, review_reasons, known_failures=lambda: known
        )
        return fail, unverified, notes

    def test_incomplete_regression_results_still_fail_named_candidate_failures(self):
        candidate = self.suite_results(failed=["test_greet.Case.test_empty"], total=1, complete=False)
        fail, unverified, _ = self.judge_regression(candidate, None)
        self.assertTrue(
            any(
                "The regression tests fail on the candidate" in reason and "test_greet.Case.test_empty" in reason
                for reason in fail
            ),
            (fail, unverified),
        )
        self.assertTrue(any("incomplete" in reason for reason in unverified))

    def test_incomplete_regression_results_keep_known_failures_as_notes(self):
        candidate = self.suite_results(failed=["test_flaky"], total=1, complete=False)
        fail, unverified, notes = self.judge_regression(candidate, {"test_flaky"})
        self.assertEqual([], fail, (fail, unverified))
        self.assertTrue(any("already fail on base" in note for note in notes), notes)
        self.assertTrue(any("incomplete" in reason for reason in unverified))


class GoResultTests(unittest.TestCase):
    """Go's per-test results come from `go test -json` (a live Go port could not be proven without them)."""

    def events(self, *rows):
        return "\n".join(json.dumps(row) for row in rows)

    def test_tests_subtests_skips_and_a_package_that_did_not_build(self):
        text = (
            self.events(
                {"Action": "start", "Package": "m/a"},
                {"Action": "run", "Package": "m/a", "Test": "TestOk"},
                {"Action": "pass", "Package": "m/a", "Test": "TestOk"},
                {"Action": "run", "Package": "m/a", "Test": "TestTable"},
                {"Action": "run", "Package": "m/a", "Test": "TestTable/case_1"},
                {"Action": "fail", "Package": "m/a", "Test": "TestTable/case_1"},
                {"Action": "fail", "Package": "m/a", "Test": "TestTable"},
                {"Action": "run", "Package": "m/a", "Test": "TestLater"},
                {"Action": "skip", "Package": "m/a", "Test": "TestLater"},
                {"Action": "fail", "Package": "m/a"},
                {"Action": "output", "Package": "m/b", "Output": "m/b/b_test.go:3: undefined: New\n"},
                {"Action": "fail", "Package": "m/b"},
            )
            + "\n# m/b\nplain compiler output\n"
        )
        results = verify._go_results(text)
        self.assertEqual(["m/a::TestOk"], results["passed"])
        self.assertEqual(["m/a::TestLater"], results["skipped"])
        self.assertEqual(["m/a::TestTable", "m/a::TestTable/case_1", "m/b::[build failed]"], results["failed"])
        self.assertEqual(["m/b::[build failed]"], results["collection_errors"])
        self.assertEqual(["m/b::[build failed]"], results["uncollected"])
        self.assertTrue(results["complete"])

    def test_a_test_that_never_ended_makes_the_results_incomplete_and_no_events_give_none(self):
        text = self.events({"Action": "run", "Package": "m", "Test": "TestHangs"}, {"Action": "fail", "Package": "m"})
        self.assertFalse(verify._go_results(text)["complete"])
        self.assertIsNone(verify._go_results("go: command not found\n"))

    def test_the_runner_asks_go_for_json_only_on_a_plain_go_test_command(self):
        go = verify.Framework("go", "go test ./...")
        self.assertEqual("go test -json ./...", verify._with_results(go, "go test ./...", "x.xml"))
        self.assertEqual("go test -json -run X .", verify._with_results(go, "go test -run X .", "x.xml"))
        self.assertEqual("go test -json ./...", verify._with_results(go, "go test -json ./...", "x.xml"))
        for command in ("cd sub && go test ./...", "go vet ./...", "go test ./... | tee log"):
            self.assertEqual(command, verify._with_results(go, command, "x.xml"))
            self.assertFalse(verify.expects_results(go, command))
        self.assertTrue(verify.expects_results(go, "go test ./..."))
        # A test id carries the test's own name, so an English case matches it (T1 -> Test_t1_...).
        import autocode_test_cases as test_cases

        self.assertEqual(
            {"T1": ["pager::Test_t1_partial_page_counts"]},
            test_cases.match_cases([{"id": "T1"}], ["pager::TestExisting", "pager::Test_t1_partial_page_counts"]),
        )


@unittest.skipUnless(shutil.which("go"), "needs a Go toolchain")
class GoVerifyTests(unittest.TestCase):
    """Real Go modules through the runner's proof: base with the new tests, then the candidate."""

    def project(self, files):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def test_a_go_bug_fix_is_proven_by_the_test_that_fails_before_and_passes_after(self):
        project = self.project(GO_SEED)
        project.write(GO_FIX)
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result["failures"] + result["unverified"])
        self.assertEqual(["pager::Test_t1_partial_page_counts"], result["fail_to_pass"])
        self.assertIn("pager::TestExisting", result["pass_to_pass"])

    def test_a_go_test_that_also_passes_before_the_fix_does_not_reproduce_the_bug(self):
        project = self.project(GO_SEED)
        project.write(
            {
                **GO_FIX,
                "pager_test.go": GO_FIX["pager_test.go"].replace("PageCount(11, 5) != 3", "PageCount(10, 5) != 2"),
            }
        )
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(
            any("do not reproduce the bug" in failure for failure in result["failures"]), result["failures"]
        )

    def test_a_go_feature_test_that_cannot_build_on_base_proves_new_behavior(self):
        project = self.project(GO_SEED)
        project.write(
            {
                "pager.go": GO_SEED["pager.go"]
                + "\nfunc Pages(total, size int) int { return PageCount(total, size) }\n",
                "pager_test.go": GO_SEED["pager_test.go"]
                + '\nfunc Test_c2_pages(t *testing.T) {\n\tif Pages(10, 5) != 2 {\n\t\tt.Fatal("pages")\n\t}\n}\n',
            }
        )
        result = project.verify(new_behavior=True)
        self.assertEqual(verify.PASS, result["verdict"], result["failures"] + result["unverified"])
        self.assertIn("pager::Test_c2_pages", result["fail_to_pass"])
        # The same change is not a bug reproduction: on base the new test only fails to build.
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(
            any("fail to import or collect" in failure for failure in result["failures"]), result["failures"]
        )


class VendoredDependencyCopyTests(unittest.TestCase):
    def test_linked_scratch_vendor_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, tree = Path(temporary) / "source", Path(temporary) / "tree"
            (source / "vendor").mkdir(parents=True)
            tree.mkdir()
            (tree / "vendor").symlink_to(source / "vendor", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                verify.copy_vendored_dependencies(source, tree)

    def test_copies_complete_ignored_vendor_as_independent_files(self):
        project = Project({**SEED, ".gitignore": "vendor/\n"})
        self.addCleanup(project.close)
        source, tree = project.root, project.evidence
        project.write({"vendor/modules.txt": "offline", "vendor/native/library.so": "native"})
        verify.copy_vendored_dependencies(source, tree)
        self.assertFalse((tree / "vendor").is_symlink())
        self.assertEqual("offline", (tree / "vendor/modules.txt").read_text())
        self.assertEqual("native", (tree / "vendor/native/library.so").read_text())
        (tree / "vendor/modules.txt").write_text("scratch")
        self.assertEqual("offline", (source / "vendor/modules.txt").read_text())

    def test_missing_dependencies_are_optional_and_existing_base_vendor_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, tree = Path(temporary) / "source", Path(temporary) / "tree"
            source.mkdir()
            tree.mkdir()
            verify.copy_vendored_dependencies(None, tree)
            verify.copy_vendored_dependencies(source, tree)
            self.assertEqual([], list(tree.iterdir()))
            (source / "vendor").mkdir()
            (tree / "vendor").mkdir()
            (source / "vendor/modules.txt").write_text("candidate")
            (tree / "vendor/modules.txt").write_text("baseline")
            verify.copy_vendored_dependencies(source, tree)
            self.assertEqual("baseline", (tree / "vendor/modules.txt").read_text())

    def test_linked_vendor_root_is_rejected_without_copying_external_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, tree, outside = (Path(temporary) / name for name in ("source", "tree", "outside"))
            for path in (source, tree, outside):
                path.mkdir()
            (outside / "secret.txt").write_text("private")
            (source / "vendor").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                verify.copy_vendored_dependencies(source, tree)
            self.assertEqual([], list(tree.iterdir()))
