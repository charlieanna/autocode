# Common Autocode orchestration driver

The code and Figma workflows now use `autocode_orchestrator.drive` for their saved
stage loop. This removes the independent Figma `for` loop. Both paths select the
next saved stage, dispatch it, apply target-specific validation and transitions,
persist the checkpoint, and continue or stop through the same driver.

The code adapter retains its existing approval, intervention, timeout, report
repair, milestone and evidence gates. The Figma adapter retains its stricter
same-file review, Sol PASS plus Astra ACCEPT requirement, bounded rework, artifact
hashing and optional build handoff. `autocode-ui` remains a thin installed alias
for `autocode ui`.

Regression coverage verifies shared driver identity, transition ordering, durable
persistence, recovery skips, missing-stage rejection, Figma rework and acceptance,
and representative normal code and milestone flows. Provider-dependent tests use
the existing explicit fake provider.

Validation results:

- Full package-aware Python suite: 379 tests passed.
- Dashboard Python suite: 174 tests passed.
- Shared driver, Figma and worktree focus: 20 tests passed.
- Intervention, report-repair and unlimited-iteration focus: 37 tests passed.
- Installed-wheel Figma handoff and worktree focus: 17 tests passed.
- The 0.6.1 wheel loads the exact same driver object from both command paths, and
  `autocode ui --dry-run` completed all four expected stages.
