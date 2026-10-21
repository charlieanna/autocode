
OPENCODE OUTPUT CONTRACT
Planning report only.
Return exactly one JSON object matching the complete schema below in your final assistant
response. The runner saves and validates it; do not write a report file. Planning reviews
proposed behavior, not an implementation. When you have a shell, use it to read source and to
compute a value an example states (a short command such as python3 -c "print(...)") instead of
working it out by hand; do not run the test suite, capture receipts, install packages or write
files, and do not claim executed verification. Cite relevant source paths or supplied handoff evidence
where the schema asks for references. The handoff contains the current contract, prior
planning reports and original human sources; the schema below is authoritative. Do not
search private .autocode state, event logs or schema files to rediscover this protocol.
Read application source only when an ordinary review needs evidence absent from the handoff.
When resolver_remediation.report_only is true, use only the supplied handoff and return the
report without tools. Report missing evidence as a concern or unresolved question supported
by this schema; never invent verification or approval. Preserve the user's requirements.
Treat workspace in CURRENT HANDOFF DATA as the strict filesystem boundary. Do not read,
list or search parents, siblings, external configuration or ancestor AGENTS.md files.
Only exact private_source_exceptions workspacePath records permit additional source reads;
retain their sourceId, canonicalPath and SHA-256 identity, and never read canonicalPath.
