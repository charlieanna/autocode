# AutoReview fixes and retest

Branch: `codex/autocode-blackbox-scenarios`. This follow-up implements fixes to
the preceding product audit; that audit remains a historical baseline.

## Changes

- Bind contract hash/revision, task ID and reviewer-owned open finding IDs in
  each review/repair attempt's generation schema. Normal runtime validation is
  retained; malformed identities are not silently accepted or rewritten.
- Supply report repair with exact original executed command/event/exit tuples
  and runner-owned report identity. The original evidence remains pinned.
- Keep findings open when their recorded scope lacks PASS criteria with evidence.
  Preserve new defects in the same report and retain unsupported closure claims
  as `pending_resolution`. BLOCKED validation cannot resolve old findings.
  Explicit, evidenced retraction remains separate from a claimed fix.
- Explain that unavailable devices/tools/permissions are verification blockers,
  not implementation defects. No browser or sandbox restriction is bypassed.
- Strengthen the Go product-test oracle: shell-wrapper exit zero alone is not
  proof that nested `go run` compiled or executed.

Current finding scopes are milestone-wide, so closure is intentionally
conservative: all criteria in that stored scope must pass. This does not yet
introduce per-finding criterion mapping.

## Deterministic verification

- 164 tests: findings, validation metadata, Builder retry policy, execution
  checkpoints, dispatch, assignment scenarios, runner, subprocesses, report repair.
- 80 tests: unit boundaries, milestone checkpoints/policy, build black-box cases,
  recovery, dashboard consumer.
- 98 tests: goals, milestone checkpoints, validation metadata.
- Final focused rerun: 44 finding/report-repair tests passed, including exact
  original receipt handoff, pinned identities and unverified closure retention.
- Dashboard monitor JavaScript test passed; Python compileall and diff checks passed.

These batches overlap; their counts must not be added as unique tests. They use
deterministic providers, not live-model quality judgments.

## Live retests and remaining limitations

Same live routes as the baseline: Luna medium validator and Sol medium completion
reviewer; scripted setup only. Artifacts are retained locally under
`/private/tmp/autoreview-fixes-20260925` and
`/private/tmp/autoreview-device-retest-20260925`.

- **Unavailable device: PASS on final fresh retest.** Accepted BLOCKED report,
  C1 NOT_VERIFIED, no defect findings, WAITING_FOR_USER; source/contract unchanged.
  An earlier retest reached a valid handoff but mislabeled the missing device as
  a defect. The explicit verification-blocker instruction was added before the
  final successful retest.
- **Go parity: report/handoff recovered, runtime reproduction still blocked.**
  The current report is accepted and detects the missing `de` override from
  source. Raw receipts show compilation blocked by temporary-directory policy.
  The first harness assertion falsely passed because `set +e` followed by
  `printf` returned zero. The strengthened assertion was replayed against those
  same receipts and correctly rejects them. This is NOT a full scenario pass.
- **Browser rendering:** not retried via an alternate route after the prior
  explicit browser-policy denial. Source-level clipping analysis is not rendered
  proof; the original PARTIAL result remains.
- **Fix one defect / introduce another: safety fix verified, full scenario still
  fails runtime coverage.** Both reviews produced accepted reports and the
  completion reviewer handed back to AutoResolver without finding-ID errors.
  The second candidate's C4 data-loss defect was retained, C1–C3 remained
  NOT_VERIFIED, and both prior reviewers' findings stayed open. No unsupported
  persistence resolution was accepted. The full scenario still requires
  executed persistence verification, so it is not counted as PASS. Source and
  contract remained unchanged in both review calls.

Read-only plus an explicit CLI scratch directory was separately probed with one
write attempt and remained denied. No broader sandbox permissions were enabled.
An authorized execution environment is still needed for the blocked runtime and
rendered checks. This push does not claim an all-green live product audit.
