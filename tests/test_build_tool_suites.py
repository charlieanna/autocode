"""A Maven, Gradle or CMake project's build tool runs its suite (autocode_toolchain_suite.build_tool).

Real bug fixes in a Maven project with JUnit and in a C++ project with CMake and CTest (Claude models,
2026-11-14) delivered correct code with a regression test, and the proof ended ``UNVERIFIED`` ("No command
to run the regression tests; pass --regression-command"): no suite was detected, and the approved plan's
own command (``mvn test``) names no test file, so it could not be adopted. The Resolver then asked the
person to set both commands. The detection cases are pure; the proof cases build and run a real CMake
project, as tests.test_ruby_test_suite runs real Ruby, and never launch a provider.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_command_guards as command_guards
import autocode_toolchain_suite as toolchain_suite
import autocode_verify as verify

CMAKE = (
    "cmake_minimum_required(VERSION 3.16)\nproject(pager CXX)\n\n"
    "add_library(pager src/pager.cpp)\ntarget_include_directories(pager PUBLIC src)\n\n"
    "enable_testing()\nadd_executable(pager_test tests/pager_test.cpp)\n"
    "target_link_libraries(pager_test PRIVATE pager)\nadd_test(NAME pager_test COMMAND pager_test)\n"
)
HEADER = "#pragma once\n\nint page_count(int total, int size);\n"
LIBRARY = '#include "pager.h"\n\nint page_count(int total, int size) { return total / size; }\n'
FIXED = LIBRARY.replace("total / size", "(total + size - 1) / size")
EXISTING = '#include "pager.h"\n\nint main() {\n    if (page_count(10, 5) != 2) return 1;\n    return 0;\n}\n'
REGRESSION = EXISTING.replace("    return 0;", "    if (page_count(11, 5) != 3) return 1;\n    return 0;")
# A new check the original code already satisfies: it cannot show that anything was fixed.
VACUOUS = EXISTING.replace("    return 0;", "    if (page_count(5, 5) != 1) return 1;\n    return 0;")
SEED = {
    "CMakeLists.txt": CMAKE,
    ".gitignore": "build/\n",
    "src/pager.h": HEADER,
    "src/pager.cpp": LIBRARY,
    "tests/pager_test.cpp": EXISTING,
}
JAVA = {"src/main/java/example/Pager.java": "", "src/test/java/example/PagerTest.java": ""}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def detect(files):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        return toolchain_suite.detect(root, sorted(files), verify.is_test_path)


class DetectionTests(unittest.TestCase):
    def test_a_maven_project_runs_its_tests_with_maven(self):
        framework = detect({**JAVA, "pom.xml": "<project/>\n"})
        self.assertEqual(("maven", "mvn -B -q test"), (framework.name, framework.suite))
        self.assertFalse(framework.per_test)  # judged by the suite's exit code, as make test is
        self.assertIsNone(framework.targeted(["src/test/java/example/PagerTest.java"]))

    def test_a_gradle_project_runs_its_tests_with_gradle(self):
        for build_file in toolchain_suite.GRADLE_ROOTS:
            with self.subTest(build_file=build_file):
                framework = detect({**JAVA, build_file: ""})
                self.assertEqual(("gradle", "gradle --no-daemon -q test"), (framework.name, framework.suite))
                self.assertFalse(framework.per_test)

    def test_the_projects_own_wrapper_runs_when_it_has_one(self):
        self.assertEqual("./mvnw -B -q test", detect({**JAVA, "pom.xml": "<project/>\n", "mvnw": ""}).suite)
        self.assertEqual("./gradlew --no-daemon -q test", detect({**JAVA, "build.gradle.kts": "", "gradlew": ""}).suite)
        # A wrapper in a subdirectory is not the root project's.
        self.assertEqual("mvn -B -q test", detect({**JAVA, "pom.xml": "<project/>\n", "tools/mvnw": ""}).suite)

    def test_a_cmake_project_that_turns_testing_on_runs_ctest(self):
        for line in ("enable_testing()", "  ENABLE_TESTING ( )", "include(CTest)", "include( ctest )"):
            with self.subTest(line=line):
                framework = detect({**SEED, "CMakeLists.txt": CMAKE.replace("enable_testing()", line)})
                self.assertEqual(("cmake", toolchain_suite.CMAKE_SUITE), (framework.name, framework.suite))
                self.assertFalse(framework.per_test)

    def test_a_cmake_project_without_tests_has_no_suite(self):
        for text in (
            CMAKE.replace("enable_testing()\n", ""),
            CMAKE.replace("enable_testing()", "# enable_testing()"),
            CMAKE.replace("enable_testing()", 'message("call enable_testing() to add tests")'),
        ):
            with self.subTest(text=text):
                self.assertIsNone(detect({**SEED, "CMakeLists.txt": text}))

    def test_a_build_file_below_the_root_is_not_the_projects_suite(self):
        for files in (
            {"service/pom.xml": "<project/>\n", "service/src/test/java/PagerTest.java": ""},
            {"app/build.gradle": "", "app/src/test/java/PagerTest.java": ""},
            {"native/CMakeLists.txt": CMAKE, "native/tests/pager_test.cpp": EXISTING},
        ):
            with self.subTest(files=sorted(files)):
                self.assertIsNone(detect(files))

    def test_a_suite_the_project_declares_another_way_keeps_priority(self):
        for extra, expected in (
            ({"Makefile": "test:\n\tmvn -o test\n"}, "make"),
            ({"go.mod": "module sample\ngo 1.22\n"}, "go"),
            ({"Cargo.toml": "[package]\nname = 'sample'\n"}, "cargo"),
            ({"package.json": '{"scripts": {"test": "jest"}, "devDependencies": {"jest": "29"}}'}, "jest"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(expected, detect({**JAVA, "pom.xml": "<project/>\n", **extra}).name)

    def test_a_stray_python_helper_does_not_hide_the_build_tools_suite(self):
        for manifest in ("pom.xml", "build.gradle", "CMakeLists.txt"):
            with self.subTest(manifest=manifest):
                self.assertTrue(toolchain_suite.python_is_secondary([manifest, "scripts/release.py"]))
                # A Python project that also builds a native part stays a Python project.
                self.assertFalse(toolchain_suite.python_is_secondary([manifest, "pyproject.toml", "pkg/core.py"]))
                self.assertFalse(toolchain_suite.python_is_secondary([manifest, "tests/test_core.py"]))

    def test_a_refactor_is_guarded_by_the_build_tools_own_command(self):
        framework = detect({**JAVA, "pom.xml": "<project/>\n"})
        guard = {"id": "AC2", "command": "mvn -B -q test"}
        self.assertEqual([guard], command_guards.usable([guard], framework, ["src/test/java/example/PagerTest.java"]))
        self.assertIsNone(command_guards.problem([guard], framework, []))
        # Another spelling is not the suite: the Planner is told which command is.
        told = command_guards.problem([{"id": "AC2", "command": "mvn verify"}], framework, [])
        self.assertIn("is not the project's suite `mvn -B -q test`", told)


@unittest.skipUnless(shutil.which("cmake") and shutil.which("c++"), "builds the project with CMake and a C++ compiler")
class ProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cmake-suite-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "project"
        self.evidence = Path(self.temp.name) / "evidence"
        self.write(SEED)
        git(self.root, "init", "-q")
        git(self.root, "config", "maintenance.auto", "false")  # see tests.test_verify.Project
        git(self.root, "config", "gc.auto", "0")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.base = git(self.root, "rev-parse", "HEAD")

    def write(self, files):
        for name, text in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)

    def verify(self):
        framework = verify.detect_framework(self.root)
        self.assertEqual("cmake", framework.name)
        base_suite = verify.baseline(
            self.root, self.base, self.evidence, framework=framework, suite_command=framework.suite, timeout=300
        )
        return verify.verify(
            self.root, self.base, self.evidence, framework=framework, base_suite=base_suite, timeout=300
        )

    def test_a_fix_with_its_regression_test_is_proved_without_a_command_from_the_person(self):
        self.write({"src/pager.cpp": FIXED, "tests/pager_test.cpp": REGRESSION})
        result = self.verify()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual([], result["unverified"], result)
        self.assertEqual("detected:cmake", result["commands"]["suite_source"])
        self.assertEqual(["tests/pager_test.cpp"], result["test_files"])
        # The new check ran against the original code and failed there.
        self.assertNotEqual(0, result["checks"]["regression_on_base"]["exit_code"], result["checks"])

    def test_a_new_test_the_original_code_already_passes_proves_nothing(self):
        self.write({"src/pager.cpp": FIXED, "tests/pager_test.cpp": VACUOUS})
        result = self.verify()
        self.assertEqual("FAIL", result["verdict"], result)
        self.assertIn("pass on the unfixed base code", " ".join(result["failures"]))

    def test_a_change_that_leaves_its_own_test_failing_is_refused(self):
        self.write(
            {"src/pager.cpp": LIBRARY.replace("total / size", "total / size + 0"), "tests/pager_test.cpp": REGRESSION}
        )
        result = self.verify()
        self.assertEqual("FAIL", result["verdict"], result)

    def test_a_change_that_no_longer_builds_is_refused(self):
        self.write({"src/pager.cpp": FIXED.replace("return", "retrun"), "tests/pager_test.cpp": REGRESSION})
        result = self.verify()
        self.assertEqual("FAIL", result["verdict"], result)


if __name__ == "__main__":
    unittest.main()
