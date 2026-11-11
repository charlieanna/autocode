"""The CLI fixture's children load AutoCode from compiled bytecode and write none (#2997)."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from . import test_subprocess

TOOLS = Path(__file__).resolve().parents[1] / "tools"
PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
import autocode
tools = sys.argv[1] + "/"
loaded = [m for m in list(sys.modules.values()) if (getattr(m, "__file__", None) or "").startswith(tools)]
print(json.dumps({
    "loaded": len(loaded),
    "writes_bytecode": not sys.dont_write_bytecode,
    "uncached": sorted(m.__name__ for m in loaded if not m.__spec__.cached or not __import__("os").path.isfile(m.__spec__.cached)),
    "caches": sorted({m.__spec__.cached for m in loaded if m.__spec__.cached}),
}))
"""


class FixtureBytecode(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp

    def test_a_cli_child_loads_every_autocode_module_from_the_shared_cache_and_writes_nothing(self):
        before = sorted(TOOLS.rglob("*.pyc"))
        result = subprocess.run(
            [sys.executable, "-c", PROBE, str(TOOLS)],
            env=self.env,
            cwd=self.project,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        probe = json.loads(result.stdout)
        self.assertGreater(probe["loaded"], 100, "the probe must import the CLI's own module graph")
        self.assertFalse(probe["writes_bytecode"])
        self.assertEqual([], probe["uncached"], "every AutoCode module the CLI imports must already be compiled")
        cache = Path(self.env["PYTHONPYCACHEPREFIX"])
        self.assertTrue(all(Path(path).is_relative_to(cache) for path in probe["caches"]), probe["caches"][:3])
        self.assertEqual(before, sorted(TOOLS.rglob("*.pyc")), "nothing is written into the checkout")
        self.assertEqual([], list(self.project.rglob("__pycache__")), "nothing is written into the workspace")


if __name__ == "__main__":
    unittest.main()
