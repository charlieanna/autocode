"""Proof receipts must not borrow source from the live candidate (#4152, phase 1)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_acceptance_reference as acceptance_reference
import autocode_check_outcome as check_outcome
import autocode_command_guards as command_guards
import autocode_companion_suites as companion_suites
import autocode_first_suite as first_suite
import autocode_follow_up_proof as follow_up_proof
import autocode_proof_stability as proof_stability
import autocode_scratch_suites as scratch_suites
import autocode_source_binding as source_binding
import autocode_source_probe as source_probe
import autocode_suite_flakes as suite_flakes
import autocode_test_framework as test_framework
import autocode_verify as verify
import autocode_verify_judgment as judgment
from autocode_verification_schedule import intact, proof_intact


class SourceBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="source-binding-4152-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.tree = self.base / "tree"
        self.workspace = self.base / "live"
        self.tree.mkdir()
        self.workspace.mkdir()

    def write(self, root, files):
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def probe(self, framework, command, *, env=None):
        request = self.base / "request.json"
        capture = self.base / "capture.json"
        request.write_text(
            json.dumps(
                {
                    "framework": framework,
                    "command": command,
                    "tree": str(self.tree),
                    "root": str(self.tree),
                    "workspace": str(self.workspace),
                    "capture": str(capture),
                }
            )
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(Path(verify.__file__).parent), *filter(None, [environment.get("PYTHONPATH", "")])]
        )
        environment.update(env or {})
        completed = subprocess.run(
            [sys.executable, str(Path(verify.__file__).with_name("autocode_source_probe.py")), str(request)],
            cwd=self.tree,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(capture.read_text())
        return source_binding.assess(result, self.tree, self.workspace)

    def test_paths_inside_the_owning_copy_are_established(self):
        result = {
            "observations": [
                {"kind": "python_test", "path": str(self.tree / "tests/test_app.py"), "role": "test"},
                {"kind": "python_module", "path": str(self.tree / "app.py"), "role": "source"},
                {"kind": "pytest_rootdir", "path": str(self.tree), "role": "root"},
                {"kind": "python_module", "path": str(self.base / "site/pkg.py"), "role": "dependency"},
            ],
            "errors": [],
        }
        self.assertEqual("established", source_binding.assess(result, self.tree, self.workspace)["status"])

    def test_live_workspace_and_other_external_sources_are_unverified(self):
        for path in (self.workspace / "app.py", self.base / "other/app.py"):
            with self.subTest(path=path):
                result = {"observations": [{"kind": "python_module", "path": str(path), "role": "source"}]}
                assessment = source_binding.assess(result, self.tree, self.workspace)
                self.assertEqual("unverified", assessment["status"])
                self.assertTrue("outside" in assessment["reason"] or "live workspace" in assessment["reason"])

    def test_missing_malformed_or_unresolved_observations_fail_closed(self):
        for result in (
            {"observations": []},
            {"observations": [{"kind": "python_test", "path": None, "role": "test"}]},
            {"observations": ["not a row"]},
            {"observations": [{"kind": "python_module", "path": str(self.tree / "app.py")}], "errors": ["failed"]},
            {"observations": [{"kind": [], "path": str(self.tree / "app.py"), "role": "source"}]},
            {"observations": [{"kind": "python_module", "path": str(self.tree / "app.py"), "role": []}]},
        ):
            with self.subTest(result=result):
                self.assertEqual("unverified", source_binding.assess(result, self.tree, self.workspace)["status"])

    def test_expected_absent_modules_are_neutral_but_required_origins_are_not(self):
        for kind in ("python_module", "node_import", "node_self"):
            with self.subTest(kind=kind):
                result = {"observations": [{"kind": kind, "path": None, "role": "source"}], "errors": []}
                self.assertEqual("established", source_binding.assess(result, self.tree, self.workspace)["status"])

    def test_present_malformed_bindings_fail_closed(self):
        for binding in (None, "established", [], {}, {"status": "established"}):
            with self.subTest(binding=binding):
                receipt = {"source_binding": binding}
                self.assertFalse(source_binding.established(receipt))
                self.assertTrue(source_binding.reasons({"candidate": receipt}))

    def test_required_binding_cannot_be_deleted_from_a_new_receipt(self):
        receipt = {"source_binding_required": True}
        self.assertFalse(source_binding.established(receipt))
        self.assertIn("binding is missing", source_binding.reasons({"candidate": receipt})[0])

    def test_attachment_rejects_malformed_trees_and_does_not_reuse_an_owner(self):
        def receipt(name):
            return {
                "execution_root": str(self.tree),
                "command": "python -m unittest -v",
                "output": str(self.tree / name),
                "output_sha256": name,
            }

        with self.assertRaises(ValueError):
            source_binding.attach(receipt("one.log"), {"tree": [], "status": "established"})
        binding = {"tree": str(self.tree), "status": "established"}
        first, second = receipt("one.log"), receipt("two.log")
        source_binding.attach(first, binding)
        source_binding.attach(second, binding)
        self.assertNotIn("owner", binding)
        self.assertEqual("one.log", first["source_binding"]["owner"]["output_sha256"])
        self.assertEqual("two.log", second["source_binding"]["owner"]["output_sha256"])
        self.assertNotEqual(first["source_binding"]["owner"], second["source_binding"]["owner"])

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_node_self_package_and_literal_absolute_import_are_observed(self):
        self.write(
            self.tree,
            {
                "package.json": json.dumps({"name": "@fixture/app", "exports": "./index.js"}),
                "index.js": "module.exports = 1;\n",
                "app.test.js": "const test = require('node:test');\nconst app = require('@fixture/app');\n",
            },
        )
        clean = self.probe("node", "node --test app.test.js")
        self.assertEqual("established", clean["status"], clean)
        self.assertTrue(any(row["kind"] == "node_self" for row in clean["observations"]))
        self.write(
            self.tree,
            {"app.test.js": f"require({json.dumps(str(self.workspace / 'index.js'))});\n"},
        )
        leaked = self.probe("node", "node --test app.test.js")
        self.assertEqual("unverified", leaked["status"])
        self.assertIn("live workspace", leaked["reason"])

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_node_default_discovery_observes_nested_test_files(self):
        self.write(
            self.tree,
            {
                "nested/test/helper.js": f"require({json.dumps(str(self.workspace / 'index.js'))});\n",
            },
        )
        result = self.probe("node", "node --test")
        self.assertEqual("unverified", result["status"], result)
        self.assertTrue(any(row["kind"] == "node_test" for row in result["observations"]))
        self.assertIn("live workspace", result["reason"])

    @unittest.skipUnless(shutil.which("go"), "Go is not installed")
    def test_go_module_and_package_directories_must_belong_to_the_copy(self):
        self.write(
            self.tree,
            {
                "go.mod": "module example.test/fixture\n\ngo 1.25\n",
                "app.go": "package fixture\n",
                "app_test.go": "package fixture\n",
            },
        )
        clean = self.probe("go", "go test ./...")
        self.assertEqual("established", clean["status"], clean)
        self.assertEqual({"go_root", "go_module", "go_package"}, {row["kind"] for row in clean["observations"]})
        (self.tree / "go.mod").unlink()
        first = self.probe("go", "go test ./...")
        self.assertEqual("established", first["status"], first)
        self.write(self.workspace, {"go.mod": "module example.test/live\n\ngo 1.25\n", "app.go": "package live\n"})
        self.write(self.tree, {"app_test.go": f"package fixture\n\nconst live = {str(self.workspace)!r}\n"})
        leaked = self.probe("go", "go test ./...")
        self.assertEqual("unverified", leaked["status"], leaked)

    @unittest.skipUnless(shutil.which("go"), "Go is not installed")
    def test_safe_configured_go_flags_are_observed(self):
        self.write(
            self.tree,
            {
                "go.mod": "module example.test/fixture\n\ngo 1.25\n",
                "app.go": "package fixture\n",
                "app_test.go": "package fixture\n",
            },
        )
        safe = self.probe("go", "go test ./...", env={"GOFLAGS": "-buildvcs=false -p=4"})
        self.assertEqual("established", safe["status"], safe)
        unsafe = self.probe("go", "go test ./...", env={"GOFLAGS": "-overlay=/tmp/overlay"})
        self.assertEqual("unverified", unsafe["status"], unsafe)
        self.assertIn("GOFLAGS", unsafe["reason"])

    @unittest.skipUnless(shutil.which("go"), "Go is not installed")
    def test_go_local_replacement_outside_the_copy_is_unverified(self):
        replacement = self.workspace / "replacement"
        self.write(
            replacement,
            {
                "go.mod": "module example.test/replacement\n\ngo 1.25\n",
                "value.go": "package replacement\n\nconst Value = 1\n",
            },
        )
        self.write(
            self.tree,
            {
                "go.mod": (
                    "module example.test/fixture\n\ngo 1.25\n\n"
                    f"require example.test/replacement v0.0.0\n\nreplace example.test/replacement => {replacement}\n"
                ),
                "app.go": 'package fixture\n\nimport "example.test/replacement"\n\nconst Value = replacement.Value\n',
                "app_test.go": "package fixture\n",
            },
        )
        result = self.probe("go", "go test ./...")
        self.assertEqual("unverified", result["status"], result)
        self.assertTrue(
            any(row["kind"] == "go_package" and row["path"] == str(replacement) for row in result["observations"])
        )

    def test_python_test_and_import_origins_are_observed(self):
        self.write(
            self.tree,
            {
                "app/__init__.py": "VALUE = 1\n",
                "tests/test_app.py": "from app import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
            },
        )
        clean = self.probe("pytest", f"{sys.executable} -m pytest -q tests/test_app.py")
        self.assertEqual("established", clean["status"], clean)
        self.assertEqual(
            {"python_root", "python_test", "python_module", "python_source", "pytest_rootdir"},
            {row["kind"] for row in clean["observations"]},
        )

    def test_file_relative_python_bootstrap_is_observed(self):
        self.write(
            self.tree,
            {
                "src/app.py": "VALUE = 1\n",
                "tests/test_app.py": (
                    "import sys\nfrom pathlib import Path\n"
                    "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
                    "from app import VALUE\n"
                ),
            },
        )
        result = self.probe("unittest", f"{sys.executable} -m unittest tests/test_app.py -v")
        self.assertEqual("established", result["status"], result)
        self.assertTrue(
            any(
                row.get("name") == "app" and row["path"] == str(self.tree / "src/app.py")
                for row in result["observations"]
            )
        )

    def test_python_os_path_and_pathlib_bootstraps_are_observed(self):
        self.write(
            self.tree,
            {
                "src/app.py": "VALUE = 1\n",
                "tests/test_os.py": (
                    "import os\nimport sys\nimport unittest\n"
                    "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
                    "from src.app import VALUE\n"
                ),
                "tests/test_pathlib.py": (
                    "import pathlib\nimport sys\nimport unittest\n"
                    "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
                    "from app import VALUE\n"
                ),
            },
        )
        for module in ("tests.test_os", "tests.test_pathlib"):
            with self.subTest(module=module):
                result = self.probe("unittest", f"{sys.executable} -m unittest {module} -v")
                self.assertEqual("established", result["status"], result)

    def test_python_bootstrap_order_and_shadowing_are_held(self):
        for source in (
            "from app import VALUE\nimport sys\nsys.path.insert(0, 'src')\n",
            "import sys\nPath=lambda value: value\nsys.path.insert(0, str(Path(__file__).parents[1] / 'src'))\n",
            "import sys\n__file__='elsewhere/test_app.py'\nsys.path.insert(0, str(Path(__file__).parents[1] / 'src'))\n",
            "import sys\nif True:\n    sys.path.insert(0, 'src')\n",
        ):
            with self.subTest(source=source):
                self.write(
                    self.tree,
                    {"src/app.py": "VALUE = 1\n", "tests/test_app.py": source},
                )
                result = self.probe("unittest", f"{sys.executable} -m unittest tests/test_app.py -v")
                self.assertEqual("unverified", result["status"], result)
                self.assertIn("import-path mutation", result["reason"])

    def test_python_bootstrap_into_the_live_workspace_is_unverified(self):
        self.write(self.workspace, {"leaked.py": "VALUE = 1\n"})
        self.write(
            self.tree,
            {
                "tests/test_app.py": (
                    f"import sys\nsys.path.insert(0, {str(self.workspace)!r})\nfrom leaked import VALUE\n"
                ),
            },
        )
        result = self.probe("unittest", f"{sys.executable} -m unittest tests/test_app.py -v")
        self.assertEqual("unverified", result["status"], result)
        self.assertTrue(
            any(
                row.get("name") == "leaked" and row["path"] == str(self.workspace / "leaked.py")
                for row in result["observations"]
            )
        )

    def test_python_stdlib_name_can_be_shadowed_from_the_live_workspace(self):
        self.write(self.workspace, {"fractions.py": "VALUE = 1\n"})
        self.write(self.tree, {"test_shadow.py": "import fractions\n"})
        result = self.probe(
            "unittest",
            f"{sys.executable} -m unittest test_shadow -v",
            env={"PYTHONPATH": str(self.workspace)},
        )
        self.assertEqual("unverified", result["status"], result)
        self.assertTrue(
            any(
                row.get("name") == "fractions" and row["path"] == str(self.workspace / "fractions.py")
                for row in result["observations"]
            )
        )

    def test_empty_runner_roots_are_positive_absence_observations(self):
        for framework, command in (
            ("unittest", f"{sys.executable} -m unittest discover -v"),
            ("node", "node --test"),
        ):
            if framework == "node" and not shutil.which("node"):
                continue
            with self.subTest(framework=framework):
                result = self.probe(framework, command)
                self.assertEqual("established", result["status"], result)
                root_kind = "python_root" if framework == "unittest" else "node_root"
                self.assertIn(root_kind, {row["kind"] for row in result["observations"]})

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_missing_node_test_selector_is_neutral_on_an_empty_base(self):
        result = self.probe("node", "node --test new.test.cjs")
        self.assertEqual("established", result["status"], result)
        self.assertIn("node_root", {row["kind"] for row in result["observations"]})
        self.assertFalse(any(row["kind"] == "node_test" for row in result["observations"]))

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_node_conditional_and_node_modules_resolution_are_held(self):
        self.write(
            self.tree,
            {
                "app.test.js": "require('node:test');\n",
                "node_modules/pkg/index.js": "require('node:test');\n",
            },
        )
        for command in ("node --conditions=custom --test app.test.js", "node --test node_modules/pkg/index.js"):
            with self.subTest(command=command):
                result = self.probe("node", command)
                self.assertEqual("unverified", result["status"], result)
                self.assertTrue(result["reason"])

    def test_pytest_configuration_origin_and_pythonpath_are_observed(self):
        self.write(
            self.tree,
            {
                "pytest.ini": "[pytest]\npythonpath = src\n",
                "src/app.py": "VALUE = 1\n",
                "tests/test_app.py": "from app import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
            },
        )
        result = self.probe("pytest", f"{sys.executable} -m pytest -q tests/test_app.py")
        self.assertEqual("established", result["status"], result)
        self.assertIn("pytest_pythonpath", {row["kind"] for row in result["observations"]})

    def test_pytest_addopts_are_part_of_source_observation(self):
        self.write(
            self.tree,
            {
                "pytest.ini": "[pytest]\naddopts = -p source_plugin\n",
                "test_app.py": "def test_value():\n    assert True\n",
            },
        )
        result = self.probe("pytest", f"{sys.executable} -m pytest -q -p no:cacheprovider test_app.py")
        self.assertEqual("unverified", result["status"], result)
        self.assertIn("source_plugin", result["reason"])
        self.assertNotIn("no:cacheprovider", result["reason"])

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_npm_build_step_cannot_hide_unobserved_source(self):
        self.write(self.workspace, {"app.js": "module.exports = 1;\n"})
        self.write(
            self.tree,
            {
                "package.json": json.dumps({"scripts": {"test": "node build.js && node --test test/app.test.js"}}),
                "build.js": f"require('node:fs').copyFileSync({str(self.workspace / 'app.js')!r}, 'app.js');\n",
                "test/app.test.js": "require('../app.js');\n",
            },
        )
        framework = test_framework.Framework("node", "npm test --silent")
        receipt = verify.run_suite(
            framework,
            "npm test --silent",
            self.tree,
            self.base / "evidence",
            "suite-on-candidate",
            timeout=60,
            source_workspace=self.workspace,
        )
        self.assertEqual(0, receipt["exit_code"], receipt["tail"])
        self.assertEqual("unverified", receipt["source_binding"]["status"], receipt["source_binding"])
        self.assertIn("literal test invocation", receipt["source_binding"]["reason"])

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_npm_package_change_after_classification_is_unverified(self):
        self.write(
            self.tree,
            {
                "package.json": json.dumps({"scripts": {"test": "node --test test/app.test.js"}}),
                "test/app.test.js": (
                    "const fs = require('node:fs');\n"
                    "fs.writeFileSync('package.json', JSON.stringify({scripts: {test: 'node build.js'}}));\n"
                    "require('node:test');\n"
                ),
            },
        )
        receipt = verify.run_suite(
            test_framework.Framework("node", "npm test --silent"),
            "npm test --silent",
            self.tree,
            self.base / "evidence",
            "suite-on-candidate",
            timeout=60,
            source_workspace=self.workspace,
        )
        self.assertEqual(0, receipt["exit_code"], receipt["tail"])
        self.assertEqual("unverified", receipt["source_binding"]["status"], receipt["source_binding"])
        self.assertIn("changed after the test command was classified", receipt["source_binding"]["reason"])

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_node_script_cannot_masquerade_as_the_test_runner(self):
        self.write(self.tree, {"run.js": "require('node:test');\n"})
        result = self.probe("node", "node run.js --test")
        self.assertEqual("unverified", result["status"], result)
        self.assertIn("executes a script", result["reason"])

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_node_deterministic_preload_is_observed_but_code_preload_is_held(self):
        self.write(
            self.tree,
            {
                "setup.cjs": "globalThis.proofSetup=17;\n",
                "app.test.cjs": "require('node:test')('works',()=>{});\n",
            },
        )
        clean = self.probe("node", "node --require ./setup.cjs --test app.test.cjs")
        self.assertEqual("established", clean["status"], clean)
        self.assertTrue(any(row["path"] == str(self.tree / "setup.cjs") for row in clean["observations"]))
        for preload in (
            "require('./app.test.cjs');\n",
            "globalThis.process=17;\n",
            "globalThis.proofSetup=Math.random();\n",
            f"globalThis.proofSetup={str(self.workspace)!r};\n",
        ):
            with self.subTest(preload=preload):
                (self.tree / "setup.cjs").write_text(preload)
                result = self.probe("node", "node -r ./setup.cjs --test app.test.cjs")
                self.assertEqual("unverified", result["status"], result)

    def test_python_bootstrap_context_reaches_imported_modules(self):
        self.write(self.workspace, {"leaked.py": "VALUE = 1\n"})
        self.write(
            self.tree,
            {
                "src/app.py": "import helper\n",
                "src/helper.py": "import leaked\n",
                "tests/test_app.py": ("import sys\nsys.path.insert(0, 'src')\nimport app\n"),
            },
        )
        result = self.probe(
            "unittest",
            f"{sys.executable} -m unittest tests/test_app.py -v",
            env={"PYTHONPATH": str(self.workspace)},
        )
        self.assertEqual("unverified", result["status"], result)
        self.assertTrue(
            any(
                row.get("name") == "leaked" and row["path"] == str(self.workspace / "leaked.py")
                for row in result["observations"]
            )
        )

    def test_python_cross_module_path_mutation_is_held(self):
        self.write(
            self.tree,
            {
                "generated/value.py": "VALUE = 1\n",
                "app.py": "import sys\nsys.path.insert(0, 'generated')\n",
                "test_app.py": "import app\nimport value\n",
            },
        )
        result = self.probe("unittest", f"{sys.executable} -m unittest test_app -v")
        self.assertEqual("unverified", result["status"], result)
        self.assertIn("cross-module", result["reason"])

    def collector_command(self):
        collector = Path(verify.__file__).with_name("autocode_component_tests.py")
        return f"{sys.executable} {collector} -v --root . --repos alpha beta"

    def collector_member(self, imports="from value import VALUE\n"):
        return (
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n" + imports
        )

    def test_component_collector_observes_each_isolated_member_context(self):
        self.write(
            self.tree,
            {
                "repos/__init__.py": "# load_tests in a comment is not a hook\nVALUE = 'load_tests'\n",
                "repos/alpha/value.py": "VALUE = 'alpha'\n",
                "repos/alpha/tests/test_value.py": self.collector_member(),
                "repos/beta/value.py": "VALUE = 'beta'\n",
                "repos/beta/tests/test_value.py": self.collector_member(),
            },
        )
        result = self.probe("unittest", self.collector_command())
        self.assertEqual("established", result["status"], result)
        paths = {row.get("path") for row in result["observations"]}
        self.assertIn(str(self.tree / "repos/alpha/value.py"), paths)
        self.assertIn(str(self.tree / "repos/beta/value.py"), paths)

    def test_component_collector_holds_a_live_import_in_one_member(self):
        self.write(self.workspace, {"leaked.py": "VALUE = 'live'\n"})
        self.write(
            self.tree,
            {
                "repos/alpha/value.py": "VALUE = 'alpha'\n",
                "repos/alpha/tests/test_value.py": self.collector_member(),
                "repos/beta/tests/test_value.py": self.collector_member("import leaked\n"),
            },
        )
        result = self.probe("unittest", self.collector_command(), env={"PYTHONPATH": str(self.workspace)})
        self.assertEqual("unverified", result["status"], result)
        self.assertIn("live workspace", result["reason"])
        self.assertTrue(
            any(
                row.get("name") == "leaked" and row.get("path") == str(self.workspace / "leaked.py")
                for row in result["observations"]
            )
        )

    def test_component_collector_allows_identical_bootstraps_in_one_process(self):
        self.write(
            self.tree,
            {
                "repos/alpha/value.py": "VALUE = 'alpha'\n",
                "repos/alpha/tests/test_one.py": self.collector_member(),
                "repos/alpha/tests/test_two.py": self.collector_member(),
                "repos/beta/value.py": "VALUE = 'beta'\n",
                "repos/beta/tests/test_value.py": self.collector_member(),
            },
        )
        result = self.probe("unittest", self.collector_command())
        self.assertEqual("established", result["status"], result)

    def test_component_collector_still_holds_different_mutations_in_one_process(self):
        self.write(
            self.tree,
            {
                "repos/alpha/value.py": "VALUE = 'alpha'\n",
                "repos/alpha/tests/test_one.py": self.collector_member(),
                "repos/alpha/tests/test_two.py": (
                    "import sys\nfrom pathlib import Path\n"
                    "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
                ),
                "repos/beta/value.py": "VALUE = 'beta'\n",
                "repos/beta/tests/test_value.py": self.collector_member(),
            },
        )
        result = self.probe("unittest", self.collector_command())
        self.assertEqual("unverified", result["status"], result)
        self.assertIn("cross-module", result["reason"])

    def test_python_indirect_import_machinery_mutation_is_held(self):
        for source in (
            "from sys import path\np = path\np.insert(0, 'src')\n",
            "import sys\ngetattr(sys, 'path').insert(0, 'src')\n",
            "import sys\ns = sys\ns.path.insert(0, 'src')\n",
            "import sys\nsys.meta_path.append(object())\n",
            "import sys\nsys.path_hooks.append(object())\n",
            "import builtins\nbuiltins.__import__ = lambda *args: None\n",
        ):
            with self.subTest(source=source):
                self.write(self.tree, {"test_app.py": source})
                result = self.probe("unittest", f"{sys.executable} -m unittest test_app -v")
                self.assertEqual("unverified", result["status"], result)
                self.assertIn("import", result["reason"])

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_node_esm_import_conditions_are_observed(self):
        self.write(
            self.tree,
            {
                "package.json": json.dumps(
                    {"name": "@fixture/app", "exports": {"import": "./esm.js", "require": "./cjs.cjs"}}
                ),
                "esm.js": f"import {json.dumps(str(self.workspace / 'leaked.js'))};\n",
                "cjs.cjs": "module.exports = 1;\n",
                "app.test.mjs": "import '@fixture/app';\n",
            },
        )
        result = self.probe("node", "node --test app.test.mjs")
        self.assertEqual("unverified", result["status"], result)
        self.assertIn("live workspace", result["reason"])

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_node_dynamic_resolution_mutation_is_held(self):
        for source in (
            "const Module=require('node:module'); Module._resolveFilename = () => {};\n",
            "require.extensions['.js'] = () => {};\n",
            "const load = require; load('node:fs');\n",
            "eval('require(\"node:fs\")');\n",
            "process.env.NODE_PATH = '/tmp';\n",
        ):
            with self.subTest(source=source):
                self.write(self.tree, {"app.test.cjs": source})
                result = self.probe("node", "node --test app.test.cjs")
                self.assertEqual("unverified", result["status"], result)
                self.assertIn("dynamic source resolution", result["reason"])

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_node_invalid_short_require_equals_is_held(self):
        self.write(self.tree, {"setup.cjs": "globalThis.ready=true;\n", "app.test.cjs": ""})
        result = self.probe("node", "node -r=./setup.cjs --test app.test.cjs")
        self.assertEqual("unverified", result["status"], result)
        self.assertIn("short require equals", result["reason"])

    def test_pth_fallthrough_to_live_source_is_unverified(self):
        self.write(self.workspace, {"leaked/__init__.py": "VALUE = 2\n"})
        self.write(self.tree, {"tests/test_leak.py": "from leaked import VALUE\n"})
        site = self.base / "site"
        site.mkdir()
        (site / "sitecustomize.py").write_text(f"import site\nsite.addsitedir({str(self.base / 'pth')!r})\n")
        pth = self.base / "pth"
        pth.mkdir()
        (pth / "editable.pth").write_text(str(self.workspace) + "\n")
        leaked = self.probe(
            "pytest",
            f"{sys.executable} -m pytest -q tests/test_leak.py",
            env={"PYTHONPATH": str(site)},
        )
        self.assertEqual("unverified", leaked["status"])
        self.assertIn("live workspace", leaked["reason"])
        self.assertTrue(any(row.get("name") == "leaked" for row in leaked["observations"]))

    def test_exact_distro_pth_hooks_are_observed_only_inside_dependencies(self):
        dependency = self.base / "dist-packages"
        dependency.mkdir()
        zope = dependency / "zope.interface-6.1-nspkg.pth"
        zope.write_text(source_probe.ZOPE_NSPKG_PTH + "\n")
        namespace = SimpleNamespace(__path__=[str(dependency / "zope")])
        with mock.patch.dict(source_probe.sys.modules, {"zope": namespace}):
            self.assertTrue(source_probe._distro_pth_observed(zope, zope.read_text(), [dependency]))
            namespace.__path__ = [str(self.workspace / "zope")]
            self.assertFalse(source_probe._distro_pth_observed(zope, zope.read_text(), [dependency]))
        self.assertFalse(
            source_probe._distro_pth_observed(zope, source_probe.ZOPE_NSPKG_PTH + "\nimport sys\n", [dependency])
        )

        distutils = dependency / "distutils-precedence.pth"
        distutils.write_text(source_probe.DISTUTILS_PRECEDENCE_PTH)
        hack = dependency / "_distutils_hack.py"
        hack.write_text("")
        with mock.patch.dict(source_probe.sys.modules, {"_distutils_hack": SimpleNamespace(__file__=str(hack))}):
            self.assertTrue(source_probe._distro_pth_observed(distutils, distutils.read_text(), [dependency]))
        with mock.patch.dict(
            source_probe.sys.modules, {"_distutils_hack": SimpleNamespace(__file__=str(self.workspace / "hack.py"))}
        ):
            self.assertFalse(source_probe._distro_pth_observed(distutils, distutils.read_text(), [dependency]))

    def test_env_prefix_preserves_python_probe_environment(self):
        self.write(
            self.tree,
            {
                "app.py": "VALUE = 1\n",
                "tests/test_app.py": "from app import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
            },
        )
        command = (
            f"env PYTHONPATH={self.tree} PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "
            f"{sys.executable} -m pytest -q tests/test_app.py"
        )
        result = self.probe("pytest", command)
        self.assertEqual("established", result["status"], result)

    def test_python_aliased_import_machinery_is_held(self):
        sources = (
            "from importlib import import_module as load\nload('app')\n",
            "from importlib.util import find_spec as fs\nfs('app')\n",
            "from builtins import __import__ as imp\nimp('app')\n",
            "import sys\nfrom builtins import getattr as ga\nga(sys, 'path')\n",
            "import builtins\nbuiltins.__import__ = lambda *args: None\n",
            "import sys\nsys.modules['app'] = None\n",
            "from sys import modules\nmodules['app'] = None\n",
            "import sys\nga = getattr\nga(sys, 'modules')\n",
        )
        for source in sources:
            with self.subTest(source=source):
                self.write(self.tree, {"app.py": "VALUE = 1\n", "tests/test_app.py": source})
                result = self.probe("unittest", f"{sys.executable} -m unittest tests/test_app.py -v")
                self.assertEqual("unverified", result["status"], result)
                self.assertIn("not observed", result["reason"])

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_node_aliased_or_optional_resolution_is_held(self):
        sources = (
            "const load = require\nload('node:fs');\n",
            "const load = require /* comment */;\nload('node:fs');\n",
            "const specifier = 'node:fs';\nrequire?.(specifier);\n",
            "require.resolve('node:fs');\n",
            "const env = process.env;\nenv.NODE_PATH = '/tmp';\n",
            "process.env['NODE_PATH'] = '/tmp';\n",
        )
        for source in sources:
            with self.subTest(source=source):
                self.write(self.tree, {"app.test.js": source})
                result = self.probe("node", "node --test app.test.js")
                self.assertEqual("unverified", result["status"], result)
                self.assertIn("dynamic source resolution", result["reason"])

    @unittest.skipUnless(shutil.which("go"), "Go is not installed")
    def test_go_cover_boolean_keeps_all_package_selectors(self):
        self.write(
            self.tree,
            {
                "go.mod": "module example.test/cover\n\ngo 1.25\n",
                "a/a.go": "package a\n",
                "a/a_test.go": "package a\n",
                "b/b.go": "package b\n",
                "b/b_test.go": "package b\n",
            },
        )
        result = self.probe("go", "go test -cover ./...")
        self.assertEqual("established", result["status"], result)
        self.assertEqual(
            {str(self.tree / "a"), str(self.tree / "b")},
            {row["path"] for row in result["observations"] if row["kind"] == "go_package"},
        )

    @unittest.skipUnless(shutil.which("go"), "Go is not installed")
    def test_go_coverpkg_adds_unimported_packages_to_observation(self):
        self.write(
            self.tree,
            {
                "go.mod": "module example.test/cover\n\ngo 1.25\n",
                "app.go": "package fixture\n",
                "app_test.go": "package fixture\n",
                "instrumented/value.go": "package instrumented\n\nconst Value = 1\n",
            },
        )
        result = self.probe("go", "go test -coverpkg ./instrumented ./...")
        self.assertEqual("established", result["status"], result)
        self.assertTrue(
            any(
                row["kind"] == "go_package" and row["path"] == str(self.tree / "instrumented")
                for row in result["observations"]
            ),
            result,
        )

    def test_project_cannot_replace_the_retained_observer_sources(self):
        original = Path(source_binding.__file__).parent
        fake = self.base / "original-runtime"
        fake.mkdir()
        for name in source_binding.PROBE_FILES:
            shutil.copy2(original / name, fake / name)

        def retain(evidence_dir, label):
            directory = Path(evidence_dir) / f"{label}.source-runtime"
            directory.mkdir(parents=True, exist_ok=True)
            files = {}
            for name in source_binding.PROBE_FILES:
                data = (fake / name).read_bytes()
                target = directory / name
                target.write_bytes(data)
                files[name] = {"path": str(target), "sha256": verify.util.file_hash(target)}
            return files

        self.write(
            self.tree,
            {
                "test_app.py": (
                    "import os\nimport unittest\nfrom pathlib import Path\n\n"
                    "class App(unittest.TestCase):\n"
                    "    def test_value(self):\n"
                    "        runtime = Path(os.environ['ORIGINAL_RUNTIME'])\n"
                    "        for name in ('autocode_source_probe.py', 'autocode_python_tests.py'):\n"
                    "            (runtime / name).write_text('raise RuntimeError(\"replaced\")\\n')\n"
                    "        self.assertEqual(1, 1)\n"
                )
            },
        )
        command = f"env ORIGINAL_RUNTIME={fake} {sys.executable} -m unittest -v test_app"
        framework = test_framework.Framework("unittest", command, python=sys.executable)
        evidence = self.base / "evidence"
        with mock.patch.object(source_binding, "retain_probe", side_effect=retain):
            receipt = verify.run_suite(
                framework,
                command,
                self.tree,
                evidence,
                "regression-on-candidate",
                timeout=60,
                source_workspace=self.workspace,
            )
        self.assertEqual(0, receipt["exit_code"], receipt["tail"])
        self.assertEqual("established", receipt["source_binding"]["status"], receipt["source_binding"])
        self.assertTrue(intact(receipt, root=evidence))
        self.assertIn("replaced", (fake / "autocode_source_probe.py").read_text())

    def test_project_cannot_replace_the_observer_before_it_runs(self):
        self.write(
            self.tree,
            {
                "test_app.py": (
                    "import os\nimport unittest\nfrom pathlib import Path\n\n"
                    "class App(unittest.TestCase):\n"
                    "    def test_value(self):\n"
                    "        runtime = Path(os.environ['EVIDENCE_DIR']) / 'regression-on-candidate.source-runtime'\n"
                    "        for name in ('autocode_source_probe.py', 'autocode_python_tests.py'):\n"
                    "            (runtime / name).write_text('raise RuntimeError(\"replaced\")\\n')\n"
                    "        self.assertEqual(1, 1)\n"
                )
            },
        )
        evidence = self.base / "evidence"
        command = f"env EVIDENCE_DIR={evidence} {sys.executable} -m unittest -v test_app"
        framework = test_framework.Framework("unittest", command, python=sys.executable)
        receipt = verify.run_suite(
            framework,
            command,
            self.tree,
            evidence,
            "regression-on-candidate",
            timeout=60,
            source_workspace=self.workspace,
        )
        self.assertEqual(0, receipt["exit_code"], receipt["tail"])
        self.assertEqual("unverified", receipt["source_binding"]["status"])
        self.assertIn("changed before observation", receipt["source_binding"]["reason"])
        self.assertFalse(intact(receipt, root=evidence))

    def test_direct_live_workspace_reference_is_unverified(self):
        self.write(
            self.tree,
            {"tests/test_app.py": f"DATA = {str(self.workspace / 'data.json')!r}\n"},
        )
        result = self.probe("pytest", f"{sys.executable} -m pytest -q tests/test_app.py")
        self.assertEqual("unverified", result["status"])
        self.assertIn("names the live workspace directly", result["reason"])

    def test_owned_probe_and_capture_are_authenticated_together(self):
        self.write(
            self.tree,
            {
                "test_app.py": (
                    "import unittest\n\nclass App(unittest.TestCase):\n"
                    "    def test_value(self):\n        self.assertEqual(1, 1)\n"
                )
            },
        )
        command = f"{sys.executable} -m unittest -v test_app"
        framework = test_framework.Framework("unittest", command, python=sys.executable)
        evidence = self.base / "evidence"
        receipt = verify.run_suite(
            framework,
            command,
            self.tree,
            evidence,
            "regression-on-candidate",
            timeout=60,
            source_workspace=self.workspace,
        )
        self.assertEqual(0, receipt["exit_code"], receipt["tail"])
        self.assertEqual("established", receipt["source_binding"]["status"])
        self.assertTrue(intact(receipt, root=evidence))
        self.assertTrue(proof_intact({"candidate": receipt}, root=evidence))
        capture = Path(receipt["source_binding"]["capture"]["path"])
        original = capture.read_bytes()
        capture.write_bytes(original + b"\n")
        self.assertFalse(intact(receipt, root=evidence))
        capture.write_bytes(original)
        request = Path(receipt["source_binding"]["request"]["path"])
        original_request = request.read_bytes()
        request.write_text("{}\n")
        self.assertFalse(proof_intact({"candidate": receipt}, root=evidence))
        request.write_bytes(original_request)
        capture.write_bytes(original)
        self.assertEqual([], verify._incomplete_receipts({"candidate": receipt}))
        swapped = json.loads(json.dumps(receipt))
        swapped["command"] = "another command"
        swapped["output"] = str(self.base / "another.log")
        self.assertFalse(source_binding.established(swapped))
        self.assertTrue(source_binding.reasons({"candidate": swapped}))
        capture.write_bytes(original + b"\n")
        self.assertTrue(
            any("source-resolution" in reason for reason in verify._incomplete_receipts({"candidate": receipt}))
        )
        capture.write_bytes(original)
        request = Path(receipt["source_binding"]["request"]["path"])
        changed = json.loads(request.read_text())
        changed["workspace"] = str(self.base / "other-live")
        request.write_text(json.dumps(changed))
        receipt["source_binding"]["request"]["sha256"] = verify.util.file_hash(request)
        self.assertFalse(proof_intact({"candidate": receipt}, root=evidence))

    def test_legacy_receipt_without_an_observation_remains_neutral(self):
        receipt = {"exit_code": 0, "timed_out": False, "output": str(self.base / "legacy.log")}
        Path(receipt["output"]).write_text("")
        receipt["output_sha256"] = verify.util.file_hash(receipt["output"])
        self.assertTrue(intact(receipt, root=self.base))
        self.assertEqual([], source_binding.reasons({"candidate": receipt}))

    def test_unsupported_proof_frameworks_stay_outside_the_phase_one_detector(self):
        def unexpected_execute(*args, **kwargs):
            raise AssertionError("an unsupported runner must not be reported as observed")

        for framework in (None, test_framework.Framework("vitest", "vitest run")):
            with self.subTest(framework=framework):
                binding = source_binding.observe(
                    framework,
                    "vitest run",
                    self.tree,
                    self.tree,
                    self.base / "evidence",
                    "suite-on-candidate",
                    workspace=self.workspace,
                    execute=unexpected_execute,
                    timeout=60,
                )
                self.assertEqual("unverified", binding["status"])
                self.assertEqual([], source_binding.reasons({"candidate": {"source_binding": binding}}))

    def test_isolated_python_runtime_custody_is_phase_two_unless_it_enters_the_workspace(self):
        runtime = self.base / "runtime/bin/python"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("")
        framework = test_framework.Framework("unittest", f"{runtime} -m unittest discover -v")
        command = framework.suite
        (runtime.parent / "python._pth").write_text(f"{self.base / 'dependency'}\nimport site\n")
        isolated = source_binding.plan(
            framework, command, self.tree, self.tree, "suite-on-candidate", workspace=self.workspace
        )
        self.assertEqual("isolated-python", isolated["binding"]["framework"])
        self.assertEqual([], source_binding.reasons({"candidate": {"source_binding": isolated["binding"]}}))
        (runtime.parent / "python._pth").write_text(f"{self.workspace}\n")
        leaked = source_binding.plan(
            framework, command, self.tree, self.tree, "suite-on-candidate", workspace=self.workspace
        )
        self.assertEqual("unittest", leaked["binding"]["framework"])
        self.assertTrue(source_binding.reasons({"candidate": {"source_binding": leaked["binding"]}}))

    def test_absent_package_does_not_make_an_opaque_node_command_observable(self):
        framework = test_framework.Framework("node", "node --test")
        for command in (
            "npm --prefix . test",
            "cd web && npm test",
            "timeout npm test",
            "sh -c 'npm test'",
            "node --run test",
        ):
            with self.subTest(command=command):
                classification = source_binding.plan(
                    framework,
                    command,
                    self.tree,
                    self.tree,
                    "suite-on-candidate",
                    workspace=self.workspace,
                )
                self.assertEqual(command, classification["command"])
        plain = source_binding.plan(
            framework,
            "npm test --silent",
            self.tree,
            self.tree,
            "suite-on-base",
            workspace=self.workspace,
        )
        self.assertEqual("node --test", plain["command"])
        (self.tree / ".npmrc").write_text("script-shell=sh\n")
        configured = source_binding.plan(
            framework,
            "npm test --silent",
            self.tree,
            self.tree,
            "suite-on-base",
            workspace=self.workspace,
        )
        self.assertEqual("npm test --silent", configured["command"])

    def test_raw_proof_command_gets_an_explicit_unsupported_binding(self):
        receipt = verify.run_suite(
            None,
            "true",
            self.tree,
            self.base / "evidence",
            "scratch-command",
            timeout=60,
            source_workspace=self.workspace,
        )
        self.assertEqual(0, receipt["exit_code"], receipt["tail"])
        self.assertTrue(receipt["source_binding_required"])
        self.assertEqual("unverified", receipt["source_binding"]["status"])
        self.assertIn("unknown", receipt["source_binding"]["reason"])

    def test_selected_native_check_gets_an_explicit_unsupported_binding(self):
        output = self.base / "native.log"
        output.write_text("")
        native = object.__new__(verify.native_checks.NativeCheckExecution)
        native.result_path = lambda _tree, path: path
        native.instrument = lambda _framework, command, _xml, _tree: command
        native.retain_results = lambda _receipt: None
        completed = {
            "command": "true",
            "exit_code": 0,
            "timed_out": False,
            "output": str(output),
            "output_sha256": verify.util.file_hash(output),
        }

        def execute(command, _tree, path, *, timeout, native_execution=None):
            self.assertIs(native_execution, native)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("")
            return {**completed, "output": str(path), "output_sha256": verify.util.file_hash(path)}

        evidence = self.base / "native-evidence"
        framework = test_framework.Framework("unittest", "true")
        with (
            mock.patch.object(verify, "run_command", side_effect=execute),
            mock.patch.object(source_binding, "observe", side_effect=AssertionError("native source was probed")),
        ):
            receipt = verify.run_suite(
                framework,
                "true",
                self.tree,
                evidence,
                "suite-on-candidate",
                timeout=60,
                native_execution=native,
                source_workspace=self.workspace,
            )
        self.assertTrue(receipt["source_binding_required"])
        self.assertEqual("unverified", receipt["source_binding"]["status"])
        self.assertEqual(str(self.tree), receipt["source_binding"]["tree"])

    def test_merged_suites_keep_every_child_source_binding(self):
        clean = {
            "execution_root": str(self.tree),
            "command": "true",
            "output": str(self.base / "clean.log"),
            "output_sha256": "0" * 64,
            "exit_code": 0,
            "source_binding": {"version": 1, "status": "established", "probe": {"exit_code": 0, "timed_out": False}},
        }
        dirty = {
            "execution_root": str(self.tree),
            "command": "true",
            "output": str(self.base / "dirty.log"),
            "output_sha256": "0" * 64,
            "exit_code": 0,
            "results": {"passed": ["ok"], "failed": []},
            "source_binding": {"status": "unverified", "reason": "live workspace source"},
        }
        receipt = scratch_suites.merged([clean, dirty])
        self.assertEqual("unverified", receipt["source_binding"]["status"])
        self.assertIn("suite 2", receipt["source_binding"]["reason"])
        self.assertTrue(source_binding.reasons({"candidate": receipt}))
        self.assertEqual(check_outcome.UNDETERMINED, check_outcome.classify(receipt)["outcome"])
        self.assertEqual(check_outcome.SOURCE_UNVERIFIED, check_outcome.classify(receipt)["reason"])

    def test_dynamic_python_imports_are_not_silently_established(self):
        self.write(
            self.tree,
            {"test_app.py": "import importlib\n\ndef test_value():\n    importlib.import_module('app')\n"},
        )
        result = self.probe("unittest", f"{sys.executable} -m unittest test_app -v")
        self.assertEqual("unverified", result["status"], result)
        self.assertIn("dynamic source resolution", result["reason"])

    def test_runpy_dynamic_loading_is_not_silently_established(self):
        self.write(
            self.tree,
            {
                "app.py": "VALUE = 1\n",
                "test_app.py": "import runpy\n\ndef test_value():\n    runpy.run_path('app.py')\n",
            },
        )
        result = self.probe("unittest", f"{sys.executable} -m unittest test_app -v")
        self.assertEqual("unverified", result["status"], result)
        self.assertIn("dynamic source resolution", result["reason"])

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_computed_node_imports_are_not_silently_established(self):
        self.write(self.tree, {"app.test.js": "const name = './app.js';\nrequire(name);\n"})
        result = self.probe("node", "node --test app.test.js")
        self.assertEqual("unverified", result["status"], result)
        self.assertIn("dynamic source resolution", result["reason"])

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
    def test_npm_lifecycle_hook_cannot_hide_unobserved_source(self):
        self.write(self.workspace, {"app.js": "module.exports = 1;\n"})
        self.write(
            self.tree,
            {
                "package.json": json.dumps(
                    {"scripts": {"pretest": "node setup.js", "test": "node --test test/app.test.js"}}
                ),
                "setup.js": f"require('node:fs').copyFileSync({str(self.workspace / 'app.js')!r}, 'app.js');\n",
                "test/app.test.js": "require('../app.js');\n",
            },
        )
        receipt = verify.run_suite(
            test_framework.Framework("node", "npm test --silent"),
            "npm test --silent",
            self.tree,
            self.base / "evidence",
            "suite-on-candidate",
            timeout=60,
            source_workspace=self.workspace,
        )
        self.assertEqual(0, receipt["exit_code"], receipt["tail"])
        self.assertEqual("unverified", receipt["source_binding"]["status"], receipt["source_binding"])
        self.assertIn("literal test invocation", receipt["source_binding"]["reason"])


class SourceHoldTests(unittest.TestCase):
    def receipt(self, **values):
        return {
            "source_binding": {"status": "unverified", "reason": "the runner resolved live workspace source"},
            **values,
        }

    def test_regression_and_suite_judgments_do_not_attribute_contaminated_failures(self):
        dirty = self.receipt(
            timed_out=True,
            exit_code=1,
            results={"complete": True, "passed": [], "failed": ["tests.test_app.App.test_value"], "total": 1},
        )
        fail, unverified, notes, proof = [], [], [], {}
        judgment.judge_regression(dirty, None, fail, unverified, notes, proof, [], known_failures=set)
        self.assertEqual([], fail)
        self.assertEqual({}, proof)
        self.assertTrue(any("source resolution" in reason for reason in unverified))

        fail, unverified = [], []
        judgment.judge_suite(dirty, {"receipt": dirty}, fail, unverified, notes)
        self.assertEqual([], fail)
        self.assertTrue(any("source resolution" in reason for reason in unverified))

    def test_retained_runner_does_not_turn_source_uncertainty_into_failure(self):
        run = self.receipt(
            timed_out=False,
            exit_code=1,
            results={"complete": True, "passed": [], "failed": ["tests.test_app.App.test_value"]},
        )
        base = {"receipt": self.receipt(results={"passed": ["tests.test_app.App.test_value"]})}
        fail, unverified = [], []
        judgment.judge_retained_runner({"boundary": "", "run": run}, base, fail, unverified)
        self.assertEqual([], fail)
        self.assertTrue(any("source resolution" in reason for reason in unverified))

    def test_first_suite_and_guard_paths_hold_source_uncertainty(self):
        dirty = self.receipt(results={"complete": True, "passed": ["tests.test_app.App.test_value"]})
        base_suite = {"receipt": dirty}
        unverified, proof = [], {}
        first_suite.existing_flip(dirty, base_suite, [], proof, [], unverified, [], False)
        self.assertEqual({}, proof)
        self.assertTrue(any("source resolution" in reason for reason in unverified))
        first_suite.suite_guards(dirty, base_suite, proof, unchanged=True)
        self.assertTrue(proof["guard_run_incomplete"])

        run = SimpleNamespace(
            existing_exit=True, checks={"suite_on_candidate": dirty}, base_suite=base_suite, unverified=[]
        )
        first_suite.exit_repair(run, {}, lambda receipt: True)
        self.assertTrue(any("source resolution" in reason for reason in run.unverified))
        self.assertEqual([], first_suite.repaired_by_flip(base_suite, dirty, dirty))

    def test_stability_and_flake_rechecks_cannot_use_contaminated_results(self):
        dirty = self.receipt(
            results={"complete": True, "passed": ["tests.test_app.App.test_value"], "failed": [], "total": 1}
        )
        self.assertEqual("missing", proof_stability.outcome_of(dirty, "tests.test_app.App.test_value"))
        run = SimpleNamespace(
            framework=test_framework.Framework("unittest", "python -m unittest"),
            base="base",
            proof={},
            unverified=[],
        )
        quarantined = suite_flakes.for_proof(
            run,
            {},
            dirty,
            {"base": "base", "receipt": dirty},
            (None, None, None),
        )
        self.assertEqual([], quarantined)
        self.assertTrue(any("source resolution" in reason for reason in run.unverified))

    def test_companion_and_command_guards_return_only_source_holds(self):
        dirty = self.receipt(exit_code=1, timed_out=False)
        failures, unverified = companion_suites.judge("pkg", "npm test", dirty, dirty)
        self.assertEqual([], failures)
        self.assertTrue(any("source resolution" in reason for reason in unverified))
        failures, unverified = command_guards.judge({"id": "keep", "command": "npm test"}, dirty, dirty)
        self.assertEqual([], failures)
        self.assertTrue(any("source resolution" in reason for reason in unverified))

    def test_original_reference_classification_reports_the_source_hold(self):
        dirty = self.receipt(timed_out=True, exit_code=1)
        problem = acceptance_reference.candidate_problem(dirty)
        self.assertIn("source resolution", problem)
        self.assertIn("live workspace source", problem)

    def test_follow_up_baseline_rejects_a_clean_looking_source_hold(self):
        receipt = self.receipt(
            exit_code=0,
            timed_out=False,
            results={"complete": True, "passed": ["tests.test_app.App.test_value"], "failed": []},
        )
        proof = {}
        follow_up_proof.apply(proof, {"cases": [{"id": "keep", "test": "test_value"}]}, receipt, {})
        self.assertEqual([], proof["case_tests"]["keep"])
        self.assertTrue(any("live workspace source" in reason for reason in proof["unverified"]), proof)


if __name__ == "__main__":
    unittest.main()
