"""A scenario seed taken from a real repository at a pinned commit (#2944).

The source is a local Git repository, so these tests need no network: the pinned commit is not the
repository's tip, which is exactly what a real case pins (an upstream branch keeps moving)."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402
from harness import catalog, pinned_seed  # noqa: E402
from harness.project import git, materialize  # noqa: E402

UNKNOWN = "0123456789abcdef0123456789abcdef01234567"


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "-c", "maintenance.auto=false", "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


class PinnedSeedTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.root)])
        self.source = self.root / "upstream"
        (self.source / "pkg").mkdir(parents=True)
        git(self.source, "init", "-q", "-b", "main")
        (self.source / "pkg/core.py").write_text("VERSION = 1\n")
        (self.source / ".gitignore").write_text("/output/\n")
        self.pinned = commit_all(self.source, "pinned")
        (self.source / "pkg/core.py").write_text("VERSION = 2\n")
        (self.source / "pkg/later.py").write_text("ADDED = True\n")
        self.tip = commit_all(self.source, "later")
        self.cache = self.root / "cache"

    def seed(self, commit=None):
        return pinned_seed.PinnedSeed(str(self.source), commit or self.pinned)

    def test_the_pinned_commits_files_not_the_tip_nor_its_history(self):
        tree = pinned_seed.tree(self.seed(), self.cache)
        self.assertEqual({".gitignore", "pkg"}, {path.name for path in tree.iterdir()}, "no .git: no history")
        self.assertEqual("VERSION = 1\n", (tree / "pkg/core.py").read_text())
        self.assertFalse((tree / "pkg/later.py").exists(), "a file from after the pin")
        self.assertEqual([tree], [path for path in self.cache.iterdir()], "only the finished tree stays cached")

    def test_a_cached_seed_is_reused_without_fetching_again(self):
        first = pinned_seed.tree(self.seed(), self.cache)
        with patch.object(pinned_seed, "_git", side_effect=AssertionError("fetched again")):
            self.assertEqual(first, pinned_seed.tree(self.seed(), self.cache))

    def test_a_tree_another_caller_cached_first_is_kept(self):
        first = pinned_seed.tree(self.seed(), self.cache)
        (first / "marker").write_text("first\n")
        self.assertEqual(first, pinned_seed._fetch(self.seed(), self.cache, first))
        self.assertEqual("first\n", (first / "marker").read_text())
        self.assertEqual([first], list(self.cache.iterdir()), "the second extraction is discarded")

    def test_a_commit_the_source_lacks_is_unavailable_and_leaves_no_cache(self):
        with self.assertRaisesRegex(pinned_seed.SeedUnavailable, f"@{UNKNOWN[:12]}: git fetch failed"):
            pinned_seed.tree(self.seed(commit=UNKNOWN), self.cache)
        self.assertEqual([], list(self.cache.iterdir()))

    def test_the_seed_table_is_validated(self):
        good = {"git": "https://example.test/project.git", "commit": self.pinned}
        self.assertEqual(pinned_seed.PinnedSeed(good["git"], self.pinned), pinned_seed.parse("s", good))
        for table, message in (
            ("https://example.test/project.git", "needs exactly git and commit"),
            ({"git": good["git"]}, "needs exactly git and commit"),
            ({**good, "path": "src"}, "needs exactly git and commit"),
            ({**good, "git": " "}, "must name a repository"),
            ({**good, "git": "--upload-pack=touch x"}, "must name a repository"),
            ({**good, "commit": "main"}, "40-character"),
            ({**good, "commit": self.pinned[:12]}, "40-character"),
            ({**good, "commit": self.pinned.upper()}, "40-character"),
        ):
            with self.subTest(table=table), self.assertRaisesRegex(ValueError, message):
                pinned_seed.parse("s", table)

    def catalog_entry(self, commit=None, seed_dir=False) -> catalog.Scenario:
        entry = self.root / "catalog" / "real-repo"
        entry.mkdir(parents=True, exist_ok=True)
        (entry / "scenario.toml").write_text(
            'title = "t"\ncategory = "feature"\nbrief = "Add it."\n'
            f'[seed]\ngit = "{self.source}"\ncommit = "{commit or self.pinned}"\n'
        )
        if seed_dir:
            (entry / "seed").mkdir()
        with patch.object(catalog, "CATALOG", entry.parent):
            return catalog.load(entry.name)

    def test_a_catalog_seed_table_is_the_scenarios_starting_project(self):
        with patch.dict("os.environ", {pinned_seed.CACHE_ENV: str(self.cache)}):
            scenario = self.catalog_entry()
            self.assertEqual([], scenario.missing_tools())
            project = materialize(scenario.seed, self.root / "run" / "project")
        self.assertEqual("VERSION = 1\n", (project / "pkg/core.py").read_text())
        self.assertEqual(["Scenario seed"], git(project, "log", "--format=%s").splitlines())
        (project / "output").mkdir()
        (project / "output/report.txt").write_text("written by the suite\n")
        self.assertEqual("", git(project, "status", "--porcelain"), "the repository's own ignore rules apply")

    def test_a_file_the_repository_tracks_inside_an_ignored_directory_stays_in_the_seed_commit(self):
        # Docutils tracks test/functional/output/README.rst under its ignored output/*, so the directory its
        # suite writes into exists in every checkout. A seed commit without it was a different repository:
        # AutoCode's clean copies of it lacked the directory and the suite failed there (#2944).
        (self.source / "output").mkdir()
        (self.source / "output/README").write_text("The suite writes here.\n")
        git(self.source, "add", "-f", "output/README")
        pinned = commit_all(self.source, "keep output/")
        with patch.dict("os.environ", {pinned_seed.CACHE_ENV: str(self.cache)}):
            project = materialize(self.catalog_entry(commit=pinned).seed, self.root / "run" / "project")
        self.assertIn("output/README", git(project, "ls-files").splitlines())
        self.assertEqual("", git(project, "status", "--porcelain", "--ignored"), "everything in the seed is committed")
        (project / "output/report.txt").write_text("written by the suite\n")
        self.assertEqual("", git(project, "status", "--porcelain"), "the repository's own ignore rules still apply")

    def test_an_unreachable_seed_skips_the_scenario_like_a_missing_toolchain(self):
        with patch.dict("os.environ", {pinned_seed.CACHE_ENV: str(self.cache)}):
            scenario = self.catalog_entry(commit=UNKNOWN)
            [missing] = scenario.missing_tools()
            self.assertIn("its pinned seed", missing)
            self.assertIn(f"{self.source}@{UNKNOWN[:12]}", missing)
            with patch.object(run, "selected", return_value=[scenario]), patch("builtins.print") as printed:
                self.assertEqual(0, run.cmd_check(type("Args", (), {"ids": ["real-repo"]})()))
        self.assertIn("requires its pinned seed", printed.call_args_list[1].args[0])

    def test_a_seed_table_and_a_seed_directory_are_refused_together(self):
        with self.assertRaisesRegex(ValueError, "cannot both be specified"):
            self.catalog_entry(seed_dir=True)


if __name__ == "__main__":
    unittest.main()
