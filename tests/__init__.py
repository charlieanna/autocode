"""AutoCode test package.

The runtime lives in tools/ and is imported by its top-level module names
(``import autocode``), so this package puts tools/ on sys.path before any
test module loads. Run the gate with ``python3 tools/run_suite.py``.
"""

import atexit
import os
import shutil
import sys
import tempfile
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

# The terminal chat turns the warm source host on for the processes it starts
# (#1707). That host outlives the command by design; a test that starts a chat
# would leave one running for 30 idle minutes. Tests run without it unless a
# test turns it on itself (tests/test_source_host.py).
os.environ.setdefault("AUTOCODE_SOURCE_HOST", "0")

# A run started without AUTOCODE_HOME records its project in the per-user project
# index (~/.autocode/projects.json, #3265). Every test, and every process it starts
# with this environment, writes an index of its own instead, so the suite never
# lists a test's temporary project among the person's chats. A test that needs
# the index names its own AUTOCODE_PROJECT_INDEX_HOME.
if "AUTOCODE_PROJECT_INDEX_HOME" not in os.environ:
    os.environ["AUTOCODE_PROJECT_INDEX_HOME"] = tempfile.mkdtemp(prefix="autocode-test-projects-")

# A new run takes its provider and the person's settings from AUTOCODE_PROVIDER
# and $XDG_CONFIG_HOME/autocode (default ~/.config): config.toml, providers/ and
# direct/. A developer's own choice there (default_provider = "kilocode") changed
# what 55 test modules, and the autocode processes they start, did on that
# machine only (#1933, #1938). Every test, and every process it starts with this
# environment, reads an empty config home instead; a test that needs a user
# config writes one under its own XDG_CONFIG_HOME. As with Git above, a
# subprocess given an environment built from scratch needs its own. A process a
# test starts may import this package too (a fixture provider does): it inherits
# the marker and keeps the XDG_CONFIG_HOME that test chose for it.
USER_CONFIG_MARKER = "AUTOCODE_TEST_USER_CONFIG_HOME"
# Where git says the repository is, whatever directory a command names (see below).
GIT_REPOSITORY_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)
if USER_CONFIG_MARKER not in os.environ:
    _config_home = tempfile.mkdtemp(prefix="autocode-test-config-")
    os.environ[USER_CONFIG_MARKER] = os.environ["XDG_CONFIG_HOME"] = _config_home
    os.environ.pop("AUTOCODE_PROVIDER", None)
    # A chat starts every process with AUTOCODE_SOURCE_CATALOG_HOME naming the
    # person's shared source catalog, and that name wins over AUTOCODE_HOME.
    # Tests isolate the catalog with their own AUTOCODE_HOME, so a suite run from
    # a chat (a self-build's Tester) or a shell that exported it added each
    # test's temporary folder to the person's catalog as a source root. Once the
    # folder was gone every new chat on that computer stopped at Requirements with
    # root_unavailable. A test that needs the variable sets it itself.
    os.environ.pop("AUTOCODE_SOURCE_CATALOG_HOME", None)
    # Git exports the repository it is working on to every hook and to each command of
    # `git bisect run` and `git rebase --exec`: GIT_DIR and its companions, which outrank
    # `git -C` and the working directory. A suite started there handed them to every
    # fixture's git, so `git init`, `git config` and `git commit` meant for a temporary
    # repository ran in the developer's own: a bisect of two test modules left this
    # checkout with core.bare = true, a fixture's user.name and its commits. A test that
    # needs one of these sets it for the process it starts, which keeps it.
    for _name in GIT_REPOSITORY_VARIABLES:
        os.environ.pop(_name, None)

    def _remove_user_config_home(path=_config_home, owner=os.getpid()):
        if os.getpid() == owner:  # not a forked test process exiting
            shutil.rmtree(path, ignore_errors=True)

    atexit.register(_remove_user_config_home)


# A handful of tests execute a real shell to prove POSIX quoting semantics
# (Codex/OpenCode wrap recorded commands as `<login-shell> -lc '...'`, and
# same_command() has to match that verbatim). zsh is macOS's default login
# shell and the closest match to what those providers actually record, but
# it isn't installed on every CI runner or contributor machine (notably
# most Linux distros), so fall back to another real POSIX shell rather than
# hard-coding a path that may not exist. All of `-lc` and quoting-error-code
# behavior is standard across zsh/bash/dash for what these tests check.
LOGIN_SHELL = shutil.which("zsh") or shutil.which("bash") or shutil.which("sh")
