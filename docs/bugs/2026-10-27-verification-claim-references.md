# Hash-bound verification claim references (#2214)

The context attribution in #2136 found repeated criterion text in execution
handoffs after the earlier lossless compaction. The full approved contract is
already present, while each verification obligation repeats its criterion as
`claim`. This change replaces only exact, uniquely matched, hash-bound claims
with same-packet `claim_ref` JSON pointers. All other obligation fields and the
complete contract stay inline. Short claims, ambiguous IDs, mismatched hashes,
changed claims and legacy records remain inline. The acceptance-test writer is
unchanged. No stage is skipped and no saved run, routing, approval or budget is
modified.

A counterfactual over retained prompts reconstructs the original packet exactly
and compares compact JSON on both sides, excluding earlier whitespace savings:

| Retained stage | References | Before bytes | After bytes | Saved bytes |
| --- | ---: | ---: | ---: | ---: |
| #2113 core Builder | 19 | 105,041 | 92,408 | 12,633 |
| paging Builder | 11 | 47,997 | 44,140 | 3,857 |
| paging Tester 1 | 11 | 71,216 | 67,359 | 3,857 |
| paging Tester 2 | 11 | 72,972 | 69,115 | 3,857 |

The input prompt SHA-256 values respectively start `07aed5b79425`,
`63445bd2a854`, `f9164ae420c4` and `7fa385b4bd94`. These are presentation-byte
measurements, not token, cost or model-latency results. No new paid calls were
made; the original missing #2136 study artifacts remain missing.

The focused Linux public TaskRun regression uses an offline provider that
resolves every generated claim pointer and checks its complete criterion hash,
method and retained command/collection records. It exercises real greeting CLI
success and invalid-name behavior through Builder, Tester, Completion Reviewer
and clean replay. It fails on unchanged master (Builder cannot consume the
expected references), then completes with the fix in 17.541 seconds. Pure tests
verify whole-packet round trips, unchanged inputs, effective prompt rendering,
idempotence and stale/ambiguous/malformed/short/nonexecution negative controls.
The existing handoff, acceptance-writer, tool, report-repair and prompt snapshots
also pass. Architecture and CI-selection checks pass. This does not establish
that the remaining slow native model loops in #2113 are resolved.
