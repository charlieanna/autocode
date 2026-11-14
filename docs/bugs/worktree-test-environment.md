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

## Launcher metadata must not invalidate a resumed proof (#3549)

An operating Codex session gives each tool invocation a new `CODEX_TOOL_CALL_ID`.
In live verification of #3233, that was the only environment difference across
two otherwise identical invocations, yet resuming an unchanged partial mutation
study started the baseline again. The execution identity hashed this inherited
launcher marker as though it were a test input.

`test_environment` now withholds that marker from the actual command environment.
The identity still hashes the complete environment tests receive; ordinary test
variables continue to invalidate both the baseline and the candidate proof.
Existing opaque hashes are not migrated or accepted under a weaker comparison.
The regression executes a proof, serializes its saved state and resumes under a
different tool-call ID without another baseline or proof execution, then changes
a real test variable and verifies that both execute again.
