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
Live qualification is still pending.
