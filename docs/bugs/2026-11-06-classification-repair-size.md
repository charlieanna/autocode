# Duplicated evidence overflows classification report repair

An Investigator classifying a failed test-evidence gate received the preceding
Tester report as its diagnosis context. Its report-only repair copied that entire
report again, in addition to the rejected Investigator report and current contract.
Large changed-code reviews therefore exceeded the existing repair prompt limit
before any provider launched.

The report-only context now omits that duplicate diagnosis. It keeps the exact
failure identity, binding, record and citation allowlist, with full original
evidence unchanged in the saved request and pinned files. The Investigator report
being repaired remains inline. Classification repair file mappings follow the
narrower failure-evidence allowlist; unrelated repository files are not offered as
valid classification citations. A repair may not invent new facts or executions.

The existing token-bound continuation can recover an older classification hold
caused by either complete-prompt or provider-decorated prompt size checks. It
retries only the retained bounded repair after a renderer correction, preserving
all attempts, source and evidence checks. An oversized source report, changed
artifact or other admission failure does not get this continuation.

Regression tests reproduce the oversized handoff and public recovery failure on
base, then check bounded rendering with exact identities and public CLI completion.
The complete live workload remains a separate, unfinished qualification.
