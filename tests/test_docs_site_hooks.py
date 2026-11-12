"""The docs build's hook renders the role table from tools/autocode_roles.py (docs/site_hooks.py)."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# MkDocs loads the hook by path, with no tools/ on sys.path; -I gives the same bare interpreter.
SCRIPT = """
import importlib.util, json, sys
from types import SimpleNamespace
spec = importlib.util.spec_from_file_location("site_hooks", sys.argv[1] + "/docs/site_hooks.py")
hooks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hooks)
page = SimpleNamespace(file=SimpleNamespace(src_uri="roles.md", abs_src_path=sys.argv[1] + "/docs/roles.md"))
config = SimpleNamespace(docs_dir=sys.argv[1] + "/docs", extra={"source_links": SimpleNamespace()})
before = list(sys.path)
print(json.dumps({"markdown": hooks.on_page_markdown("<!-- canonical-role-table -->", page, config),
                  "path_kept": sys.path == before}))
"""


class RoleTableTests(unittest.TestCase):
    def test_the_role_table_renders_in_a_bare_interpreter_and_leaves_its_path_alone(self):
        result = subprocess.run(
            [sys.executable, "-I", "-c", SCRIPT, str(ROOT)], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(0, result.returncode, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output["path_kept"])
        self.assertIn("| Stage in code | Job on screen |", output["markdown"])
        self.assertIn("| `builder` | Builder |", output["markdown"])


if __name__ == "__main__":
    unittest.main()
