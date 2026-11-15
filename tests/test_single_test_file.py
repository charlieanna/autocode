"""One test file beside the code it tests is a test: the change to it is the regression proof.

A Python application keeps its tests in ``tests.py`` beside the code (what Django's ``startapp`` writes, and
``python3 -m unittest discover`` collects), and many small projects keep one ``test.js``, ``test.py``, ``test.c``
or ``test.sh`` at the top of the repository. None of these was a test file by its name, so a bug fix that added
its regression test there, where the project keeps its tests, failed its proof with "No regression test was
added or changed", and the person was asked to set a regression command (survey section 43).

These cases run real regression proofs in real Git projects.
tools/autocode_project_kinds.py (is_test_path, the languages' rows), tools/autocode_python_test_roles.py
"""

import json
import shlex
import shutil
import sys
import unittest
from unittest import mock

import autocode_protected_oracles as guard
import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify

from .test_verify import Project

PAGER = (
    "def page_count(total, size):\n"
    "    if size <= 0:\n        raise ValueError('size must be positive')\n"
    "    return total // size\n"
)
PAGER_FIXED = PAGER.replace("total // size", "(total + size - 1) // size")
PYTHON_TESTS = (
    "import unittest\n\nfrom {module} import page_count\n\n\n"
    "class PagerTests(unittest.TestCase):\n"
    "    def test_exact_multiple(self):\n        self.assertEqual(2, page_count(10, 5))\n\n"
    "    def test_size_must_be_positive(self):\n"
    "        with self.assertRaises(ValueError):\n            page_count(10, 0)\n"
)
PYTHON_NEW = "\n    def test_partial_last_page_is_counted(self):\n        self.assertEqual(3, page_count(11, 5))\n"
PYTHON_NO_PROOF = "\n    def test_one_page(self):\n        self.assertEqual(1, page_count(5, 5))\n"

INDEX = (
    "'use strict';\n\nfunction pageCount(total, size) {\n"
    "  if (size <= 0) throw new RangeError('size must be positive');\n"
    "  return Math.floor(total / size);\n}\n\nmodule.exports = { pageCount };\n"
)
INDEX_FIXED = INDEX.replace("Math.floor", "Math.ceil")
NODE_TESTS = (
    "'use strict';\nconst test = require('node:test');\nconst assert = require('node:assert');\n"
    "const { pageCount } = require('./index.js');\n\n"
    "test('an exact multiple needs no extra page', () => {\n  assert.strictEqual(pageCount(10, 5), 2);\n});\n"
)
NODE_NEW = "\ntest('a partial last page is counted', () => {\n  assert.strictEqual(pageCount(11, 5), 3);\n});\n"
SCRIPT_TESTS = (
    "'use strict';\nconst assert = require('assert');\nconst { pageCount } = require('./index.js');\n\n"
    "assert.strictEqual(pageCount(10, 5), 2);\nconsole.log('ok');\n"
)
SCRIPT_NEW = SCRIPT_TESTS.replace("console.log", "assert.strictEqual(pageCount(11, 5), 3);\nconsole.log")


def package(script):
    return json.dumps({"name": "pager", "version": "1.0.0", "scripts": {"test": script}}, indent=2) + "\n"


SHELL = "page_count() { echo $(( $1 / $2 )); }\n"
SHELL_FIXED = "page_count() { echo $(( ($1 + $2 - 1) / $2 )); }\n"
SHELL_TESTS = '#!/bin/sh\nset -e\n. ./pager.sh\n[ "$(page_count 10 5)" = 2 ]\necho ok\n'
SHELL_NEW = SHELL_TESTS.replace("echo ok", '[ "$(page_count 11 5)" = 3 ]\necho ok')
MAKE_SHELL = "test:\n\tsh test.sh\n.PHONY: test\n"

C_CODE = "int page_count(int total, int size) { return total / size; }\n"
C_FIXED = "int page_count(int total, int size) { return (total + size - 1) / size; }\n"
C_TESTS = (
    "#include <assert.h>\n#include <stdio.h>\nint page_count(int, int);\n"
    'int main(void) {\n    assert(page_count(10, 5) == 2);\n    puts("ok");\n    return 0;\n}\n'
)
C_NEW = C_TESTS.replace("    puts", "    assert(page_count(11, 5) == 3);\n    puts")
MAKE_C = "test: test.c pager.c\n\t$(CC) -o run_tests test.c pager.c\n\t./run_tests\n.PHONY: test\n"


class SingleTestFileProofTests(unittest.TestCase):
    def proof(self, seed, change):
        project = Project(seed)
        self.addCleanup(project.close)
        project.write(change)
        return project.verify()

    def python(self, name, module, new=PYTHON_NEW, fixed=PAGER_FIXED):
        tests = PYTHON_TESTS.format(module=module)
        code = module.replace(".", "/") + ".py"
        seed = {code: PAGER, name: tests}
        if "/" in code:
            seed[code.rsplit("/", 1)[0] + "/__init__.py"] = ""
        return self.proof(seed, {code: fixed, name: tests + new})

    def test_a_tests_module_beside_a_packages_code_proves_the_fix(self):
        result = self.python("shop/tests.py", "shop.pager")
        self.assertEqual(("PASS", [], []), (result["verdict"], result["failures"], result["unverified"]))
        self.assertEqual(["shop/tests.py"], result["test_files"])
        self.assertEqual(["shop.tests.PagerTests.test_partial_last_page_is_counted"], result["fail_to_pass"])
        self.assertEqual(
            ["shop.tests.PagerTests.test_exact_multiple", "shop.tests.PagerTests.test_size_must_be_positive"],
            result["pass_to_pass"],
        )
        self.assertTrue(result["commands"]["regression"].endswith("-m unittest -v shop/tests.py"))

    def test_a_new_test_that_passes_on_the_original_code_is_still_no_proof(self):
        result = self.python("shop/tests.py", "shop.pager", new=PYTHON_NO_PROOF)
        self.assertEqual("FAIL", result["verdict"])
        self.assertEqual([], result["fail_to_pass"])
        self.assertTrue(any("No test fails on the unfixed base code" in text for text in result["failures"]))

    def test_a_fix_with_no_change_to_the_tests_module_is_still_no_proof(self):
        tests = PYTHON_TESTS.format(module="shop.pager")
        result = self.proof(
            {"shop/__init__.py": "", "shop/pager.py": PAGER, "shop/tests.py": tests}, {"shop/pager.py": PAGER_FIXED}
        )
        self.assertEqual("FAIL", result["verdict"])
        self.assertEqual(
            ["No regression test was added or changed, so the bug is not shown to be reproduced"], result["failures"]
        )

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "the project's suite is npm test")
    def test_a_node_test_file_beside_index_js_proves_the_fix_by_its_named_test(self):
        result = self.proof(
            {"package.json": package("node --test"), "index.js": INDEX, "test.js": NODE_TESTS},
            {"index.js": INDEX_FIXED, "test.js": NODE_TESTS + NODE_NEW},
        )
        self.assertEqual(("PASS", [], []), (result["verdict"], result["failures"], result["unverified"]))
        self.assertEqual(["test.js"], result["test_files"])
        self.assertEqual(["test.js::a partial last page is counted"], result["fail_to_pass"])
        self.assertEqual("node --test test.js", result["commands"]["regression"])

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "the project's suite is npm test")
    def test_a_script_run_as_the_whole_suite_proves_the_fix_by_its_exit_code(self):
        seed = {"package.json": package("node test.js"), "index.js": INDEX, "test.js": SCRIPT_TESTS}
        result = self.proof(seed, {"index.js": INDEX_FIXED, "test.js": SCRIPT_NEW})
        self.assertEqual(("PASS", [], []), (result["verdict"], result["failures"], result["unverified"]))
        self.assertEqual(["test.js"], result["test_files"])
        # The same new assertion with the code left as it was: the script fails, so nothing is proven.
        result = self.proof(seed, {"index.js": INDEX + "// unchanged\n", "test.js": SCRIPT_NEW})
        self.assertNotEqual("PASS", result["verdict"])

    @unittest.skipUnless(shutil.which("make"), "the project's suite is make test")
    def test_a_shell_test_script_at_the_top_of_a_project_proves_the_fix(self):
        result = self.proof(
            {"Makefile": MAKE_SHELL, "pager.sh": SHELL, "test.sh": SHELL_TESTS},
            {"pager.sh": SHELL_FIXED, "test.sh": SHELL_NEW},
        )
        self.assertEqual(("PASS", [], []), (result["verdict"], result["failures"], result["unverified"]))
        self.assertEqual(["test.sh"], result["test_files"])

    @unittest.skipUnless(shutil.which("make") and shutil.which("cc"), "the project's suite is make test with cc")
    def test_a_c_test_program_at_the_top_of_a_project_proves_the_fix(self):
        result = self.proof(
            {"Makefile": MAKE_C, ".gitignore": "run_tests\n", "pager.c": C_CODE, "test.c": C_TESTS},
            {"pager.c": C_FIXED, "test.c": C_NEW},
        )
        self.assertEqual(("PASS", [], []), (result["verdict"], result["failures"], result["unverified"]))
        self.assertEqual(["test.c"], result["test_files"])

    def test_a_product_module_named_test_inside_a_package_is_still_product(self):
        command = '"""The test command of the tool."""\n\n\ndef run(arguments):\n    return 0 if arguments else 2\n'
        tests = PYTHON_TESTS.format(module="shop.pager")
        result = self.proof(
            {
                "shop/__init__.py": "",
                "shop/pager.py": PAGER,
                "shop/commands/__init__.py": "",
                "shop/commands/test.py": command,
                "tests/__init__.py": "",
                "tests/test_pager.py": tests,
            },
            {"shop/commands/test.py": command.replace("2", "1"), "shop/pager.py": PAGER_FIXED},
        )
        self.assertEqual("FAIL", result["verdict"])
        self.assertEqual([], result["test_files"])

    def test_a_product_module_named_tests_that_the_package_imports_is_still_product(self):
        # jinja2/tests.py: the template language's own tests, as functions named test_odd.
        rules = "def test_odd(value):\n    return value % 2 == 0\n\n\nTESTS = {'odd': test_odd}\n"
        suite = (
            "import unittest\n\nfrom tmpl.defaults import DEFAULT_TESTS\n\n\n"
            "class RuleTests(unittest.TestCase):\n"
            "    def test_names(self):\n        self.assertIn('odd', DEFAULT_TESTS)\n"
        )
        result = self.proof(
            {
                "tmpl/__init__.py": "",
                "tmpl/tests.py": rules,
                "tmpl/defaults.py": "from .tests import TESTS as DEFAULT_TESTS\n",
                "tests/__init__.py": "",
                "tests/test_rules.py": suite,
            },
            {
                "tmpl/tests.py": rules.replace("== 0", "== 1"),
                "tests/test_rules.py": suite
                + "\n    def test_odd(self):\n        self.assertTrue(DEFAULT_TESTS['odd'](3))\n",
            },
        )
        self.assertEqual(("PASS", [], []), (result["verdict"], result["failures"], result["unverified"]))
        self.assertEqual(["tests/test_rules.py"], result["test_files"])
        self.assertEqual(["tests.test_rules.RuleTests.test_odd"], result["fail_to_pass"])


class RootPythonTestScriptTests(unittest.TestCase):
    """``pager.py`` and ``test.py``, run as ``python3 test.py``: no runner is detected by a name that is a
    test only at the top of the repository, so the approved plan's own command proves the fix
    (autocode_plan_proof_command), which it could not while ``test.py`` was not a test file."""

    COMMAND = f"{shlex.quote(sys.executable)} test.py"
    TESTS = PYTHON_TESTS.format(module="pager") + "\n\nif __name__ == '__main__':\n    unittest.main()\n"
    NEW = TESTS.replace("\n\nif __name__", PYTHON_NEW + "\n\nif __name__")

    def prove(self, change):
        project = Project({"pager.py": PAGER, "test.py": self.TESTS})
        self.addCleanup(project.close)
        self.assertIsNone(verify.detect_framework(project.root))
        project.write(change)
        row = {
            "id": "AC1",
            "criterion": "pages",
            "verification_method": f"`{self.COMMAND}` fails on the original code and passes with the fix",
        }
        sealed = {"task_id": "task-1", "revision": 1, "body": {"task_kind": "bugfix", "acceptance_criteria": [row]}}
        state = {
            "workspace": str(project.root),
            "base_commit": project.base,
            "iteration": 1,
            "stages": [],
            "history": [],
            "settings": {"regression": {}},
            "goal_contract": {**sealed, "hash": util.digest(sealed), "approval_status": "approved"},
        }
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            return regression.prove(state, project.root, project.root.parent / "run")

    def test_the_approved_plans_command_proves_the_fix(self):
        proof = self.prove({"pager.py": PAGER_FIXED, "test.py": self.NEW})
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(self.COMMAND, proof["commands"]["regression"])
        self.assertEqual(self.COMMAND, proof["commands"]["suite"])
        self.assertEqual(["test.py"], proof["test_files"])

    def test_the_new_test_without_the_fix_proves_nothing(self):
        proof = self.prove({"pager.py": PAGER + "# unchanged\n", "test.py": self.NEW})
        self.assertNotEqual(verify.PASS, proof["verdict"], proof)


class SingleTestFileBindingTests(unittest.TestCase):
    def test_the_original_tests_of_a_run_hold_the_single_test_file(self):
        for seed, name in (
            (
                {
                    "shop/__init__.py": "",
                    "shop/pager.py": PAGER,
                    "shop/tests.py": PYTHON_TESTS.format(module="shop.pager"),
                },
                "shop/tests.py",
            ),
            ({"pager.py": PAGER, "test.py": PYTHON_TESTS.format(module="pager")}, "test.py"),
            ({"package.json": package("node --test"), "index.js": INDEX, "test.js": NODE_TESTS}, "test.js"),
            ({"Makefile": MAKE_SHELL, "pager.sh": SHELL, "test.sh": SHELL_TESTS}, "test.sh"),
            ({"Makefile": MAKE_C, "pager.c": C_CODE, "test.c": C_TESTS}, "test.c"),
        ):
            with self.subTest(name=name):
                project = Project(seed)
                self.addCleanup(project.close)
                self.assertEqual([name], sorted(guard.inventory(project.root, verify.is_test_path)))


if __name__ == "__main__":
    unittest.main()
