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
original 45–73 second read delay has not been isolated to one Core function;
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

Actual VS Code verification subsequently passed on the frozen candidate
`4f14a6f51bd569da6ef776805448baa210e27c09`, with extension bundle SHA-256
`811913bd2a370439d1a1220b103ae48a2b4d6f4817e804f110415e0a3eca370b`.
In the Extension Development Host, a deliberately held public status read
crossed the unchanged 30-second deadline. The error appeared and Resume and
Show changes became disabled. Clicking Refresh launched no replacement while
the same reader and transport remained alive. After release, both exited zero;
only then did a new public read start and restore the visible actions. The
observer's final receipt, `ROOT_ACTUAL_VSCODE_FINAL.json`, has SHA-256
`914097a26c7577d821e586b1f3e6f5d9c14f99ef9aab51429e066559e1c7030e`.
Its private Linux namespace was collected and exited zero. No saved-run action,
model or paid provider was launched.

[PR #3554](https://github.com/charlieanna/autocode/pull/3554) merged with all
eight changed production files and all 54 extension inputs matching that
candidate. Its required Ubuntu check reported 883 tests across 51 modules,
including four host-specific skips, with no failures. This binds the scoped
editor result to the relevant merged files; it does not qualify every change
in the later merge tree or establish complete live-provider delivery.

Intermittent latency remains unresolved. Later unheld status and registry reads
in the same observer took 65.043 and 72.040 seconds from recorded Core start to
wrapper completion. The status payload's observation timestamp came 58.689
seconds after recorded Core start, before final JSON output, so output transport
alone does not explain the delay. There was no process CPU or phase trace for
those reads. All 51 native reads exited zero; two later host transports ended
with SIGPIPE and remain failures with an unestablished cause. The critical held
and fresh transports both exited zero.

A bounded follow-up profile of five public reads took 4.35–6.38 seconds and did
not reproduce the outliers. Its hot output used native files rather than the
original live host journal and pipes, so it is normal-path evidence, not proof
that the latency disappeared. The reader lifecycle fix, parser fix and usage
fix must not be presented as having eliminated every slow read.

## Follow-up for #3849, 2026-11-20

The 65.043 s status and 72.040 s registry reads above remain unexplained. This
follow-up makes the next one diagnosable and measures the read path under load.

**Read phases are now logged.** Every public read (`invokeRead`) records when its
process was spawned, when its first output byte arrived, when it exited, when its
pipes closed and when its result was delivered. A read that takes longer than
`READ_SLOW_MS` (5 s), or misses its deadline, writes those phases as one line to
the extension's AutoCode output channel. A deadline-missing read writes a second
line when its reader finally ends. A long gap between spawn and first output points
at the Core's own work. A gap between exit and close points at a child holding the
pipes. A gap between close and delivery points at the extension host.

**Measured here.** A Linux cloud container with 4 CPUs, this branch's compiled
`cli.js`, and the master CLI (`python tools/autocode.py`) with
`AUTOCODE_SOURCE_HOST=0`, reading a saved run in this checkout. Each read went through
`invokeRead` with the default 30 s deadline, five reads of each kind. Phases are
milliseconds from spawn.

| Load | Read | First output | Exited | Closed | Delivered |
| --- | --- | --- | --- | --- | --- |
| none | `--status` (1.78 MB JSON) | 721–849 | 953–1232 | 953–1232 | 953–1232 |
| none | `registry list --json` | 682–887 | 762–959 | 763–959 | 763–959 |
| 8 busy loops on 4 CPUs | `--status` | 1888–2049 | 2451–2677 | 2451–2677 | 2451–2677 |
| 8 busy loops on 4 CPUs | `registry list --json` | 1805–2282 | 1945–2526 | 1945–2527 | 1945–2527 |

What this rules out, for these conditions: CPU contention alone scales a read by
about 2.5 times, not by a minute. Pipe transport is not involved, because close
followed exit within 1 ms in every read. Delivery is not involved either, because
the result reached the caller within 1 ms of close. Most of each read happens before
the Core's first byte, which is the Core's own work.

What is still open: the outliers came from a Docker/native reader under an offline
editor qualification, with a live host journal and pipes and an active run writing
events. Those conditions are not reproduced here. The cause of the minute is not
established, and this follow-up does not claim the latency is resolved. The next
outlier in an editor now writes its phases to the AutoCode output channel, which is
the evidence the earlier trace lacked.

Two related defects in the same read path are fixed alongside (#3850). A CLI that
exited before its deadline, while a grandchild held its pipes open, used to be
reported as a deadline error. Its result is now delivered when the pipe grace ends
or the pipes close. Reads and actions still running at the deadline keep the
behavior described above.
