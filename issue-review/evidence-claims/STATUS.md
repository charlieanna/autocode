# Evidence claims batch

The branch began at `6519d439a`. The previously referenced handoff was not present in the branch or accessible project files. The commit identifies issues #3940, #3939, #3904, #3898, and #3947.

## Verification in progress

- `ruff check tools/ tests/ scenarios/` and `ruff format --check tools/ tests/ scenarios/` passed.
- `mypy tools/` passed.
- Dedicated claim tests passed (13 tests). The VS Code extension suite passed (751 passed, 22 skipped). Two targeted regression tests failed by assertion on master `73e7e284e1` and passed on this branch.
- The broader Python run is still in progress. The sandboxed attempt could not enumerate processes and was repeated outside the sandbox.
- Draft PR #3971 is open and CI is running.
- Plan review now distinguishes a model-reported current-behavior statement from an exact matching Investigator report with a recorded probe. Both are identified as claims not independently verified. A successful probe cannot establish every claim in the Investigator's prose, so this avoids presenting collect-only as a verified full-suite baseline (#3940). Tests fail on master and pass here. Collection and execution evidence for #3939 and #3940 still needs a shared mechanism.
- The Requirements card identifies an inferred project convention as a report that has not been independently verified or approved. This makes the source of a suite-collection claim visible (#3939) while the collection evidence mechanism remains outstanding.
- The reference check only applies to criteria that explicitly say references resolve. The reported #3947 task asked for a linked executable page without such a criterion, so the exact incident is not yet shown fixed. A clean comparison is recorded as unverified and still admitted. Resolve that gap before claiming #3947.
- The current claim reader accepts cited JSON by path without authenticating the capture event or its run/source binding. Reuse the existing receipt authentication and replay contracts before treating a claim as established or contradicted.
- Diagnostics comparison now normalizes source line/column locations only, preserving numbers in diagnostic messages. The previous branch falsely treated `BROKEN ref1` and `BROKEN ref2` as the same warning; the new capture regression passes here and the earlier implementation fails it. This corrects comparison data but does not resolve the separate authentication and semantic-coverage gaps above.
- The focused Python suite passed 115 tests with one skipped outside the sandbox. Architecture and run-state checks passed 34 tests.
