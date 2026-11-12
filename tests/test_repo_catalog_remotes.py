"""Network-free regressions for the `autocode registry remotes` family.

The remote is a local bare repository mapped onto the GitHub URL by a
test-only `url.<local>.insteadOf` rewrite in the fixture home's global Git
config, so no test reaches the network.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_registry as registry

OWNER_NAME = "OWNER/NAME"
GITHUB_URL = "https://github.com/OWNER/NAME.git"


class RemotesCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    @contextlib.contextmanager
    def catalog_home(self, name="home", bare=None, github_url=GITHUB_URL):
        home = self.root / name
        (home / ".config").mkdir(parents=True, exist_ok=True)
        config = ""
        if bare is not None:
            config = f'[url "{bare}"]\n\tinsteadOf = {github_url}\n'
        (home / ".gitconfig").write_text(config)
        environment = patch.dict(
            os.environ,
            {
                "AUTOCODE_SOURCE_CATALOG_HOME": str(home),
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
            },
            clear=False,
        )
        environment.start()
        try:
            yield home
        finally:
            environment.stop()

    def run_cli(self, args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                code = registry.cli(list(args))
            except SystemExit as exit_error:
                code = exit_error.code if isinstance(exit_error.code, int) else 2
        return code, output.getvalue()

    def payload(self, output):
        return json.loads(output)

    def git(self, *args, cwd=None):
        subprocess.run(
            ["git", *args],
            cwd=None if cwd is None else str(cwd),
            check=True,
            capture_output=True,
        )

    def commit(self, path, message):
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "-C",
            str(path),
            "commit",
            "--allow-empty",
            "-qm",
            message,
        )

    def git_repo(self, path, *, remote=None, message="base"):
        path.mkdir(parents=True)
        self.git("init", "-q", str(path))
        self.commit(path, message)
        if remote:
            self.git("-C", str(path), "remote", "add", "origin", remote)
        return path

    def make_bare(self, name, branch):
        """A local bare repository whose branch resolves to one commit."""
        work = self.root / f"{name}-work"
        work.mkdir()
        self.git("init", "-q", str(work))
        self.commit(work, "base")
        self.git("-C", str(work), "branch", "-M", branch)
        bare = self.root / name
        self.git("clone", "-q", "--bare", str(work), str(bare))
        self.git("-C", str(bare), "symbolic-ref", "HEAD", f"refs/heads/{branch}")
        head = subprocess.run(
            ["git", "-C", str(work), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return bare, head

    def head(self, path):
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def remotes_list(self):
        code, output = self.run_cli(["remotes", "list", "--json"])
        self.assertEqual(0, code, output)
        return self.payload(output)

    def remotes_entries(self):
        return self.remotes_list()["remotes"]

    def entry_for(self, name=OWNER_NAME):
        matches = [row for row in self.remotes_entries() if row.get("name") == name]
        self.assertEqual(1, len(matches), self.remotes_entries())
        return matches[0]

    def repository_paths(self):
        code, output = self.run_cli(["repositories", "list", "--json"])
        self.assertEqual(0, code, output)
        return [row["path"] for row in self.payload(output)["repositories"]]

    def assert_source_catalog_error(self, code, output):
        self.assertEqual(2, code, output)
        payload = self.payload(output)
        self.assertEqual("source_catalog_error", payload["error"]["code"], payload)

    def managed_clone(self, home, name=OWNER_NAME):
        parts = name.split("/")
        return home.joinpath("clones", *parts)

    def clone_owner(self):
        code, output = self.run_cli(["remotes", "clone", OWNER_NAME])
        return code, output

    def test_c1_remotes_add_lists_entry_and_replaces_ref(self):
        with self.catalog_home() as home:
            (home / "source-catalog.json").write_text(json.dumps({"version": 1, "roots": [], "repositories": []}))
            code, output = self.run_cli(["remotes", "add", OWNER_NAME, "--ref", "BRANCH"])
            self.assertEqual(0, code, output)
            entry = self.entry_for()
            self.assertEqual(GITHUB_URL, entry["url"])
            self.assertEqual("BRANCH", entry["ref"])
            self.assertFalse(entry["cloned"])
            self.assertIsNone(entry["path"])
            self.assertIsNone(entry["pinned_commit"])
            code, output = self.run_cli(["remotes", "add", OWNER_NAME, "--ref", "OTHER"])
            self.assertEqual(0, code, output)
            entries = self.remotes_entries()
            self.assertEqual(1, len(entries))
            self.assertEqual("OTHER", entries[0]["ref"])

    def test_c2_remotes_remove_drops_only_allowlist_entry(self):
        bare, _ = self.make_bare("owner-name-bare", "BRANCH")
        with self.catalog_home(bare=bare) as home:
            self.run_cli(["remotes", "add", OWNER_NAME, "--ref", "BRANCH"])
            code, output = self.clone_owner()
            self.assertEqual(0, code, output)
            clone = self.managed_clone(home)
            self.assertTrue(clone.is_dir())
            code, output = self.run_cli(["remotes", "remove", OWNER_NAME])
            self.assertEqual(0, code, output)
            self.assertEqual([], self.remotes_entries())
            code, output = self.clone_owner()
            self.assert_source_catalog_error(code, output)
            self.assertFalse(self.managed_clone(home, "OTHER/REPO").exists())
            self.assertTrue(clone.is_dir())
            self.assertIn(str(clone), self.repository_paths())
            self.run_cli(["remotes", "add", "OTHER/REPO", "--ref", "main"])
            before = self.remotes_entries()
            extra_root = self.root / "extra-root"
            extra_root.mkdir()
            standalone = self.git_repo(self.root / "standalone")
            for args in (
                ["roots", "add", str(extra_root)],
                ["roots", "remove", str(extra_root)],
                ["roots", "discover"],
                ["repositories", "add", str(standalone)],
                ["repositories", "remove", str(standalone)],
            ):
                code, output = self.run_cli(args)
                self.assertEqual(0, code, output)
            self.assertEqual(before, self.remotes_entries())
            clones = home / "clones"
            self.assertEqual(["OWNER"], [child.name for child in clones.iterdir()])

    def test_c3_remotes_clone_creates_pinned_registered_clone(self):
        bare, commit = self.make_bare("owner-name-bare", "BRANCH")
        with self.catalog_home(bare=bare) as home:
            self.run_cli(["remotes", "add", OWNER_NAME, "--ref", "BRANCH"])
            code, output = self.clone_owner()
            self.assertEqual(0, code, output)
            clone = self.managed_clone(home)
            self.assertTrue(clone.is_dir())
            branch = subprocess.run(
                ["git", "-C", str(clone), "rev-parse", "--abbrev-ref", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual("BRANCH", branch)
            self.assertEqual(commit, self.head(clone))
            entry = self.entry_for()
            self.assertTrue(entry["cloned"])
            self.assertEqual(str(clone), entry["path"])
            self.assertEqual(commit, entry["pinned_commit"])
            self.assertIn(str(clone), self.repository_paths())
            code, output = self.run_cli(["roots", "discover", "--json"])
            self.assertEqual(0, code, output)
            found = self.payload(output)
            candidates = {candidate["path"] for row in found["repositories"] for candidate in row["candidates"]}
            self.assertIn(str(clone), candidates)

    def test_c4_remotes_clone_refuses_not_allowlisted(self):
        with self.catalog_home() as home:
            self.run_cli(["remotes", "add", OWNER_NAME, "--ref", "BRANCH"])
            code, output = self.run_cli(["remotes", "clone", "OTHER/REPO"])
            self.assert_source_catalog_error(code, output)
            clones = home / "clones"
            if clones.exists():
                self.assertEqual([], list(clones.iterdir()))
            self.assertNotIn("OTHER/REPO", self.repository_paths())

    def test_c5_remotes_second_clone_reports_existing(self):
        bare, commit = self.make_bare("owner-name-bare", "BRANCH")
        with self.catalog_home(bare=bare) as home:
            self.run_cli(["remotes", "add", OWNER_NAME, "--ref", "BRANCH"])
            code, output = self.clone_owner()
            self.assertEqual(0, code, output)
            recorded = self.entry_for()
            self.assertTrue(recorded["cloned"])
            # Remove the URL rewrite: a second clone that tried to reach the
            # remote again could not succeed, so exit 0 proves it did not.
            (home / ".gitconfig").write_text("")
            code, output = self.clone_owner()
            self.assertEqual(0, code, output)
            payload = self.payload(output)
            self.assertTrue(payload["already_cloned"], payload)
            self.assertEqual(recorded["path"], payload["path"])
            self.assertEqual(commit, payload["pinned_commit"])
            entry = self.entry_for()
            self.assertEqual(recorded["path"], entry["path"])
            self.assertEqual(commit, entry["pinned_commit"])
            self.assertEqual(commit, self.head(self.managed_clone(home)))

    def snapshot_tree(self, path):
        return {
            str(child.relative_to(path)): (child.read_bytes() if child.is_file() else self.snapshot_tree(child))
            for child in sorted(path.iterdir())
        }

    def test_c6_remotes_failed_clone_leaves_nothing(self):
        # Case 1: the remote exists but lacks the allowlisted ref.
        bare, _ = self.make_bare("wrong-branch-bare", "OTHERBRANCH")
        with self.catalog_home("case-1", bare=bare) as home:
            self.run_cli(["remotes", "add", OWNER_NAME, "--ref", "BRANCH"])
            code, output = self.clone_owner()
            self.assert_source_catalog_error(code, output)
            clones = home / "clones"
            if clones.exists():
                self.assertEqual([], list(clones.iterdir()))
            self.assertNotIn(str(self.managed_clone(home)), self.repository_paths())
        # Case 2: the insteadOf rewrite points at a path that does not exist.
        with self.catalog_home("case-2", bare=self.root / "missing-remote") as home:
            self.run_cli(["remotes", "add", OWNER_NAME, "--ref", "BRANCH"])
            code, output = self.clone_owner()
            self.assert_source_catalog_error(code, output)
            clones = home / "clones"
            if clones.exists():
                self.assertEqual([], list(clones.iterdir()))
            self.assertNotIn(str(self.managed_clone(home)), self.repository_paths())
        # Case 3: a pre-existing managed folder that is not a verifiable clone
        # is refused byte-for-byte untouched, with no new registration.
        with self.catalog_home("case-3") as home:
            self.run_cli(["remotes", "add", OWNER_NAME, "--ref", "BRANCH"])
            folder = self.managed_clone(home)
            folder.mkdir(parents=True)
            (folder / "keep.txt").write_text("operator data")
            before = self.snapshot_tree(folder)
            code, output = self.clone_owner()
            self.assert_source_catalog_error(code, output)
            self.assertEqual(before, self.snapshot_tree(folder))
            self.assertNotIn(str(folder), self.repository_paths())

    def test_a_clone_that_loses_a_race_leaves_the_other_clone_untouched(self):
        # Two confirmations for one repository: the folder the other one finished first must not be
        # moved into, registered or deleted by this one's cleanup.
        catalog = registry.source_catalog
        bare, _ = self.make_bare("race-bare", "BRANCH")
        with self.catalog_home("race", bare=bare) as home:
            self.run_cli(["remotes", "add", OWNER_NAME, "--ref", "BRANCH"])
            folder = self.managed_clone(home)
            real_clone = catalog._git_clone

            def clone_while_another_finishes(url, destination, ref):
                real_clone(url, destination, ref)
                folder.mkdir(parents=True)
                (folder / "keep.txt").write_text("the other clone")

            with patch.object(catalog, "_git_clone", clone_while_another_finishes):
                code, output = self.clone_owner()
            self.assert_source_catalog_error(code, output)
            self.assertEqual(["keep.txt"], sorted(path.name for path in folder.iterdir()))
            self.assertEqual("the other clone", (folder / "keep.txt").read_text())
            self.assertEqual([], [path.name for path in home.iterdir() if path.name.startswith("remote-clone-")])
            self.assertNotIn(str(folder), self.repository_paths())
            self.assertFalse(self.entry_for()["cloned"])

    def test_c7_remotes_refuse_non_owner_name_arguments(self):
        invalid = [
            "",
            "/NAME",
            "OWNER/",
            "OWNER/NAME/EXTRA",
            "OWNER/NAME/",
            ".",
            "..",
            "./NAME",
            "OWNER/..",
            "OWNER/.",
            "OWN ER/NAME",
            "OWNER/NAME!",
            "OWNER/NAME_#1",
        ]
        with self.catalog_home():
            for value in invalid:
                for args in (["remotes", "add", value, "--ref", "BRANCH"], ["remotes", "clone", value]):
                    code, output = self.run_cli(args)
                    self.assert_source_catalog_error(code, output)
            self.assertEqual([], self.remotes_entries())

    def test_c9_remotes_clone_reports_unrecorded_verifiable_clone(self):
        bare, commit = self.make_bare("owner-name-bare", "BRANCH")
        with self.catalog_home(bare=bare) as home:
            self.run_cli(["remotes", "add", OWNER_NAME, "--ref", "BRANCH"])
            code, output = self.clone_owner()
            self.assertEqual(0, code, output)
            clone = self.managed_clone(home)
            self.run_cli(["remotes", "remove", OWNER_NAME])
            self.run_cli(["remotes", "add", OWNER_NAME, "--ref", "BRANCH"])
            entry = self.entry_for()
            self.assertFalse(entry["cloned"])
            self.assertIsNone(entry["path"])
            # Remove the URL rewrite: reporting the existing clone must not
            # need the remote at all.
            (home / ".gitconfig").write_text("")
            code, output = self.clone_owner()
            self.assertEqual(0, code, output)
            self.assertEqual(commit, self.head(clone))
            entry = self.entry_for()
            self.assertTrue(entry["cloned"])
            self.assertEqual(str(clone), entry["path"])
            self.assertEqual(commit, entry["pinned_commit"])
            self.assertIn(str(clone), self.repository_paths())

    def test_g1_legacy_catalog_keeps_loading(self):
        standalone = self.git_repo(self.root / "standalone")
        common = subprocess.run(
            ["git", "-C", str(standalone), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        configured_root = self.root / "configured-root"
        configured_root.mkdir()
        second_root = self.root / "second-root"
        second_root.mkdir()
        with self.catalog_home("guard-home") as home:
            (home / "source-catalog.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "roots": [{"path": str(configured_root)}],
                        "repositories": [
                            {
                                "path": str(standalone),
                                "git_common_dir": str(Path(common).resolve()),
                                "head": self.head(standalone),
                            }
                        ],
                    }
                )
            )
            code, output = self.run_cli(["roots", "list", "--json"])
            self.assertEqual(0, code, output)
            payload = self.payload(output)
            self.assertEqual([str(configured_root)], [row["path"] for row in payload["roots"]])
            self.assertEqual([str(standalone)], [row["path"] for row in payload["repositories"]])
            code, output = self.run_cli(["roots", "discover", "--json"])
            self.assertEqual(0, code, output)
            found = self.payload(output)
            candidates = {candidate["path"] for row in found["repositories"] for candidate in row["candidates"]}
            self.assertIn(str(standalone), candidates)
            code, output = self.run_cli(["roots", "add", str(second_root), "--json"])
            self.assertEqual(0, code, output)
            code, output = self.run_cli(["repositories", "remove", str(standalone), "--json"])
            self.assertEqual(0, code, output)
            code, output = self.run_cli(["roots", "list", "--json"])
            self.assertEqual(0, code, output)
            payload = self.payload(output)
            self.assertEqual(
                {str(configured_root), str(second_root)},
                {row["path"] for row in payload["roots"]},
            )


if __name__ == "__main__":
    unittest.main()
