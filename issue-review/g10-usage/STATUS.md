# Usage accounting handoff

The starting handoff file was unavailable in the branch tree and project workspace. The branch commit `6122e828aa` contains the previously completed usage, preflight and store-concurrency changes for #3903, #3867, #3888 and #3858.

- Focused tests passed on the branch: 102 tests across concurrent user stores, preflight, route identity and usage grouping.
- Two route-grouping regressions fail on master `73e7e284e` and pass on this branch.
- Ruff check and format check passed for `tools/ tests/ scenarios/`. Mypy passed for all changed runtime modules.
- A process-supervised focused and architecture run is in progress, followed by PR and `ci-required` verification.
