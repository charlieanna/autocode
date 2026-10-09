# Integer defects in generated applications

Six retained live deliveries violated their documented unbounded integer inputs:
inventory quantities, CSV quantities, ledger balances, queue clocks/deadlines,
outbox amounts/limits, and event deltas/snapshot totals. These are application
defects in generated projects, rather than six defects in AutoCode's own database.

The original campaign is preserved. Repaired copies live under the ignored
`.scenario-runs/20260930-integer-repairs/<scenario>/project/` tree. Changes retain
each implementation's structure and public API:

- Inventory stores exact quantities as text and preserves decimal JSON output.
- CSV import decodes long decimal quantities in bounded chunks.
- Ledger and outbox persist hexadecimal text and decode to Python integers;
  ledger arithmetic stays inside its transaction in Python. Large outbox limits
  saturate at SQLite's maximum limit, which still includes every possible row.
- Queue lease comparisons decode integers in Python under the same transaction.
- Journal stores exact signed deltas and serializes/parses snapshot JSON integers
  without changing Python's process-global decimal conversion limit.

Every repaired copy passes the original independent oracle, including project
tests and hidden boundary cases. Seven additional public-interface regression
tests use identical source against the original and repaired implementations:
all six original projects fail, and all six repairs pass. The ledger has two
cases: crossing 64 bits must retain integer type, and a 5001-digit transfer must
remain exact, durable and idempotent. Original source hashes are unchanged.
`manifest.json`, `summary.json` and `integer-regressions.json` in the ignored
repair root record paths, hashes, verdicts and executed output.

AutoCode's Planner and Validator instructions now require examples at 64-bit and
decimal-conversion boundaries whenever the public contract has no integer bound.
Existing executable case proofs remain responsible for checking those examples.
These instructions improve generated coverage; they do not establish that every
future generated application handles every integer correctly. Manual repairs are
not counted as successful live AutoCode completions. Fresh OpenCode/Codex runs
are recorded separately under `.scenario-runs/20260930-live-fixes/`.
