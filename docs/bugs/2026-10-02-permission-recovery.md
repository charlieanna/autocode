# Repeated external-directory denials

Issue: [#228](https://github.com/charlieanna/autocode/issues/228).

Before this change, a fresh Builder request received advice about workspace-only
diagnostics but no provisioned scratch path. The CLI regression observed three
denied attempts before the aggregate allowance stopped the run; the corrected
diagnostic could not find a supplied directory.

Recovery now provides a directory below `.autocode/recovery-evidence/`, scoped
to the run and incident, and carries the denied capability, path and
classification in the existing recovery record. Temporary filename changes do
not create a new incident. A second denial holds before another request, with
the stopped attempts, partial edits and counters retained. An unchanged restart
cannot bypass that hold. Existing allowances still apply after a source change.
Separate runs cannot share scratch files, and symlinked directory ancestors
are refused. The Builder artifact policy names the same directory as the
recovery handoff, so its ordinary instructions do not contradict recovery.
No permission policy or completion gate is expanded.

Reproduce the offline regression and its controls:

```sh
.venv/bin/python -m unittest tests.test_autocode_permission_recovery tests.test_permission_recovery_cli
```

The positive control completes only after normal independent validation and
completion review. The broken greeting variant remains incomplete after its
diagnostic succeeds. Repeated-denial controls replay two CLI restarts and check
unchanged attempt counts, preserved source and byte-identical archived logs.

Native qualification on 2026-10-02 replayed the corrected CLI handoff against
three isolated synthetic fixtures through the configured OpenCode connections:
GLM-5.3 (`zai-coding-plan`), MiMo-2.6-Pro (`xiaomi-token-plan-sgp`), and GPT-6 Sol
(`openai`). Each request had a 180-second cap. All three used the supplied
directory, wrote the correct input hash, executed the diagnostic checker,
passed a separately executed check, preserved source and returned a valid
report with `task_complete: false`. These are native diagnostic probes, not
three complete live builds; recurrence and restart are proved by CLI tests.

Final evidence is ignored under `.scenario-runs/permission-recovery-live/4a5e567c6e/`.
Initial harness attempts and report followups are retained under `ac36318a0a/`
as invalid qualification attempts: the initial prompt omitted schema injection,
and the followup supplied paths relative to the launcher's directory. An
intermediate qualification (`c0dd46e270/`) also passed after evaluating MiMo's
valid relative receipt from its workspace. Run-scoped paths were qualified in
`2df4ddda95/`. Review then caught conflicting Builder artifact instructions;
the strengthened CLI test failed until the two paths were aligned. Final native
qualification replayed both actual policy fields together, supplied the schema,
and resolved relative references from the fixture workspace. All three passed
directly. Previous receipts and results remain unchanged.
