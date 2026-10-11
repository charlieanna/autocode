# Report preparation must stay in run artifacts

A real Go Arena requirements revision wrote its JSON payload and a small
validation script in the repository root before copying the report to its
assigned output path. Those temporary files changed the source snapshot, so
AutoCode correctly paused the read-only stage. The stopped attempt remains a
non-pass.

Registered report-file providers now receive a distinct directory beside each
assigned report for temporary report payloads and validation scripts. Reporting
artifacts must remain there and must not be staged or committed. The existing
source-change check continues to apply.

Native Codex persistence and event-output providers retain their existing report
protocols. Prompt coverage checks absolute paths with spaces, relative artifact
paths, native persistence and event output. Fresh real-provider qualification is
still required for this addition.

A subsequent original Go trial exposed a report-repair conflict: its generic
instructions forbade modifying files, but its report-file provider required a
JSON write. The model emitted JSON as its final response without creating the
assigned report. Repair now forbids repository source edits while explicitly
following the provider's output contract: write the assigned report when
required, or return JSON for native persistence. Reporting scratch stays in
the assigned artifact directory. This stopped trial remains a non-pass.
