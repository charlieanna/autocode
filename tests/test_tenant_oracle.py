"""Focused documentation controls; the existing HTTP behavior checks stay separate."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "catalog" / "ladder-16-tenant-http-api"
sys.path.insert(0, str(SCENARIO.parents[1]))
spec = importlib.util.spec_from_file_location("tenant_oracle", SCENARIO / "oracle.py")
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)

INTERFACE = 'Call app.make_server(host="127.0.0.1", port=0) to create the server.'
TENANT = 'Send the X-Tenant header with each request to select the tenant.'
ENDPOINTS = """POST /items creates an item from a JSON title.
GET /items lists the tenant's items.
GET /items/ID reads an item.
PATCH /items/ID updates its title.
DELETE /items/ID removes it.
"""
COMMAND = "python3 -m unittest discover -s tests -t ."
README = f"{INTERFACE}\n{TENANT}\n{ENDPOINTS}\nTests: `{COMMAND}`.\n"


class TenantDocumentationOracleTests(unittest.TestCase):
    def checks(self, text):
        with tempfile.TemporaryDirectory(prefix="tenant-docs-") as tmp:
            project = Path(tmp)
            if text is not None:
                (project / "README.md").write_text(text, encoding="utf-8")
            (project / "tests").mkdir()
            (project / "tests" / "__init__.py").touch()
            (project / "tests" / "test_server_api.py").write_text(
                "raise AssertionError('documentation checks must not execute tests')\n")
            behavior = oracle.Check("behavior_control", False, "retained failure")
            with mock.patch.object(oracle, "python_change_checks", return_value=[behavior]) as check_behavior:
                result = oracle.check(project, SCENARIO, {"view": {"workflow": "build"}})
            check_behavior.assert_called_once_with(project, SCENARIO, "app")
            self.assertIs(result[0], behavior)
            self.assertEqual(result[-1].name, "workflow_recognized")
            self.assertTrue(result[-1].ok)
            return next(check for check in result if check.name == "readme_delivered")

    def test_seed_readme_is_not_documentation(self):
        result = self.checks((SCENARIO / "seed" / "README.md").read_text())
        self.assertFalse(result.ok)

    def test_reference_readme_passes(self):
        self.assertTrue(self.checks((SCENARIO / "reference" / "README.md").read_text()).ok)

    def test_missing_or_empty_readme_fails(self):
        for text in (None, "", "# Tenant API\n"):
            with self.subTest(text=text):
                self.assertFalse(self.checks(text).ok)

    def test_each_public_interface_part_is_required(self):
        for missing in (INTERFACE, TENANT, *ENDPOINTS.splitlines(), f"Tests: `{COMMAND}`."):
            with self.subTest(missing=missing):
                self.assertFalse(self.checks(README.replace(missing, "")).ok)

    def test_prose_and_markdown_tables_allow_case_and_spacing_variations(self):
        table = """Import make_server from app, then call make_server( host='127.0.0.1', port=0 ).
Use the `x-tenant` header to choose a tenant.
| Route | Methods | Use |
| /items | get, post | List or create items |
| /items/{id} | get, patch, delete | Read, change or remove an item |

```sh
python -m unittest discover -v --start-directory tests --top-level-directory .
```
"""
        self.assertTrue(self.checks(table).ok)
        self.assertTrue(self.checks(README.replace("GET /items", "get\t/items")).ok)

    def test_unittest_discovery_command_variants(self):
        for command in ("python -m unittest", "python3 -m unittest -v",
                        "python -m unittest discover", "python3  -m  unittest  discover  -s  tests",
                        "python3 -m unittest discover --start-directory=tests --pattern='test*.py'",
                        "$ python -m unittest discover -s ./tests/ -t ./ -q"):
            with self.subTest(command=command):
                self.assertTrue(self.checks(README.replace(COMMAND, command)).ok)

    def test_standard_python_options_versions_and_unittest_selectors(self):
        for command in ("python3 -m unittest discover -s tests -p 'test_*.py'",
                        "python3 -B -m unittest discover",
                        "python3 -m unittest tests.test_server_api",
                        "python3.11 -m unittest discover -s tests -p '*.py'",
                        "python3.14\t-B\t-m\tunittest\t-v\ttests.test_server_api",
                        "python -m unittest tests/test_server_api.py",
                        "python3 -m unittest discover tests 'test_*.py' .",
                        "python3 -W error -X dev -m unittest discover -s tests --durations 5",
                        "python3 -m unittest -k '*server*' tests.test_server_api"):
            with self.subTest(command=command):
                self.assertTrue(self.checks(README.replace(COMMAND, command)).ok)

    def test_import_and_explanation_document_the_callable_without_parentheses(self):
        interface = "`from app import make_server`: the factory accepts host and port and returns the HTTP server."
        self.assertTrue(self.checks(README.replace(INTERFACE, interface)).ok)

    def test_fake_or_invalid_test_commands_fail(self):
        for command in ("notpython3 -m unittest discover -s tests", "python3 -m unittestish discover",
                        "python3 -m unittest --pretend", "python3 -m unittest discover -s missing",
                        "python3 -m unittest discover -p no_tests_here.py", "python3 -m unittest discover -s",
                        "echo 'python3 -m unittest discover -s tests'", "python3 -c 'print(\"unittest\")'",
                        "python3 -m unittest discover; true", "python3 -Z -m unittest discover",
                        "python3 -m unittest tests.nonexistent", "python3 -m unittest discover --pretend",
                        "python3 -m unittest --durations five", "python3 -m unittest discover -p 'unterminated"):
            with self.subTest(command=command):
                self.assertFalse(self.checks(README.replace(COMMAND, command)).ok)


if __name__ == "__main__":
    unittest.main()
