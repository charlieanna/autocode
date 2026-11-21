# Usage accounting handoff

The starting handoff file was unavailable in the branch tree and project workspace. The branch commit `6122e828aa` contains the previously completed usage, preflight and store-concurrency changes for #3903, #3867, #3888 and #3858.

- Focused tests passed on the branch: 102 tests across concurrent user stores, preflight, route identity and usage grouping.
- Two route-grouping regressions fail on master `73e7e284e` and pass on this branch.
- Ruff check and format check passed for `tools/ tests/ scenarios/`. Mypy passed for all changed runtime modules.
- The process-supervised focused and architecture run passed 120 tests. PR #3968 is open and awaiting `ci-required`.

## 2026-11-21 follow-up

PR #3968 merged the WIP commit before the AutoCode run finished. The run (plan r4) then completed
"Complete, and verified against the current checkout": its Tester and Completion Reviewer passed with
no product change beyond #3968. The follow-up PR adds the Tester's extra regression tests for
overlapping saves and for probe stop sentences, and the CHANGELOG entry.
