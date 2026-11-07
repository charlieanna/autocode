# Discovery report repair encoding (#2469)

In live TUI run `20261029-063039-chat-f67103` on `e0fd061ad`, Discovery
returned an unexpected `contract.contract_changes_placeholder`. Its first
repair replaced that with another misplaced field, `contract.contract_changes`.
The two repairs took 111.77s and 97.08s, and their native final responses were
21,303 and 19,268 UTF-8 bytes (5,458 and 4,926 output tokens). The paused
public bundle manifest is
`50c0fc0aef16118186510172a90fd4ee121e09b5c9bba3211ed2dc1431927410`.

The #2421/#2428 optional bounded edit protocol covered Requirements and revision,
but its stage allowlist omitted Discovery. Discovery now receives that same
object/text edit offer. Its exact rejected artifact hash, original schema,
source and goal pins, execution evidence, full semantic validation and approval
remain mandatory. Existing complete responses and saved schemas are unchanged.
This does not skip stages, grant retries or change routes, budgets or state keys.

Offline comparison found a 798-byte object-edit response sufficient to remove
the first misplaced field and restore the protected failure-case list. A
173-byte removal fixes the second nested field. Each reconstructed report
matches the accepted report in every field except its explanatory summary.
Removing the first placeholder alone only fixes its first schema error; the
protected obligations still require validation. These byte counts describe
representation, not saved time or guaranteed model adoption.

The public fake-provider regression exercises both object and malformed-text
Discovery repairs through the real CLI, preserves raw edits and rejected input,
and reaches independent review and the exact-plan gate without a Builder.
Stale source hashes and fabricated user decisions remain rejected. A loader
regression also proves reconstructed protected changes fail the ordinary revision
guard. The old runtime refuses both positive cases because the edit offer is
absent. No live calls are used for this protocol fix; live adoption and end-to-end
latency remain unverified.
