"""Exercise project-import proof through the complete owned regression runner."""

import unittest

import autocode_verify as verify

from tests.test_verify import Project, isolated_python


class ProjectImportRegressionTests(unittest.TestCase):
    def check_feature(self, candidate_test):
        project = Project(
            {
                "app.py": "def old(): return 1\n",
                "test_existing.py": "import unittest\nclass Existing(unittest.TestCase):\n"
                "    def test_old(self):\n        from app import old\n        self.assertEqual(old(), 1)\n",
            }
        )
        self.addCleanup(project.close)
        python = isolated_python(self)
        framework = verify.detect_framework(project.root, python=python)
        base_suite = verify.baseline(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=framework.suite,
            timeout=120,
        )
        project.write({"app.py": "def old(): return 1\ndef new(): return 42\n", "test_feature.py": candidate_test})
        return verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            base_suite=base_suite,
            timeout=120,
            new_behavior=True,
        )

    def test_new_project_import_without_execution_proof_stays_unverified(self):
        result = self.check_feature(
            "import unittest\nclass Feature(unittest.TestCase):\n"
            "    def test_new(self):\n        from app import new\n        self.assertEqual(new(), 42)\n"
        )
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertEqual([], result.get("fail_to_pass"), result)

    def test_missing_external_dependency_is_not_a_feature_failure(self):
        result = self.check_feature(
            "import unittest\nclass Feature(unittest.TestCase):\n"
            "    def test_new(self):\n        import app\n"
            "        if not hasattr(app, 'new'):\n            import missing_external_autocode_fixture\n"
            "        self.assertEqual(app.new(), 42)\n"
        )
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertEqual([], result.get("fail_to_pass"), result)

    def test_forged_origin_cannot_turn_missing_external_import_into_proof(self):
        result = self.check_feature(
            "import importlib.machinery, sys, types, unittest\n"
            "class Feature(unittest.TestCase):\n"
            "    def test_new(self):\n"
            "        import app\n"
            "        if hasattr(app, 'new'):\n"
            "            module = types.ModuleType('external_fake_3926')\n"
            "            module.__spec__ = importlib.machinery.ModuleSpec('external_fake_3926', None, origin=app.__file__)\n"
            "            module.new = app.new\n"
            "            sys.modules['external_fake_3926'] = module\n"
            "        from external_fake_3926 import new\n"
            "        self.assertEqual(new(), 42)\n"
        )
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertEqual([], result.get("fail_to_pass"), result)

    def test_module_collection_failure_is_not_an_executed_python_test(self):
        result = self.check_feature(
            "import unittest\nfrom app import new\nclass Feature(unittest.TestCase):\n"
            "    def test_new(self):\n        self.assertEqual(new(), 42)\n"
        )
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertEqual([], result.get("fail_to_pass"), result)

    def test_forged_finder_origin_cannot_prove_an_external_import(self):
        result = self.check_feature(
            "import importlib.abc, importlib.util, sys, unittest\n"
            "class Feature(unittest.TestCase):\n"
            "    def test_new(self):\n"
            "        import app\n"
            "        if hasattr(app, 'new'):\n"
            "            class Loader(importlib.abc.Loader):\n"
            "                def create_module(self, spec): return None\n"
            "                def exec_module(self, module): module.new = app.new\n"
            "            class Finder(importlib.abc.MetaPathFinder):\n"
            "                def find_spec(self, name, path=None, target=None):\n"
            "                    if name == 'external_finder_3926':\n"
            "                        return importlib.util.spec_from_loader(name, Loader(), origin=app.__file__)\n"
            "            sys.meta_path.insert(0, Finder())\n"
            "        from external_finder_3926 import new\n"
            "        self.assertEqual(new(), 42)\n"
        )
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertEqual([], result.get("fail_to_pass"), result)
