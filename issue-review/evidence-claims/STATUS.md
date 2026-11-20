# Evidence claims batch

The branch began at `6519d439a`. The previously referenced handoff was not present in the branch or accessible project files. The commit identifies issues #3940, #3939, #3904, #3898, and #3947.

## Verification in progress

- `ruff check tools/ tests/ scenarios/` and `ruff format --check tools/ tests/ scenarios/` passed.
- `mypy tools/` passed.
- Focused Python tests and the VS Code extension suite are running. The sandboxed Python attempt was unable to enumerate processes. It is being repeated outside the sandbox.
- Verify regression tests against current master, review scope against each issue, open a PR and follow required CI through merge.
