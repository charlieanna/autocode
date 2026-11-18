"""Git subprocesses used to prepare proof trees, with distinct source-read failures."""

import subprocess
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def command(cwd, *args, check=True, env=None, input=None):
    git_env = util.git_environment() if env is None else env
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace", env=git_env, input=input
    )
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def read(cwd, *args, check=True, env=None):
    """A required source query failing establishes no fact about a model answer."""
    return util.source_git(cwd, *args, text=True, check=check, env=env)


def scratch_env(workspace, scratch, *, keep_objects=False):
    env = util.git_environment({"GIT_INDEX_FILE": str(Path(scratch, "index"))})
    if not keep_objects:
        objects = Path(workspace, read(workspace, "rev-parse", "--git-path", "objects").strip()).resolve()
        obj_dir = Path(scratch, "objects")
        obj_dir.mkdir()
        env.update(GIT_OBJECT_DIRECTORY=str(obj_dir), GIT_ALTERNATE_OBJECT_DIRECTORIES=str(objects))
    return env
