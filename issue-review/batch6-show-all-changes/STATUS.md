# Show all changes handoff

The starting handoff file was unavailable in the branch tree and project workspace. The branch commit `389f9c88ae` contains the previously completed changes for #3945, #3933 and #3871.

- Added type narrowing for the existing checkpoint implementation to resolve ten mypy errors without changing its intended behavior.
- The extension tests passed after merging master, with 766 passing and 22 skipped. Ruff and mypy pass.
- Three focused regressions on master `73e7e284e` produced two missing-file assertion failures and one error.
- The first sandboxed process-supervised suite could not enumerate provider processes. An unsandboxed focused and contract run is in progress. PR #3969 is open and awaiting `ci-required`.
