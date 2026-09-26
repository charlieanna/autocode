# AutoCode honesty scenarios — adapted to the real CLI

This harness implements the eight black-box scenarios from the original draft,
rewritten against this repository's actual CLI surface after probing
(`./lib.sh --probe`). The draft assumed `autocode plan brief|run|status|
resume --plan|--doctor|config --check` with a JSON `--config`; none of those
exist.

## Flag mapping used

| Draft assumed | Real CLI |
| --- | --- |
| `autocode -C REPO run BRIEF` | `autocode "brief" --workspace REPO --engine codex [--in-place] [--chat]` |
| `plan brief --phase requirements` | first launch pauses at `DISCOVERING`/`WAITING_FOR_USER`; questions via `--answer Q1=…`; approval gate is `AWAITING_GOAL_APPROVAL` |
| `status` | `--run-dir RUN --status` (JSON), or read `<workspace>/.autocode/runs/<id>/state.json` |
| `resume --plan X` | `--run-dir RUN` (plain relaunch) or `--resume-paused` for `PAUSED_*` statuses |
| `--parallel 2` | `--max-parallel-builders 2` |
| `--config stub.json` (forced verdict / budgets) | fixture hooks: `AUTOCODE_FIXTURE_MODE=rework|stalled|milestones|standard`, `AUTOCODE_FIXTURE_QUOTA_STAGE=terra`, `AUTOCODE_FIXTURE_SLOW_STAGE=terra` (added by `codex_shim.py`) |
| `--doctor` / `config --check` | do not exist; probe records that |

## Provider

Every run is offline and deterministic: `lib.sh` copies `tools/fake_codex.py`
onto `PATH` as `codex` (via `codex_shim.py`) and launches with `--engine
codex`. `AUTOCODE_HOME` is isolated per harness run so the real registry is
never touched. The entry point defaults to `python3 tools/autocode.py` from
this checkout (set `AUTOCODE_BIN=/path/to/autocode` to test an installed CLI).

## What the scenarios prove — and what they cannot

Deterministic (runner mechanics): crash atomicity + exactly-once resume (03),
bounded retry/escalation + iteration-budget pause (05), quota pause →
PAUSED_BUDGET + resume (06), evidence substance + behavioral completion (07),
dirty-workspace isolation (08), approval gates before any build (01),
per-criterion evidence gates (02), milestone ordering/isolation (04).

Live-model only (recorded as REVIEW rows, per the repo's own testing doc):
gatherer question quality on vague prose (01), rejecting vague criteria
(02), planner file-overlap detection (04), judging hollow test suites (07).

## Run

```sh
./run-all.sh          # probes, runs scenarios 01–08, prints summary table
./lib.sh --probe      # CLI surface dump only
```

Evidence (logs, per-run state copies, probe output) is under the printed
`WORK_ROOT` (a fresh `/tmp/autocode-sc.*` per invocation).

## Findings so far (2026-09-25 run)

- **06 (budget exhaustion)**: all honesty gates hold — quota failure maps to
  `PAUSED_BUDGET`, plain `--resume-paused` refuses to auto-replay the
  uncertain stage (as designed), COMPLETE is never claimed. But the recovery
  path the pause message itself prescribes (`--abandon-stage <attempt>` →
  `--resume-paused`) dead-ends: the Builder is never re-dispatched, the
  completion gate honestly refuses three times, and the run settles in
  `PAUSED_REPEATED_FAILURE` with no documented command that reaches COMPLETE.
  Recoverability gap worth triaging; evidence in `evidence/s06-resume*.log`.
- **03 (crash/resume)**: SIGKILL of the whole process group mid-Builder left
  atomic non-complete state (no tmp/partial files), a `resolver` stage
  recorded the recovery, the Builder ran exactly once after resume
  (stage-prefix stable), and the finished artifact behaves correctly. When
  the kill leaves an orphaned provider process, resume correctly refuses
  (`PAUSED_WORKSPACE_BUSY`) until it is gone.

## Mutation suite (scenarios 09–11)

These use `cart_fixture.py` (harness-only) as the Validator: it executes cart
spec behaviors, checks requirement coverage in the test suite, and refuses
builder-claimed evidence. Scores therefore measure the **runner's plumbing**
of independent verdicts (deliver code → record findings → gate COMPLETE), not
whether a live model would judge this sharply. Live-model trial is separate.

| Scenario | Score | Control | Diagnosis |
| --- | --- | --- | --- |
| 09 implementation mutants | 7/7 caught, 100% | accepts correct code | 7/7 name the actual defect |
| 10 lying builder | forged evidence rejected, no COMPLETE | — | flags unverifiable claims |
| 11 test-suite mutants | 7/7 caught, 100% | accepts strong suite | 7/7 name every gap; 0 hallucinations |

What the runner had to get right for these to pass (all verified):

1. `sol` stage hands the *current* tree to the Validator (not a canned
   greet.py path).
2. Validator reports must be schema-valid (`v3-sol`, `additionalProperties:
   false`) and use only **contract-approved criterion IDs** (`C1`, …) —
   inventing `R1`–`R6` is rejected as `Criterion evidence references a
   missing executed event` / `unique approved IDs`.
3. Each `event:<id>` evidence ref must match exactly one
   `item.completed` / `command_execution` item in the event stream.
4. A FAIL verdict reaches REWORK, then the iteration ceiling — COMPLETE is
   never claimed on unproven work.

Weakened-test mutants that merely *delete* a case are caught by any marker
scan. The sharp one is `weaken_assertion` (`assert discount(101) == 90.9`
→ `is not None`): it looks covered unless the check requires the expected
*value*, not just the call. Coverage markers that key on `discount(101)`
miss it; markers that key on `90.9` catch it. That is the decay real suites
actually suffer — add domain-specific loosening mutants before trusting a
score.
