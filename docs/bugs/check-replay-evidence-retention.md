# Check replay evidence retention (#341)

A Validator output name repeats across iterations, and can repeat when replaying
one attempt. Saving under that name overwrote the receipt and first check log;
when the later replay ran fewer checks, leftover logs belonged to the older
attempt. A saved rejection could consequently cite a later PASS and source.

Every replay now exclusively allocates `<run>/check-replay/<report-stem>-<uuid>/`
before protected-test or check execution. Failure messages and stored validation
rows keep their original receipt/log paths. Existing directories are untouched;
evidence already overwritten by an older version cannot be reconstructed.

On 2026-10-04, live OpenCode `openai/gpt-6-sol` runs executed the same real-scratch
probe before and after the change. Cross-iteration reports, cross-iteration
repair reports, a repeated attempt, and the missing-output fallback all changed
an earlier FAIL reference into a later PASS before the fix. Afterward, all four
kept the original receipt and both original logs byte-for-byte, with two distinct
receipts and source revisions. The model independently checked the saved copies.
Native exports and source/runtime hashes were audited. These are live component
checks with explicitly crafted report inputs, not natural model-generated
validation collisions or proof of complete project delivery.

The baseline probe initially failed because its parent output directory was
missing; the model created it and completed the four cases within the original
limits. The fresh final fixture supplies that empty parent. Driver, brief and
600/240/120-second total/stage/idle limits and three-iteration cap are unchanged.
The original setup failure remains in the evidence.

Two regression tests cover the four rejected-replay variants plus preservation
between successful replays; all five cases failed on the old code. Visual and
progressive-verification receipt readers were updated for the fresh directories;
the protected-oracle CLI checks retain the same directory depth.

Raw evidence is ignored under `.scenario-runs/check-replay-341/`, including
`component-before`, `live-probe-before`, `live-probe-after`, `live-before`, and
`live-after`. The baseline project build passed its independent eight-check
oracle. Its second-turn planning failure is tracked separately in #364; it never
reached another Validator and is not a reproduction of the overwrite.

A fresh final-code build using native `openai/gpt-6-luna` and `openai/gpt-6-sol`
reached `TASK_COMPLETE` and passed the same eight-check independent oracle.
Its runner regression proof and four replayed checks passed, and the original
test file was unchanged. Planning first failed on a missing schema field,
requirement-trace gaps and malformed JSON; the Investigator diagnosed these and
planning recovered autonomously. Those failures and the unchanged
1800/300/120-second total/stage/idle limits and six-iteration cap are retained.
All 345 pinned runtime files matched between the final live component and build
runs. This single-turn success does not resolve the earlier follow-up failure.

The affected suite passed (60 tests, including architecture; its 32 image tests
were initially skipped without Pillow, then all 32 passed with bundled Pillow).
The nine protected-oracle CLI tests passed. The fake compatibility catalog had
54 PASS, one existing NOT_EXERCISED and one live-Investigator SKIPPED; these
supplement the live evidence. The full suite was not rerun for this local change.
