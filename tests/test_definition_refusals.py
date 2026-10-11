"""Real-command controls for #652 G9, G10 and G11."""

import shutil
import subprocess
import unittest
from unittest import mock

import autocode_verify as verify

from tests.test_base_definition_proof import CALC, CALC_TEST, FIXED, LABEL, change, manifest, seed
from tests.test_verify import Project


@unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node and npm are required")
class DefinitionRefusalTests(unittest.TestCase):
    def project(self, files, candidate):
        project = Project(files)
        self.addCleanup(project.close)
        project.write(candidate)
        return project

    def verify(self, project, *, suite="npm test --silent", **options):
        framework = verify.detect_framework(project.root)
        base = verify.baseline(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=suite,
            timeout=120,
        )
        self.assertEqual("passing", base["health"], base)
        return verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=suite,
            base_suite=base,
            timeout=120,
            **options,
        )

    def test_g9_unrelated_scripts_do_not_refuse_a_correct_feature(self):
        scripts = {
            "start": "node app.js",
            "build": "cd dist && node build.js",
            "deploy": "cd $DEST && node deploy.js",
            "description": "echo don't",
        }
        files = {
            **seed({"test": "node --test", **scripts}),
            "app.js": "require('./unused.js'); require(process.env.APP); import(`./${process.env.APP}.js`);\n",
            "unused.js": "module.exports = 42;\n",
        }
        project = self.project(files, change(FIXED))
        result = self.verify(project, new_behavior=True)
        self.assertEqual(verify.PASS, result["verdict"], result)

    def test_g9_unrelated_workspace_package_scripts_are_not_roots(self):
        project = self.project(
            {**seed(), "unused/package.json": manifest({"test": "cd $DEST && node run.js"})},
            change(FIXED),
        )
        result = self.verify(project, new_behavior=True)
        self.assertEqual(verify.PASS, result["verdict"], result)

    def test_g10_product_import_and_export_maps_work_under_the_base_tests(self):
        for field, specifier, value in (
            ("imports", "#ops", {"#ops": "./ops.js"}),
            ("exports", "calc/ops", {"./ops": "./ops.js"}),
        ):
            with self.subTest(field=field):
                candidate = change(
                    "exports.add = require('" + specifier + "').add;\nexports.mul = (a, b) => a * b;\n",
                    **{"ops.js": CALC, "package.json": manifest({"test": "node --test"}, **{field: value})},
                )
                project = self.project(seed({"test": "node --test test/calc.test.js"}), candidate)
                result = self.verify(project, new_behavior=True)
                self.assertEqual(verify.PASS, result["verdict"], result)
                self.assertEqual(0, result["checks"][LABEL]["exit_code"], result)

    def test_g11_unchanged_definition_does_not_execute_a_third_suite(self):
        project = self.project(seed(), {"calc.js": CALC + "// unchanged behavior\n"})
        result = self.verify(project, preserve_only=True)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertNotIn(LABEL, result["checks"], result)

    def test_g11_an_already_failed_regression_does_not_run_an_extra_suite(self):
        project = self.project(seed(), {"calc.js": FIXED, "test/calc.test.js": CALC_TEST + "// same test\n"})
        result = self.verify(project)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertNotIn(LABEL, result["checks"], result)

    def test_g10_loading_maps_cannot_redirect_the_base_runner_selector(self):
        runner = (
            "require('child_process').execSync('node --test ' + require('#selector').join(' '), {stdio:'inherit'});\n"
        )
        project = self.project(
            {
                **seed({"test": "node run.js"}, imports={"#selector": "./old-list.js"}),
                "run.js": runner,
                "old-list.js": "module.exports = ['test/calc.test.js'];\n",
            },
            change(
                **{
                    "package.json": manifest({"test": "node run.js"}, imports={"#selector": "./new-list.js"}),
                    "new-list.js": "module.exports = ['test/feature.test.js'];\n",
                }
            ),
        )
        result = self.verify(project, new_behavior=True)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertTrue(any("loading fields" in reason for reason in result["unverified"]), result)

    def test_g9_nested_scripts_and_hooks_still_retain_the_base_selector(self):
        scripts = {"test": "npm run unit", "preunit": "node setup.js", "unit": "node --test"}
        setup = "require('fs').writeFileSync('test/calc.test.js', '');\n"
        project = self.project(
            {**seed(scripts), "setup.js": "// baseline setup\n"},
            change(**{"setup.js": setup}),
        )
        result = self.verify(project, new_behavior=True)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertNotEqual(0, result["checks"][LABEL]["exit_code"], result)

    def test_g9_static_template_product_imports_are_understood(self):
        runner = "require(`./calc.js`); require('child_process').execSync('node --test', {stdio:'inherit'});\n"
        project = self.project({**seed({"test": "node run.js"}), "run.js": runner}, change(FIXED))
        result = self.verify(project, new_behavior=True)
        self.assertEqual(verify.PASS, result["verdict"], result)

    def test_g9_unchanged_selector_outside_exec_does_not_refuse_known_product_changes(self):
        runner = "const list = require('./selector.js'); require('child_process').execSync('node --test ' + list.join(' '), {stdio:'inherit'});\n"
        project = self.project(
            {
                **seed({"test": "node run.js"}),
                "run.js": runner,
                "selector.js": "module.exports = ['test/calc.test.js'];\n",
            },
            change(FIXED),
        )
        result = self.verify(project, new_behavior=True)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(0, result["checks"][LABEL]["exit_code"], result)

    def test_g11_added_tests_still_trigger_the_retained_definition(self):
        runner = "const fs = require('fs'), path = require('path'); for (const name of fs.readdirSync('test').filter(n => n.endsWith('.test.js')).sort()) require(path.resolve('test', name));\n"
        old = "const assert = require('assert'); assert.equal(require('../calc.js').add(2, 3), 5);\n"
        early = "const assert = require('assert'); assert.equal(require('../calc.js').mul(2, 3), 6); process.exit(0);\n"
        project = self.project(
            {**seed({"test": "node run.js"}), "run.js": runner, "test/calc.test.js": old},
            {"calc.js": "exports.add = (a, b) => a - b; exports.mul = (a, b) => a * b;\n", "test/aaa.test.js": early},
        )
        result = self.verify(project, new_behavior=True)
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertNotEqual(0, result["checks"][LABEL]["exit_code"], result)


class DefinitionCostTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required")
    def test_unknown_javascript_layout_cannot_skip_the_retained_suite(self):
        project = Project(
            {
                "calc.jsx": "exports.add = (a, b) => a + b;\n",
                "test/old.jsx": "require('assert').equal(require('../calc.jsx').add(2, 3), 5);\n",
            }
        )
        self.addCleanup(project.close)
        project.write(
            {
                "calc.jsx": "exports.add = (a, b) => a - b; exports.mul = (a, b) => a * b;\n",
                "test/old.jsx": "// deselected old behavior\n",
                "test/feature.jsx": "require('assert').equal(require('../calc.jsx').mul(2, 3), 6);\n",
            }
        )
        suite = "node -e \"require('./test/old.jsx')\""
        regression = "node -e \"require('./test/feature.jsx')\""
        base = verify.baseline(project.root, project.base, project.evidence, framework=None, suite_command=suite)
        self.assertEqual("passing", base["health"], base)
        result = verify.verify(
            project.root,
            project.base,
            project.evidence,
            suite_command=suite,
            regression_command=regression,
            base_suite=base,
            new_behavior=True,
        )
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertNotEqual(0, result["checks"][LABEL]["exit_code"], result)

    def test_non_javascript_suite_does_not_run_the_package_definition_proof(self):
        project = Project(
            {
                "calc.py": "def add(a, b): return a + b\n",
                "test_calc.py": "import unittest\nfrom calc import add\nclass T(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2, 3), 5)\n",
            }
        )
        self.addCleanup(project.close)
        project.write({"calc.py": "def add(a, b): return b + a\n"})
        framework = verify.detect_framework(project.root)
        suite = "python3 -c 'from calc import add; assert add(2, 3) == 5'"
        base = verify.baseline(project.root, project.base, project.evidence, framework=framework, suite_command=suite)
        self.assertEqual("passing", base["health"], base)
        result = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=suite,
            base_suite=base,
            preserve_only=True,
        )
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertNotIn(LABEL, result["checks"], result)

    def test_g11_reads_many_test_blobs_with_one_git_process(self):
        files = {f"test/t{index}.test.js": "require('../calc.js');\n" for index in range(60)}
        project = Project({**seed(), **files})
        self.addCleanup(project.close)
        real_popen, commands = subprocess.Popen, []

        def popen(*args, **kwargs):
            commands.append(args[0])
            return real_popen(*args, **kwargs)

        with mock.patch.object(subprocess, "Popen", popen):
            definition = verify._base_suite_definition(project.root, project.base, "npm test")
        self.assertFalse(definition.boundary)
        self.assertTrue(set(files) <= definition.paths)
        self.assertLessEqual(len(commands), 6, commands)


if __name__ == "__main__":
    unittest.main()
