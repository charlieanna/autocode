# Status labels batch

The requested handoff was not present in the branch or accessible project files. The branch contains changes for #3924, #3864, #3853, and work in progress for #3927.

## Verification in progress

- Fixed two mypy errors in the retained retry decision at `bdd94ce1a`, pushed to this branch.
- `ruff check tools/ tests/ scenarios/`, `ruff format --check tools/ tests/ scenarios/`, and `mypy tools/` passed.
- Focused Python tests and the VS Code extension suite are running. Sandboxed tests could not enumerate processes, so they are being repeated outside the sandbox.
- Verify new regressions against current master, review scope against each issue, open a PR and follow required CI through merge.
