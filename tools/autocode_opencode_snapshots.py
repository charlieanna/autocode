"""Which launches record native OpenCode 1.x Git snapshots, and what those snapshots leave out.

OpenCode (and Kilo, an OpenCode fork) records a Git tree of the workspace at each step. The
read-only guard (autocode_readonly_events) pauses a read-only stage whose recorded snapshots
changed. No provider is launched, and no Git index or user exclusion is changed here.
"""

from __future__ import annotations

# What a native snapshot must leave out, as the runner's own source snapshot does (autocode_util.snapshot): run
# state, the dashboard's state and Python bytecode, each a path a stage may add mid-run without touching source.
# .autocode/ carries its own .gitignore (autocode_workspaces.keep_out_of_git); bytecode has nothing of the kind.
EXCLUDES = ("/.autocode/", "/.autocode-ui/", "__pycache__/", "*.pyc")


def records_native_snapshots(engine, output_mode) -> bool:
    """Whether a launch's events carry OpenCode-format Git snapshots the runner reads.

    The one predicate behind the read-only guard (autocode_readonly_events.assert_unchanged_review) and the
    launch-time exclusions, so the two cannot disagree: Kilo and every other opencode_events tool count, a
    report_file tool and Codex do not. Before this, the guard judged every such launch while the exclusions
    were installed for the built-in OpenCode engine only (#1630)."""
    return engine == "opencode" and (output_mode or "opencode_events") == "opencode_events"


def _git_config_count(env) -> int:
    try:
        return max(int(env.get("GIT_CONFIG_COUNT", "0")), 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def git_config_env(env) -> dict:
    """The GIT_CONFIG_COUNT/KEY_n/VALUE_n entries of ``env``, kept whole: git refuses a partial set."""
    count = _git_config_count(env)
    if not count:
        return {}
    names = ["GIT_CONFIG_COUNT"] + [f"GIT_CONFIG_{part}_{i}" for i in range(count) for part in ("KEY", "VALUE")]
    return {name: env[name] for name in names if name in env}


def excludes_env(path, env) -> dict:
    """Environment entries that make every git the provider runs read ``path`` as core.excludesFile.

    A snapshot repository is a separate git dir with core.worktree set, so it reads its own info/exclude and never
    the project's; the environment is the one setting every git invocation honors, including the provider's own
    snapshot ``add``. Appended after any GIT_CONFIG_* set the environment already carries, so a later entry wins."""
    count = _git_config_count(env)
    return {
        "GIT_CONFIG_COUNT": str(count + 1),
        f"GIT_CONFIG_KEY_{count}": "core.excludesFile",
        f"GIT_CONFIG_VALUE_{count}": str(path),
    }
