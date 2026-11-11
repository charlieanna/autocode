"""Installed dependency directories are third-party code, never changed product lines (#3164)."""

import unittest

import autocode_dependency_dirs as dependency_dirs


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


if __name__ == "__main__":
    unittest.main()
