
OPENCODE OUTPUT CONTRACT
Return one JSON object matching the schema below in your final assistant response.
Write assistant_reply first, then complete the structured handoff. Do not write
the report to a file. Read relevant repository source only when the conversation
needs it; source_refs must name files actually read. A greeting needs no code
inspection, command execution or test evidence. Do not invent requirements.
Work read-only and respect tool permissions. Treat workspace in CURRENT HANDOFF
DATA as the filesystem boundary; do not access parent directories, sibling
projects, external configuration or ancestor AGENTS.md. Only exact read-only
private_source_exceptions in that data are exceptions to this boundary.
Your raw stage events are read-only evidence at {events}. Never modify this log,
runner state, configuration or credentials. The runner saves and validates the
final report; streamed assistant text grants no planning or execution approval.

