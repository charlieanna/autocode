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
replay re-derives the current value from that identity. A Tester that names a gap
by its short handle (`L12`) is bound the same way: replay writes the current full
`gap_id` of that exact line in its place. A citation of the proof
file itself binds to the replay's own proof, whatever its directory is named. A
gap whose file, bytes or line differ in the replay is not rebound, and a replay whose Tester runs
without a current changed-line proof refuses rather than guess.
Planning references (`retain_contract`, `revise_contract` and a criteria delta's
`retain_from`) likewise bind only when they match the original dispatch's exact
contract revision and hash. Replay derives their new value from the current
runner schema and verifies its contract hash. Stale references remain stale;
prose and unrelated fields are never rewritten.
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

Retained answers referencing native tool captures that depend on an immutable
verification copy are also unsupported in this format. Export refuses those
dependencies before writing a bundle, including when export is implicit in a direct saved-run replay. The
original completed run and its evidence remain usable and unchanged. A receipt's
old paths and hashes cannot establish fresh capture authority in another run.
Unreferenced tool output, such as a printed documentation example, remains exportable.

With `--json`, this refusal includes `native_capture_dependencies`, a version 1
inventory of referenced capture **claims** from the already-read original tool
events. Each entry identifies the dispatch, normalized event/line occurrence,
original event artifact digest, before/after snapshot selectors, source selection
digest and task/contract/case-scope digest. It records digests of the claimed
command, receipt text and output path, the claimed copy/log pins, comparisons with
the dispatch's source and capture context, and specific `issues`. Raw commands,
paths, case names and nonces are not printed. `source_revision_match` and
`capture_context_match` describe equality only, never authenticity or approval.

The inventory always reports `closure_status: "unverified"` and
`execution_authority: false`. Capture receipt files and raw logs remain
uninspected: no model-claimed path is opened or resolved. Even a path classified
lexically as `run` may contain a symlink, and a matching digest may be forged.
Missing, stale, malformed and cross-run claims cannot enable export. Claim
inspection stops after 128 dependencies or 16,384 tool-output lines and sets
`truncated: true`; that limit also cannot enable export.

When the original core stage record retained a direct verification-copy selector,
the public `replay_source.records[]` projection includes its optional version 1
`verification_copy` field: `manifest`, `sha256` and `source_revision`. Inventory
uses only that selector to add `copy_evidence` to a referenced dispatch. It reads
the pinned manifest in that run's `tool-containment-<id>` directory and checks
retained regular source files against the dispatch's historical source snapshot,
including exact bytes, executable bits and source selection. It does not consult
the current workspace source or run Git or archived commands. A matching copy
pin in a claim is still just a comparison; a different claim is reported without
rewriting it.

`copy_evidence.status` is `matched`, `unavailable`, `invalid`, `unsupported` or
`limited`, with a specific `reason` and counts of source files and bytes checked.
Old runs without the selector report `unavailable` / `selector_missing`.
`matched` covers only the pinned manifest and regular source, not the entire
copy or the original capture's custody. Generated Git metadata and other files
outside the historical source inventory are not read. Linked dependencies,
submodule or deleted source entries, joint inputs, other copy layouts and unknown
manifest versions remain unsupported. Symlinks, hardlinks, special files, unsafe
paths, duplicate JSON keys and observed replacements cannot match. Reads use held
no-follow directories and recheck observed file and directory identities. This
is a bounded observation of retained evidence, not a frozen execution boundary.
Per-file reads stop at 8 MiB; one inventory shares a 64 MiB / 10,000-file budget.
Snapshot and manifest entry counts are also bounded at 10,000.
Relative names stop at 4,096 characters and 128 components before path parsing.

This diagnostic is not an import format or a portable native replay capability.
Export and direct replay still refuse before destination writes or startup,
even when the retained copy matches. If the retained events cannot be inventoried,
the refusal is returned without this field. Existing non-JSON refusal text and
exit code 3 are unchanged.

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
