# Report repair rewrites the history it must copy, and a stale handoff drops the fix

Date: 2026-10-05
Found during: the three RELIABILITY.md live cases on Claude models, master `d5484d81`
(profile `claude-tiers`, three runs per case; see `RELIABILITY.md`)

## Symptom

A correct deliverable is left unfinished. The Builder's report is rejected for one bad
evidence ref, every report repair then changes execution history the repair prompt says to
copy exactly, each repair is rejected for that, and the run stops at `PAUSED_STALE_HANDOFF`
after the Investigator has already diagnosed the problem and written the correct retry.
Oracle 10/10 — the code and tests were right throughout.

One run of nine hit this (`greenfield-todo-cli`, run 2 of 3). No run reported completion it
had not earned.

## Reproduction

The run's own archived reports show the whole chain, in
`iterations/002/` of the run directory:

```text
archived-builder-01-b1fb45/builder-01.json              the original report
archived-builder-report-repair-01-5aa3cc/…-01.json      first repair
archived-builder-report-repair-02-948e51/…-02.json      second repair
stuck-investigation-01.json                             the Investigator's diagnosis
```

The original cites `.autocode/evidence/test-without-todo` in `evidence_refs`. That path is a
directory (a copy of the workspace), not a capture receipt, so the report is rejected.

The repairs do fix that ref, and only that needed fixing. But each also rewrites fields the
prompt (`tools/autocode_report_repair.py`) says to copy verbatim from `original_report`:

| Field | `builder-01.json` | after repair 2 |
| --- | --- | --- |
| `results` | 3 strings | 2 different strings |
| `remaining_risks` | `[]` | 1 new entry |
| `evidence_refs` | 3 (one bad) | 2 (both valid) |

So the runner rejects each repair with `Report repair changed recorded Builder history: results`.

The Investigator names all of this exactly, and its guidance is a correct repair: copy the
history fields with no change at all and drop the one bad ref. That guidance is never applied
— the run stops at `PAUSED_STALE_HANDOFF`, `Recovery packet: current source, task, settings,
contract or scope changed before admission`.

## Why this is the third sweep in a row at this boundary

`RELIABILITY.md` has recorded the same shape since 2026-10-01: a handoff that asks a model to
preserve or transcribe something exactly, and nothing independent checks that it did. First
the brief-to-criteria transcription (a false completion), then Validators citing `event:` IDs
their provider rejects. This one is the report-repair handoff: the instruction to copy
`commands_run`, `results`, `changed_files`, `remaining_risks`, `untested_behavior`,
`addressed_requirements` and `deferred_backlog` exactly is only a prompt rule.

A repair legitimately edits `summary`, `recommended_checks` and `evidence_refs`. It has no
reason to touch the rest, and when it does the repair is thrown away and a correct run is
abandoned.

## Suggested fix

Stop relying on the model to copy those fields. After a repair, restore the seven history
fields from `original_report` mechanically and keep only the repair's `evidence_refs`,
`summary` and `recommended_checks`; then validate as today. A repair that only had a bad ref
to drop would then pass on its first attempt, and the repair loop would stop spending calls
on a change that must be undone anyway. Six of nine runs in this sweep needed at least one
report repair (six in one run alone), so the loop is common, not an edge case.

Second, smaller: the Investigator's guidance was right and still lost. A recovery packet whose
only content is a stage-output repair should not be refused because unrelated state moved; or
the refusal should say what to re-run. As it stands the one agent that diagnosed the problem
cannot act on its own answer.

## Not a model-quality problem

The Builder's code and tests passed every oracle check (10/10), the bad ref was a directory
path rather than a receipt, and both repairs got the ref fix right. The loss is in the
report-repair bookkeeping, and in recovery guidance that reached the user as a dead end.
