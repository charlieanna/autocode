# Private approval and recovery transport (#818)

On the fresh master baseline `8007a4f`, five real TaskRun calls exposed newly
generated, non-authorizing canaries in actual CLI argv. A native Sol/OpenCode
discussion independently reproduced a TaskRun call against a nonexistent run.
These demonstrate process-argument exposure on this Mac; they do not prove a
cross-user Linux exploit or unauthorized approval.

The candidate uses a bounded schema-1 JSON envelope on private stdin. TaskRun,
dashboard commands and bundled drivers replace literal token arguments with
`@stdin` before process launch and command recording. CLI admission consumes
the envelope once, binds values after final parsing and detaches stdin before
provider dispatch. Existing authorization checks are retained. Guidance shows
an executable token-free command and a separate private JSON document.

Literal argv remains a compatibility path and retains its exposure. Public
token fields and intentional approval receipts are also preserved. The private
transport protects process arguments, not same-user access to operator files.
Environment variables were avoided because the provider environment currently
preserves `AUTOCODE_*` variables.

On the candidate integrated onto master `72bda5c`, 20 real CLI launches covered
all nine fields, malformed envelopes, command wrappers, dashboard persistence
and driver records. Their observed argv omitted the canaries; every observed
process was gone or reused after completion. Thirty focused pure/real-CLI tests
and two JSON-parser tests passed. Fake-provider campaigns and the full suite
were not run locally, following the user's live-only qualification requirement.

Native Sol through OpenCode independently executed real TaskRun and malformed
stdin refusals before and after the change. The latest sealed candidate run
completed in 264 seconds after one report repair for a missing plain-English
example. Its genuine final `--json` output reported completion. The earlier
candidate run also completed in 214 seconds. These are read-only discussions,
not full multi-model build campaigns or successful real approvals, retries,
program agreements or checkpoint restores. Those paths remain unexercised by
this qualification. Test-expectation, formatting and documentation updates
after the sealed run do not change runtime syntax trees.
