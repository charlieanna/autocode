"""Static recognition never executes an installed or project-selected launcher."""

import os
import tempfile
import unittest
from pathlib import Path

import autocode_python_entrypoints as entrypoints


@unittest.skipUnless(os.name == "posix", "pytest console containment is a POSIX boundary")
class PytestLauncherTests(unittest.TestCase):
    def test_standard_pip_and_uv_bodies_select_the_paired_interpreter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            launcher, python = root / "pytest", root / "python3.12"
            for module, entry in (("pytest", "console_main"), ("_pytest.config", "_console_main")):
                for imports, bootstrap in (
                    ("import sys", ""),
                    (
                        "import re\nimport sys",
                        r"    sys.argv[0] = re.sub(r'(-script\.pyw|\.exe)?$', '', sys.argv[0])" + "\n",
                    ),
                    ("import sys", '    if sys.argv[0].endswith(".exe"):\n        sys.argv[0] = sys.argv[0][:-4]\n'),
                ):
                    with self.subTest(module=module, bootstrap=bootstrap):
                        launcher.write_text(
                            f"#!{python}\n# coding: utf-8\n{imports}\nfrom {module} import {entry}\n"
                            f'if __name__ == "__main__":\n{bootstrap}    sys.exit({entry}())\n'
                        )
                        self.assertEqual(python, entrypoints.pytest_interpreter(launcher))

    def test_modified_launcher_is_refused_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            marker = root / "EXECUTED"
            launcher = root / "pytest"
            launcher.write_text(
                f"#!{root / 'python3'}\nimport sys\nfrom pytest import console_main\n"
                f"open({str(marker)!r}, 'w').write('executed')\n"
                "if __name__ == '__main__':\n    sys.exit(console_main())\n"
            )
            with self.assertRaisesRegex(RuntimeError, "Cannot qualify"):
                entrypoints.pytest_interpreter(launcher)
            self.assertFalse(marker.exists())

    def test_unpaired_interpreter_and_env_or_argument_shebangs_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            launcher = root / "pytest"
            for header in ("#!/usr/bin/env python3", "#!/usr/bin/python3", f"#!{root}/python3 -c", "#!/bin/sh"):
                with self.subTest(header=header):
                    launcher.write_text(
                        header + "\nimport sys\nfrom pytest import console_main\n"
                        "if __name__ == '__main__':\n    sys.exit(console_main())\n"
                    )
                    with self.assertRaisesRegex(RuntimeError, "Cannot qualify"):
                        entrypoints.pytest_interpreter(launcher)

    def test_oversized_and_invalid_source_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            launcher = root / "pytest"
            for body in ("#" * 16385, "invalid python source (", "\x00"):
                with self.subTest(body=body[:20]):
                    launcher.write_text(f"#!{root}/python3\n" + body)
                    with self.assertRaisesRegex(RuntimeError, "Cannot qualify"):
                        entrypoints.pytest_interpreter(launcher)
