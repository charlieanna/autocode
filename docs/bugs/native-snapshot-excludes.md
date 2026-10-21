# Native snapshot excludes never reached the snapshot repository

A read-only stage on Kilo that ran `python -m unittest` in the workspace was paused with
`PAUSED_STALE_VALIDATION` twice on 2026-10-21 (#1630 investigation). The two trees its
events recorded differ by exactly `A __pycache__/pqueue.cpython-314.pyc`.

## Cause

`prepare_opencode_snapshots` appends `/.autocode/`, `/.autocode-ui/`, `__pycache__/` and
`*.pyc` to the project's `.git/info/exclude` (#354). A provider's snapshot repository is a
separate git dir under `<data>/<app>/snapshot/<project>/<hash>` with `core.worktree` on the
project, and git reads `info/exclude` from its own git dir, never the project's. The rules
never reached any snapshot repository, OpenCode's included. `.autocode/` stayed out only
because `autocode_workspaces.keep_out_of_git` gives it a `.gitignore`, which is a worktree
file the snapshot does read; bytecode has nothing of the kind.

Two rule sources also disagreed about when they apply, the pattern `docs/plans/stage-access.md`
describes: `assert_unchanged_review` judged every launch whose events carry OpenCode-format
snapshots (`engine == "opencode"` and `output_mode == "opencode_events"`, so Kilo too), while
the exclusions ran only for `engine == "opencode" and not configured_tool`, which leaves Kilo out.

Checked by: a bare repository with `core.worktree` on a project whose `info/exclude` holds the
rules does not ignore `pkg/__pycache__/a.pyc` (`git check-ignore`); with
`GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.excludesFile GIT_CONFIG_VALUE_0=<file>` in the
environment it does. Kilo's two snapshot repositories from the paused runs have an empty
`info/exclude`.

## Fix

- `autocode_opencode_snapshots.records_native_snapshots(engine, output_mode)` is the one
  predicate; the guard and the launch-time exclusions both read it.
- `autocode_readonly_events.snapshot_excludes` writes `<workspace>/.autocode/snapshot-excludes`
  (the user's global ignore rules first, since `core.excludesFile` replaces git's default, then
  the runner's four) and returns the `GIT_CONFIG_*` entries that name it. `launch_excludes`
  computes them before the provider environment is built, so an isolated launch's allowlisted
  environment carries them (both isolation layers pass the `GIT_CONFIG_*` set through whole),
  and a plain launch merges them in `prepare_opencode_snapshots`. The project's `info/exclude`
  is still written: the runner's own git-based checks read it.
- Live check, Kilo 7.8.12 with `zai-coding-plan/glm-5.3` low on a project holding
  `pkg/__pycache__/a.cpython-314.pyc`: with the environment set, the tree Kilo recorded holds
  only `pkg/a.py`.
