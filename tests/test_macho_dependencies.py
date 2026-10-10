"""Real loader fixture: a dylib's install ID is not another dependency."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_macho_dependencies as macho


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"), "requires macOS compiler")
class LibraryIdentityTests(unittest.TestCase):
    def test_relative_load_does_not_need_to_resolve_librarys_own_rpath_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, main = root / "library.c", root / "main.c"
            library, executable = root / "libfixture.dylib", root / "main"
            source.write_text("int fixture_value(void) { return 42; }\n")
            main.write_text(
                "#include <stdio.h>\nextern int fixture_value(void);\n"
                'int main(void) { printf("%d\\n", fixture_value()); return 0; }\n'
            )
            commands = [
                ["clang", "-dynamiclib", str(source), "-Wl,-install_name,@rpath/libfixture.dylib", "-o", str(library)],
                ["clang", str(main), str(library), "-o", str(executable)],
                [
                    "/usr/bin/install_name_tool",
                    "-change",
                    "@rpath/libfixture.dylib",
                    "@executable_path/libfixture.dylib",
                    str(executable),
                ],
                ["/usr/bin/codesign", "--force", "--sign", "-", str(executable)],
            ]
            for command in commands:
                subprocess.run(command, check=True, capture_output=True, timeout=30)
            actual = subprocess.run([str(executable)], check=True, capture_output=True, text=True, timeout=10)
            self.assertEqual("42\n", actual.stdout)
            self.assertEqual({library, executable}, macho.dependencies(executable))
