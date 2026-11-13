# Resolver recovery and reopening verified in VS Code (#3367)

The owner requested actual VS Code extension verification before continuing the
reliability fixes. The actual editor reached the changed recovery path and
reopened the saved chat with its diagnosis intact. A seamless workbench reload
is not established: the blank window required closing and reopening.

## Candidate and fixture

- Runtime: `2c8e96939` plus the local #3367 change in
  `autocode_stuck_job.py`; the Investigator can handle a stuck repair Resolver.
- Extension: the same revision, built with `npm run build`, in an actual
  Extension Development Host. No status view is substituted.
- Task-owned fixture: `/private/tmp/autocode-vscode-recovery-3367.RrX7xh`.
- Real core runs in the offline Linux test image with the existing process and
  tool boundaries. The runtime is copied to Linux-local `/workspace`; all 1,104
  tool files matched the candidate, aggregate SHA-256
  `c2612b6e85d1a8a708e4c95b726707c24bb3298726bcb01fe5a5f3f7d2c99725`.
- The forwarding bridge preserves CLI arguments and issued token environment.
  The project is mounted at its identical absolute path. No host credentials
  are mounted, and the container has no network.
- Only provider output and the Resolver timeout clock are scripted, as in
  `tests.test_investigator_after_exhausted_recovery_cli`.

The public CLI created `resolver-vscode-3367` up to its first question. This
existing-run fixture does not use spec-first mode: the small scripted provider
does not implement acceptance-test authoring. The extension's Full mode is
unchanged. There have been no live model calls.

## Observed in the actual editor

The owner explicitly approved trusting only the generated project folder after
automatic approval review rejected the initial trust action. The trusted
Development Host loaded AutoCode. History listed **Build greeting**, and opening
it showed the actual question, **Should the greeting be a CLI or web endpoint?**,
the proposed CLI answer, and **Send answers and continue**.

After reconnecting native automation, the extension submitted the proposed CLI
answer and displayed revision 2 of the greeting plan. The operating agent read
that exact plan, recorded its review under the 2026-10-29 delegation, and used
the extension's approval action. The saved approval names the delegated agent
review in its Git identity; it does not claim the owner personally reviewed it.

The extension's Continue action ran the task planner, one Builder, and the
Tester and Completion Reviewer. Two interrupted Resolver attempts then reached
exactly one Investigator. The actual sidebar displayed **Resolver: 2 attempts,
2 failed**, a completed **Investigator**, the approved revision 2, and the saved
code change. The run stayed incomplete. Its public recovery reason retains
both idle timeouts and the fixture's diagnosis: it cannot diagnose the failure,
so the existing stop is preserved. The Investigator's ability to produce a
useful real-model diagnosis is not established by this scripted result.

There was one intervening `PAUSED_STALE_VALIDATION`: preparing a verification
copy could not query Git's shallow metadata. The exact read-only Git query
succeeded on inspection. One bounded retry through the extension's named
Continue action reached the second Resolver attempt without discarding the
first failure, approval, or work. This transient fixture failure is retained;
no verification gate or Git ownership rule was relaxed.

After **Developer: Reload Window**, the VS Code workbench was blank and native
automation could see only its window and menus. The extension host restarted
and continued public inventory/status reads. Native automation also
intermittently returned stale snapshots, missing frames and invalid elements.
The blank workbench is not attributed to AutoCode without a reproducer that
distinguishes it from VS Code or automation.

Closing only the task's Development Host window and relaunching the same project
with the same candidate extension restored **Build greeting** automatically.
The actual sidebar retained the approved revision 2, the saved code change,
**Resolver: 2 attempts, 2 failed**, and one completed **Investigator**. Opening
Run details and selecting the exact Development Host through the Window menu
showed `PAUSED_REPEATED_FAILURE` and the retained idle-timeout and Investigator
diagnosis text. No extra retry action was taken during reopening. An attempted
separate disposable profile could not be reliably selected by native automation;
the successful reopen used the original profile and unchanged folder settings.

Read-only public snapshots before reload, after reload and after reopening match
for status, completion, recovery, usage, cost, approval, routes, route assignments
and code checkpoints; the exact contract token and product-file hash also match.
There are 11 model attempts, including one Builder, two timed-out Resolvers and
one Investigator. The displayed dollar/token figures are scripted usage, not
live spend; the two interrupted Resolver attempts correctly remain unpriced.

The first container setup refused Git ownership before saving a run. The
fixture's own Git config now names only its exact project as safe. Cold
bind-mounted source also exceeded the client's 30-second inventory timeout;
the byte-identical Linux-local runtime reduced the registry read to 20.460 s.
These are retained fixture setup findings.

The redacted run bundle has manifest SHA-256
`88069b013e654f964f3ceecb996833a4bad3a1e0d5ea0c15ed2afa24dc211ff7`.
Its export omits the replay recording because an archived check has no retained
output; this bundle is diagnostic evidence, not a qualified replay fixture.

## Automated evidence and qualification boundary

`npm run typecheck` passed. `npm test` passed 504 tests, with 17 optional
real-CLI walkthrough tests skipped because their environment was not configured.
These checks do not replace the native editor flow.

This qualifies the offline existing-run question, exact-plan approval, stuck
Resolver investigation and retained-chat reopening path. It does not qualify
live-model diagnosis, Full-mode intake, or a seamless workbench reload. The
separate #3358 stop/action defect is not claimed fixed; the editor still offers
a person-facing retry for the exhausted scripted failure. No product or gate
change was made to work around either the fixture Git pause or editor reload.

After verification, the task's Development Host window was closed and its idle
Linux container stopped. The fixture, public snapshots and redacted diagnostic
bundle remain on disk; the owner's other editor windows were left open.
