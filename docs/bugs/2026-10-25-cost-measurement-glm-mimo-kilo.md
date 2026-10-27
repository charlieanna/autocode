# Cost measurement, glm-mimo-kilo, master eeb64352f (#2136)

This is the cost-measurement note that #2136 cites as
`plans/cost-measurement-2026-10-25.md`. It was never in the repository tree. It
was recovered on 2026-10-27 from `~/autocode-cost-measure-out/summary.md` on
the measuring machine, written 2026-10-26 07:42:40 UTC; an earlier copy,
`summary-runs-1-3.md` (06:41:45 UTC), holds runs 1 to 3 only. The note's text
is kept as written below. What it can and cannot support:

- The Mac rebooted during run 4 and wiped `/tmp`, including the run
  directories of runs 1 to 3 on `eeb64352f`. Those runs survive only as the
  per-stage ledger reads below: no bundle manifest, no `.prompt.md` or native
  event stream, no request-level accounting. They cannot be added to the
  per-request tables in
  [context growth attribution](2026-10-27-context-growth-attribution.md).
- The figures the issue quotes are these reads: Builder plus Tester $0.389 on
  `bugfix-trivial` ($0.194 + $0.195) and $0.565 on `bugfix-iso-weeks`
  ($0.281 + $0.284); Builder cached input 523,392 and 801,024; Tester fresh
  input 101,995 on `bugfix-trivial`. The "5% of first-request input was
  cached in older saved runs" observation is not in this note and stays
  unsourced.
- Tokens are the usage ledger's per-attempt `fresh_input_tokens`,
  `cached_input_tokens` and `output_tokens`, summed per stage by the
  `tools/analyze.py` kept beside the note. "cached" is the provider-reported
  cache read of each completed OpenCode step (`tokens.cache.read`); fresh and
  cached together are the inclusive input. USD is
  `historical_estimated_cost_usd` at the rate card retrieved 2026-10-14
  (`mimo-v2.6-pro` 0.435/0.87/0.0036, `glm-5.3` 1.4/4.4/0.26 per million input,
  output and cached input), an API-equivalent estimate, not a provider bill or
  a subscription invoice.
- Run 4 (re-run on master `c9f4063b7`, 2026-10-26) kept its full run
  directory. Its Builder is case B3 in the attribution note: event file sha256
  `33718dcf9164254e0d479f9bdb03a1f743d9fac057bf8848e162a9854f35ae0b`, prompt
  sha256 `90374e28c6ec05cd3e5a628f711145010cd700f90eee0c584a4cfb1494d65e62`.
  The harness's 60-minute scenario budget, not an AutoCode limit, stopped that
  Builder (`stage_timeout_seconds` was 3600): planning took 2,786 s of the
  3,600, and the resumed Builder was stopped after 806 s.

## The note as written

Profile: Requirements/Planner/Tester/Completion on zai-coding-plan/glm-5.3
(high); Plan Reviewer, Builder, Investigator (bug) and Resolver on
xiaomi-token-plan-sgp/mimo-v2.6-pro (Builder medium). Kilo provider.
`--timeout-minutes 60`. USD = the ledger's historical_estimated_cost_usd
(API-equivalent estimate, not a subscription invoice). No attempt was unpriced
in any run.

**The Mac rebooted during run 4. That wiped /tmp, including all run evidence
directories, so the figures below are from my reads taken right after each
run.**

| Scenario | Verdict | Wall min | Est. USD | Attempts | Rejected / repair | Plan approved at | Pauses |
|---|---|---|---|---|---|---|---|
| bugfix-trivial | PASS (14/14) | 33.9 | 0.763 | 13 | 0 / 0 | +19.2 min | none (harness boundaries only) |
| bugfix-cent-drift | INTERRUPTED_UNGRADED (60-min budget ran out while still planning) | 60.0 | 0.950 | 10 | 1 / 1 | never | 1 user question, at +49.8 min |
| bugfix-iso-weeks | PASS (8/8) | 49.6 | 1.177 | 13 | 1 / 1 | +31.4 min | none |
| feature-timesheet-by-project | unknown: lost in the reboot | - | - | - | - | - | - |

Cumulative known estimate: **$2.89**. Run 4's spend is unrecorded; the next
run would also have been under the $4 stop.

### Per-stage (attempts · fresh in / cached in / output tokens · seconds · USD)

#### bugfix-trivial

| Stage | n | fresh | cached | out | s | USD |
|---|---|---|---|---|---|---|
| model_preflight | 2 | 1,283 | 0 | 21 | 25 | 0.0006 |
| requirements_gather | 1 | 11,308 | 20,608 | 3,289 | 58 | 0.026 |
| investigate_bug | 1 | 15,000 | 191,232 | 8,896 | 174 | 0.073 |
| astra_discovery | 1 | 25,020 | 25,152 | 14,166 | 192 | 0.061 |
| astra_challenge | 1 | 26,119 | 89,088 | 14,155 | 246 | 0.052 |
| glm_revise | 1 | 23,749 | 30,784 | 7,040 | 92 | 0.048 |
| astra_finalize | 1 | 25,501 | 56,576 | 18,913 | 342 | 0.047 |
| terra (Builder) | 1 | 38,013 | 523,392 | 21,071 | 373 | 0.194 |
| sol (Tester) | 1 | 101,995 | 117,824 | 28,865 | 408 | 0.195 |
| astra_review | 1 | 44,711 | 51,136 | 3,978 | 62 | 0.066 |

#### bugfix-cent-drift

| Stage | n | fresh | cached | out | s | USD |
|---|---|---|---|---|---|---|
| model_preflight | 2 | 1,295 | 0 | 21 | 26 | 0.0006 |
| requirements_gather | 1 | 11,668 | 27,968 | 7,823 | 120 | 0.041 |
| investigate_bug | 1 | 53,215 | 287,744 | 45,426 | 768 | 0.157 |
| astra_discovery | 2 | 88,401 | 221,376 | 52,788 | 669 | 0.302 |
| astra_challenge | 2 (2nd interrupted) | 134,184 | 371,072 | 70,948 | 1347 | 0.237 |
| glm_revise | 2 (rejected + report repair) | 71,744 | 93,952 | 51,684 | 630 | 0.213 |

Rejected glm_revise: "Changing a protected contract item 'Tax ties: a 2.00
line's exact tax 0.165 must round half up to 0.17; today round() gives 0.16.'
needs a saved user answer or feedback event; change='reworded'". Sequence:

1. The Plan Reviewer found that the item's "today" note was factually wrong.
2. The reviser's reword was refused, a report repair followed, and a
   non-delegable user question was raised (Q1, answered in 1.4 s).
3. Discovery and the challenge then ran again, and the time budget ran out
   during the second challenge.

Repeated planning: about $0.55 and 27 min.

#### bugfix-iso-weeks

| Stage | n | fresh | cached | out | s | USD |
|---|---|---|---|---|---|---|
| model_preflight | 2 | 1,309 | 0 | 23 | 28 | 0.0006 |
| requirements_gather | 1 | 6,490 | 32,320 | 5,899 | 88 | 0.036 |
| investigate_bug | 1 | 59,060 | 474,240 | 42,858 | 927 | 0.211 |
| astra_discovery | 1 | 32,779 | 60,992 | 18,340 | 251 | 0.097 |
| astra_challenge | 1 | 50,618 | 373,888 | 31,507 | 570 | 0.165 |
| terra (Builder) | 1 | 51,514 | 801,024 | 21,011 | 451 | 0.281 |
| sol (Tester) | 1 | 68,765 | 283,584 | 32,788 | 452 | 0.284 |
| astra_review | 2 (rejected + report repair) | 66,895 | 66,944 | 10,064 | 147 | 0.102 |

Rejected astra_review: "$.contract_hash: invalid enum" (the reviewer wrote a
wrong contract hash); one report repair fixed it.

### Where the money goes

- Before any code is written (Requirements → Investigator → discovery → plan
  review/revise/finalize): 30% of trivial ($0.23), 44% of iso-weeks ($0.51)
  and 100% of cent-drift ($0.95).
- Builder + Tester: 51% of trivial and 48% of iso-weeks. Cached input
  dominates: the Builder re-reads 0.5–0.8 M cached tokens. Fresh input is high
  on the Tester (102k in trivial).
- Investigator: 3–15 min. Discovery then explores the same code again
  (discovery's fresh input is 25–88k).
- Waste in this sample: one planning loop from a refused protected-item
  correction (≈$0.55, run never finished), and two report repairs from schema
  errors.

### Run 4 (re-run 2026-10-26): feature-timesheet-by-project, master c9f4063b7

Note: master moved from eeb64352f (runs 1–3) to c9f4063b7. The checkout was
~/autocode-cost-measure; evidence is in
~/autocode-cost-measure-out/feature-timesheet-by-project/.

| Scenario | Verdict | Wall min | Est. USD | Attempts | Rejected / repair | Plan approved at | Pauses |
|---|---|---|---|---|---|---|---|
| feature-timesheet-by-project | INTERRUPTED_UNGRADED (60-min budget ran out during the Builder) | 60.0 | 1.084 (0 unpriced) | 12 | 2 rejected (1 of them a repair) / 2 report repairs | +46.4 min | none (harness approve-plan at the boundary; no questions) |

| Stage | n | fresh | cached | out | s | USD |
|---|---|---|---|---|---|---|
| model_preflight | 3 | 1,727 | 256 | 35 | 38 | 0.0008 |
| requirements_gather | 1 | 13,853 | 22,912 | 6,032 | 96 | 0.035 |
| astra_discovery | 1 | 27,417 | 47,360 | 17,205 | 243 | 0.083 |
| astra_challenge | 1 | 119,259 | 157,696 | 44,223 | 869 | 0.136 |
| glm_revise | 3 (rejected, repair rejected, repair) | 69,772 | 74,880 | 38,297 | 616 | 0.171 |
| astra_finalize | 1 | 76,908 | 391,040 | 44,467 | 887 | 0.194 |
| terra (Builder) | 1 (interrupted at the budget) | 73,812 | 1,337,088 | 34,266 | 805 | 0.464 |

Rejections:

- glm_revise: "$.responses[3]: missing change" (schema).
- glm_revise report repair: "Planner revision drops or changes 'Hours
  formatting and alignment of the plain weekly report must not change; output
  without `--by-project` ... stays byte-identical.' without a user-backed
  contract change" (the protected-contract guard again).
- A second report repair was accepted.

Timing:

- Planning (Requirements → finalize) took 46 min and $0.62, which is 57% of
  the run.
- The Plan Reviewer's challenge and finalize took 14.5 and 14.8 min (MiMo).
- The Builder then had only 13.4 min left and was cut off after reading
  1.34 M cached tokens.

### Totals across all four

Known estimated spend: $0.763 + $0.950 + $1.177 + $1.084 = **$3.97**. 2 PASS,
2 INTERRUPTED_UNGRADED (both time-budget, both after a protected-contract
rejection loop in planning).
