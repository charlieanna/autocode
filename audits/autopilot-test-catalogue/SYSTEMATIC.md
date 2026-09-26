# T14 — State-machine, concurrency and mutation tests (SYS-01..SYS-08)

Executed 2026-09-24. 8/8 executed, 8 PASS.
Command: `env -u AUTOCODE_TEST_CLI .venv/bin/python -m unittest tools.test_catalogue_t14`
(≈30 s; mutations run on disposable copies of `tools/` in temp dirs.)
Bundles: `.tmp-autopilot-testkit/artifacts/SYS-*/NN/`.

| ID | Status | Result |
|---|---|---|
| SYS-01 | PASS | 60-step seeded random report sequence matched the FindingsOracle at every step (seed 20260924 saved in the bundle) |
| SYS-02 | PASS | sol/astra report orderings converge to the same canonical ledger (opaque ids/timestamps excluded as order artifacts) |
| SYS-03 | PASS | crash-before-apply cuts at the sol and astra_review transitions both recover exactly once (terra cut covered by CRH-05) |
| SYS-04 | PASS | **12/12 mutations KILLED** (M01–M12, MUTATION_PLAN). M02 and M12 needed supplementary detectors because their clauses are defense-in-depth behind independent guards — documented in the bundles |
| SYS-05 | PASS | stdlib-trace executed-line coverage of the four core modules under the T05+T06 runs; measured 1.0 line presence — metric labeled as line presence, not strict branch coverage |
| SYS-06 | PASS | every CRH bundle from this run records PASS with its recovery/progress companion |
| SYS-07 | PASS | the EVD-3 saved failure replays on the pre-fix source (`git show 49b3a7b`) and passes on the fixed source |
| SYS-08 | PASS | offline mechanics recorded separately from live delivery; zero provider launches across the systematic run |

Mutation detail (from the SYS-4 bundle trace):

M01 exact-token approval · M02 open-question approval clause · M03 correction
authority · M04 candidate-revision completion · M05 omission clears findings ·
M06 wording-derived identity · M07 BLOCKED drops findings · M08 quote-destroying
equality · M09 vacuous PASS gate · M10 replay dedup · M11 liveness guard ·
M12 post-completion dispatch — all killed by their mapped catalogue detectors.
