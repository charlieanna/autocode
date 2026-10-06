# Approved deliverables under Git ignore rules

A Builder could create the requested document under an ignored `docs/` directory,
while AutoCode recorded an unchanged source revision, an empty changed-file list,
and an empty review diff. The valid deliverable was treated as no progress and
another Builder was scheduled. Clean verification copies also omitted the file,
and editing it did not invalidate those copies.

The source inventory now includes literal files and directories declared in the
explicitly approved plan's milestone and initial-task `affected_paths`. All
milestones remain in scope after completion. Files outside that approved scope
retain the usual Git visibility rules. A Builder report or a current-task path
suggestion cannot expand the inventory.

This inventory is shared by progress detection, source freshness, clean replay,
verification copies, review diffs, and workflow-job recovery. Nested Git
repositories retain their own source identities and receive the corresponding
relative selection. Their selected files are included in verification copies
and replay; nested review diffs carry repository-relative prefixes. Existing
restrictions on restoring submodules as code checkpoints and dispatching them
to parallel Builders remain in force.

The user's index, HEAD and ignore rules are not changed. Review diffs and code
checkpoints use separate temporary indexes. Editing an approved ignored output
after completion makes the saved completion evidence stale.

For a task that must deliver an ignored file, name it in the plan's affected paths
before approval. An empty path list does not authorize scanning every ignored
file or dependency directory. Absolute paths, traversal, runner metadata,
unbounded paths and patterns are not source selections.

Coverage is in `tests/test_source_snapshot.py` and
`BuildBlackbox.test_ignored_document_completes_and_edit_invalidates_completion`.
The latter uses the public CLI and checks completion with one Builder, preserved
Git state, and stale evidence after a later document edit. These deterministic
checks supplement the live qualification; they do not stand in for model runs.

## Qualification (2026-10-06)

Before the fix, two real Sol Builder attempts created the correct ignored
checklist, but AutoCode recorded no changed files, identical source revisions
and an empty diff. A third attempt stopped on a separate containment startup
failure; this reproduces false no-progress twice, not a final retry-limit stop.

After integration with master at ba3fd4bb, native OpenCode 1.18.33 with Sol for
requirements/planning/building and Astra for review/testing/completion reached
TASK_COMPLETE in one Builder attempt and 452.78 active seconds. Run:
`20261005-233144-create-docs-rollout-checklist-md-as-the-only-del-91e1d01b`
under `.scenario-runs/verification-508/openai-after-r1`. The original caps were
1800 active seconds, 360 stage seconds, 120 idle/tool seconds and four iterations.
The approved ignored document appeared in the snapshot, changed-file list,
review diff and verification copy. All 30 terminal audit checks passed,
including native streams, source pins, protected seed files and Git state.

A separate eight-check challenge edited the completed ignored document:
public `--status --inspect-evidence` changed completion_current from true to
false with stale evidence, then back to true after restoring the original bytes.
No model stream or saved run state changed during this challenge.

Supplemental gates passed: 4095 tests across 286 modules before integration;
1330 tests across 67 modules for the integration delta; and the fake catalog
with 60 PASS, one existing NOT_EXERCISED and one live-Investigator SKIPPED.
The earlier full-suite run was not clean: 4620 tests across 314 modules had
seven failing modules (process, recovery novelty, visual checks, risk protocols,
build blackbox, build recovery blackbox and catalogue T12). Failures included
subprocess timeouts and process-observation races; isolated checks passed in
several cases, but their causes are not all established. Those original failures
are retained and the later gates are not represented as a full-suite pass.
The live before and after revisions include intervening master changes, so this
is not a single-commit controlled comparison or qualification of every scenario.
