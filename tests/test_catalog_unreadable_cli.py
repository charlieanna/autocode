"""One unreadable checkout under a source root no longer stops a task at Requirements.

A chat run "fix this <ticket>" stopped at Requirements with "AutoCode stopped: the model or its provider
failed. Source discovery is incomplete: repository_unreadable" and asked the person to continue
(2026-11-09). One checkout below the configured source roots had a ``.git`` that Git could not read (an
unborn ``git init`` is enough), so the whole intake refused, and the ValueError became
PAUSED_INVALID_OUTPUT, blamed on the model. Each test drives greenfield-greeting-cli through the real CLI
with the scripted model, so this module is in tests/suite_slow.json; the pure rules are in
test_repo_catalog and test_catalog_context.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


class UnreadableCatalogCheckoutCLI(unittest.TestCase):
    def setUp(self):
        from scenarios import run  # noqa: F401, I001 - initialize the harness before importing it
        from harness import catalog

        results = Path(__file__).resolve().parents[1] / ".scenario-runs"
        results.mkdir(exist_ok=True)
        scratch = tempfile.TemporaryDirectory(prefix="catalog-unreadable-", dir=results, ignore_cleanup_errors=True)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.scenario = catalog.load("greenfield-greeting-cli")
        for name in ("config", "codex-home"):
            (self.root / name).mkdir()
        self.sources = self.root / "sources"
        readable = self.sources / "api"
        readable.mkdir(parents=True)
        (readable / "README.md").write_text("api\n")
        git(readable, "init", "-q")
        git(readable, "add", ".")
        git(
            readable,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "base",
        )
        self.unborn = self.sources / "unborn"
        self.unborn.mkdir()
        git(self.unborn, "init", "-q")  # Git cannot read HEAD of a checkout with no commit

    def drive(self):
        from harness.driver import Driver, default_autocode, fake_setup
        from harness.project import materialize

        project = materialize(self.scenario.seed, self.root / "project")
        flags, env = fake_setup(self.scenario, self.root, self.scenario.reference)
        env.update(
            XDG_CONFIG_HOME=str(self.root / "config"),
            CODEX_HOME=str(self.root / "codex-home"),
            AUTOCODE_HOME=str(self.root / "home"),
        )
        subprocess.run(
            [*default_autocode(), "registry", "roots", "add", str(self.sources), "--json"],
            env={**os.environ, **env, "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.environ["PATH"]))},
            check=True,
            capture_output=True,
        )
        self.driver = Driver(
            project, self.root, flags, env, autocode=default_autocode(), max_steps=30, timeout_seconds=300
        )
        return self.driver.drive(self.scenario.brief)

    def test_an_unreadable_checkout_is_skipped_and_recorded_instead_of_stopping_requirements(self):
        view = self.drive()
        self.assertEqual("TASK_COMPLETE", view.get("status"), view.get("stop_reason"))
        receipts = list(self.root.glob("project/.autocode/runs/*/planning/source-catalog.json"))
        self.assertEqual(1, len(receipts), receipts)
        inventory = json.loads(receipts[0].read_text())
        self.assertEqual(["api"], [row["name"] for row in inventory["repositories"]])
        self.assertEqual([{"code": "repository_unreadable", "path": str(self.unborn)}], inventory["unreadable"])

    def test_a_root_that_hides_sources_is_an_environment_stop_naming_the_path(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.sources / "link"
        link.symlink_to(outside, target_is_directory=True)
        view = self.drive()
        self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", view.get("status"), view.get("stop_reason"))
        self.assertEqual("your_environment", view.get("pause_category"))
        self.assertIn(f"symlink_escape at {link}", view["stop_reason"])
        self.assertNotIn("repository_unreadable", view["stop_reason"])


if __name__ == "__main__":
    unittest.main()
