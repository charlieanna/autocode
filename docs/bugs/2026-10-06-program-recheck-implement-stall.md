# A program re-check that plans an implementation stalls when nothing needs changing

Open; found while building the `program-notes-cli` scenario for #22 and #23
([Programs](../program.md#revisions-and-stale)).

An approved agreement revision re-checks every workstream whose part of the agreement
it changed: the workstream becomes `STALE`, and a fresh run plans, asks for approval
and verifies again. An accepted interface change request is such a revision; it
re-checks the interface's producer and every consumer. A merged producer whose files
already conform to the new version has nothing to change. Its re-check's brief says
so ("keep what still conforms, change what does not, and verify again"), but if the
planner still plans an `implement` task, the run cannot finish:

- the Builder changes nothing, three times;
- the Resolver stops with `operational_exhaustion`, and the run waits at
  `WAITING_FOR_USER`;
- the program shows plain `WAITING`. The re-checked producer never merges, so its
  consumers stay `STALE` and the final check never starts.

## Evidence

A scratch run of `program-notes-cli` (not kept in the repository) with the scripted
model forced to plan `implement` on the re-check. After the change request on the
`store` interface was accepted as revision 2, the producer S's re-check ran
`requirements_gather`, `astra_discovery`, `astra_challenge`, `orchestrator`, `terra`
three times and the Resolver, and stopped at `WAITING_FOR_USER` (scope
`operational_exhaustion`). T and U stayed `STALE`, the integration workstream
`PENDING`. Verdict `HONEST_BLOCKER`, oracle 13 of 23 checks.

The catalog scenario passes because its scripted model plans a validate-only task when
a workstream's files already match the solution, and that re-check completes. That is
the fake's choice, not the product's; whether a real planner plans validation there is
not known (no live run has reached a re-check).

## Options

- Let a re-check accept a Builder that changes nothing when the cumulative checks
  pass.
- Tell the planner explicitly, in the re-check brief, to plan validation for files that
  already conform.
