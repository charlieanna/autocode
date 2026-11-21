# Status labels batch

The requested handoff was not present in the branch or accessible project files. The branch contains changes for #3924, #3864, #3853, and work in progress for #3927.

## Verification in progress

- Fixed two mypy errors in the retained retry decision at `bdd94ce1a`, pushed to this branch.
- `ruff check tools/ tests/ scenarios/`, `ruff format --check tools/ tests/ scenarios/`, and `mypy tools/` passed.
- The dedicated retry, progress, and recovery-history suite passed after merging master (90 tests). The VS Code extension suite passed (768 passed, 22 skipped). Four targeted progress and retry regressions failed by assertion on master `73e7e284e1` and passed on this branch.
- A broader Python run passed 292 tests on the earlier branch head. The sandboxed attempt could not enumerate processes and was repeated outside the sandbox.
- The bounded Investigator still handles eligible failures and directs test-cause advice to a fresh Tester. Exhausted internal recovery now reports its blocked outcome without requesting machinery feedback. Two tests fail on master and the prior branch head and pass with this correction.
- PR #3970 is open. CI on `3f2a6d2a2` failed `test_chat_client.ExhaustedRecheckTests.test_ac22_terminal_chat_offers_feedback_at_exhausted_recheck` because the new terminal test still expected the machinery feedback action removed above. The test now checks the blocked outcome and takes no feedback or resume. It fails on the prior branch and passes with the correction. Rerun the affected suite and require fresh CI before merge.
