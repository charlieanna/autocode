# OpenCode streaming can be mistaken for inactivity (#298)

OpenCode 1.18.33 emits text in its JSON CLI stream only when a text part ends.
Its native event interface also emits intermediate `message.part.delta` events.
AutoCode watched the CLI stream, so a long, actively streaming report could exceed
the inactivity limit, be killed and consume recovery attempts.

A real GPT-6.1 Sol Planner request reproduced this with the production
`ActivityMonitor` and process supervisor. The original 120-second idle and
300-second stage limits were preserved. An independent, per-invocation OpenCode
observer recorded timestamps, lengths and hashes without feeding the monitor.
The monitor killed the request as idle after 181.216 seconds. Native traffic
contained 4,953 deltas / 20,432 characters, including 165 deltas in the last five
seconds; the last arrived 0.097 seconds before supervised cleanup finished.
The original paused task state and project source were unchanged, and no owned
provider process survived. The native export confirms GPT-6.1 Sol.

This supplies evidence missing from the earlier #298 investigation. An exported
unfinished message alone cannot reveal the native traffic; it does not prove a
completed report was lost or distinguish a stall from silent processing.

The adapter now registers a packaged, local hook for each OpenCode invocation,
preserving existing plugins and permissions. On genuine native text/reasoning
deltas, the hook emits bounded content and suffix hashes, never report text or
command evidence. The monitor requires advancing positions and novel hashes;
replayed events, repeated content, counter-only changes, blank updates and arbitrary
log messages cannot renew inactivity. The hook emits no timer-based heartbeats.
It is included in the wheel and runtime/transport identity checks.

This changes observation, not allowances. Explicit idle limits, fixed tool limits,
stage hard caps, recovery accounting and terminal-report checks remain in force.
Progress records have no native `part` field usable as command or report proof.

The new monitor regression fails before the correction. The focused monitor,
provider, JavaScript-hook, readiness and architecture checks pass (91 tests).
A built wheel contains byte-identical hook source. A separate final-code live replay
completed in 200.604 seconds under the same idle=120/stage=300 limits. Native exports
confirm GPT-6.1 Sol; 5,855 native deltas produced 603 bounded progress records. The
CLI-only event gap was 181.674 seconds, versus 10.309 seconds with progress included.
The terminal report parsed successfully, runtime hashes stayed unchanged, the
original paused task and project were untouched, and cleanup was verified.
This is a component qualification, not completion of the paused follow-up task.
The full suite ran 3,547 tests in 254 modules. Two modules failed on the known
offline milestone filename mismatch, also reproduced on the untouched master
base and already corrected by PR #384. A third configuration-identity test was
updated to assert both the explicit configuration and packaged hook hashes; all
12 tests in that module now pass. The original failed gate is retained.

The fresh complete two-turn campaign also passed: 22 independent checks, all
15 final criteria verified, seventeen native exports confirming Luna/Sol/Sol 6.1,
unchanged accepted tests and limits, and zero timed-out stages. It needed six
report repairs; successful completion does not mean uniform model conformance.
The existing PR #384 offline fixture correction was then integrated unchanged;
every production runtime byte still matches the qualified live source. The
integrated full suite passes 3,547 tests / 254 modules in 731 seconds. The fake
catalog has 54 passes, one existing NOT_EXERCISED and one live-Investigator SKIPPED.

A separate native MiMo 2.6 Pro component check used the same archived read-only
prompt and explicit idle=120/stage=300 caps. It emitted 4,241 native deltas and
1,032 progress records, then hit the original 300-second hard stage limit without
a terminal report. Its runtime, paused source task and project stayed unchanged;
cleanup was verified. This remains a failed component qualification. It confirms
activity observation and hard-cap enforcement, not completed MiMo planning or the
original etcd workload. #298's broader MiMo qualification remains open.

Ignored evidence: `.scenario-runs/follow-up-provenance-364/transport-probe/`
(red reproduction and native observer) and `transport-after/` (candidate replay).
Earlier #364 campaigns remain failed. The successful fresh campaign and its
protected revision are documented in [follow-up-contract-provenance.md](follow-up-contract-provenance.md).

Provider source: [OpenCode 1.18.33 CLI](https://github.com/anomalyco/opencode/blob/v1.18.33/packages/opencode/src/cli/cmd/run.ts#L681-L735)
and [native event hook](https://github.com/anomalyco/opencode/blob/v1.18.33/packages/opencode/src/plugin/index.ts#L236-L241).
