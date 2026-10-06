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
