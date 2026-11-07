# Small-task verification latency (#2113)

The retained AutoCode TUI follow-up completed exact approved revision 12 in
515.065 seconds after Resume on `45c2997ffad16e1ce3b999daa85d0e5f5d1b8f85`.
Codex Medium implemented the change; GLM-5.3 Low tested and reviewed it through
Kilo 7.8.3. The redacted bundle manifest is
`2fc5b922ad297fd9cbf5271604e073b45617bbbe780624627b2608b578bc6e25`.

Tester took 239.338 seconds over nine model requests. Its ten shell calls used
only 3.499 seconds of combined wall time. The first four requests read current
source and rediscovered Git/baseline facts; the eleven-test suite first ran
124.291 seconds into the stage and took about 0.203 seconds. Two mistakes in a
new generated invariant probe then required two correction requests: 50.359
seconds between the first failed probe turn and the final corrected turn.
The final report emitted 3,198 output tokens in a 38.602-second request.
These intervals overlap the stage; they must not be added as independent savings.

Three concrete causes are addressed:

- `validator_note` used the historical `investigation` directly. The previous
  bug's diagnosis therefore ordered another generated-input probe during a
  feature follow-up, bypassing the existing current-turn diagnosis policy.
  It now uses that policy. Historical evidence remains, current reproduced
  bugs retain their independent invariant checks, and approved preservation
  criteria still require verification.
- The reviewer handoff named files without supplying their tiny current source.
  A bounded attachment now carries the complete approved scope, exact UTF-8
  content and matching source identity when it fits sixteen files and 16 KiB
  of source. Missing, changed, symlinked, binary or oversized source omits the
  entire attachment. It never widens scope or supplies execution evidence.
- OpenCode's transport instructions contradicted the Tester's existing support
  for null receipt-backed command fields. They now prefer authenticated runner
  hydration; a supplied value must still match and forged receipts remain refused.

No stage was combined or removed. Models, efforts, approvals and fresh runner
verification retain their existing authority. The first Tester request used
34,217 input tokens; later requests largely used cached context. This fix does
not claim every token or provider delay is avoidable. Builder's 95.115 seconds,
Completion Reviewer's 58.888 seconds, preflight's 45.699 seconds and controller
gaps remain separate parts of the original 515.065-second observation.

Deterministic regressions fail on the old stale-diagnosis path, old compiled
transport instructions and old reviewer handoff. Negative controls retain
current-turn invariant checks, approved-scope and source-identity checks, and
receipt authentication. Focused Linux public-interface evidence and the matched
live comparison are recorded in the linked pull request and issue; pending
measurements are not a speedup claim.
