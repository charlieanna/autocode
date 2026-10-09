# Task verification can select a Python without project dependencies

A linked task worktree may be recorded as its own project workspace. Since Git
worktrees omit ignored virtualenvs, the old verification fallback selected the
system Python. On macOS this could lose `psutil` and produce import errors in
otherwise working tests. Fixtures launching `python3` could also use a different
environment from their parent test process.

Verification now locates the main checkout through Git's common directory and
inherits its virtualenv when the task has none. A task-local virtualenv still
wins. Scratch comparisons link that same virtualenv and give child commands its
`bin` directory on PATH. Base and candidate retain the same dependency source.

The cached regression proof records its interpreter, explicit commands and
timeout in `execution_context`. A context change requires new runner evidence;
a previous verdict cannot be reused solely because the source is unchanged.
Model routes and contract requirements do not participate in this environment
selection.

Coverage uses a real linked worktree and a dependency present only in the main
virtualenv, including a fixture that launches `python3`. A second check covers
local environment precedence. The existing proof test now changes the selected
interpreter without deleting the saved proof or changing source.
