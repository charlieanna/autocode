# Large proof handoffs in the heavy Builder qualification

The LedgerLab live run on `5201aaa82fec933b52feeae262390b75a6515574`
integrated two independent Builders, then sent its Tester a 2.35 MB prompt.
Its 131 changed-code receipts carried approximately 1.71 MB, principally raw
commands, output tails, test results and supervision records. The Tester timed
out after 2,235.7 seconds. This is evidence of an oversized handoff and a slow
attempt, not proof that payload size alone caused the timeout.

Review handoffs now move those four bulky receipt fields into the existing
immutable context artifact when the inline receipt list exceeds 64 KiB and the
replacement saves space. Every receipt's outcome, output identities, timeout
and supervision errors remain inline. The artifact contains the complete original
receipts, addressed by list index and pinned by SHA-256. Instructions require
reading the full details before relying on them; missing or mismatched evidence
does not authorize PASS. Current obligations, mutation results, findings and
approval/completion gates are unchanged. Small packets, other stages and packets
with an existing context artifact retain their current presentation.

Applying this transformation to the retained live handoff reduced its JSON from
2,330,994 to 710,265 bytes (69.5%), preserving all 131 receipts. This measurement
does not establish that a subsequent real Tester will be faster or complete.
The full heavy qualification remains in progress.

The regression fails on the base for all three review stages, both at the
compaction boundary and through actual stage-prompt generation. The candidate's
supported-Linux changed selection passed 67 tests in seven modules in 14 seconds,
including the public verification CLI consumer and architecture checks. Tests
cover exact receipt recovery, inline failure/timeout signals, source nonmutation,
artifact hashing and tamper refusal, repeated compaction, small and malformed
inputs, existing artifacts and cases where moving detail would not save space.
An initial local run encountered macOS sandbox sysctl errors in the unchanged
status-liveness test; that module passed all 19 tests in Linux. Ruff check and
format passed for 3,265 files.

The qualification used base `4813c890e77fe12e065cedd8dc50a89d7b8c754c`, base
archive SHA-256 `e71cabac9156f325bee3b4abe948cfb4b08df092d63685968508704159b92403`
and candidate patch SHA-256
`15a0124abdc1fb7d06f4f7fc71526a0b16a762fdb58f8bba3fadd55797588cc7`.
Only this evidence note was added afterward. Commands, logs, the measured packet
and the immutable receipt artifact remain in the ignored local directory
`.scenario-runs/orchestrator-heavy-live-20261105/`.

## Follow-up: mutation and per-line detail (#2806)

A later live run on Kilo integrated a two-Builder wave of about 900 changed lines.
It sent its GLM 5.3 Tester a 3.63 MB handoff, and Z.ai refused the first request with
"Prompt exceeds max length". With the receipt bound above, the same handoff was still
1.99 MB. Four other fields made up most of it:

- every mutant per file (333 KB);
- every surviving mutant once per criterion (597 KB);
- the same mutants again under the evidence's inputs (226 KB);
- one obligation row per changed line (492 KB).

For review stages, `autocode_proof_detail` moves each of these fields into the same
artifact once it is over 64 KiB, and leaves a summary inline. Each file keeps its
mutant count and outcomes. Each criterion keeps its survivor count and the tests that
let a mutant survive. Every changed line keeps its handle, line, status, execution and
mutation outcomes, grouped by file. The Tester names a gap by its handle, which the
generation schema already offers and the runner resolves against the run's own
assessment. Nothing moves when another part of the handoff points into an obligation.

That left the receipt outlines, 209 KB for 357 mutant runs. When the outline list is still
over 64 KiB, it becomes a count per exit code, plus every receipt that timed out or has
supervision errors, kept whole with its index into the artifact. Each mutant's outcome is
already in `mutation_check` and on its changed line.

On the retained handoff the packet went from 3.63 MB to 0.25 MB, with all 892 lines and
every gap still named. A live GLM 5.3 Tester accepted the 0.45 MB version (about 204k
input tokens on its first request) before the receipt step was added.
