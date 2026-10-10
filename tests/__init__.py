"""AutoCode test package.

The runtime lives in tools/ and is imported by its top-level module names
(``import autocode``), so this package puts tools/ on sys.path before any
test module loads. Run the gate with ``python3 tools/run_suite.py``.
"""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
# scenarios/ is on the path so `from harness import ...` resolves by itself. It previously
# worked only after something imported scenarios.run, which inserts the same directory
# (scenarios/run.py:34); importing the harness by its dotted name instead creates a second
# module object and breaks identity-sensitive phase guards.
SCENARIOS = ROOT / "scenarios"
for _p in (str(ROOT), str(TOOLS), str(SCENARIOS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Every `git commit` (also merge, fetch, a push's receive-pack) starts
# `git maintenance run --auto --detach`, which outlives the command. On CI's Git
# 2.55 it repacks once two loose objects share the objects/17 shard, writing
# under .git while a test deletes or copies the repository ("Directory not
# empty"). Configuration from GIT_CONFIG_COUNT outranks every config file and
# reaches every git process a test starts, including those AutoCode starts in
# a test's repository, so no test-created repository starts maintenance.
# Two exceptions set GIT_TEST_CONFIG in the repository instead: a subprocess
# given an environment built from scratch, and a local repository a test
# pushes into (git starts its receive-pack without GIT_CONFIG_COUNT).
GIT_TEST_CONFIG = {"maintenance.auto": "false", "gc.auto": "0"}
_count = int(os.environ.get("GIT_CONFIG_COUNT") or 0)
_inherited = dict(
    (os.environ.get(f"GIT_CONFIG_KEY_{i}"), os.environ.get(f"GIT_CONFIG_VALUE_{i}")) for i in range(_count)
)
for _key, _value in GIT_TEST_CONFIG.items():
    if _inherited.get(_key) != _value:  # the last entry for a key wins, as in git
        os.environ[f"GIT_CONFIG_KEY_{_count}"] = _key
        os.environ[f"GIT_CONFIG_VALUE_{_count}"] = _value
        _count += 1
os.environ["GIT_CONFIG_COUNT"] = str(_count)

# A handful of tests execute a real shell to prove POSIX quoting semantics
# (Codex/OpenCode wrap recorded commands as `<login-shell> -lc '...'`, and
# same_command() has to match that verbatim). zsh is macOS's default login
# shell and the closest match to what those providers actually record, but
# it isn't installed on every CI runner or contributor machine (notably
# most Linux distros), so fall back to another real POSIX shell rather than
# hard-coding a path that may not exist. All of `-lc` and quoting-error-code
# behavior is standard across zsh/bash/dash for what these tests check.
LOGIN_SHELL = shutil.which("zsh") or shutil.which("bash") or shutil.which("sh")
