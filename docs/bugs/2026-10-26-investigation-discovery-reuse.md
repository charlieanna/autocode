# Investigation evidence in discovery (#2134)

Master `1f69347e8` already passed the Investigator's diagnosis to planning. It did
not say whether its evidence was still current, and `discovery.md` unconditionally
requested another 5–8 source reads. A diagnosis's presence alone therefore did not
provide a safe way to avoid rediscovering its baseline facts.

The investigation unit now brackets the runner's reproduction with the existing
complete execution identity and seals a planning-only receipt after the probe and
its counterexample pass. The receipt pins the original run, stage, iteration,
report, before-source artifact, cited source hashes, human requirements, and the
reproduction's runtime/dependencies/environment. The exact runner-written note is
the sole permitted source difference during that handoff. No raw environment
values enter the packet. The existing `investigation` record owns this nested
metadata; no old state or accounting is rewritten.

Before Requirements/Planner discovery receives reusable context, the runner
rechecks every pin. A different source file, test, configuration, dependency,
environment, requirement, report, receipt, attempt or turn requires fresh
discovery. Source hashes cover the whole Git-visible tree, not only the reported
affected files; ignored launch inputs and installed dependencies stay bound by the
execution identity. `cited_sources` are the diagnosis's references, not a complete
or authenticated count of the model's file reads. Uncovered and conflicting claims
still need investigation. External/nondeterministic behavior is never established
as current by these local bindings.

This uses the conservative runtime identity already available: a directly invoked
isolated Python `-c` reproduction, optionally with `-B`. Literal environment
assignments are accepted only if they match the clean execution environment and
are independent of scratch-tree paths; the environment is rechecked during
identity collection. The identity collector inspects that interpreter without
executing the probe text. Shell compositions,
command substitutions, other runtimes, global/unbound dependencies, missing pins
and legacy reports remain fresh-discovery cases. A refused reuse binding does not
reject an otherwise valid diagnosis or bypass any launch-input guard. Extending
supported identities should use the existing runtime contract, not weaker keys.

Discovery now starts with an eligible diagnosis and its recorded baseline
observation instead of repeating the same reads/reproduction solely to copy them.
This can also prevent a guessed baseline note (#2133). It grants no protected
criterion edit, stage omission, implementation approval or completion evidence.
The Plan Reviewer, exact-plan gate, Tester and Completion Reviewer remain separate.

## Verification

- The public Linux TaskRun regression runs a supervised fake Investigator,
  independently reproduces the local bug and discriminates its counterexample,
  then inspects the actual discovery packet. There is exactly one Investigator
  call and one discovery call; source stays unfixed and the run cannot complete.
- The same regression against unchanged master fails with missing
  `bug_diagnosis_evidence`, rather than passing a fake response unconditionally.
- Focused negative controls cover source/test/config changes, additions/deletions,
  human requirements, diagnosis/report/receipt tampering, old/foreign attempts,
  unsupported runtimes and shell syntax, and mutations while the probe runs.
  Real Linux checks change an ignored installed dependency and environment input;
  each changes the runtime identity.
- Five discovery prompt fingerprints change; the other 188 remain identical.

No paid comparison or token/latency saving is claimed. The old #2134 cost figures
lack their retained note/run identities, so they remain reported observations.
A future matched live measurement must retain the same scenario, source, models,
efforts and independent verdict, count discovery reads/tokens/time including
failed attempts, and include binding overhead. Handoff availability proves none
of those savings by itself.
