# Mixed-model planning report recovery

Live mixed-model runs exposed avoidable report repairs before a build could
start. An OpenCode GLM/Sol outbox plan omitted one `requirement_id` and described
seven new criteria and an appended failure-case list as protected rewordings.
The report needed two full model repairs. A KiloCode GLM/Luna inventory plan
omitted two requirement IDs; successive repairs surfaced the omissions one at
a time. Other plans needed several rounds to correct coverage citations.

AutoCode now normalizes planning metadata after provider decoding and before
schema validation, independently of which tool or model produced the report:

- Recover one omitted requirement ID only when every other ID is valid and
  unique, the row count matches the saved requirements, and exactly one saved
  ID remains. Never infer identity from position or overwrite an explicit ID.
- Remove redundant, agent-proposed rewording declarations for genuinely new
  criteria or pure additions to protected lists. Keep the contract untouched;
  normal revision checks still reject removed obligations, proof downgrades,
  changed permissions, and invented user authorization.
- Report all ambiguous missing IDs and all coverage failures together so a
  bounded report repair can address them in one response. Protected-edit errors
  identify the item that needs authorization.

The canonical report is saved beside the preserved `.reported.json` provider
report. Provider event logs remain the original wire evidence. Existing saved
schemas and timeout recovery are unchanged.

Read-only replay of the original outbox plan recovers R16, removes eight
redundant declarations, and passes schema, revision, and coverage validation
without changing the contract. The two-ID KiloCode report remains rejected,
with both paths and the unassigned R18/R21 identities in one diagnostic.
Public CLI regressions cover approval without a model repair and rejection of
an attempted proof downgrade; the recovery regression fails on unmodified
master. Unit tests cover raw preservation and ambiguous or invalid identities.

The fresh inventory recheck used the same GLM/Luna configuration and caps as
the earlier run, with concurrent local test load. It stopped at the saved
active-time limit after six GLM calls, two planning timeouts, and zero report
repairs. It never reached Luna's review or the Builder, so this is not a live
mixed-model workflow pass. Final import cleanup and stricter malformed-receipt
preservation were verified locally after that run.

The broad suite also exposed a test compatibility issue: Python 3.12.14's
`unittest` exits 5 when `setUpClass` fails before any tests run. The HTTP
startup-failure regression now accepts either failure code 1 or 5, while still
requiring the planted startup error and proof that all four children were reaped.

These fixes remove specific mechanical repair loops. They do not establish
that all model combinations complete uniformly: semantic coverage errors,
weak model-authored tests, provider permission failures, and timeouts still
need separate verification. The earlier five capped live configurations all
stopped honestly; one delivered inventory implementation passed all seven
independent checks but still failed AutoCode's internal proof requirement.
