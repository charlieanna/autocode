"""bubblewrap's user namespace keeps the caller's access to files (#4152 phase 3).

CI's demo, run under sudo, stopped at PAUSED_INVALID_OUTPUT: inside ``bwrap --unshare-user`` root
could not enter the runner's 0750 home, where the proof command's own interpreter was.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_agent_sandbox as agent_sandbox
import autocode_read_boundary as read_boundary
import autocode_userns as userns

PTRACE, ADMIN = 19, 21


class CommandLineTests(unittest.TestCase):
    def test_an_ordinary_user_gets_bubblewraps_own_namespace(self):
        self.assertEqual(["/usr/bin/bwrap", "--unshare-user"], userns.bubblewrap("/usr/bin/bwrap", euid=1000))

    def test_root_maps_every_id_and_keeps_only_the_capabilities_its_children_get(self):
        everything = (1 << 41) - 1
        line = userns.bubblewrap("/usr/bin/bwrap", euid=0, mask=everything & ~(1 << PTRACE) & ~(1 << ADMIN))
        self.assertEqual([sys.executable, "-I", os.path.abspath(userns.__file__), "/usr/bin/bwrap"], line[:4])
        self.assertEqual(["--cap-add", "ALL", "--cap-drop", "CAP_SYS_PTRACE", "--cap-drop", "CAP_SYS_ADMIN"], line[4:])
        self.assertEqual(["--cap-add", "ALL"], userns.capability_flags(everything))
        self.assertEqual(["--cap-drop", "ALL"], userns.capability_flags(None))

    def test_the_bounding_set_is_read_from_the_kernels_status(self):
        with tempfile.TemporaryDirectory() as folder:
            status = Path(folder) / "status"
            status.write_text("Name:\tpython\nCapBnd:\t000001ffffdfffff\n")
            self.assertEqual(0x1FFFFDFFFFF, userns.bounding(str(status)))
            status.write_text("Name:\tpython\n")
            self.assertIsNone(userns.bounding(str(status)))
        self.assertIsNone(userns.bounding("/nonexistent/status"))

    def test_both_sandboxes_start_bubblewrap_through_it(self):
        start = userns.bubblewrap(agent_sandbox.BWRAP)
        self.assertEqual(start, read_boundary.bubblewrap_arguments([])[: len(start)])
        self.assertEqual(start, agent_sandbox.bubblewrap_arguments([], [])[: len(start)])


class LostAccessTests(unittest.TestCase):
    """Whether a sandboxed command keeps root's access is observed from what a probe holds."""

    def probe(self, caller, stdout, returncode=0):
        from unittest import mock

        calls = []

        def run(argv, **options):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, returncode, stdout, "bwrap: denied\n")

        with (
            mock.patch.object(userns, "bounding", return_value=caller),
            mock.patch.object(userns.subprocess, "run", side_effect=run),
        ):
            return userns.lost_access(["bwrap", "--"]), calls

    def test_a_caller_without_root_access_needs_no_probe(self):
        self.assertEqual((None, []), self.probe(0, ""))

    def test_a_command_that_keeps_root_access(self):
        found, calls = self.probe(0x1FFFFDFFFFF, "Name:\tsh\nCapEff:\t000001ffffdfffff\n")
        self.assertIsNone(found)
        self.assertEqual(["bwrap", "--", "/bin/sh", "-c", "cat /proc/self/status"], calls[0])

    def test_a_command_a_host_policy_strips_of_capabilities(self):
        found, _ = self.probe(0x1FFFFDFFFFF, "Name:\tsh\nCapEff:\t0000000000000000\n")
        self.assertIn("would not keep root's access to files other users own", found)

    def test_a_probe_that_does_not_start(self):
        found, _ = self.probe(0x1FFFFDFFFFF, "", returncode=1)
        self.assertEqual("could not start its command here (exit 1: bwrap: denied)", found)

    def test_the_read_boundary_is_not_used_where_root_would_lose_access(self):
        from unittest import mock

        with (
            mock.patch.object(read_boundary.Path, "is_file", return_value=True),
            mock.patch.object(read_boundary.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")),
            mock.patch.object(userns, "lost_access", return_value="its command holds no CAP_DAC_OVERRIDE"),
        ):
            found = read_boundary._bubblewrap_problem()
        self.assertEqual("the read boundary is not used here: its command holds no CAP_DAC_OVERRIDE", found)


@unittest.skipUnless(sys.platform == "linux", "Linux user namespaces")
class LauncherFailureTests(unittest.TestCase):
    """The launcher runs nothing when it cannot set the namespace up, and says why on stderr."""

    def launch(self, argv, unshare=None):
        import contextlib
        import io
        from unittest import mock

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.ExitStack() as stack:
            if unshare is not None:
                stack.enter_context(mock.patch.object(userns.ctypes, "CDLL", return_value=unshare))
            code = userns.main(argv)
        return code, stderr.getvalue()

    def test_no_command(self):
        self.assertEqual((126, "autocode_userns: no command to run\n"), self.launch([]))

    def test_a_namespace_the_kernel_refuses(self):
        from types import SimpleNamespace

        code, message = self.launch(["/bin/true"], SimpleNamespace(unshare=lambda flags: -1))
        self.assertEqual(126, code)
        self.assertIn("could not create a user namespace", message)

    def test_an_id_map_that_cannot_be_written(self):
        from types import SimpleNamespace

        # Without the namespace the child writes this process's initial map, which the kernel refuses.
        code, message = self.launch(["/bin/true"], SimpleNamespace(unshare=lambda flags: 0))
        self.assertEqual(126, code)
        self.assertIn("could not write the namespace's uid_map", message)


@unittest.skipUnless(
    sys.platform == "linux" and os.geteuid() == 0 and Path(agent_sandbox.BWRAP).is_file(),
    "needs root and bubblewrap on Linux",
)
class RootTests(unittest.TestCase):
    """Root keeps reading what another user owns inside both sandboxes, and the hidden path stays hidden."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.home = self.base / "other-home"
        self.home.mkdir(mode=0o750)
        (self.home / "tool").write_text("readable by its owner's group only\n")
        os.chown(self.home / "tool", 4242, 4242)
        os.chown(self.home, 4242, 4242)
        os.chmod(self.home, 0o750)
        self.hidden = self.base / "live"
        self.hidden.mkdir()
        (self.hidden / "candidate.py").write_text("secret\n")
        if read_boundary.problem():
            self.skipTest(f"no read boundary here: {read_boundary.problem()}")

    def run_inside(self, arguments, script):
        return subprocess.run([*arguments, "/bin/sh", "-c", script], capture_output=True, text=True, timeout=60)

    def test_the_read_boundary_keeps_roots_access_and_hides_the_live_tree(self):
        rules = [(read_boundary.HIDE, self.hidden)]
        result = self.run_inside(
            read_boundary.bubblewrap_arguments(rules), f"cat '{self.home}/tool'; ls -A '{self.hidden}' | wc -l"
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["readable by its owner's group only", "0"], result.stdout.split("\n")[:2])

    def test_the_agent_sandbox_keeps_roots_access(self):
        lost = userns.lost_access(agent_sandbox.bubblewrap_arguments([], []))
        if lost:
            self.skipTest(f"this host takes root's access away inside bubblewrap: {lost}")
        result = self.run_inside(agent_sandbox.bubblewrap_arguments([], []), f"cat '{self.home}/tool'")
        self.assertEqual((0, "readable by its owner's group only\n"), (result.returncode, result.stdout), result.stderr)

    def test_a_command_that_cannot_be_run(self):
        result = subprocess.run(
            [sys.executable, "-I", userns.__file__, "/nonexistent/bwrap"], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(126, result.returncode)
        self.assertIn("could not run /nonexistent/bwrap", result.stderr)

    def test_the_command_holds_no_capability_this_process_could_not_pass_on(self):
        mask = userns.bounding()
        result = self.run_inside(read_boundary.bubblewrap_arguments([]), "grep -E '^Cap(Prm|Eff)' /proc/self/status")
        known = (1 << len(userns.CAPABILITIES)) - 1
        for line in result.stdout.splitlines():
            self.assertEqual(0, int(line.split(":")[1], 16) & ~mask & known, result.stdout)
        self.assertEqual(2, len(result.stdout.splitlines()), result.stderr)


if __name__ == "__main__":
    unittest.main()
