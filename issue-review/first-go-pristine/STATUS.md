## First Go project verification

The first Go project scenario can discover the live candidate's ancestor module
when the new tests run in the nested original-source copy. The first-project
policy takes its absence evidence from the separate pristine baseline and
requires passing named candidate tests. The regression fails on master because
it still launches the nested original-side suite.

The original #4071 head `88ac429cb` passed the real Linux Extension Host and
scenario jobs. Its required check failed with one Ubuntu assertion in
`test_scoped_explicit_go_command_cannot_use_unscoped_first_suite_allowance`.
The failure reproduced locally. A separate task worktree cannot provide an
in-place manifest, and a module-less package names itself using the actual
scratch path. The correction checks the prepared worktree against its base
index and intended overlays, and attributes those native package names to
the changed test functions. Command capture keeps its durable path check
when the host spells the same temporary directory through a system alias.

The final focused selection ran 85 tests successfully, with six platform
skips. Full Ruff, format and mypy passed. The updated
editor fixture uses a real task worktree, verifies the exact reviewed
approval, and requires its completion card and runner proof. Its updated
host evidence and required CI are pending. The shared proof-read boundary
and protected-link replay in #3981 remain separate.

The actual macOS retry exposed an environment issue in the host harness:
an editor child selected the installed provider after the fixture's login
PATH changed. The test was stopped and its fixture-owned source hosts were
closed. The extension host wrapper now validates the exact local script and
its isolated configuration before invoking AutoCode. Two focused controls
cover the login PATH conflict and refusal after a fixture script changes.
The new worktree editor check needs fresh qualification with this guard.
