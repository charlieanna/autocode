# Follow-up contract provenance (#364)

A completed run retained its approved contract, but `--follow-up` did not create
an input receipt that planning could cite to revise protected behavior. The real
new request was present in conversation text; it was not a valid saved change
basis. Report repairs could therefore exhaust their allowance.

Follow-ups now append the same standard `brief_feedback` receipt to the two
existing input ledgers, recording the exact message, ID, time and preceding
contract token. Existing handoffs and the unchanged contract guard can use it.
The new plan still needs approval. No schema, allowance or recovery history is
reset, and older unrecorded follow-ups are not silently authorized retroactively.
Clear follow-ups also refresh an existing Requirements handoff, so the Planner
uses the new request's requirements instead of stale first-turn trace IDs.

The baseline was reproduced with native OpenCode Luna/Sol: `word_counts(text)`
completed and passed eight independent checks, but its opt-in casefolding
follow-up could not cite the genuine request and never reached a second build.
CLI and pure regressions fail on the old code and pass with these corrections.
They preserve stale-approval rejection and refuse forged input IDs, missing
ledger entries and undeclared criterion or permission changes.

A fresh final-production-source campaign with the stream-activity fix (#298)
**passed both turns** under the same profile and caps as the preceding failed
campaign. The audit verified:

- Eight first-turn and fourteen follow-up independent checks; all fifteen final
  criteria validated, including Unicode casefolding and keyword-only behavior.
- Both previously accepted test files unchanged, refreshed Requirements, and an
  accepted protected revision citing the saved follow-up ID before fresh approval.
- Seventeen native exports confirming GPT-6 Luna, GPT-6 Sol and GPT-6.1 Sol;
  source-bound proof and six replay logs; 356 unchanged runtime file hashes.
- Zero timed-out stages; 1,489.984 active seconds within the original 1,800-second
  total, 300-second stage, 120-second idle and six-iteration limits. No extra grant,
  manual report edit or limit reset.

Six reports were rejected and repaired before acceptance. Completion demonstrates
bounded recovery, not flawless model conformance or success on every model/task.
Earlier campaigns remain failed: `live-after` stopped on an undeclared revision;
`live-integrated` exposed stale Requirements; `live-refreshed` stopped after trace
errors and denied malformed Investigator paths; `live-sol-separated` hit idle
limits. The initial same-model profile remains separately `SETUP_REJECTED`.
Their original caps, native exports, failures and accepted tests remain retained.

Ignored evidence lives under `.scenario-runs/follow-up-provenance-364/`;
`baseline-reference.json` identifies the original reproduction, and
`live-native-progress/qualification.json` holds the successful audit.
After that audit, existing PR #384 was integrated unchanged. Only its offline
fake Codex fixture filename differs; every production runtime byte remains
identical to the live-qualified source (`integration-384.json`). The original
full-suite failure is retained. The integrated suite passes all 3,547 tests in
254 modules (731 seconds); the fake catalog has 54 passes, one existing
NOT_EXERCISED and one real-Investigator SKIPPED.
