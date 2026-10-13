# Source trace: criteria cite the original request (#595)

## Problem

`brief_acceptance` covers one bounded kind of declaration (a CLI's backticked
exact output). Every other obligation reached completion through derived text
alone: a criterion could invent a requirement nobody asked for, a sentence of
the brief could silently drop, and a quoted limit, literal or negation could
change between the brief and the criteria that "cover" it. The false completion
on 2026-10-01 (`RELIABILITY.md`, `docs/bugs/2026-10-01-reliability-live-cases.md`)
was one shape of that; the Plan Reviewer's prose `BRIEF_TRACE_RULE` and the
mechanical `brief_literals` guard covered only part of it.

Separately, `brief_obligations.sources()` derived source ids positionally over
state a follow-up rewrites, so `task:0` changed identity across turns and a
sealed source binding could no longer be checked against what it named.

## Change

Five pieces, all on the existing brief / Plan Reviewer / completion path; no
new controller, no new import cycle:

1. **Append-only hashed `source_log`** (`autocode_source_log`): seeded at run
   setup from the initial task; appended at the same boundary that saves each
   answer / feedback ledger event. `brief_obligations.sources()` reads it;
   legacy states without the key fall back to positional derivation.
2. **Per-criterion `sources`** and a Plan Reviewer **`source_trace`** seal
   (`autocode_source_trace`): citations of the run's human sources (verbatim
   quotes), plus the sentences the reviewer deliberately leaves out of scope
   and the quoted negations it accepts dropping. Sealed into the contract body
   with the review's execution provenance; a Planner, Builder or edited
   contract cannot replace it. A report without the field seals the strictest
   judgment (nothing out of scope, no dropped negation).
3. **`validate_body` ready gate**: once any criterion cites, every criterion
   must; every source sentence is covered or out of scope; numbers, backticked
   literals and negations inside cited sentences survive in the citing
   criteria (or the contract's restated behaviors / constraints /
   technical_approach). A dropped negation stands only with the reviewer's
   pinned waiver. A contract whose criteria cite nothing predates the trace
   and validates as before.
4. **Completion Reviewer `source_verdict`**: when the approved contract carries
   the seal, the `astra_review` generation schema gains a required
   `source_verdict` (PASS/FAIL, uncovered, summary) and the prompt asks the
   reviewer to judge the delivery against the original sources. The runner's
   mechanical gate stays the completion replay's own revalidation; the verdict
   is the reviewer's attestation beside it.
5. **Providers and fixtures**: the scenario fake, the CLI fixture provider,
   the live-fixture provider and the multicomponent fake emit citations and
   `source_trace` / `source_verdict` so `--fake` stays green. The Resolver's
   fail-closed contract validator accepts the new runner-owned key and
   criterion field. Progressive approval's exact-contract check ignores
   runner-owned keys.

## Evidence

- Unit: `tests/test_source_log.py`, `tests/test_source_trace.py` (named
  regressions: dropped negation, changed numeric limit, omitted constraint,
  invented requirement, dropped non-goal, acceptable paraphrase, follow-up
  preserves earlier sources byte for byte; schema conditionality for
  `source_verdict`).
- Adjacent: `tests/test_brief_obligations`, `tests/test_brief_evidence`,
  `tests/test_workflow`, `tests/test_brief_cli`, progressive modules.
- Fake: `greenfield-todo-cli` → `TASK_COMPLETE` oracle 11/11; the
  `broken/unbracketed-status` mutant → `HONEST_BLOCKER` (oracle 7/10);
  `requirements_drops_literal` → not `TASK_COMPLETE` / not `FALSE_COMPLETE`.
- Live / fake catalog: owed on a host that can launch providers. On Darwin,
  current master pauses every launch with `PAUSED_PROCESS_CLEANUP` /
  `Provider session ownership requires Linux native x86_64 or aarch64`
  (#454 / #1187); the same `greenfield-todo-cli --fake` HONEST_BLOCKER
  reproduces on tip master without this change. Before that tip, fake
  greenfield reached TASK_COMPLETE 11/11 with the source-trace emitters.

## Limits

The gate is mechanical over quoted spans. Semantic fidelity of a derived
example that invents new text (padding counted from a layout rule with no
sample in the brief) is still the Plan Reviewer's check, as #676's
base-revision note and the existing brief-example receipt already cover for
that shape. Checking every worked example against the brief still depends
partly on a model; this change makes the handoff machine-checkable where the
rule is exact.
