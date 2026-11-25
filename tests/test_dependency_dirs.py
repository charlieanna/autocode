"""Installed dependency directories are third-party code, never changed product lines (#3164)."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_dependency_dirs as dependency_dirs
import autocode_verification_copy as copies
import autocode_verify as verify


class InstalledDependencyTests(unittest.TestCase):
    def test_a_file_under_a_dependency_directory_at_any_depth_is_installed(self):
        for path in (
            "node_modules/jsdom/lib/api.js",
            "vscode-extension/node_modules/dep/index.ts",
            ".venv/lib/python3.12/site-packages/six.py",
            "services/api/venv/bin/activate.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(dependency_dirs.installed(path))

    def test_product_files_that_only_resemble_a_dependency_directory_are_not(self):
        for path in ("src/node_modules_loader.js", "tools/venv.py", "venv", "node_modules", "app/my_venv/run.py"):
            with self.subTest(path=path):
                self.assertFalse(dependency_dirs.installed(path))

    def test_the_summary_counts_files_and_names_each_dependency_directory_once(self):
        record = dependency_dirs.summary(
            [
                "node_modules/a/index.js",
                "node_modules/b/lib/x.js",
                "web/node_modules/c/main.js",
                "src/app.ts",
            ]
        )
        self.assertEqual(3, record["files"])
        self.assertEqual(["node_modules", "web/node_modules"], record["directories"])
        self.assertEqual(dependency_dirs.REASON, record["reason"])
        many = dependency_dirs.summary([f"p{n}/node_modules/x.js" for n in range(30)], listed=5)
        self.assertEqual((30, 5), (many["files"], len(many["directories"])))
        self.assertEqual(0, dependency_dirs.summary(["src/app.ts"])["files"])


def empty_as_npm_ci_does(directory: Path) -> None:
    """`npm ci` removes every entry of node_modules before it installs: a link is unlinked, a directory
    is removed with what it holds."""
    for entry in list(directory.iterdir()):
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


class ScratchCopyTests(unittest.TestCase):
    """A check run in a scratch copy uses the person's installed packages and cannot delete them.

    On a real run of "upgrade ms to 2.1.3" the plan's check `npm ci` ran in a scratch copy whose
    node_modules was a link to the workspace's: it emptied the workspace's packages, and every later
    check failed with "Cannot find module 'ms'" until the run stopped for the person."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home / "project"
        self.installed = {
            "node_modules/dep/index.js": "module.exports = 1;\n",
            "node_modules/@scope/tool/index.js": "module.exports = 2;\n",
            "node_modules/.bin/dep": "#!/bin/sh\n",
            "node_modules/.package-lock.json": '{"packages": {"node_modules/dep": {"version": "1.0.0"}}}\n',
            "packages/web/node_modules/other/main.js": "exports.value = 3;\n",
        }
        files = {
            ".gitignore": "node_modules/\n",
            "package.json": '{"name": "root"}\n',
            "packages/web/package.json": '{"name": "web"}\n',
            **self.installed,
        }
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for arguments in (
            ("init", "-q"),
            ("add", "-A"),
            ("-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "original"),
        ):
            subprocess.run(["git", *arguments], cwd=self.root, check=True, capture_output=True)

    def assert_installed(self):
        for name, text in self.installed.items():
            self.assertEqual(text, (self.root / name).read_text(), name)

    def test_a_copy_reads_each_installed_package_through_one_link(self):
        target, view = self.home / "copy/node_modules", self.home / "views/node_modules"
        target.parent.mkdir()
        dependency_dirs.expose(self.root / "node_modules", target, view)
        self.assertTrue(target.is_symlink())
        for name in ("dep/index.js", "@scope/tool/index.js", ".bin/dep", ".package-lock.json"):
            self.assertEqual(self.installed["node_modules/" + name], (target / name).read_text(), name)
        self.assertEqual((self.root / "node_modules/dep").resolve(), (target / "dep").resolve())

    def test_emptying_the_copys_packages_as_npm_ci_does_leaves_the_persons_installed(self):
        target = self.home / "copy/node_modules"
        target.parent.mkdir()
        dependency_dirs.expose(self.root / "node_modules", target, self.home / "views/node_modules")
        empty_as_npm_ci_does(target)
        self.assertEqual([], list(target.iterdir()))
        self.assert_installed()

    def test_a_file_the_package_manager_rewrites_in_the_copy_is_the_copys_own(self):
        target = self.home / "copy/node_modules"
        target.parent.mkdir()
        dependency_dirs.expose(self.root / "node_modules", target, self.home / "views/node_modules")
        (target / ".package-lock.json").write_text("{}\n")  # `npm install` writes what it installed here
        self.assert_installed()

    def test_a_virtualenv_without_live_roots_is_linked_whole(self):
        environment = self.root / ".venv"
        (environment / "bin").mkdir(parents=True)
        target, view = self.home / "copy/.venv", self.home / "views/.venv"
        target.parent.mkdir()
        dependency_dirs.expose(environment, target, view)
        self.assertEqual(str(environment), os.readlink(target))
        self.assertFalse(view.exists())

    def test_a_runner_scratch_tree_keeps_its_view_out_of_the_trees_files(self):
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout.strip()
        tree = verify.make_tree(self.root, base, self.home / "scratch/tree", self.root, {}, dependencies_from=self.root)
        self.addCleanup(verify.remove_tree, self.root, tree)
        for package in ("", "packages/web/"):
            with self.subTest(package=package or "the root"):
                self.assertTrue((tree / package / "node_modules").is_symlink())
        self.assertEqual("module.exports = 1;\n", (tree / "node_modules/dep/index.js").read_text())
        self.assertEqual("exports.value = 3;\n", (tree / "packages/web/node_modules/other/main.js").read_text())
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=tree,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        # As before: the two links (an ignore rule for a directory does not cover a link) and no view.
        tracked = [".gitignore", "package.json", "packages/web/package.json"]
        self.assertEqual(sorted([*tracked, "node_modules", "packages/web/node_modules"]), sorted(listed))
        self.assertEqual({}, verify.changed_files(tree, base))
        empty_as_npm_ci_does(tree / "node_modules")
        empty_as_npm_ci_does(tree / "packages/web/node_modules")
        self.assert_installed()

    def test_a_testers_verification_copy_cannot_empty_the_persons_packages(self):
        run_dir = self.root / ".autocode/runs/fixture"
        run_dir.mkdir(parents=True)
        result = copies.allocate(self.root, run_dir)
        tree, _ = copies.execution(result["manifest"], result["sha256"], self.root)
        self.assertEqual("module.exports = 1;\n", (tree / "node_modules/dep/index.js").read_text())
        self.assertFalse((tree / "node_modules").resolve().is_relative_to(tree))  # the manifest binds the link only
        empty_as_npm_ci_does(tree / "node_modules")
        self.assert_installed()
        # The copy's inputs changed, so the next captured command asks for a fresh copy, as before.
        with self.assertRaisesRegex(ValueError, "Verification"):
            (tree / "node_modules").unlink()
            copies.execution(result["manifest"], result["sha256"], self.root)


class CustodyTests(unittest.TestCase):
    """A proof copy's installed directories lead to the copy's own source, never the live tree (#4152, phase 2).

    On #4057 a task `.venv` with an editable install pointed Python at the live workspace for any module
    the original code lacked: changing only the candidate's value flipped the supposed run of the original
    code from exit 0 to 1. A workspace package linked into `node_modules` did the same for Node."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.live, self.tree = self.home / "live", self.home / "tree"
        for root, value in ((self.live, 2), (self.tree, 1)):
            (root / "src/pkg").mkdir(parents=True)
            (root / "src/pkg/__init__.py").write_text(f"VALUE = {value}\n")
        (self.live / "src/added.py").write_text("ADDED = True\n")  # the candidate's new module

    def test_a_copys_virtualenv_reaches_the_copys_source_not_the_live_trees(self):
        environment = self.live / ".venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True)
        site = next((environment / "lib").glob("python*/site-packages"))
        (site / "__editable__.pkg.pth").write_text(str(self.live / "src") + "\n")
        (site / "__editable___other_finder.py").write_text(f"MAPPING = {{'other': {str(self.live / 'src/other')!r}}}\n")
        (site / "installed").mkdir()
        (site / "installed/__init__.py").write_text("")
        (environment / "bin/tool").write_text(f"#!{environment}/bin/python\nimport pkg\n")
        (environment / "bin/tool").chmod(0o755)
        before = {
            path: path.read_bytes() for path in environment.rglob("*") if path.is_file() and not path.is_symlink()
        }
        target = self.tree / ".venv"
        record = dependency_dirs.expose(
            environment, target, self.home / "views/.venv", live=[self.live], tree=self.tree
        )

        copied = next((target / "lib").glob("python*/site-packages"))
        self.assertEqual(str(self.tree / "src") + "\n", (copied / "__editable__.pkg.pth").read_text())
        self.assertIn(repr(str(self.tree / "src/other")), (copied / "__editable___other_finder.py").read_text())
        self.assertEqual(f"#!{target}/bin/python\n", (target / "bin/tool").read_text().splitlines(True)[0])
        self.assertEqual((site / "installed").resolve(), (copied / "installed").resolve())  # third-party: linked
        rewritten = {Path(path).name for path in record["rewritten"]}
        self.assertLessEqual({"__editable__.pkg.pth", "__editable___other_finder.py", "tool"}, rewritten, record)

        def run(python, code):
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}  # as every proof command runs
            env.pop("PYTHONPATH", None)
            return subprocess.run([str(python), "-c", code], cwd=self.home, env=env, capture_output=True, text=True)

        # Through the person's environment the live tree's module is found; through the copy's it is not.
        self.assertEqual(0, run(environment / "bin/python", "import added").returncode)
        missing = run(target / "bin/python", "import added")
        self.assertIn("No module named 'added'", missing.stderr)
        found = run(target / "bin/python", "import pkg, installed, sys; print(pkg.VALUE, sys.prefix)")
        self.assertEqual(f"1 {target}", found.stdout.strip(), found.stderr)
        after = {path: path.read_bytes() for path in environment.rglob("*") if path.is_file() and not path.is_symlink()}
        self.assertEqual(before, after)  # the person's environment is never written

    def test_a_linked_workspace_package_resolves_to_the_copys_package(self):
        for root in (self.live, self.tree):
            for name in ("one", "two"):
                (root / "packages" / name).mkdir(parents=True)
                (root / "packages" / name / "cli.js").write_text(f"// {root.name}\n")
        modules = self.live / "node_modules"
        (modules / "dep").mkdir(parents=True)
        (modules / "dep/index.js").write_text("module.exports = 1;\n")
        (modules / "one").symlink_to("../packages/one")  # npm workspaces write relative links
        (modules / "@scope").mkdir()
        (modules / "@scope/two").symlink_to(self.live / "packages/two")  # npm link writes absolute ones
        (modules / ".bin").mkdir()
        (modules / ".bin/one").symlink_to("../one/cli.js")
        (modules / ".bin/dep").symlink_to("../dep/index.js")
        (modules / ".bin/plain.cmd").write_text("@node ..\\dep\\index.js\n")
        (modules / ".pnpm/stored@1.0.0").mkdir(parents=True)
        (modules / ".pnpm/stored@1.0.0/index.js").write_text("module.exports = 3;\n")
        (modules / "stored").symlink_to(".pnpm/stored@1.0.0")  # pnpm links inside node_modules: installed
        target = self.tree / "node_modules"
        dependency_dirs.expose(modules, target, self.home / "views/node_modules", live=[self.live], tree=self.tree)
        for name, expected in (
            ("one/cli.js", self.tree / "packages/one/cli.js"),
            ("@scope/two/cli.js", self.tree / "packages/two/cli.js"),
            (".bin/one", self.tree / "packages/one/cli.js"),
            ("dep/index.js", modules / "dep/index.js"),
            (".bin/dep", modules / "dep/index.js"),
            ("stored/index.js", modules / ".pnpm/stored@1.0.0/index.js"),
        ):
            with self.subTest(name=name):
                self.assertEqual(expected, (target / name).resolve())
        self.assertEqual("@node ..\\dep\\index.js\n", (target / ".bin/plain.cmd").read_text())
        empty_as_npm_ci_does(target)
        self.assertTrue((modules / "@scope/two").is_symlink() and (modules / ".bin/one").is_symlink())


if __name__ == "__main__":
    unittest.main()
