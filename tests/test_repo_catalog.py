"""Authorized repository source catalog tests."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_repo_catalog as catalog
import autocode_registry as registry


class RepositoryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.environment = patch.dict(os.environ, {"AUTOCODE_SOURCE_CATALOG_HOME": str(self.home)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def git_repo(self, path, *, remote=None, message="base"):
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                        "commit", "--allow-empty", "-qm", message], check=True)
        if remote:
            subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
        return path

    def test_source_roots_are_shared_independent_of_launch_directory(self):
        first = self.root / "first-root"
        second = self.root / "elsewhere" / "second-root"
        first.mkdir()
        second.mkdir(parents=True)
        catalog.add_root(first)
        catalog.add_root(second)
        with patch.dict(os.environ, {"AUTOCODE_SOURCE_CATALOG_HOME": str(self.home)}, clear=True), patch.object(
            Path, "cwd", return_value=first
        ):
            from_first = catalog.list_roots()
        with patch.dict(os.environ, {"AUTOCODE_SOURCE_CATALOG_HOME": str(self.home)}, clear=True), patch.object(
            Path, "cwd", return_value=self.root
        ):
            from_unrelated = catalog.list_roots()
        self.assertEqual(from_first, from_unrelated)
        self.assertEqual(sorted([str(first), str(second)]), [row["path"] for row in from_unrelated["roots"]])

    def test_discovery_finds_repositories_outside_current_directory_and_honors_standalone_entries(self):
        root = self.root / "services"
        api = self.git_repo(root / "nested" / "fulfillment-rg-api", remote="https://example.test/api.git")
        unrelated = self.git_repo(root / "other" / "decoy", remote="https://example.test/decoy.git")
        standalone = self.git_repo(self.root / "separate" / "history", remote="https://example.test/history.git")
        catalog.add_root(root)
        catalog.add_repository(standalone)
        with patch.object(Path, "cwd", return_value=self.root / "launch-here"):
            found = catalog.discover()
        paths = {row["path"] for row in found["repositories"]}
        self.assertEqual({str(api), str(unrelated), str(standalone)}, paths)
        self.assertTrue(found["complete"])
        self.assertEqual([], found["diagnostics"])

    def test_git_metadata_at_configured_workspace_root_does_not_hide_child_repositories(self):
        workspace = self.git_repo(self.root / "workspace")
        first = self.git_repo(workspace / "arbitrary-service", remote="https://example.test/service.git")
        second = self.git_repo(workspace / "nested" / "different-name", remote="https://example.test/consumer.git")
        catalog.add_root(workspace)
        catalog.add_root(workspace / "nested")
        with patch.object(Path, "cwd", return_value=self.root / "unrelated-launch"):
            found = catalog.discover()
        paths = [row["path"] for row in found["repositories"]]
        self.assertEqual({str(workspace), str(first), str(second)}, set(paths))
        self.assertEqual(3, len(paths))
        self.assertTrue(found["complete"], found["diagnostics"])

    def test_generated_python_environments_do_not_exhaust_workspace_discovery(self):
        root = self.root / "workspace"
        service = self.git_repo(root / "service")
        environment = root / "arbitrarily-named-environment"
        environment.mkdir()
        (environment / "pyvenv.cfg").write_text("home = /runtime\n")
        self.git_repo(environment / "lib" / "nested-fixture")
        catalog.add_root(root)
        found = catalog.discover(max_depth=1)
        self.assertTrue(found["complete"], found["diagnostics"])
        self.assertEqual([str(service)], [row["path"] for row in found["repositories"]])

    def test_file_symlinks_are_not_repository_traversal_or_incompleteness(self):
        root = self.root / "workspace"
        service = self.git_repo(root / "service")
        outside = self.root / "outside-file"
        outside.write_text("not a source repository")
        (root / "file-link").symlink_to(outside)
        (root / "broken-file-link").symlink_to(self.root / "missing-file")
        catalog.add_root(root)
        found = catalog.discover()
        self.assertTrue(found["complete"], found["diagnostics"])
        self.assertEqual([str(service)], [row["path"] for row in found["repositories"]])

    def test_worktrees_deduplicate_and_conflicting_clones_remain_visible(self):
        origin = self.git_repo(self.root / "origin", remote="https://example.test/api.git")
        worktree = self.root / "worktree"
        subprocess.run(["git", "-C", str(origin), "worktree", "add", "--detach", str(worktree), "HEAD"],
                       check=True, capture_output=True)
        clone = self.root / "clone"
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
        subprocess.run(["git", "-C", str(clone), "remote", "set-url", "origin", "https://example.test/api.git"], check=True)
        (clone / "baseline.txt").write_text("alternate")
        subprocess.run(["git", "-C", str(clone), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                        "add", "baseline.txt"], check=True)
        subprocess.run(["git", "-C", str(clone), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                        "commit", "-qm", "other baseline"], check=True)
        catalog.add_root(self.root)
        found = catalog.discover()
        rows = [row for row in found["repositories"] if row["remote_identity"] == "example.test/api"]
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0]["ambiguous_baseline"])
        self.assertEqual(2, len(rows[0]["candidates"]))

    def test_symlinks_and_incomplete_traversal_cannot_be_reported_as_complete(self):
        root = self.root / "root"
        root.mkdir()
        outside = self.git_repo(self.root / "outside")
        (root / "escape").symlink_to(outside, target_is_directory=True)
        catalog.add_root(root)
        with patch.object(catalog, "DIRECTORY_BUDGET", 1):
            found = catalog.discover()
        self.assertFalse(found["complete"])
        self.assertTrue(any(row["code"] in {"symlink_escape", "budget_exhausted"} for row in found["diagnostics"]))

    def test_depth_bound_reports_incomplete_discovery_when_children_are_not_visited(self):
        root = self.root / "root"
        self.git_repo(root / "nested" / "repository")
        catalog.add_root(root)
        found = catalog.discover(max_depth=0)
        self.assertFalse(found["complete"])
        self.assertTrue(any(row["code"] == "depth_limit" for row in found["diagnostics"]))

    def test_stale_root_can_be_removed_after_its_directory_disappears(self):
        root = self.root / "temporary-source-root"
        root.mkdir()
        catalog.add_root(root)
        root.rmdir()
        result = catalog.remove_root(root)
        self.assertTrue(result["removed"])
        self.assertEqual([], catalog.list_roots()["roots"])

    def test_remote_identity_normalizes_scheme_host_and_git_suffix(self):
        first = self.git_repo(self.root / "clone-a", remote="https://EXAMPLE.test/Fulfillment/API.git")
        second = self.git_repo(self.root / "clone-b", remote="https://example.test/Fulfillment/API")
        third = self.git_repo(self.root / "clone-c", remote="git@EXAMPLE.test:Fulfillment/API.git")
        for index, path in enumerate((first, second, third)):
            (path / "identity.txt").write_text(str(index))
            subprocess.run(["git", "-C", str(path), "add", "identity.txt"], check=True)
            subprocess.run(["git", "-C", str(path), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                            "commit", "-qm", f"snapshot {index}"], check=True)
        catalog.add_root(self.root)
        found = catalog.discover()
        self.assertEqual(1, len(found["repositories"]))
        self.assertEqual("example.test/Fulfillment/API", found["repositories"][0]["remote_identity"])
        self.assertEqual("https://example.test/Fulfillment/API", found["repositories"][0]["remote"])
        self.assertEqual(3, len(found["repositories"][0]["candidates"]))

    def test_discovery_snapshot_binds_catalog_membership_and_source_revisions(self):
        source = self.git_repo(self.root / "source")
        catalog.add_root(source)
        first = catalog.discover()
        self.assertEqual(first["catalog_identity"], catalog.discover()["catalog_identity"])
        self.assertEqual(first["source_snapshot"], catalog.discover()["source_snapshot"])
        other = self.root / "other-root"
        other.mkdir()
        catalog.add_root(other)
        second = catalog.discover()
        self.assertNotEqual(first["catalog_identity"], second["catalog_identity"])
        self.assertNotEqual(first["source_snapshot"], second["source_snapshot"])

    def test_git_environment_cannot_redirect_repository_identity(self):
        intended = self.git_repo(self.root / "intended", remote="https://example.test/intended")
        other = self.git_repo(self.root / "other", remote="https://example.test/other")
        catalog.add_root(intended)
        with patch.dict(os.environ, {"GIT_DIR": str(other / ".git")}, clear=False):
            found = catalog.discover()
        self.assertEqual([str(intended)], [row["path"] for row in found["repositories"]])
        self.assertEqual("example.test/intended", found["repositories"][0]["remote_identity"])
        self.assertEqual("https://example.test/intended", found["repositories"][0]["remote"])

    def test_explicit_autocode_home_selects_source_catalog_before_project_local_default(self):
        selected = self.root / "selected-home"
        root = self.root / "sources"
        root.mkdir()
        with patch.dict(os.environ, {"AUTOCODE_HOME": str(selected)}, clear=True):
            catalog.add_root(root)
        self.assertTrue((selected / catalog.CATALOG_NAME).exists())
        self.assertFalse((self.home / catalog.CATALOG_NAME).exists())

    def test_explicit_autocode_home_is_not_reinterpreted_as_a_run_registry_directory(self):
        selected = self.root / "custom" / "registry"
        selected.mkdir(parents=True)
        with patch.dict(os.environ, {"AUTOCODE_HOME": str(selected)}, clear=True):
            self.assertEqual(selected.resolve(), catalog.storage_root())

    def test_registry_cli_exposes_root_configuration_and_discovery(self):
        root = self.root / "outside-current-directory"
        root.mkdir()
        with patch("sys.stdout") as output:
            self.assertEqual(0, registry.cli(["roots", "add", str(root), "--json"]))
        self.assertIn(str(root), output.write.call_args_list[0].args[0])
        with patch("sys.stdout") as output:
            self.assertEqual(0, registry.cli(["roots", "list", "--json"]))
        self.assertIn(str(root), output.write.call_args_list[0].args[0])


if __name__ == "__main__":
    unittest.main()
