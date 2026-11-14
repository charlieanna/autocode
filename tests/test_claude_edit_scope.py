"""A Claude Code stage is told why an edit outside its directory is refused (providers/claude_edit_scope).

Two real runs (Claude models, 2026-11-14) ended ``PAUSED_ORCHESTRATOR_DRIFT`` because a parallel Builder
edited the project by absolute path instead of its own worktree. With Edit and Write accepted only inside
the stage's directory, another Builder took Claude Code's refusal for a permission to ask for, and the
person was asked to grant it. The hook's reason names the stage's own file, so the path is corrected in the
stage. The script is run as Claude Code runs it: isolated, the call on standard input.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from providers import claude, claude_edit_scope


class EditScopeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="edit-scope-")
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve() / "project"
        self.worktree = self.project / ".autocode" / "builders" / "b1" / "2"
        for root in (self.project, self.worktree):
            (root / "packages" / "web" / "src").mkdir(parents=True)
            (root / "packages" / "web" / "src" / "format.js").write_text("old\n")

    def hook(self, tool, path, *, directory=None, cwd=None):
        """The hook's decision for one call, run as Claude Code runs it; None when it says nothing."""
        call = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"file_path": path}}
        if cwd is not None:
            call["cwd"] = str(cwd)
        out = subprocess.run(
            [sys.executable, "-I", str(claude.EDIT_SCOPE), str(directory or self.worktree)],
            input=json.dumps(call),
            capture_output=True,
            text=True,
            cwd=cwd or self.worktree,
            check=True,
        )
        self.assertEqual("", out.stderr)
        if not out.stdout:
            return None
        decision = json.loads(out.stdout)["hookSpecificOutput"]
        self.assertEqual(("PreToolUse", "deny"), (decision["hookEventName"], decision["permissionDecision"]))
        return decision["permissionDecisionReason"]

    def test_an_edit_of_the_projects_file_names_the_worktrees_own_file(self):
        stray = self.project / "packages" / "web" / "src" / "format.js"
        own = self.worktree / "packages" / "web" / "src" / "format.js"
        for tool in ("Edit", "Write"):
            with self.subTest(tool=tool):
                reason = self.hook(tool, str(stray))
                self.assertIn(f"{stray} is outside the directory this stage works in, {self.worktree}.", reason)
                self.assertIn(f"it is {own}: read and change that path instead", reason)
                self.assertIn("not a permission anyone can grant", reason)

    def test_a_new_file_is_placed_by_its_directory(self):
        reason = self.hook("Write", str(self.project / "packages" / "web" / "src" / "new.js"))
        self.assertIn(f"it is {self.worktree / 'packages' / 'web' / 'src' / 'new.js'}:", reason)

    def test_a_path_with_no_file_of_the_stage_gets_no_guess(self):
        for path in ("/etc/hosts", str(self.project.parent / "elsewhere" / "notes.txt"), str(self.project / "new.txt")):
            with self.subTest(path=path):
                reason = self.hook("Write", path)
                self.assertIn(f"Use a path inside {self.worktree} instead", reason)
                self.assertNotIn("it is ", reason)

    def test_a_path_inside_the_directory_is_left_to_claude_code(self):
        inside = self.worktree / "packages" / "web" / "src" / "format.js"
        for path in (str(inside), "packages/web/src/format.js", "packages/web/src/new.js", str(self.worktree / "a")):
            with self.subTest(path=path):
                self.assertIsNone(self.hook("Edit", path))
        # The stage's directory is the one it was launched in, wherever a command has since moved to.
        self.assertIsNone(self.hook("Edit", str(inside), cwd=self.worktree / "packages" / "web"))
        self.assertIsNone(self.hook("Edit", "../../.gitignore", cwd=self.worktree / "packages" / "web"))

    def test_a_path_that_leaves_the_directory_is_refused_however_it_is_written(self):
        outside = self.project / "packages" / "web" / "src" / "format.js"
        link = self.worktree / "shared"
        link.symlink_to(self.project / "packages")
        for path in ("../../../../packages/web/src/format.js", str(link / "web" / "src" / "format.js")):
            with self.subTest(path=path):
                self.assertIn("is outside the directory this stage works in", self.hook("Edit", path))
        self.assertEqual("old\n", outside.read_text())

    def test_a_call_it_cannot_read_is_left_to_claude_code(self):
        for text in ("", "not json", "[]", '{"tool_input": {}}', '{"tool_input": {"file_path": 7}}'):
            with self.subTest(text=text):
                out = subprocess.run(
                    [sys.executable, "-I", str(claude.EDIT_SCOPE), str(self.worktree)],
                    input=text,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual((0, ""), (out.returncode, out.stdout))
        self.assertEqual(0, claude_edit_scope.main([]))  # no directory to judge by

    def test_a_writing_stage_is_launched_with_the_hook_and_a_reading_stage_without(self):
        (self.project / "schema.json").write_text("{}")
        launch = lambda **kwargs: claude.launch(  # noqa: E731
            "builder", self.worktree, self.project / "run", None, claude.HAIKU, "medium", schema="s", **kwargs
        )[0]
        claude_path = self.project / "bin" / "claude"
        claude_path.parent.mkdir()
        claude_path.write_text("#!/bin/sh\n")
        claude_path.chmod(0o755)
        env = {"PATH": str(claude_path.parent), "HOME": str(self.project)}
        builder = launch(allow_write=True, env=env)
        settings = json.loads(builder[builder.index("--settings") + 1])
        (hook,) = settings["hooks"]["PreToolUse"]
        self.assertEqual("Edit|Write", hook["matcher"])
        self.assertEqual(
            [{"type": "command", "command": f"{sys.executable} -I {claude.EDIT_SCOPE} {self.worktree}"}], hook["hooks"]
        )
        self.assertLess(builder.index("--settings"), builder.index("--allowedTools"))
        self.assertNotIn("--settings", launch(allow_write=False, planning=True, env=env))
        self.assertNotIn("--settings", launch(allow_write=False, planning=True, report_recovery=True, env=env))


if __name__ == "__main__":
    unittest.main()
