"""Real loader fixtures for Linux toolchain discovery (#709): what a tool loads outside /usr."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_elf_dependencies as elf

LINUX_COMPILER = sys.platform.startswith("linux") and shutil.which("cc")


@unittest.skipUnless(LINUX_COMPILER, "requires Linux and a C compiler")
class LoaderFixtureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # A toolchain installed outside the system roots, as a hosted Python or Node is.
        self.root = Path(directory.name).resolve() / "toolchain"
        (self.root / "bin").mkdir(parents=True)
        (self.root / "lib").mkdir()

    def compile(self, *arguments):
        subprocess.run(["cc", *map(str, arguments)], check=True, capture_output=True, timeout=60)

    def library(self, name, body, *extra):
        source = self.root / (name + ".c")
        source.write_text(body)
        target = self.root / "lib" / ("lib" + name + ".so")
        self.compile("-shared", "-fPIC", source, "-o", target, *extra)
        return target

    def program(self, *extra):
        source = self.root / "main.c"
        source.write_text(
            '#include <stdio.h>\nextern int outer(void);\nint main(void) { printf("%d\\n", outer()); return 0; }\n'
        )
        target = self.root / "bin" / "main"
        lib = self.root / "lib"
        self.compile(source, "-o", target, "-L", lib, "-Wl,-rpath-link," + str(lib), "-louter", *extra)
        return target

    def test_runpath_origin_library_is_followed_and_system_libraries_are_not(self):
        library = self.library("outer", "int outer(void) { return 42; }\n")
        program = self.program("-Wl,--enable-new-dtags", "-Wl,-rpath,$ORIGIN/../lib")
        self.assertEqual("42\n", subprocess.run([program], check=True, capture_output=True, text=True).stdout)
        # libc and the loader come from /usr or /lib, which the boundary mounts already.
        self.assertEqual({program, library}, elf.dependencies(program))

    def test_rpath_is_inherited_down_the_loading_chain(self):
        inner = self.library("inner", "int inner(void) { return 41; }\n")
        outer = self.library(
            "outer",
            "extern int inner(void);\nint outer(void) { return inner() + 1; }\n",
            "-L",
            self.root / "lib",
            "-linner",
        )
        # DT_RPATH (not RUNPATH) on the program also serves libouter's own dependency.
        program = self.program("-Wl,--disable-new-dtags", "-Wl,-rpath,$ORIGIN/../lib")
        self.assertEqual("42\n", subprocess.run([program], check=True, capture_output=True, text=True).stdout)
        self.assertEqual({program, outer, inner}, elf.dependencies(program))

    def test_library_found_only_by_the_system_search_is_not_granted(self):
        self.library("outer", "int outer(void) { return 42; }\n")
        program = self.program()  # No search path: the loader would not find libouter.
        self.assertEqual({program}, elf.dependencies(program))

    def test_unqualified_search_path_token_is_refused(self):
        self.library("outer", "int outer(void) { return 42; }\n")
        program = self.program("-Wl,-rpath,$PLATFORM/lib")
        with self.assertRaisesRegex(ValueError, "Unsupported dynamic string token"):
            elf.dependencies(program)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux system tools")
class SelectionTests(unittest.TestCase):
    def test_script_is_refused_like_a_non_mach_o_tool_on_macos(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "tool"
            script.write_text("#!/bin/sh\nexit 0\n")
            with self.assertRaisesRegex(ValueError, "not a native ELF executable"):
                elf.dependencies(script)

    def test_system_tool_needs_nothing_beyond_the_mounted_runtime(self):
        self.assertEqual(set(), elf.dependencies(Path("/bin/sh").resolve()))


if __name__ == "__main__":
    unittest.main()
