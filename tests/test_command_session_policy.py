"""Runner-owned command nesting collects exits without adding provider listeners.

Real public command execution uses tiny offline scripts. Native filter counts
distinguish inherited host policy from an extra per-command session boundary.
"""

import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_command_receipt as receipts
import autocode_command_supervision as commands

TOOLS = Path(commands.__file__).resolve().parent
NATIVE_OBSERVER = """\
def observed():
    filters = None
    for line in Path('/proc/self/status').read_text().splitlines():
        key, _, value = line.partition(':')
        if key == 'Seccomp_filters':
            filters = int(value)
    return {'pid': os.getpid(), 'parent': os.getppid(), 'filters': filters}
"""

INNER = (
    """\
import json, os, sys
from pathlib import Path
"""
    + NATIVE_OBSERVER
    + """
root = Path(sys.argv[1])
(root / 'inner-observed.json').write_text(json.dumps(observed()))
(root / 'inner-executed').write_text('collected exit 7\\n')
print('offline inner command')
raise SystemExit(7)
"""
)

OUTER = (
    """\
import json, os, shlex, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import autocode_command_supervision as commands
"""
    + NATIVE_OBSERVER
    + """
root = Path(sys.argv[1])
(root / 'outer-observed.json').write_text(json.dumps(observed()))
result = commands.run(
    'exec ' + shlex.join([sys.executable, str(root / 'inner.py'), str(root)]),
    root, root / 'inner.log', timeout=15, env=dict(os.environ),
)
(root / 'inner-result.json').write_text(json.dumps(result))
if result['exit_code'] != 7:
    raise SystemExit(99)
(root / 'outer-executed').write_text('collected nested exit 7\\n')
print('offline outer command')
raise SystemExit(7)
"""
)


@unittest.skipUnless(sys.platform == "linux", "Linux inherited filter policy observation")
class RunnerCommandSessionPolicyTests(unittest.TestCase):
    def test_nested_runner_commands_collect_actual_exits_without_additional_listeners(self):
        with tempfile.TemporaryDirectory(prefix="command-session-policy-") as directory:
            root = Path(directory).resolve()
            (root / "inner.py").write_text(INNER)
            (root / "outer.py").write_text(OUTER)
            native = {}
            exec(NATIVE_OBSERVER, {"os": os, "Path": Path}, native)
            parent_filters = native["observed"]()["filters"]
            result = commands.run(
                "exec " + shlex.join([sys.executable, str(root / "outer.py"), str(root), str(TOOLS)]),
                root,
                root / "outer.log",
                timeout=30,
                env=dict(os.environ),
            )
            self.assertEqual(7, result["exit_code"], result)
            self.assertEqual("collected exit 7\n", (root / "inner-executed").read_text())
            self.assertEqual("collected nested exit 7\n", (root / "outer-executed").read_text())
            inner = json.loads((root / "inner-result.json").read_text())
            self.assertEqual(7, inner["exit_code"], inner)
            outer_observed = json.loads((root / "outer-observed.json").read_text())
            inner_observed = json.loads((root / "inner-observed.json").read_text())
            self.assertEqual(os.getpid(), outer_observed["parent"])
            self.assertEqual(outer_observed["pid"], inner_observed["parent"])
            for command_result, actual in ((result, outer_observed), (inner, inner_observed)):
                self.assertEqual(actual["pid"], command_result["supervision"]["provider"]["pid"])
                self.assertFalse(command_result["timed_out"])
                self.assertEqual([], command_result["supervision_errors"])
                self.assertTrue(receipts.completed(command_result, root=root))
                if parent_filters is not None:
                    self.assertEqual(
                        parent_filters, actual["filters"], "A runner-owned command added a seccomp listener"
                    )


if __name__ == "__main__":
    unittest.main()
