# Replay a saved run

[← Back to README](../README.md)

Replay drives a fresh AutoCode task run with a saved brief, model routes and
provider events. It makes no model calls. Normal replay runs the current
verification checks again; a previous PASS cannot establish a current PASS.
The original run and its evidence remain unchanged.

```sh
# Export a portable recording from a saved run.
autocode replay /work/project/.autocode/runs/RUN --export /work/recording

# Re-drive it in an empty, isolated workspace at this exact fresh run path.
autocode replay /work/recording --into /work/reproduced/.autocode/runs/reproduction

# A saved run can also be replayed directly; export happens before launch.
autocode replay /work/project/.autocode/runs/RUN \
  --into /work/reproduced-again/.autocode/runs/reproduction --json
```

The destination must be an immediate child of the new workspace's
`.autocode/runs/`. The workspace must be empty before replay prepares its
recorded initial source. Existing run directories, including empty ones, and
symlink destinations are refused. A replay is created at its selected path;
its bound artifacts are never moved from a temporary run directory.

An explicit initial `--workflow` choice is replayed only at creation. A job
chosen by the model keeps its recorded Job recognizer dispatch; replay does not
infer a workflow pin from the final job or skip that dispatch. Older bundles
without this explicit choice retain their original validation behavior.

The recording includes only the selected source payloads, stage artifacts,
launch policy and semantic operator decisions. It does not carry the original
provider configuration, credentials, environment, Git hooks or approval tokens.
Its source and artifact payloads have digest and size checks. Inspect a bundle
before sharing it: source files and model-authored prose can themselves contain
private project data.

Every recorded provider dispatch must match the current stage, occurrence,
model route, session continuation and source. Plan approvals and answers also
must match the current public request, including a plan's scope exclusions and
Git delivery terms. Older recordings that omitted those fields cannot authorize
new nonempty exclusions or a commit obligation; replay refuses that mismatch.
Replay displays the new plan and uses
fresh tokens from that run. It never invents an answer or adopts an old token
after a mismatch. A project's `require-approver` does not apply: no person approves
in a replay ([Required approver](execution.md#required-approver)).

Replay stops with `REPLAY_REFUSED` when a payload is missing, changed or outside
the supported format, or when the current controller asks for an unrecorded
stage or different operator decision. It cannot reconstruct already overwritten
evidence. A historical hash snapshot is sufficient only when its exact bytes
can still be recovered from a saved source capture, a Git revision or a matching
current file. The final tree cannot substitute for a missing intermediate tree.
A recording that ended before a trustworthy provider exit cannot be emitted as
a completed attempt.

A recorded answer may name an identity that digests where the original run
happened, such as the independent Tester's changed-line `gap_id`s (each one
digests the proof's source revision and execution context). The recording keeps
what each one stably identifies (the gap's exact file, bytes and line), and
replay re-derives the current value from that identity. A citation of the proof
file itself binds to the replay's own proof, whatever its directory is named. A
gap whose file, bytes or line differ in the replay is not rebound, and a replay whose Tester runs
without a current changed-line proof refuses rather than guess.
The archived check commands compare without the interpreter's location: `/usr/bin/python3 -m
unittest discover -v` on the recording host is the same obligation as the replay host's own
`python3`.

Replay preserves the Plan Reviewer's archived effective reasoning effort instead
of reducing it again for a small task. Review allowances still apply, and each
dispatch must match its recorded route exactly. Optional provider `light_effort`
values remain part of the portable role configuration.

The format stores the saved role configuration, not its complete configuration
history. If one role used different efforts at earlier stages, its final saved
effort may not reproduce those stages; replay refuses that mismatch. It does not
guess an earlier tier for legacy recordings.

The first recording format supports serial Builder dispatches. Export refuses
concurrent Builders, retained protected-test files or commands, and external
visual or Figma startup. Those paths need additional portable evidence; export
does not drop them to make a recording appear replayable. An empty protected-test
binding is recreated by the fresh runner.

Fresh checks also require the evidence their current verifier expects. Some older
report-file provider archives lack retained tool-command receipts; replay refuses
those paths instead of copying old supervision records into current proof.
The original check summary must also be retained. For older runs without its
explicit reference, export requires one owned summary matching the saved digest;
a missing or ambiguous match causes refusal.

## Recorded checks

```sh
autocode replay /work/recording \
  --into /work/simulated/.autocode/runs/reproduction --recorded-checks --json
```

This explicitly simulates archived checks instead of running them. A simulated
completion is `REPLAY_COMPLETE`, with `replay_result.simulated` true,
`completion_current` false and `done` false. Its recorded verdict describes the
historical run; it supplies no current delivery or canonical completion proof.
Missing, invalid or mismatched check evidence causes refusal, never a fabricated
PASS. Use normal replay when current check results matter.

Recorded checks currently support serial runs with one milestone covering all
approved criteria. Multiple milestones, active progressive execution, protected
oracles, and brief, lifecycle or visual evidence obligations are refused. The
usual semantic checks still require the current contract, task, criteria,
findings and human reviews to match; an archived PASS cannot override a gap.
Recorded regression checks also require an authenticated pristine Git baseline
and proof for every required named case. Missing or unsupported baseline mappings
cause refusal.

## Public task-run interface

The CLI uses [`TaskRun`](task-run.md), including its ordinary approval and
answer methods. Status adds `replay_progress` with the recording digest, check
mode, dispatch count and next recorded dispatch, plus `replay_result` for a
simulation result. `replay_source` exposes the bounded export projection of the
saved run. These fields do not grant approval or establish evidence authenticity;
the exporter and ordinary runner still validate the artifacts they consume.
