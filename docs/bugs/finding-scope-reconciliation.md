# Historical finding scopes after a milestone reassignment

An approved plan can move a criterion to a later milestone while older open
findings retain the previous milestone's complete criterion list. The finding
scope gate correctly fails closed on this mismatch, but that can block an
unrelated earlier milestone even after fresh proof and independent validation.

`--reconcile-finding-scopes MANIFEST` corrects an explicitly reviewed attribution
at an authenticated, stopped permission checkpoint. It requires the exact
approved contract and current request/token, rejects active or uncertain
attempts and queued interventions, and cannot be combined with execution
recovery. Each manifest change names the finding ID, exact expected scope,
new approved scope and attribution evidence. Only a historical scope mismatch
can change, and the new criteria must be drawn from the original recorded
criteria. All other finding fields remain intact; nothing is closed, made
nonblocking, or accepted.

The existing `user_events` list stores the original/new scopes in a
`finding_scopes_reconciled` receipt. The additive status view fields
`finding_scope_records` and `finding_scope_reconciliations` expose attribution
and its audit trail. Each record includes a hash of every field except scope,
allowing an operator to verify preservation without reading private state.

The command saves the correction and consumes that permission request without
launching an agent. A separate ordinary continuation must still obtain the
Completion Owner's checkpoint decision and satisfy all later verification
and finding-disposition gates.
