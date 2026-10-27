"""Native Windows ratchet (#698): which runtime modules still import a POSIX-only module at load.

Python on Windows has no fcntl, termios, pwd, grp, resource or pty, so one such import at
module level stops `autocode` itself from starting there, before any platform check can
run. The last such imports, thirteen file locks, were removed on 2026-10-16, so any
module-level import now fails this test: take the lock through `autocode_util.lock_file` /
`unlock_file`, or import the POSIX module inside the POSIX-only branch. The dashboard
is out of scope.
"""

import ast
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
POSIX_ONLY = frozenset({"fcntl", "termios", "pwd", "grp", "resource", "pty", "tty", "posix", "syslog", "crypt"})

# Modules that must load with fcntl unimportable, as on native Windows: the CLI, which
# `autocode --version` and `doctor` load, and the thirteen that imported fcntl at load until
# 2026-10-16. The CLI loads nine of them at start; the others load with the command that
# needs them or run as their own program.
LOADS_WITHOUT_FCNTL = (
    "autocode",
    "autocode_checkout_lock",
    "autocode_checkpoint_cli",
    "autocode_controller_owner",
    "autocode_conversation",
    "autocode_conversation_session",
    "autocode_coordination_store",
    "autocode_dependencies",
    "autocode_interventions",
    "autocode_milestones",
    "autocode_registry",
    "autocode_usage",
    "autocode_workspaces",
    "autocode_worktrees",
    "opencode_stage_router",
)
# A fresh interpreter, so modules this process already loaded are imported again.
IMPORT_WITHOUT_FCNTL = """
import importlib, json, sys
sys.path.insert(0, sys.argv[1])
sys.modules["fcntl"] = None  # `import fcntl` now raises ImportError, as on Windows
failed = {}
for name in sys.argv[2:]:
    try:
        importlib.import_module(name)
    except ImportError as error:
        failed[name] = str(error)
print(json.dumps(failed))
"""


def load_time_imports(tree):
    """Top-level module names imported when the module loads: module body and its if/try
    blocks, not function or class bodies, which run only when called."""
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.Import):
            yield from (alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]
        else:
            for field in ("body", "orelse", "finalbody", "handlers"):
                pending.extend(getattr(node, field, None) or [])


def posix_only_imports():
    found = {}
    for path in sorted(TOOLS.rglob("*.py")):
        relative = path.relative_to(TOOLS)
        if relative.parts[0] == "dashboard":
            continue
        names = set(load_time_imports(ast.parse(path.read_text(encoding="utf-8")))) & POSIX_ONLY
        if names:
            found[relative.as_posix()] = names
    return found


class LoadTimeImportTests(unittest.TestCase):
    def test_no_new_module_imports_a_posix_only_module_at_load(self):
        self.assertEqual(
            {},
            posix_only_imports(),
            "these modules stop `autocode` loading on native Windows; use autocode_util.lock_file",
        )

    def test_the_cli_and_the_former_fcntl_modules_load_without_fcntl(self):
        """The scan above reads each file; this loads them, with everything they import."""
        result = subprocess.run(
            [sys.executable, "-c", IMPORT_WITHOUT_FCNTL, str(TOOLS), *LOADS_WITHOUT_FCNTL],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({}, json.loads(result.stdout), "these modules need fcntl to load")

    def test_the_shared_helpers_load_without_posix_modules(self):
        self.assertNotIn("autocode_util.py", posix_only_imports())

    def test_the_scan_sees_imports_inside_blocks_but_not_inside_functions(self):
        tree = ast.parse(
            "import os\n"
            "try:\n    import fcntl\nexcept ImportError:\n    pass\n"
            "if True:\n    from termios import TCSANOW\n"
            "def later():\n    import pwd\n"
            "class Later:\n    import grp\n"
        )
        self.assertEqual({"os", "fcntl", "termios"}, set(load_time_imports(tree)))


if __name__ == "__main__":
    unittest.main()
