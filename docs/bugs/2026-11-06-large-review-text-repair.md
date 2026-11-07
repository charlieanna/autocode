# Large completed review text repair

A completed review with hundreds of required per-line decisions can exceed
128 KiB. If it contains a small JSON syntax error, the repair input reader used
to refuse it despite the existing 512 KiB artifact ceiling. Review stages also
could not use the bounded text-edit protocol available to planning stages, and
a JSON fence prevented that protocol from being offered.

Complete inputs and reconstructed results now share the existing 512 KiB hard
bound. Review stages may receive a text-edit offer for malformed object text or
one JSON fence with a short prose prefix. This only offers an operation: the
runner does not remove the prefix, unwrap the fence, or correct JSON itself.
Every correction must name a unique original span in the hash-pinned source.
The existing 32-edit and 4096-byte total change limits remain in force.

The reconstructed report still passes the normal schema, provenance,
authenticated check, changed-code evidence and protected-contract gates.
Truncated responses, oversized artifacts, ambiguous wrappers, stale hashes,
overlapping edits and unoffered patches remain ineligible or rejected.

Regression coverage includes a large per-line report preserved by a tiny patch,
the unchanged hard byte ceiling, malformed/wrapped review stages, refusal cases,
and a public CLI run that completes after three text edits while preserving the
original authenticated checks and raw response.
