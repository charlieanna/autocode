# Status labels batch

The requested handoff was not present in the branch or accessible project files. The branch contains changes for #3924, #3864, #3853, and work in progress for #3927.

## Verification in progress

- Fixed two mypy errors in the retained retry decision at `bdd94ce1a`, pushed to this branch.
- `ruff check tools/ tests/ scenarios/`, `ruff format --check tools/ tests/ scenarios/`, and `mypy tools/` passed.
- The dedicated retry suite passed (28 tests), as did progress and recovery-history tests after merging master (60 tests). The VS Code extension suite passed (756 passed, 22 skipped). Four targeted progress and retry regressions failed by assertion on master `73e7e284e1` and passed on this branch.
- The broader Python run is in progress outside the sandbox. The sandboxed attempt could not enumerate processes.
- Draft PR #3970 is open. Current master was merged, preserving both sides of a test conflict. Recheck CI, the exact final diff, issue scope and the tests after the merge before marking it ready.
