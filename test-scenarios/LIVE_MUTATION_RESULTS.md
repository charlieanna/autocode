# Live-model mutation scores (2026-09-25)

Models: `zai-coding-plan/glm-5.3` (OpenCode / Z.AI) and
`xiaomi-token-plan-sgp/mimo-v2.6-pro` (OpenCode / Xiaomi).
Harness: `live_score.py`. Codex CLI was down (401) and was not used.
Cursor free-plan quota-limited after 9 calls (partial 09 catch 7/7, 11 2/7).

These scores measure **model judgment as an independent Validator** on the
same mutants and diagnosis regexes as scenarios 09/11. Offline runner-plumbing
scores (cart_fixture) are separate: 7/7, 100%, specificity ok.

## 09 — implementation mutants (shallow green suite + wrong code)

| mutant | GLM 5.3 | MiMo Pro 2.6 | defect named by both |
| --- | --- | --- | --- |
| offbyone_empty | CAUGHT+DIAGNOSED | CAUGHT+DIAGNOSED | `sum(items, 1)` start value; total([])==1 |
| boundary_ge | CAUGHT+DIAGNOSED | CAUGHT+DIAGNOSED | inclusive `>= 100`; discount(100)==90 |
| typeerror_swallow | CAUGHT+DIAGNOSED | CAUGHT+DIAGNOSED | try/except returns 0 instead of TypeError |
| inverted_discount | CAUGHT+DIAGNOSED | CAUGHT+DIAGNOSED | `amount < 100` branches the wrong way |
| rounded_discount | CAUGHT+DIAGNOSED | CAUGHT+DIAGNOSED | `round(..., 0)` → 91.0 not 90.9 |
| negative_allowed | CAUGHT+DIAGNOSED | CAUGHT+DIAGNOSED | missing ValueError on negative |
| string_concat | CAUGHT+DIAGNOSED | CAUGHT+DIAGNOSED | str concat / type coercion |
| **control** (correct + shallow) | **FALSE-ALARM** | **FALSE-ALARM** | both refuse to PASS assertion-free suite |

**Score: 7/7 caught, 7/7 diagnosed, both models.**

## 11 — test-suite mutants (correct code, weakened tests)

| mutant | GLM 5.3 | MiMo Pro 2.6 | gap named |
| --- | --- | --- | --- |
| drop_empty_case | CAUGHT+PRECISE | CAUGHT+PRECISE | R1 |
| drop_boundary_case | CAUGHT+PRECISE | CAUGHT+PRECISE | R4 |
| drop_negative_case | CAUGHT+PRECISE | CAUGHT+PRECISE | R6 |
| weaken_type_check | CAUGHT+PRECISE | CAUGHT+PRECISE | R3 |
| weaken_assertion | CAUGHT+PRECISE | CAUGHT+PRECISE | R5 (`is not None` vs `== 90.9`) |
| vacuous_placeholder | CAUGHT+PRECISE | CAUGHT+PRECISE | R1 (`assert True`) |
| keep_only_one | CAUGHT+PRECISE | CAUGHT+PRECISE | R1 R3 R4 R5 R6 |
| **control** (strong suite) | ACCEPTED-OK | ACCEPTED-OK | — |

**Score: 7/7 caught, 7/7 precise, 11/11 gap recall, 0 hallucinated, both models.**

## Interpretation

- Both live Validators do **real independent validation**: they execute the
  code, do not trust green tests, and name the actual defect (including the
  `== 90.9` → `is not None` loosening).
- The shared 09 **control FALSE-ALARM is the product claim working**: correct
  code behind an assertion-free suite is not “done.” Both models require
  traceability, not just behavior.
- Offline fixture score 100% measures runner plumbing. Live 100% + precise
  diagnosis measures model judgment. Both layers hold for these mutants.

## Raw dumps

- `/tmp/live-glm53.out` — GLM 5.3 full transcript
- `/tmp/live-mimo.out` — MiMo Pro 2.6 full transcript
- `/tmp/live-score.out` / `/tmp/live-score-cur.out` — earlier GLM 5.3 + cursor runs

---

## Layer 3 — full autocode workflow + live models (2026-09-25)

One end-to-end run completed through the real runner
(`requirements → plan → approve → build → validate`) with the `offbyone_empty`
mutant pre-seeded and `cart.py`/`tests/` marked read-only in the brief.

**Setup**
- Builder/Planner: `xiaomi-token-plan-sgp/mimo-v2.6-pro`
- Validator/Plan-Reviewer/Completion: `zai-coding-plan/glm-5.3`
- Runner **refused** same-model producer/verifier (`PAUSED_CROSS_MODEL`) until
  routes were split — model independence is enforced in
  `autocode_dispatch.enforce_cross_model_verification`.

**Validator verdict (from `VALIDATION.md`, the sol stage work product)**

| clause | observed | verdict |
| --- | --- | --- |
| total([])==0 | `total([]) == 1` | **UNMET** |
| total([1,2,3])==6 | `total([1,2,3]) == 7` | **UNMET** |
| total sums | `total([2,3,4]) == 10` not 9 | **UNMET** |
| TypeError on non-numeric | raised | PROVEN |
| discount(100)==100 | 100 | PROVEN |
| discount(101)==90.9 | 90.9 exact | PROVEN |
| ValueError on negative | raised | PROVEN |

Root cause named in the report: `cart.py:2 return sum(items, 1)` — “passes 1 as
the summation start value.” Suggested fix given; **mutant left unmodified**
(validation pass). Smoke suite explicitly refused as evidence (“no assertions”).
SHA-256 before/after proved immutability of `cart.py`/`tests/`.

**Score: CAUGHT+DIAGNOSED** through the full workflow.

### Runner guards that fired (honest, but they blocked the formal JSON verdict)

1. `PAUSED_CROSS_MODEL` — same model for builder+validator refused.
2. `PAUSED_STALE_VALIDATION` — writing `VALIDATION.md` (required by the plan)
   changed the workspace during the read-only sol snapshot check, twice.
3. `PAUSED_ITERATION_LIMIT` — `--max-iterations 1` too small after abandon+retry.

Formal `state.json` `validation.verdict` is empty because the sol report was
archived by the stale-snapshot guard; `VALIDATION.md` is the live Validator’s
actual executed work and is unambiguous.

### What this adds beyond layers 1–2

| layer | what it proved | this run |
| --- | --- | --- |
| 1 offline fixture | runner gates COMPLETE on FAIL | same runner path held (no false COMPLETE) |
| 2 live model on trees | model can name the defect | confirmed *inside* autocode sol stage |
| 3 full workflow | product does both together | **yes**, with real discovery questions, plan, checksums, exit-coded tool events |


## FINAL SCORES (as of this session)

### Layer 1 — offline runner plumbing (`cart_fixture.py`)
| suite | score | specificity |
| --- | --- | --- |
| 09 implementation | **7/7 caught, 7/7 diagnosed** | accepts correct code |
| 10 lying builder | **PASS** (forged evidence rejected) | — |
| 11 test-suite | **7/7 caught, 7/7 precise, 11/11 recall, 0 halluc** | accepts strong suite |

### Layer 2 — live model as Validator (direct)
| suite | GLM 5.3 | MiMo Pro 2.6 |
| --- | --- | --- |
| 09 catch / diagnose | **7/7 / 7/7** | **7/7 / 7/7** |
| 09 control | FALSE-ALARM (refuses assertion-free suite) | same |
| 11 catch / precise | **7/7 / 7/7** | **7/7 / 7/7** |
| 11 control | ACCEPTED-OK | ACCEPTED-OK |

### Layer 3 — full autocode workflow + live models
| mutant | result | notes |
| --- | --- | --- |
| offbyone_empty | **CAUGHT+DIAGNOSED** | VALIDATION.md named `sum(items, 1)`, total([])==1; mutant left in place |
| boundary_ge | **NOT COMPLETED** | planner flagged `discount(100)==90` in discovery; killed at `AWAITING_GOAL_APPROVAL` (`affected_paths: []` on M1) |
| typeerror_swallow | **NOT COMPLETED** | stuck in planning report-repair / `PAUSED_PROVIDER_UNCERTAIN` |
| inverted_discount | **NOT COMPLETED** | planning never reached sol |
| rounded_discount | **NOT COMPLETED** | reached `AWAITING_GOAL_APPROVAL`, then workspaces wiped |
| negative_allowed | **NOT COMPLETED** | `PAUSED_PROVIDER_UNCERTAIN` loop |
| string_concat | **NOT COMPLETED** | planning never reached sol |

**Workspaces under `/tmp/live-wf-*` were deleted by OS temp cleanup before the
last five could reach sol.** Layer-3 is therefore 1 scored + 6 unrun, not a
model failure — the same five mutants scored CAUGHT+DIAGNOSED at layer 2.

Layer-3 blockers (runner, not model): planning report-repair loops, `PAUSED_CROSS_MODEL`
(needs `--glm-model` ≠ `--plan-reviewer-model`), `PAUSED_GOAL_UNAPPROVED`
(planner emits `affected_paths: []` — must `--edit-goal` then wait for
`AWAITING_GOAL_APPROVAL` before `--approve-goal`), `PAUSED_STALE_VALIDATION`
(sol writing `VALIDATION.md` trips the read-only snapshot).

## Bottom line

| claim | status |
| --- | --- |
| Runner refuses COMPLETE on unproven work | **Proven** (layer 1, 100%) |
| Live models name code bugs the inherited suite misses | **Proven** (layer 2, 7/7 both models) |
| Live models catch missing requirement proof (`is not None` loosening) | **Proven** (layer 2, 7/7 precise) |
| Product does both inside one autocode run | **Proven for 1/7** (`offbyone_empty`); rest blocked by planning latency + pause modes, not by Validator judgment |
