# Retained work after a repair assignment

The visual-evidence live run exposed a recovery loop: a Builder left working
source but timed out before its report; the Resolver then issued a new task ID
for the same milestone and scope. The next Builder preserved the source and
submitted a report, but AutoCode compared only attempts bearing the new task ID.
It counted the unchanged tree as no progress and never reached a fresh Validator.

An unchanged repair can now use the earliest Builder snapshot of an archived
assignment with the same approved contract, milestone and owned paths. Scope
checks still cover that entire retained delta, and the saved after-snapshot must
match the current source. Missing evidence or changed ownership refuses this
route. This grants fresh validation, never acceptance or a previous PASS.

The CLI regression injects an initial review rejection, creates a repair task,
and has its Builder preserve the candidate. The valid candidate must reach a
second Validator and complete; a broken candidate must remain incomplete.
Pure tests cover contract, milestone, scope, source and missing-snapshot guards.

The unchanged live candidate reached a fresh Validator after this fix. AutoCode
recorded the older assignment as its source, ran new regression proof, obtained
independent PASS for all ten criteria, and reached `TASK_COMPLETE` on 2026-10-03.
The old implementation fails the same CLI regression at completion; the repaired
implementation passes, while its broken-candidate control stays incomplete.
