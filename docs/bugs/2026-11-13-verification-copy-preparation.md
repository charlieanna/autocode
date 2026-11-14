# Verification-copy preparation failed before the Tester

The actual VS Code offline trial on Core
`327a0ddd2c68e71637379e7a4055ad6b93235821` reached its reviewed fourth plan
revision, Builder and runner regression proof, then paused before launching the
Tester. Preparing its verification copy raised Git exit 128. The public reason
included the fast-import command and exit code but discarded its captured
stderr, so the original cause remains unknown. The failed attempt and approval
are retained in bundle manifest
`cc5b5d5d9a3ebb7cbe45cdcc505bf324e393c1dc8ef9ebacd89c493d0741d589`.

A separate, disposable paired diagnostic used the same runtime helper and
public product bytes on the pinned Linux image. The native Linux copy passed;
the Mac bind copy failed later while retaining Git history, with a captured
"dubious ownership" diagnostic. Its identical fast-import stream passed. That
establishes a filesystem-boundary problem in the diagnostic, not the cause of
the original fast-import failure. It does not authorize a Git trust exception,
blind resume or removal of verification checks. Editor fixtures need their
actual project and Git storage on supported native Linux storage.

Verification preparation opts into captured subprocess diagnostics explicitly.
Bytes decode with replacement for invalid UTF-8; the existing credential
redactor runs before the 2,000-character limit and truncation marker. Other
formatter callers retain their existing behavior, including ENOSPC guidance.
The preparation diagnostic names a full filesystem without advising a person
to resume a terminal failure or an unresolved hold.
Only actual captured stderr is included; stdout and arbitrary error prose do
not become subprocess evidence.

The collected pre-provider failure and cleanup identity travel as a typed fact,
separate from the diagnostic text. Only a current source/task/contract and
confirmed cleanup with no unsettled workers can end as `could_not_finish`.
Unknown cleanup or work remains blocked. Neither outcome asks a person to retry
AutoCode's machinery, and neither grants completion authority. Genuine source
drift keeps its stale-validation meaning. The thin client reads the Core's
outcome; a missing next action does not mean the task completed.

The regression injects a collected Git failure before Tester launch and checks
its public diagnostic, redaction, retained source and exact approval. A paused
or failed run is not a completed task, and this diagnostic improvement does not
establish an actual editor rerun or a successful delivery.
