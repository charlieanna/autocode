
OPENCODE OUTPUT CONTRACT
Review report only.
Return exactly one JSON object matching the complete schema below in your final
assistant response. Do not write the report to a file; the runner saves and
validates it. Record commands actually executed in tests_run and report findings
under this schema. Do not invent acceptance-check fields or capture receipts:
this report has no build-delivery checks or criterion evidence fields. Ordinary
tool execution records the checks; no extra capture invocation is required for
tests_run. Preserve the review instructions for scratch work, delivered tests
and executable blocking findings; the runner independently proves those findings.
Respect tool permissions. Treat workspace in CURRENT HANDOFF DATA as the strict
filesystem boundary: do not access parents, siblings, external configuration or
ancestor AGENTS.md. Only exact private_source_exceptions workspacePath records
permit additional read-only source access; preserve their canonicalPath, sourceId
and SHA-256 identity, and do not read the external canonical location.
Your raw stage events are read-only evidence at {events}. Never write, truncate,
replace, delete, chmod or repair that log, runner state, configuration or credentials.
