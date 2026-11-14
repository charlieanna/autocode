# VS Code read deadlines and overlapping readers, 2026-11-14

A VS Code status deadline could terminate only the configured host wrapper while
its Docker/native reader continued running. The next poll then launched another
reader. Retained receipts from the offline Writer editor fixture show a status
read lasting 45.085 seconds and the next status starting before it finished,
lasting 72.658 seconds; a registry read lasted 68.757 seconds. All eventually
exited zero. The fixture's source was d7773996ef7a633b738f9f5432e23e8039a20c04;
its manifest SHA-256 was
80419199f6d17df0452e2eee2f0e28be1994b5e65021752c696a469cdb37afe8.

Bounded public reads now separate the UI result deadline from process collection.
At 30 seconds (60 for model/placement lists), the client reports the read as
unavailable and withholds actions requiring a fresh view. It sends no signal to
the configured reader. Identical launch attempts remain held until that reader
naturally exits, including action preflights. Late output is discarded. A fresh
process supplies the next successful view; healthy concurrent preflights remain
fresh. The identity includes executable, prefix, cwd, effective environment and
arguments. These are in-memory process leases, not cached status or persisted
collection claims. A reader that never exits remains unavailable for that
extension session. Ordinary named actions keep their existing lifecycle.

The retained final paused run was profiled separately with its original source,
Git archive, Linux image and native filesystem. Its read completed in 6.603
seconds: plain imports took 2.585 seconds and profiled CLI main took 1.962
seconds. This does not reproduce the earlier active-load delay. Parser building
made 158 terminal-size queries, consuming 1.199 seconds in that profile. A
separate source defect repeated native request accounting and tool-activity
summarization within one event-file observation. Parser width is now sampled once
per parser using the standard formatter's calculation; the next parser samples
the current terminal again. Reusing only that same read's
accounting preserves event identities, freshness and public output.

Two earlier diagnostic probes crashed during imports before producing status;
they are retained as failed probes and supply no latency or fix evidence. The
successful probe removed import instrumentation until after the plain import.
No production crash cause is inferred from those diagnostic failures. The
original 45–73 second CPU delay has not been isolated to one Core function;
the reader-overlap defect and the repeated work above are independently proven.

Deterministic regressions fail on the base for reader replacement, late-output
callbacks, repeated accounting and terminal-width sampling. Candidate checks:

- Extension suite: 562 passed, 19 explicitly opt-in Core-walk tests skipped;
  after the isolated-controller read was also moved to the shared primitive,
  its three direct controls, typecheck and build passed.
- Nine read-lifecycle controls cover strict deadlines, held admission, exact
  launch identity, collection, fresh approval preflights, and an actual local
  child waiting for EOF. DOM coverage checks disabled actions and recovery with
  the newly displayed plan token.
- Accounting module: 12 passed; affected usage, efficiency, context-window and
  tool-activity consumers: 139 passed. Complete, unfinished, conflicting and
  truncated event streams preserve output and file bytes.
- CLI surface: 14 passed, including byte-identical standard help at several
  widths and without a terminal.

Actual VS Code verification is still pending in this revision; these results
alone do not qualify the editor host. No model or paid provider was launched.
