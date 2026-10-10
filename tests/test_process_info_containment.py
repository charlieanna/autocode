"""Actual kernel boundary for process environments, using only owned markers."""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_tool_containment as containment


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"), "requires macOS and clang")
class ProcessInformationTests(unittest.TestCase):
    def test_other_process_environment_is_denied_but_self_inspection_works(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, binary = root / "probe.c", root / "probe"
            source.write_text(r"""
#include <sys/types.h>
#include <sys/sysctl.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc != 2) return 9;
    if (!strcmp(argv[1], "wait")) { for (;;) pause(); }
    int pid = !strcmp(argv[1], "self") ? getpid() : atoi(argv[1]);
    int mib[3] = {CTL_KERN, KERN_PROCARGS2, pid};
    char data[262144]; size_t size = sizeof(data);
    if (sysctl(mib, 3, data, &size, NULL, 0)) {
        printf("errno=%d\n", errno); return errno == EPERM ? 2 : 4;
    }
    const char marker[] = "AUTOCODE_SYNTHETIC_MARKER=fixture-only";
    for (size_t i = 0; i + sizeof(marker) <= size; i++) {
        if (!memcmp(data + i, marker, sizeof(marker))) { puts("OWNED_MARKER"); return 0; }
    }
    return 3;
}
""")
            subprocess.run(["clang", str(source), "-o", str(binary)], check=True, capture_output=True, timeout=30)
            child = subprocess.Popen([str(binary), "wait"], env={"AUTOCODE_SYNTHETIC_MARKER": "fixture-only"})
            try:
                # Prove the fixture is observable before testing the boundary.
                control = subprocess.run([str(binary), str(child.pid)], capture_output=True, text=True, timeout=10)
                self.assertEqual(0, control.returncode, control.stderr)
                self.assertEqual("OWNED_MARKER\n", control.stdout)
                boundary = containment.prepare(root, environment=os.environ)
                denied = subprocess.run(
                    [boundary["shell"], "-c", shlex.join([str(binary), str(child.pid)])],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(2, denied.returncode, denied.stdout + denied.stderr)
                self.assertEqual("errno=1\n", denied.stdout)
                own = subprocess.run(
                    [
                        boundary["shell"],
                        "-c",
                        "AUTOCODE_SYNTHETIC_MARKER=fixture-only " + shlex.join([str(binary), "self"]),
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(0, own.returncode, own.stderr)
                self.assertEqual("OWNED_MARKER\n", own.stdout)
            finally:
                child.terminate()
                child.wait(timeout=10)
