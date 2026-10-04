# Paused source edits and stale report repairs (#302)

After an operator edits the source of a paused run, a queued report repair
restates a report about the old source, so it can never run. Before this fix
every resume refused it ("Saved report-repair inputs changed; do not retry")
and the only way out was editing `state.json`.

An explicit `--resume-paused` now archives such a repair (`report_repair_archive`,
evidence intact), rotates the role's provider session, which judged the old
source, and starts a fresh attempt of the same stage on the current source. It
applies only when the source is the one thing that moved: the goal, the pinned
evidence and the next stage are unchanged, no attempt is active, the run is
`RUNNING` or `PAUSED_*`, and no AutoResolver operational request is published
or queued.

The last condition was added after review. The first version also withdrew a
published request when it archived, and in chat mode that let `--resume-paused`
cancel an `operational_exhaustion` request and launch the Validator, where
`--no-chat` (rightly) held. Archiving also changes the frontier the request is
bound to, so it would make the displayed token stale.

`--accept-completion` with a validation of another source, goal revision or task
now says which one and to resume to re-validate.

## Still open

A repair whose attempts ran out the same way twice pauses the run as
`PAUSED_REPEATED_FAILURE` and keeps the repair. Any invocation without a
recovery flag then publishes an `operational_exhaustion` request. If the
operator edits the source to fix the cause, there is no way forward:

- `--resume-paused` holds: "AutoResolver retained the operational request".
- After answering the request (`--resolver-response provide_information`),
  `--resume-paused` holds: "AutoResolver retained the human guidance".
- `--retry-failed-stage` is refused: "Reconcile the active attempt or pending
  report repair before authorizing a retry". Without the repair it would be
  refused anyway, since the failure is no longer at the current source.

Letting `--resume-paused` pass a published operational request changes what
that command authorizes, which is the operational-exhaustion decision left
open in #288/#301. `tests/test_paused_source_edit.py` builds this state
(`exhausted_report_repair_published_after_a_source_edit`).
