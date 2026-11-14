"""Known own-suite recursion is refused; distinct runner fixtures remain useful tests."""

import hashlib
import tempfile
import unittest
from pathlib import Path

import autocode_acceptance_tests as acceptance
import autocode_acceptance_tests_job as job
import autocode_regression as regression
import autocode_wrapped_runner as policy
from autocode_test_framework import Framework

from tests.test_acceptance_tests import approved
from tests.test_verify import Project

ARCHIVED = Path(__file__).with_name("fixtures") / "python_self_suite.py.txt"
SOURCE = ARCHIVED.read_text()
COMMAND = "[sys.executable, '-m', 'unittest', 'test_todo', '-v']"
HEADER = "import os, sys, subprocess, unittest\nfrom pathlib import Path\n"


class PythonSelfSuiteTests(unittest.TestCase):
    def check_source(self, source, refused=True, filename="test_todo.py"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
            found = policy.file_refusals(root, [filename])
            self.assertEqual(refused, filename in found, source)
            return found

    def test_archived_guarded_suite_is_recognized_without_running_it(self):
        self.assertEqual(
            "e521d929673fe642e7e2f07c38cfeedcf726b9929881e8d50c6f210f45017946",
            hashlib.sha256(ARCHIVED.read_bytes()).hexdigest(),
        )
        found = self.check_source(SOURCE)
        self.assertIn("launches its own Python test suite", found["test_todo.py"])

    def test_known_own_module_file_and_discovery_forms(self):
        variants = [
            f"subprocess.run({COMMAND})",
            f"subprocess.run(args={COMMAND})",
            "subprocess.check_call([sys.executable, '-B', '-m', 'unittest', 'test_todo.Suite'])",
            "subprocess.run([sys.executable, '-m', 'unittest', 'test_todo.Suite.test_own'])",
            "subprocess.run([sys.executable, '-m', 'unittest', 'test_todo.py'])",
            "subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', '.', '-p', 'test_*.py', '-t', '.'])",
            "subprocess.run([sys.executable, '-m', 'pytest', str(Path(__file__).resolve())])",
            "subprocess.run(['pytest', 'test_todo.py::Suite::test_own'])",
            "subprocess.run('python -m unittest test_todo', shell=True)",
            "subprocess.run([sys.executable, __file__])",
        ]
        for call in variants:
            with self.subTest(call=call):
                self.check_source(
                    HEADER
                    + "class Suite(unittest.TestCase):\n def test_own(self):\n  "
                    + call
                    + "\nif __name__ == '__main__':\n unittest.main()\n"
                )
        self.check_source(
            "import subprocess as sp\nfrom sys import executable as py\nfrom pathlib import Path as P\nfrom unittest import TestCase\nclass Suite(TestCase):\n def test_own(self):\n  argv=[py, '-m', 'unittest', 'test_todo']\n  here=P(__file__).parent\n  sp.run(argv, cwd=here)\n"
        )

    def test_product_distinct_fixture_and_excluding_selectors_are_allowed(self):
        calls = [
            "subprocess.run([sys.executable, 'todo.py', 'list'])",
            "subprocess.run([sys.executable, '-m', 'unittest', 'other_module'])",
            f"subprocess.run({COMMAND}, cwd=Path(__file__).parent / 'fixture')",
            "subprocess.run([sys.executable, '-m', 'unittest', 'test_todo.Suite.test_other'])",
            "subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-p', 'test_other.py'])",
            "subprocess.run(['pytest', 'test_todo.py::Suite::test_other'])",
            "subprocess.run(['pytest', 'test_todo.py', '-k', 'other'])",
            "subprocess.run([sys.executable, '-m', 'unittest', '-k', 'other', 'test_todo'])",
        ]
        for call in calls:
            with self.subTest(call=call):
                self.check_source(
                    HEADER + "class Suite(unittest.TestCase):\n def test_own(self):\n  " + call + "\n", False
                )
        self.check_source(HEADER + "if __name__ == '__main__':\n unittest.main()\n", False)
        self.check_source(
            HEADER + "# subprocess.run(" + COMMAND + ")\nexpected = 'python -m unittest test_todo'\n", False
        )
        # A file that is also a product worker is not assumed to execute unittest.
        self.check_source(HEADER + "subprocess.run([sys.executable, __file__, '--worker'])\n", False)

    def test_unknown_and_shadowed_bindings_do_not_prove_recursion(self):
        method = "class Suite(unittest.TestCase):\n def test_own(self):\n  "
        sources = [
            HEADER
            + "class Suite(unittest.TestCase):\n def test_own(self, subprocess):\n  "
            + f"subprocess.run({COMMAND})\n",
            HEADER + "subprocess = fake\n" + method + f"subprocess.run({COMMAND})\n",
            HEADER + "subprocess.run = fake\n" + method + f"subprocess.run({COMMAND})\n",
            HEADER + method + f"subprocess = fake\n  subprocess.run({COMMAND})\n",
            HEADER + "if unknown:\n subprocess = fake\n" + method + f"subprocess.run({COMMAND})\n",
            HEADER + method + f"subprocess.run({COMMAND}, cwd=unknown)\n",
            HEADER + method + f"subprocess.run({COMMAND}, **options)\n",
            HEADER + method + f"subprocess.run({COMMAND}, executable='product_cli')\n",
            HEADER + method + f"subprocess.run({COMMAND}, shell=True)\n",
            HEADER + method + f"subprocess.run({COMMAND}, shell=unknown)\n",
            HEADER + method + f"subprocess.Popen({COMMAND}, 0, 'product_cli')\n",
            HEADER + method + f"subprocess.run({COMMAND})\nsubprocess.__dict__['run'] = product_cli\n",
            HEADER + method + f"subprocess.run({COMMAND})\nsubprocess = fixture_runner\n",
            HEADER
            + "subprocess = fixture_runner\nclass Suite(unittest.TestCase):\n import subprocess\n def test_own(self):\n  "
            + f"subprocess.run({COMMAND})\n",
            HEADER + f"argv = {COMMAND}\n" + method + "subprocess.run(argv)\nargv = product_command\n",
            HEADER + method + "subprocess.run(make_command())\n",
            HEADER + method + f"subprocess.run({COMMAND})\nfor subprocess in [fixture_runner]: pass\n",
            HEADER + method + f"del subprocess\n  subprocess.run({COMMAND})\n",
            HEADER + method + f"subprocess.run({COMMAND})\nwith fixture_context() as subprocess: pass\n",
            HEADER + method + f"subprocess.run({COMMAND})\ntry: pass\nexcept Exception as subprocess: pass\n",
        ]
        for source in sources:
            with self.subTest(source=source):
                self.check_source(source, False)

    def test_relative_imports_do_not_identify_standard_library_runners(self):
        method = "class Suite(unittest.TestCase):\n def test_own(self):\n  "
        sources = [
            HEADER + "from . import helper\n" + method + "self.assertTrue(True)\n",
            "import sys, unittest\nfrom . import subprocess\n" + method + f"subprocess.run({COMMAND})\n",
            "import sys, subprocess\nfrom .unittest import TestCase\n"
            + "class Suite(TestCase):\n def test_own(self):\n  "
            + f"subprocess.run({COMMAND})\n",
            HEADER + "from .subprocess import run\n" + method + f"run({COMMAND})\n",
            HEADER + method + f"from .subprocess import run\n  run({COMMAND})\n",
            HEADER + method + f"from . import subprocess\n  subprocess.run({COMMAND})\n",
            HEADER
            + "from .unittest import main\n"
            + method
            + "subprocess.run([sys.executable, __file__])\nif __name__ == '__main__':\n main()\n",
        ]
        for source in sources:
            with self.subTest(source=source):
                self.check_source(source, False)

    def test_unrelated_relative_import_keeps_absolute_runner_detection(self):
        self.check_source(
            HEADER
            + "from . import helper\nclass Suite(unittest.TestCase):\n def test_own(self):\n  "
            + f"subprocess.run({COMMAND})\n"
        )

    def test_relative_wildcard_exports_remain_unknown(self):
        suite = "class Suite(unittest.TestCase):\n def test_own(self):\n  " + f"subprocess.run({COMMAND})\n"
        self.check_source(HEADER + "from .shim import *\n" + suite, False)
        self.check_source(HEADER + suite)

    def test_uncalled_helpers_and_runner_collection_distinctions(self):
        self.check_source(
            HEADER
            + f"def development_helper():\n subprocess.run({COMMAND})\nclass ProductTests(unittest.TestCase):\n def test_product_behavior(self): self.assertEqual(1+1,2)\n",
            False,
        )
        self.check_source(HEADER + f"def test_pytest_function():\n subprocess.run({COMMAND})\n", False)
        self.check_source(HEADER + "def test_pytest_function():\n subprocess.run(['pytest', 'test_todo.py'])\n")
        self.check_source(
            HEADER + "class Suite:\n def test_not_collected(self):\n  subprocess.run(['pytest', 'test_todo.py'])\n",
            False,
        )
        self.check_source(
            HEADER + "class TestSuite:\n def test_collected(self):\n  subprocess.run(['pytest', 'test_todo.py'])\n"
        )

    def test_lazy_expression_and_generator_bodies_are_not_direct_execution(self):
        for body in (
            f"unused = lambda: subprocess.run({COMMAND})",
            f"False and subprocess.run({COMMAND})",
            f"True or subprocess.run({COMMAND})",
            f"0 == 1 == subprocess.run({COMMAND})",
            f"unused = subprocess.run({COMMAND}) if False else None",
            f"unused = (subprocess.run({COMMAND}) for _ in ())",
            f"unused = [subprocess.run({COMMAND}) for _ in ()]",
            f"subprocess.run({COMMAND})\n  yield 1",
            f"return\n  subprocess.run({COMMAND})",
            f"raise RuntimeError('stop')\n  subprocess.run({COMMAND})",
        ):
            with self.subTest(body=body):
                self.check_source(
                    HEADER
                    + "class Suite(unittest.TestCase):\n def test_behavior(self):\n  "
                    + body
                    + "\n  self.assertTrue(True)\n",
                    False,
                )

    def test_owned_python_ids_and_discovery_paths_are_resolved_physically(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pkg").mkdir()
            (root / "pkg/__init__.py").write_text("")
            (root / "pkg/test_todo.py").write_text(
                HEADER
                + "class Suite(unittest.TestCase):\n def test_own(self):\n  subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'pkg', '-p', 'test_todo.py'])\n def test_behavior(self): self.assertEqual(1+1,2)\n"
            )
            ids = [
                "pkg.test_todo.Suite.test_own",
                "pkg/test_todo.py::Suite::test_own",
                "missing.Suite.test_own",
                "../pkg/test_todo.py::test_own",
            ]
            self.assertEqual(set(ids[:2]), set(policy.refusals(root, ids)))
            self.assertFalse(policy.refusals(root, ["pkg.test_todo.Suite.test_behavior"]))
            (root / "pkg/test_todo.py").write_text(
                HEADER + "subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'other'])\n"
            )
            self.assertFalse(policy.refusals(root, ids))
            (root / "test_todo.py").write_text(SOURCE)
            (root / "test_todo").mkdir()
            (root / "test_todo/TodoCliTests.py").write_text("# separate namespace fixture\n")
            name = "test_todo.TodoCliTests.test_ac8_regression_suite_runs"
            self.assertIn(name, policy.refusals(root, [name]))

    def test_decorators_async_and_custom_loaders_remain_unknown(self):
        method = f"class Suite(unittest.TestCase):\n def test_own(self):\n  subprocess.run({COMMAND})\n"
        for source in (
            HEADER + "@unittest.skip('development helper')\n" + method,
            HEADER + method.replace(" def test_own", " @replace_with_fixture\n def test_own"),
            HEADER + method.replace(" def test_own", " async def test_own"),
            HEADER + method + "def load_tests(*args): return unittest.TestSuite()\n",
            HEADER + method + "from fixture import load_tests\n",
            HEADER + method + "load_tests = fixture_loader\n",
            HEADER + "__test__ = False\n" + method,
            HEADER + method.replace(" def test_own", " __test__ = False\n def test_own"),
            HEADER + method + "Suite = ProductTests\n",
            HEADER
            + method
            + "\nclass ProductTests(unittest.TestCase):\n def test_behavior(self): self.assertEqual(1+1,2)\n"
            + "Suite = ProductTests\n",
            HEADER + "if False:\n" + "\n".join(" " + line for line in method.splitlines()) + "\n",
            HEADER + method.replace("  subprocess.run", "  if False:\n   subprocess.run"),
        ):
            with self.subTest(source=source):
                self.check_source(source, False)
        for source in (
            HEADER
            + "class TestSuite(UnknownBase):\n def test_own(self):\n  subprocess.run(['pytest','test_todo.py'])\n",
            HEADER
            + "class TestSuite:\n def __new__(cls): return object()\n def test_own(self):\n  subprocess.run(['pytest','test_todo.py'])\n",
            HEADER + method + " def test_own(self): self.assertEqual(2,1+1)\n",
            HEADER + method + " test_own = fixture_test\n",
        ):
            with self.subTest(source=source):
                self.check_source(source, False)


class AcceptancePreflightTests(unittest.TestCase):
    def test_aggregate_delivery_guidance_reaches_planner_builder_and_acceptance_packets(self):
        import autocode_test_cases as cases
        from units import autoplanner

        state = approved(
            {
                "C1": "test: test_product_behavior",
                "C2": "Inspect the actual current Core suite receipt and existing C1 named outcome",
            },
            task="Deliver product behavior and its runnable suite",
        )
        before = str(state["goal_contract"])
        acceptance.route(state)
        text, _ = job.prompt(state)
        self.assertIn("Never satisfy a suite-delivery criterion", text)
        self.assertIn("aggregate evidence obligation", autoplanner.EXAMPLE_CRITERIA_RULE)
        self.assertIn("do not invent another named test", cases.BUILDER_NOTE.lower())
        self.assertEqual(["C1"], [row["id"] for row in job.packet(state)["cases"]])
        self.assertEqual(before, str(state["goal_contract"]))

    def test_archived_source_is_refused_before_execution_and_protection(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "test_todo.py").write_text(SOURCE)
            name = "test_ac8_regression_suite_runs"
            state = approved({"AC8": "test: " + name}, task="Verify the delivered suite")
            acceptance.route(state)
            report = {
                "tests": [{"case_id": "AC8", "test_name": name, "file": "test_todo.py"}],
                "cannot_write": [],
                "already_passing": [],
            }
            executed = []

            def run(files):
                command = "python -m unittest test_todo -v"
                refused = policy.execution_refusal(
                    workspace, files, [(Framework("unittest", command), command, "acceptance-tests")]
                )
                if refused:
                    return refused
                executed.extend(files)
                return {"results": {"passed": [], "failed": [name]}}

            record = {"changed_files": ["test_todo.py"], "output": str(workspace / "author.json")}
            for _ in range(job.MAX_REWRITES):
                job.apply(state, report, record, workspace, run_tests=run)
                self.assertEqual([], executed, "Reject the real archived wrapper before invoking any suite")
            self.assertFalse(acceptance.receipts(state))
            self.assertEqual(job.MAX_REWRITES, len(job.packet(state)["runner_feedback"]))
            self.assertEqual(
                "test: " + name, state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"]
            )
            with self.assertRaisesRegex(job.TestsNeedRewriting, "already sent these tests back"):
                job.apply(state, report, record, workspace, run_tests=run)
            self.assertFalse(acceptance.receipts(state))


class PythonNamedProofTests(unittest.TestCase):
    def prove(self, source):
        project = Project(
            {
                "app.py": "value = 1\n",
                "runner_fixture/test_todo.py": "import unittest\nclass Child(unittest.TestCase):\n def test_child(self): self.assertEqual(4, 2 + 2)\n",
                "test_todo.py": HEADER
                + "from app import value\nclass Suite(unittest.TestCase):\n def test_c1_value(self): self.assertGreater(value, 0)\n",
            }
        )
        self.addCleanup(project.close)
        project.write({"app.py": "value = 2\n", "test_todo.py": source})
        state = {
            "base_commit": project.base,
            "settings": {},
            "iteration": 1,
            "stages": [],
            "history": [],
            "goal_contract": {
                "body": {
                    "task_kind": "build",
                    "acceptance_criteria": [
                        {"id": "C1", "criterion": "value is two", "verification_method": "test: test_c1_value"},
                        {"id": "C2", "criterion": "runner fixture passes", "verification_method": "guard: test_runner"},
                    ],
                    "milestones": [{"id": "M1"}],
                }
            },
        }
        return regression.prove(state, project.root, project.evidence)

    def test_later_self_suite_cannot_supply_a_named_guard(self):
        source = (
            HEADER
            + "from app import value\nclass Suite(unittest.TestCase):\n def test_c1_value(self): self.assertEqual(2, value)\n def test_runner(self):\n  if os.environ.get('INNER'): return\n  subprocess.run([sys.executable, '-m', 'unittest', 'test_todo'], env={**os.environ, 'INNER':'1'}, check=False, capture_output=True, text=True)\n"
        )
        proof = self.prove(source)
        self.assertEqual("FAIL", proof["verdict"], proof)
        self.assertEqual([], proof["case_tests"]["C2"])
        self.assertIn("launches its own Python test suite", str(proof["failures"]))

    def test_distinct_runner_fixture_and_product_behavior_retain_named_proof(self):
        source = (
            HEADER
            + "from app import value\nclass Suite(unittest.TestCase):\n def test_c1_value(self): self.assertEqual(2, value)\n def test_runner(self):\n  root = Path(__file__).parent / 'runner_fixture'\n  result = subprocess.run([sys.executable, '-m', 'unittest', 'test_todo', '-v'], cwd=root, capture_output=True, text=True)\n  self.assertEqual(0, result.returncode, result.stderr)\n  self.assertIn('test_child', result.stderr)\n"
        )
        proof = self.prove(source)
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertTrue(proof["case_tests"]["C1"])
        self.assertTrue(proof["case_tests"]["C2"])


if __name__ == "__main__":
    unittest.main()
