# OpenCode request timing crossed message boundaries

Pending request starts were held in a session-wide FIFO. If one assistant
message emitted multiple starts before a finish, its leftover start could be
assigned to the finish of a later message. This produced an incorrect request
interval even though the later message had its own start.

Pending starts now stay within their session/message identity. Multiple pending
starts in one message make its start time unknown, including later finishes in
that message. One count is consumed per distinct finish; unresolved starts remain
unresolved. Finish replays cannot consume newer starts. Message-free sessions
retain legacy FIFO, while mixed or malformed identities cannot borrow another
message's start.

Replayed parts keep their original message binding. A conflicting binding makes
the affected timing uncertain without consuming another pending start or
discarding independently known finish usage. Pure controls cover this defensive
case; it has not been observed in the retained live traces.

Ambiguous timing remains explicitly uncertain even if counts later balance.
Known finish-token quantities remain available. The correction does not infer
provider retries, missing requests or a complete bill. Pure parser controls,
recorded real-event replay and an actual native AutoCode run verify the change;
the rare extra-start anomaly did not recur in that fresh run.
