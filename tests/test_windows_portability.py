"""Native Windows ratchet (#698): which runtime modules still import a POSIX-only module at load.

Python on Windows has no fcntl, termios, pwd, grp, resource or pty, so one such import at
module level stops `autocode` itself from starting there, before any platform check can
run. The list below is the known debt recorded in docs/windows.md. A new module-level
import fails this test: take the lock through `autocode_util.lock_file` / `unlock_file`,
or import the POSIX module inside the POSIX-only branch. Removing a module is progress:
take it off the list (the test says when). The frozen dashboard is out of scope.
"""

import ast
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
POSIX_ONLY = frozenset({"fcntl", "termios", "pwd", "grp", "resource", "pty", "tty", "posix", "syslog", "crypt"})

KNOWN = {
    "autocode_checkout_lock.py": {"fcntl"},
    "autocode_checkpoint_cli.py": {"fcntl"},
    "autocode_controller_owner.py": {"fcntl"},
    "autocode_conversation.py": {"fcntl"},
    "autocode_conversation_session.py": {"fcntl"},
    "autocode_dependencies.py": {"fcntl"},
    "autocode_interventions.py": {"fcntl"},
    "autocode_milestones.py": {"fcntl"},
    "autocode_registry.py": {"fcntl"},
    "autocode_usage.py": {"fcntl"},
    "autocode_workspaces.py": {"fcntl"},
    "autocode_worktrees.py": {"fcntl"},
    "opencode_stage_router.py": {"fcntl"},
}


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
        found = posix_only_imports()
        added = {name: names for name, names in found.items() if not names <= KNOWN.get(name, set())}
        self.assertEqual(
            {}, added, "these modules stop `autocode` loading on native Windows; use autocode_util.lock_file"
        )
        fixed = sorted(set(KNOWN) - set(found))
        self.assertEqual([], fixed, "progress: take these modules off KNOWN in this test and docs/windows.md")

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
