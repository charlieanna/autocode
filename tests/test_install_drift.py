"""Installed payload comparison uses a real verified checkout, not customer HEAD."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_install_drift as drift

PROJECT = """[project]
name = "autocode-supervisor"
version = "0.7.1"
[tool.setuptools]
packages = ["autocode_cli", "autocode_cli.providers"]
package-dir = {autocode_cli = "tools"}
[tool.setuptools.package-data]
autocode_cli = ["prompts/*.md"]
"autocode_cli.providers" = ["*.mjs"]
"""


class PayloadComparisonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="installed-drift-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.checkout = self.directory / "checkout"
        self.checkout.mkdir()
        self.write(self.checkout / "pyproject.toml", PROJECT)
        for name, content in {
            "autocode.py": "# CLI\n",
            "autocode_util.py": "# utilities\n",
            "providers/opencode.py": "# provider\n",
            "providers/events.mjs": "export default 1;\n",
            "prompts/requirements.md": "Ask a question.\n",
        }.items():
            self.write(self.checkout / "tools" / name, content)
        self.git(self.checkout, "init", "-q")
        self.git(self.checkout, "add", "pyproject.toml", "tools")
        self.git(
            self.checkout, "-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-qm", "Seed"
        )
        self.installed = self.directory / "autocode_cli"
        shutil.copytree(self.checkout / "tools", self.installed)

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    @staticmethod
    def git(workspace, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        return subprocess.check_output(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", str(workspace), *args],
            env=env,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).strip()

    def test_matching_declared_modules_and_resources_are_equal(self):
        result = drift.compare(self.checkout, self.installed)
        self.assertEqual(self.checkout, result.checkout)
        self.assertEqual(self.git(self.checkout, "rev-parse", "--short", "HEAD"), result.commit)
        self.assertEqual(((), (), (), None), (result.changed, result.missing, result.extra, result.error))

    def test_changed_python_and_declared_resource_are_named(self):
        self.write(self.installed / "autocode_util.py", "# older utilities\n")
        self.write(self.installed / "prompts/requirements.md", "Infer the answer.\n")
        result = drift.compare(self.checkout, self.installed)
        self.assertEqual(("autocode_util.py", "prompts/requirements.md"), result.changed)

    def test_missing_module_and_obsolete_module_are_named(self):
        (self.installed / "providers/opencode.py").unlink()
        self.write(self.installed / "obsolete.py", "# old module\n")
        result = drift.compare(self.checkout, self.installed)
        self.assertEqual(("providers/opencode.py",), result.missing)
        self.assertEqual(("obsolete.py",), result.extra)

    def test_unpackaged_fixtures_bytecode_and_generated_metadata_do_not_create_drift(self):
        self.write(self.checkout / "tools/fixtures/private.json", "not package data")
        self.write(self.installed / "__pycache__/autocode.pyc", "generated cache")
        self.write(self.installed.parent / "autocode_supervisor-0.7.1.dist-info/RECORD", "generated metadata")
        result = drift.compare(self.checkout, self.installed)
        self.assertEqual(((), (), ()), (result.changed, result.missing, result.extra))

    def test_executing_the_source_tree_or_an_editable_link_needs_no_comparison(self):
        self.assertIsNone(drift.compare(self.checkout, self.checkout / "tools"))
        link = self.directory / "editable"
        link.symlink_to(self.checkout / "tools", target_is_directory=True)
        self.assertIsNone(drift.compare(self.checkout, link))

    def test_foreign_project_never_supplies_an_autocode_commit(self):
        self.write(self.checkout / "pyproject.toml", PROJECT.replace("autocode-supervisor", "customer-project"))
        self.assertIsNone(drift.compare(self.checkout, self.installed))

    def test_lookalike_metadata_without_tracked_core_files_is_not_a_checkout(self):
        self.git(self.checkout, "rm", "--cached", "tools/autocode.py")
        self.assertIsNone(drift.compare(self.checkout, self.installed))

    def test_malformed_foreign_packaging_metadata_does_not_crash(self):
        self.write(
            self.checkout / "pyproject.toml",
            '[project]\nname="autocode-supervisor"\nversion="0.7.1"\n[tool]\nsetuptools="not a mapping"\n',
        )
        self.assertIsNone(drift.compare(self.checkout, self.installed))

    def test_verified_checkout_path_keeps_trailing_whitespace_and_newline(self):
        renamed = self.directory / "checkout \n"
        self.checkout.rename(renamed)
        self.checkout = renamed
        self.write(self.installed / "autocode_util.py", "# older utilities\n")
        result = drift.compare(self.checkout, self.installed)
        self.assertEqual(self.checkout, result.checkout)
        self.assertEqual(("autocode_util.py",), result.changed)
        self.assertIsNone(result.error)

    def test_caller_git_redirects_cannot_select_another_checkout(self):
        with patch.dict(
            os.environ, {"GIT_DIR": str(self.directory / "missing.git"), "GIT_WORK_TREE": str(self.installed)}
        ):
            result = drift.compare(self.checkout, self.installed)
        self.assertEqual(self.checkout, result.checkout)
        self.assertIsNone(result.error)

    def test_fifo_payload_is_unknown_without_opening_for_a_writer(self):
        target = self.installed / "prompts/blocked.md"
        os.mkfifo(target)
        with patch.object(drift.os, "open", wraps=os.open) as opened:
            result = drift.compare(self.checkout, self.installed)
        self.assertIn("not a regular file", result.error)
        self.assertEqual((), result.changed)
        self.assertNotIn(target, [call.args[0] for call in opened.call_args_list])

    def test_external_resource_link_is_unknown_instead_of_read_as_payload(self):
        outside = self.directory / "outside.md"
        outside.write_text("not AutoCode payload")
        target = self.installed / "prompts/requirements.md"
        target.unlink()
        target.symlink_to(outside)
        result = drift.compare(self.checkout, self.installed)
        self.assertIn("escapes", result.error)
        self.assertEqual((), result.changed)


if __name__ == "__main__":
    unittest.main()
