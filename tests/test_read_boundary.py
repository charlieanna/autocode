"""A proof command cannot read the live workspace or another proof copy (#4152, phase 3)."""

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_read_boundary as read_boundary
import autocode_scratch_location as scratch_location
import autocode_verify as verify


def git(*arguments, cwd):
    return subprocess.run(["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class PlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.live = self.base / "project"
        self.tree = self.base / "project.autocode-scratch/.autocode/runs/r/tree"
        for path in (self.live / ".venv", self.live / ".autocode/runs/r/evidence", self.tree):
            path.mkdir(parents=True)
        (self.tree / ".venv").symlink_to(self.tree / ".autocode/dependencies/.venv")
        (self.tree / ".autocode/dependencies/.venv").mkdir(parents=True)

    def test_the_copy_its_outputs_and_its_dependencies_are_shown_inside_what_is_hidden(self):
        other = self.base / "project.autocode-scratch/.autocode/runs/r/other"
        other.mkdir()
        rules = read_boundary.plan(
            self.tree, hidden=[self.live, other, self.tree], outputs=[self.live / ".autocode/runs/r/evidence"]
        )
        self.assertEqual(
            [
                (read_boundary.HIDE, self.live),
                (read_boundary.SHOW, self.live / ".venv"),
                (read_boundary.HIDE, other),
                (read_boundary.SHOW, self.live / ".autocode/runs/r/evidence"),
            ],
            rules,
        )

    def test_a_task_worktree_inside_a_shown_directory_is_hidden_again(self):
        worktree = self.live / ".autocode/runs/r/evidence/worktree"  # deeper than a shown path
        worktree.mkdir()
        rules = read_boundary.plan(
            self.tree, hidden=[self.live, worktree], outputs=[self.live / ".autocode/runs/r/evidence"]
        )
        order = [path for _, path in rules]
        self.assertLess(order.index(self.live / ".autocode/runs/r/evidence"), order.index(worktree))
        self.assertIn((read_boundary.HIDE, worktree), rules)

    def test_nothing_to_hide_or_a_checkout_holding_home_gives_no_boundary(self):
        self.assertIsNone(read_boundary.plan(self.tree, hidden=[]))
        self.assertIsNone(read_boundary.plan(self.tree, hidden=[self.base / "missing"]))
        with mock.patch.object(Path, "home", return_value=self.live / "user"):
            self.assertIsNone(read_boundary.plan(self.tree, hidden=[self.live]))

    def test_seatbelt_denies_contents_in_rule_order_so_the_deeper_rule_wins(self):
        rules = [(read_boundary.HIDE, Path("/w")), (read_boundary.SHOW, Path("/w/.venv"))]
        profile = read_boundary.seatbelt_profile(rules)
        self.assertLess(
            profile.index('(deny file-read-data (subpath "/w"))'),
            profile.index('(allow file-read-data (subpath "/w/.venv"))'),
        )
        arguments = read_boundary.bubblewrap_arguments(rules)
        self.assertEqual(["--tmpfs", "/w", "--bind", "/w/.venv", "/w/.venv", "--"], arguments[-6:])

    def test_a_host_without_a_boundary_runs_the_command_unchanged_and_says_why(self):
        with mock.patch.object(read_boundary, "problem", return_value="no bubblewrap"):
            argv, record = read_boundary.wrap(["sh", "-c", ":"], self.tree, hidden=[self.live])
        self.assertEqual((["sh", "-c", ":"], {"backend": None, "reason": "no bubblewrap"}), (argv, record))
        self.assertEqual("no read boundary is available on win32", read_boundary.problem("win32"))

    def test_a_directory_outside_a_scratch_mirror_is_not_a_proof_copy(self):
        git("init", "-q", cwd=self.live)
        self.assertIsNone(read_boundary.copy_of(self.live))
        self.assertIsNone(read_boundary.copy_of(self.base))
        prefix, record = read_boundary.for_command(self.live)
        self.assertEqual(([], None), (prefix, record["backend"]))


@unittest.skipIf(read_boundary.problem(), f"no read boundary on this host: {read_boundary.problem()}")
class BoundaryTests(unittest.TestCase):
    """Run on a host with the boundary: Linux with bubblewrap (the CI suite), macOS with sandbox-exec."""

    def test_a_proof_copy_reads_its_own_source_and_never_the_live_tree(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        live = Path(temporary.name).resolve() / "project"
        live.mkdir()
        (live / "app.py").write_text("VALUE = 1\n")
        (live / ".gitignore").write_text(".venv/\n.autocode/\n")
        git("init", "-q", cwd=live)
        git("add", "-A", cwd=live)
        git("-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-qm", "base", cwd=live)
        head = git("rev-parse", "HEAD", cwd=live)
        (live / "app.py").write_text("VALUE = 2\n")  # the candidate, uncommitted in the live workspace
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(live / ".venv")], check=True)
        destination = scratch_location.relocate(live / ".autocode/runs/r/scratch/base", live)
        tree = verify.make_tree(live, head, destination, live, {}, dependencies_from=live)
        self.addCleanup(verify.remove_tree, live, tree)
        evidence = live / ".autocode/runs/r/evidence"
        evidence.mkdir(parents=True)
        code = "\n".join(
            [
                "import app, pathlib, sys",
                "print('copy', app.VALUE, sys.prefix)",
                f"path = pathlib.Path({str(live / 'app.py')!r})",
                "try:\n    print('live', path.read_text().strip())\nexcept OSError:\n    print('live hidden')",
                f"pathlib.Path({str(evidence / 'out.xml')!r}).write_text('<x/>')",
            ]
        )
        receipt = verify.run_command(
            ".venv/bin/python -c " + shlex.quote(code), tree, evidence / "check.log", timeout=60
        )
        output = Path(receipt["output"]).read_text()
        self.assertEqual(0, receipt["exit_code"], output)
        # The copy's own interpreter: its .venv, or on macOS the dependency view that .venv links to.
        prefix = Path(output.splitlines()[0].removeprefix("copy 1 "))
        self.assertTrue(output.startswith("copy 1 "), output)
        self.assertTrue(prefix.is_relative_to(tree), output)
        self.assertIn("live hidden", output)
        self.assertEqual("<x/>", (evidence / "out.xml").read_text())
        self.assertEqual(str(live), receipt["read_boundary"]["hidden"][0])
        # The live workspace itself is untouched and still readable outside the boundary.
        self.assertEqual("VALUE = 2\n", (live / "app.py").read_text())
        self.assertTrue(os.access(live / "app.py", os.R_OK))


if __name__ == "__main__":
    unittest.main()
