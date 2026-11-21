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

    def test_new_project_export_is_observed_and_proves_feature(self):
        result = self.check_feature(
            "import unittest\nclass Feature(unittest.TestCase):\n"
            "    def test_new(self):\n        from app import new\n        self.assertEqual(new(), 42)\n"
        )
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertTrue(result["project_import_observation"]["imports"], result)

    def test_missing_external_dependency_is_not_a_feature_failure(self):
        result = self.check_feature(
            "import unittest\nclass Feature(unittest.TestCase):\n"
            "    def test_new(self):\n        import app\n"
            "        if not hasattr(app, 'new'):\n            import missing_external_autocode_fixture\n"
            "        self.assertEqual(app.new(), 42)\n"
        )
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertEqual([], result.get("fail_to_pass"), result)
