# Programs: large requirements as parallel workstreams

[← Back to README](../README.md)

One bounded run cannot absorb a requirement that spans many components. A **program**
splits it into workstreams with explicit dependencies and literal ownership. The
program manifest is the program's **agreement**: you approve it by exact token before
any workstream starts, and you approve every later revision of it the same way. Each
workstream runs as an ordinary AutoCode build run in its own Git worktree, with its own
requirements, plan review, exact human approval, Builders, independent validation and
completion gate, and completed workstreams are merged onto one integration branch in
dependency order. The walking skeleton is merged and verified before any other
workstream starts; every merge re-runs the cumulative checks of everything merged so
far; the final check follows the agreement's user journeys by name.

The program controller schedules, briefs each child with the agreement, checks each
child plan against it, and integrates. It never approves a plan on your behalf, never
merges into your default branch, and never runs a deployment workstream without
explicit authorization.

```text
 Big request
     |
     v
 autocode program plan     ->  ordinary planning units (Requirements, Planner, Plan Reviewer)
     |                         you answer questions and approve the exact displayed plan
     v
 autocode program derive   ->  program.json: one workstream per approved milestone, the first
     |                         one the walking skeleton, plus the final check (integration)
     |                         you review or edit it: interfaces, journeys, checks
     v
 autocode program show     ->  the agreement and the token that approves it, a1:<digest>
 autocode program approve --token a1:<digest>
     |                         nothing starts before this; every later revision is approved too
     v
 autocode program run      ->  integration branch  autocode/program-<key>/integration
                                walking skeleton first: merged, cumulative checks pass
                                then the workstreams that extend it, one worktree each,
                                  branched from the merged integration head
                                each merge --no-ff re-runs the cumulative checks;
                                  a failing merge is undone
                                final check on the integration branch itself:
                                  verifies every user journey by name
                                deployment workstreams wait for --authorize-deployment
```

## Plan it

```sh
autocode program plan "Build an order system: catalog, cart, checkout, gateway, ..." \
  --workspace /path/to/project
```

This launches the normal planning-only unit (`--unit autoplanner`, read-only, in place)
with a program preamble: milestones must be independently deliverable workstreams
with disjoint `affected_paths`, explicit `depends_on`, and acceptance criteria that can
be verified on that workstream's own merged result. The first milestone is the walking
skeleton: the thinnest version that works from start to finish across every layer,
walking the main user journey, with the shared interface contracts it needs. Every
other milestone depends on it and extends it. Deployment, credentials and external
systems stay outside every milestone. Answer the questions and approve the displayed
plan exactly as for any run:

```sh
autocode --workspace /path/to/project --run-dir RUN --answer 'Q1=...'
autocode --workspace /path/to/project --run-dir RUN --show-goal
autocode --workspace /path/to/project --run-dir RUN --approve-goal 'r3:<hash>'
```

When the plan run ends, `program plan` prints the next commands (derive, show, approve,
run).

## Derive the manifest

```sh
autocode program derive --run-dir RUN --output program.json
```

`derive` reads the plan run's `approved_contract` status-view field (see
[Task-run interface](task-run.md#status-view)), never its `state.json`, and refuses a
plan that is not approved. `--workspace` names the plan run's project when it is not
the directory that holds `.autocode/runs/RUN`; `--name` sets the program name (default:
the approved outcome, cut to 60 characters); without `--output` the manifest is printed.

It turns each approved milestone into a `code` workstream: objective plus its acceptance
criteria as the brief, `affected_paths` as ownership, `depends_on` as dependencies,
and the milestone's criterion ids as `acceptance_criteria`. Then:

- The first milestone without dependencies is the walking skeleton (`skeleton: true`).
  Any other milestone without dependencies is made to depend on it, and
  `derivation_notes` says so.
- One `integration` workstream is appended. It depends on every sink milestone,
  validates the approved end-to-end flow, and lists every acceptance criterion. If a
  milestone is already named `integration`, the generated one gets a noncolliding id.
- `journeys` gets one journey, `J1` "Main user journey" (another id if a criterion is
  already `J1`), whose steps are the approved `end_to_end_flow` (or the outcome when
  there is none).
- Shared constraints, permission boundaries, technical approach, end-to-end flow and
  deliverables are copied into `shared`; `shared.interfaces` is left empty.
- `contract` holds the whole approved contract body with its `task_id`, `revision` and
  `hash`: required behaviors, failure cases, exclusions, assumptions and human-review
  obligations are not discarded. `source_run` records the plan run; it is provenance,
  not part of the agreement's digest.
- No `checks` are derived.

Before approving, add what derivation cannot know: the interfaces between workstreams
(with a producer and consumers), better journey names and steps, and program `checks`
that walk the journey. Without a runnable check the skeleton cannot be verified (see
[Walking skeleton and cumulative checks](#walking-skeleton-and-cumulative-checks)).

Derivation does not approve a child plan. Each child still plans and asks for its own
exact approval; a seven-workstream program can require seven additional plan approvals
and additional clarification. The benefit is isolation and parallelism, not fewer
human decisions. The parent contract is supplied as child planning context, not a
replacement for reviewing the child plan or for its human-review criteria.

You can also write a manifest by hand. `tools/task_scenarios.py` carries a complete
example (`PROGRAM_MANIFEST`) for a four-service order system: a walking skeleton, a
journey, a program check and a versioned interface with a producer and consumers.

## Approve the agreement

```sh
autocode program show program.json --workspace /path/to/project
autocode program approve program.json --workspace /path/to/project --token a1:<digest>
```

`show` prints the agreement as you approve it and saves nothing:

- the revision and `Approve with token: a<revision>:<digest>`, where the digest covers
  the whole manifest except `source_run`;
- for a revision, the changes since the approved revision and the workstreams that lose
  their approval. The changes name the parts that differ, the parent contract's
  milestones and its `revision` included, and order-only changes too (`order of
  requirements`, `workstream X owns order`). The list is never empty for a revision: one
  that changes only parts no line names shows `the manifest changed in a way no
  workstream is built from: <keys>`;
- the outcome, the shared lists and the requirements;
- the user journeys, with what each simulated one does not prove;
- the workstreams: the walking skeleton marked, ownership, dependencies, any
  `skeleton_exempt` reason, the inherited requirement ids, the first brief line and the
  workstream's own checks;
- the interfaces with version, producer, consumers, paths, schema and behavior;
- the program checks;
- a closing line: approving the agreement approves no workstream's plan.

When nothing is pending it prints the approved revision instead.

`approve` accepts only the token of the manifest file as it is now; any other token is
refused, including one shown for a manifest that has since been edited. It prints
`{approved, affected, next}`; the first approval lists every workstream as affected.
Approval does not check that you ran `show`: the token also appears in the summary of
`program run` and `program status` (`agreement.pending.token` and `next`). Read the
agreement before you approve it.

Until the first approval, `program run` saves the program's state, prints
`WAITING_AGREEMENT_APPROVAL` and exits 2. It creates no integration branch and no
worktree.

### Manifest rules (version 1)

| Field | Rule |
| --- | --- |
| `version`, `name`, `brief` | `version` is 1; `name` and `brief` are nonempty. The name keys the program's state (`.autocode/programs/<key>`): another name is another program |
| `workstreams[]` fields | Only `id`, `kind`, `brief`, `owns`, `depends_on`, `acceptance_criteria`, `engine`, `skeleton`, `skeleton_exempt`, `checks` |
| `workstreams[].id` | Starts with a letter or digit, then letters, digits, `.`, `_`, `-`; at most 64 characters; unique |
| `kind` | `code`, `integration`, `deployment`. Program-level `ui` is explicitly deferred until UI checkpoint recovery is supported; use `autocode ui` separately |
| `owns` | Literal repository-relative paths (no globs, `..`, `.git`, or `.autocode`). Non-integration workstreams must declare at least one. Two workstreams that could run at the same time may not own the same or nested paths; workstreams ordered by a dependency may |
| `depends_on` | Known ids, no self, no duplicates, acyclic |
| integration | Exactly one: the final check of the whole product on the merged result. It must (transitively) depend on every non-deployment workstream |
| deployment | Must (transitively) depend on the integration workstream; nothing except another deployment may depend on it |
| `skeleton` | Exactly one workstream, a `code` workstream with no dependencies. Every other workstream must (transitively) depend on it unless it is `skeleton_exempt` |
| `skeleton_exempt` | The reason a `code` workstream other than the skeleton need not wait for it (nonempty text, shown on approval) |
| `contract` or `requirements` | Where the requirements come from: the parent contract's acceptance criteria (derived manifests), or a top-level `requirements` list of `{id, criterion, verification_method, human_review}` with unique ids. Never both; both may be absent. `contract` is an object and its `body` an object; each of the body's `acceptance_criteria` needs an `id` and a `criterion`, and the ids are unique |
| `workstreams[].acceptance_criteria` | Requirement ids the workstream inherits. When the agreement has requirements, each must be one of them, and every requirement must be listed by some workstream. When it has none (no parent contract acceptance criteria and no `requirements`), the ids are listed in the workstream's brief but neither inherited nor checked |
| `journeys` | Required and nonempty: `{id, name, steps, simulated, does_not_prove}` with nonempty `steps`. A `simulated: true` journey must say what it does not prove. Ids are unique and differ from requirement ids |
| `checks` | Top level: program checks, commands re-run on the integration branch after every merge. Per workstream: commands re-run once it is merged and after every later merge |
| `engine` | `codex` or `opencode` for that workstream's run (overrides `--engine`) |
| `shared` | Optional lists (`constraints`, `permission_boundaries`, `end_to_end_flow`, `technical_approach`, `deliverables`) passed into every child brief, and `interfaces` |
| `shared.interfaces[]` | A list. Each entry: `id` (unique), `summary`, `paths` (a list, same rules as `owns`), `version` (positive integer, default 1), `producer` (a workstream id; that workstream owns every interface path), `consumers` (a list of distinct workstream ids other than the producer, each depending on it transitively; consumers need a producer), `schema` and `behavior` (text or JSON) |
| `derivation_notes`, `source_run` | Written by `derive`. `source_run` is not part of the agreement's digest |

`autocode program run program.json --dry-run` validates and previews without touching Git.

Writing deployment descriptors is ordinary `code`, not `deployment`. Put that work
before integration so it is included in final verification. Reserve `deployment` for
actual deployment work, which requires `--authorize-deployment` on every invocation
that starts or resumes it, as well as permission in its own approved child plan.

## Run it

```sh
autocode program run program.json --workspace /path/to/project --max-parallel 2
```

Each invocation does one pass:

1. Reads the manifest. If it differs from the approved agreement, it records a pending
   revision and stops at `WAITING_AGREEMENT_APPROVAL` (exit 2): nothing starts, resumes
   or merges until you approve it. A manifest that is not a revision of the approved
   agreement is refused (see [Revisions and STALE](#revisions-and-stale)).
2. Creates the integration branch and worktree on first use (from the project's
   committed `HEAD`, under `.autocode/worktrees/program-<key>-integration`). `<key>` is
   the program name as a slug plus a short hash of it. Takes back a merge an
   interrupted pass made and never verified (see below).
3. Refreshes every launched workstream from its child run's status view
   (`autocode --status`, read through the [task-run interface](task-run.md)).
4. Marks stale every workstream whose part of the agreement an approved revision
   changed, and retires its run.
5. Checks each child's plan against the requirements it inherits (see
   [Inherited requirements](#inherited-requirements)). From a completed child it keeps
   the checks its run replayed and passed and, for the final check, its journey results.
6. Merges, one at a time, each completed workstream that nothing holds (step 7), unless
   the last plan the program saw from its run drops an inherited requirement. For a
   non-integration workstream it first checks the delivered changes against `owns`,
   including committed changes, deletions, rename source/destination paths and new
   files; out-of-scope changes pause without merging. Runner metadata under
   `.autocode/` is excluded; pre-staged metadata must be unstaged. A delivery that
   changes a shared interface without an approved change pauses too. Then it commits,
   merges (`--no-ff`) and re-runs the cumulative checks, undoing its merge when they
   fail. Any pause raised while merging (failing cumulative checks, ownership, metadata,
   an interface change, a conflict, a dirty integration worktree, an unverified journey)
   ends the whole pass: no later workstream is merged and nothing starts. A delivery
   that fails again unchanged raises the same pause on every rerun, before any
   workstream after it is merged and before anything starts, so one such workstream
   holds up the rest of the program until it is fixed or retired. Only a conflict is
   set aside: the workstream stays `CONFLICT` and later passes merge and start the
   others.
7. Starts or resumes every workstream whose dependencies are all merged and that
   nothing holds, up to `--max-parallel` at once, each new one in a fresh worktree
   branched from the current integration head. A workstream is held while the walking
   skeleton is not yet verified; while a change request is open on an interface the
   agreement binds it to (see
   [Interfaces and change requests](#interfaces-and-change-requests)); while its plan
   holds the program at `PAUSED_INHERITANCE`, until a person sends its run back to plan
   again (see [Inherited requirements](#inherited-requirements)); and, for deployment,
   without `--authorize-deployment`. The final check is held until every code
   workstream is merged. The summary gives the reason as `blocked_reason`.
8. Repeats steps 3 to 7 until nothing more can start. Each child is invoked at most
   once per pass.
9. Prints a JSON summary and exits `0` only when the program is `COMPLETE`, otherwise `2`.

Child runs are ordinary `autocode <brief> --in-place --no-chat --workflow build` runs:
a workstream is a build job by construction, never left to the recognizer to route.
`--engine` (or the workstream's `engine`) and any unrecognized flags are passed through
to them when they start; a resumed child keeps its saved settings.

The summary has the program `status` and `next`, `state_file`, `integration_branch` and
`integration_workspace`, `agreement` (`revision`, `approved`, `token` and the `pending`
revision with its `token`, `affected` workstreams and `changes`), `skeleton` (the
verified skeleton's commit and check count), `journeys`, `change_requests`, one row per
workstream and, once complete, `final_check`. A workstream row carries its status, run
directory and worktree, what its child needs, and the program's own records: `pin` (the
agreement revision and scope fingerprint its run was built from), `approved_plan`,
`plan_check`, `stale_reason`, `retired_runs`, `checks`, `verification`,
`integration_check` and `blocked_reason`.

A child run pauses at its own human gates. The summary lists each waiting run
directory with what the child needs (`needs`: its `kind`; for a plan approval, the
`token` to approve; for a question, its `request_kind`, `resolver_scope`,
`resolver_request_id` and each question's `id` and text, never an answer token) and
its one-line `progress`; answer or approve there with the
normal CLI, then rerun `program run`. A run you have acted on (its status is back to
`RUNNING`) is resumed by the program; a run still at a gate or at any `PAUSED_*`
status is left alone.

An operational failure requires an explicit retry after inspecting its logs:

```sh
autocode program run program.json --workspace /path/to/project --retry-workstream catalog
```

The retry reuses the worktree and existing child checkpoint, if one was created. It
does not approve a plan or resume a child-level pause. The controller saves the
worktree, and the runs already in it, before starting a child; after a controller
interruption, a later invocation reattaches to the one child run created in that
worktree since. A run that was already there, such as your own run in the integration
worktree, is never adopted. An interruption without a child checkpoint is marked
failed and requires the same explicit retry. A checkpoint whose status cannot be read
(including one whose worktree was removed), or a worktree holding several new runs,
is refused rather than replaced.

A pass interrupted after merging a workstream but before verifying the merge leaves a
merge nobody verified. The next pass first takes it back (`git reset --hard` to the
integration head before it, recorded as the event `unverified_merge_undone`), then
merges and verifies the workstream again. Only a merge still at the integration head is
taken back. When the integration worktree has uncommitted tracked changes at that
point, the pass pauses at `PAUSED_INTEGRATION_DIRTY` instead.

| Program status | Meaning | Your next action |
| --- | --- | --- |
| `NOT_STARTED` | Shown only by `run --dry-run` and `status` before the program has saved any state | Read the agreement with `show`, approve it, then `program run` |
| `WAITING_AGREEMENT_APPROVAL` | The manifest is an agreement revision nobody has approved yet, the first one included. Nothing starts, resumes or merges | `program show`, then `program approve --token` with the token it prints, then rerun |
| `RUNNING` | Workstreams are under way and none waits for you | Rerun to continue |
| `WAITING` | A child run needs a question answered, a plan approved, or an explicit resume, or waits only for its next invocation | Act in the listed run directory, rerun |
| `WAITING_CHANGE_REQUEST` | An interface change request is open, even if every workstream is merged. The workstreams the agreement binds to its interface (its producer and consumers, every workstream when it has no producer) and the final check neither start, resume nor merge; other workstreams go on | Accept it (publish the interface's next version and approve that revision), reject it with `program resolve-change`, or approve a revision that removes the interface, which withdraws it; serve any listed child runs; rerun |
| `AUTHORIZATION_REQUIRED` | Everything else is merged; deployment workstreams need authorization to start or resume | Rerun with `--authorize-deployment` after deciding deployment is wanted |
| `BLOCKED` | A child invocation failed, with or without a saved checkpoint | Inspect its logs, then use `--retry-workstream ID`; missing checkpoints must be restored |
| `PAUSED_MERGE_CONFLICT` | A completed workstream conflicts with the integration branch; the merge was aborted, both branches are intact. That pass ends; later passes merge and start the other workstreams | Merge it by hand in the integration worktree, commit, rerun (the program adopts the manual merge once the cumulative checks pass) |
| `PAUSED_INTEGRATION_DIRTY` | The integration worktree has uncommitted tracked changes, such as a retired final check run's edits ([open bug](bugs/2026-10-06-program-integration-retired-leftovers.md)), or is not on its recorded branch. Raised before a merge or an adopted conflict resolution, before the final check starts and before an [interrupted merge](#run-it) is taken back, and repeated there on every rerun until it is cleared | Commit or discard (`git restore`) the changes, or restore the branch, then rerun |
| `PAUSED_OWNERSHIP` | A completed workstream changed files outside `owns`, or switched branches. Nothing was merged; every rerun repeats it, and nothing starts until it is fixed | Correct the workstream delivery or restore its recorded branch, then rerun |
| `PAUSED_METADATA` | Runner metadata was staged or committed. Every rerun repeats it, and nothing starts until it is fixed | Unstage `.autocode` without deleting it, or remove the metadata diff, then rerun |
| `PAUSED_INTERFACE_CHANGE` | A delivery changes a shared interface without an approved change. Nothing was merged; every rerun repeats it, and nothing starts until it is fixed | Remove that change, or raise it with `program request-change` and approve the new interface version |
| `PAUSED_INHERITANCE` | A workstream's plan still drops an inherited requirement after the automatic rejections, or its run refused the program's feedback (`plan_check.feedback_error`) | Give its run feedback yourself (the next rerun resumes it to plan again, and checks that plan), or revise the agreement, then rerun |
| `PAUSED_SKELETON_UNVERIFIED` | The walking skeleton has no runnable check on the integration branch. The program undid its own merge of it (a merge you made by hand stays, but is not accepted), and nothing else starts | Add program checks that walk the journey, approve that revision, rerun |
| `PAUSED_INTEGRATION_CHECK` | After a merge, the integrated product fails its cumulative checks. The program undid its own merge of a code workstream; the final check's own commits stay, and so does a conflict resolution you merged by hand (the message says it is still on the integration branch). An unchanged rerun repeats it, and nothing starts until it is fixed | Fix the workstream (for example `--follow-up` on its run), or undo or repair your own merge, then rerun; the receipts are listed |
| `PAUSED_JOURNEY_UNVERIFIED` | The final check completed without verifying every user journey, and was not merged. Every rerun repeats it until the run is followed up | Follow up its run so each journey is verified, then rerun |
| `COMPLETE` | Every workstream is merged on the integration branch, every journey is verified and no change request is open | Review the branch and merge it into your default branch yourself |

When several apply, the summary shows the first of: a pending agreement revision;
everything merged with no change request open (`COMPLETE` or
`PAUSED_JOURNEY_UNVERIFIED`); a conflict; `BLOCKED`; `PAUSED_INHERITANCE`;
`WAITING_CHANGE_REQUEST`; `AUTHORIZATION_REQUIRED`; `WAITING`; `RUNNING`. So a change
request raised after every workstream merged keeps the program at
`WAITING_CHANGE_REQUEST` until it is accepted, rejected or withdrawn. A pause raised
during the pass replaces the status, and its message becomes `next`. `program status`
keeps showing that pause; the next `program run` clears it and looks again.

Workstream statuses:

| Workstream status | Meaning |
| --- | --- |
| `PENDING` | Not started |
| `RUNNING` | Being invoked in this pass |
| `WAITING` | Its run waits at a question or plan approval, or waits only for its next invocation |
| `PAUSED` | Its run stopped at any other status, such as a `PAUSED_*` of its own; a person decides |
| `COMPLETE` | Its run completed and it is not merged yet: held (`blocked_reason`), not reached before a pause ended the pass, its merge failed the cumulative checks, or (the final check) a journey is unverified |
| `CONFLICT` | Its merge conflicted; see `PAUSED_MERGE_CONFLICT` |
| `MERGED` | Merged on the integration branch, and the cumulative checks did not fail |
| `FAILED` | An invocation failed or its status could not be read; see `BLOCKED` |
| `STALE` | Its run was retired, by an agreement revision or a dropped inherited requirement; a fresh run starts when its dependencies are merged and nothing holds it |

`autocode program status program.json --workspace ...` prints the same summary without
launching anything: it reads each unfinished child's status view and saves nothing.

## Revisions and STALE

The manifest file is the agreement. When it differs from the approved agreement, the
next `show`, `run`, `status` or `approve` sees it as revision N+1, pending. `show` names
what changed and which workstreams lose their approval; nothing starts, resumes or
merges until you approve it. Editing the file again before approving replaces the
pending revision (same number, new token); restoring the approved file withdraws it.
The program state keeps the approved agreement in full (`agreement.approved`) and, for
each approved revision, its number, digest, approval time, affected workstreams and
changes (`agreement.history`); earlier revisions are not stored in full.

A revision may not change:

- the workstream graph: ids, `kind`, `owns`, `depends_on`, which workstream is the
  `skeleton` and which are `skeleton_exempt`;
- an interface's definition without raising its version, and a version may never go
  down, not even across a removal: an interface that was delivered and removed since
  may come back only with a version greater than the delivered one.

Such a manifest is refused with the reason (for the last rule: `interface X was
delivered as version N and removed since; it may come back only as version N+1 or
later`) by `show`, `approve`, `run` and `status`; restore it, or start a new program
with a new name (the name keys the program's state, so a renamed manifest is a new
program). The graph and interface definitions are compared ignoring order and
spelled-out defaults: reordering `owns`, `depends_on`, or an interface's `paths` or
`consumers`, writing out `skeleton: false`, `consumers: []` or `producer: null`, or
rewording a `skeleton_exempt` reason is neither a new program nor an interface change
that needs a new version. It still changes the agreement's digest, so it is a revision
to approve, but no workstream's scope (below) counts these differences, so no
workstream loses its approval.
Briefs, requirements, journeys, checks, shared lists, the parent contract and
interfaces at a new version can all change. Adding an interface, or removing one, is an
ordinary revision. Program state saved before agreements existed is refused the same
way: use a new program name.

A revision affects a workstream when it changes that workstream's part of the
agreement (its scope fingerprint):

- the program outcome, the shared lists and the parent contract other than its
  acceptance criteria and milestones (these affect every workstream);
- its own manifest row, except `engine`, the order of its `owns` and `depends_on`, a
  spelled-out `skeleton: false` and the wording of a `skeleton_exempt` reason;
- the definitions of the requirements it inherits;
- the interfaces it produces or uses, apart from the order of their `paths` and
  `consumers` and spelled-out empty defaults (an interface with neither producer nor
  consumers binds every workstream; the final check is bound by all of them);
- the journeys, for the walking skeleton and the final check.

The program checks, `engine`, `derivation_notes`, the parent contract's milestones and
its `task_id`, `revision` and `hash`, and the order of the workstreams, requirements and
interfaces are in no workstream's scope: a revision that changes only them needs
approval but takes no workstream's approval away. A changed program check applies from
the next merge on.

Every workstream records, when its run starts, the revision and the scope fingerprint
it is built from (`pin`). After a revision is approved, the next `program run` marks
`STALE` each started workstream whose fingerprint changed, whatever it was doing, merged
or waiting at plan approval included:

- its run is retired: it is listed in `retired_runs` (with its run directory, status,
  worktree, branch, merged commit and pin), and its plan's approval no longer counts.
  The retired run and its worktree stay on disk;
- `stale_reason` names the revision;
- a fresh run starts when its dependencies are merged and nothing holds it. Its brief
  carries a `RE-CHECK:` line that asks it to plan against the agreement, keep what
  still conforms, change what does not, and verify again. It asks for plan approval
  like any run;
- a workstream that was merged, or that depends on a workstream re-checked by the same
  revision, restarts in a fresh worktree from the current integration head. Any other
  plans again in its own worktree;
- when the walking skeleton is stale, everything that waits for it is held again until
  its re-check is merged and verified.

`STALE` never reverts code. A merged workstream's earlier merge stays on the integration
branch; its re-check starts from there and is merged and verified again, even when it
changes nothing. Workstreams the revision does not affect keep their runs and their
approval.

A re-check whose files already conform can stall when its run plans an implementation
task, since its Builder has nothing to change
([open bug](bugs/2026-10-06-program-recheck-implement-stall.md)).

The final check runs in the shared integration worktree, and retiring its run (a
revision that changes its scope before it merged, or an approved plan that drops a
journey) leaves that run's uncommitted edits there. The next merge, or the fresh final
check's start, pauses at `PAUSED_INTEGRATION_DIRTY` until you commit or discard them
([open bug](bugs/2026-10-06-program-integration-retired-leftovers.md)).

## Inherited requirements

A workstream inherits the requirement ids in its `acceptance_criteria` (only when the
agreement has requirements); the final check inherits every journey id and every
requirement id that a workstream other than a deployment workstream lists. A
requirement only deployment workstreams list is theirs: the final check, which runs
before them and may not deploy, does not inherit it, and a change to it does not
affect the final check. A workstream's brief lists its inherited ids, and its plan must
keep each one as an acceptance criterion with exactly that id. Every pass, the program
reads each child's plan from its status view: the draft shown for approval
(`displayed_plan`) or the plan in force (`approved_contract`):

- A draft that drops an inherited id gets the program's `--feedback` naming the dropped
  ids before anyone approves it, and plans again at its next invocation. This feedback
  is the only input the program ever gives a child; it never approves anything.
- An approved plan that drops one is never resumed or merged. Its run is retired
  (`STALE`) and a fresh run plans again in the same worktree.
- Nothing merges while the last plan the program saw from a workstream's run drops an
  inherited id, even when that run completes.
- The program rejects a workstream's dropping plans twice at most (`plan_rejections`,
  counted across its runs). A third dropping plan is held, and the program pauses at
  `PAUSED_INHERITANCE`. So does a child that refuses the program's feedback; the error
  is in `plan_check.feedback_error`.
- To go on from `PAUSED_INHERITANCE`, give the run feedback yourself
  (`autocode --workspace WORKTREE --run-dir RUN --feedback "..."`), or revise the
  agreement. Once the run is back at `RUNNING` with no plan shown, the next `program
  run` resumes it to plan again. The program checks that plan like any other but sends
  no more feedback of its own: a plan that still drops an inherited id pauses again, and
  nothing merges until a plan keeps every inherited id.

`plan_check` records the plan token checked, the ids it dropped and whether it was
approved. A plan that keeps every inherited id is recorded in `approved_plan` once it
is approved. Ids are compared exactly: a renamed criterion counts as dropped.

## Interfaces and change requests

An interface in `shared.interfaces` is how two workstreams agree to connect: a version,
the paths it lives in, the producer that owns them, the consumers that use it, and its
schema and behavior. Every child brief lists the interfaces with their versions and
says that an interface changes only through the program: a child that finds one wrong
or insufficient must not change it or work around it, but stop and ask the user, who
raises a change request:

```sh
autocode program request-change program.json --workspace /path/to/project \
  --interface store --by search --reason "load() does not promise an order" \
  --proposal "load() returns the notes in the order they were added"
```

A change request needs an approved agreement. It is numbered `CR-1`, `CR-2`, ... and
records the interface version it was raised against. While it is open, every workstream
the agreement binds to the interface neither starts, resumes nor merges: its producer
and consumers, every workstream when the interface has no producer, and always the final
check. The program shows `WAITING_CHANGE_REQUEST` once nothing outranks it, even when
every workstream is already merged; other workstreams go on. You decide it:

- **Accept:** publish the interface anew in the manifest, with the next version and the
  new definition, and approve that revision. Open requests on that interface become
  `accepted` (`accepted_in_revision`, `version`, `resolved_at`). Every workstream bound
  to the interface that had started (the producer and consumers, the final check, and
  every workstream for an interface without a producer) becomes `STALE`, and is
  planned, approved and checked again against the new version; a consumer re-checked
  with its producer restarts from the integration head that will hold the new version.
- **Reject:** `program resolve-change`, which records your reason (`rejected`, with
  `resolution` and `resolved_at`).
- **Withdraw:** approve a revision that removes the interface. Its open requests become
  `withdrawn` (`withdrawn_in_revision`, `resolved_at`).

```sh
autocode program resolve-change program.json --workspace /path/to/project \
  --request CR-1 --reject --reason "order is not part of the interface"
```

Only a person accepts an interface change, by approving the revision; no model does.

Quiet changes are refused twice. A revision that changes an interface's definition
without a new version is not a revision of the agreement. A delivery that touches an
interface's paths pauses at `PAUSED_INTERFACE_CHANGE` with nothing merged when the
workstream is not the interface's producer, when the producer edits a version it has
already delivered, or when the final check edits an interface that has no producer. The
program state records each delivered version (`interfaces`: `version`, `commit` and
`by`, the producer that delivered it), and keeps that record after the interface is
removed.

## Walking skeleton and cumulative checks

Exactly one workstream is the walking skeleton. Every other workstream, apart from a
`skeleton_exempt` code workstream, is held until the skeleton is merged and its
cumulative checks pass on the integration branch (`skeleton` in the summary). The
skeleton's brief lists each journey's steps (and what a simulated one does not prove)
and asks for runnable checks that prove the journey end to end; every other brief,
apart from a `skeleton_exempt` workstream's, says to extend the skeleton, not rebuild
or bypass it.

Each merge, the program re-runs the cumulative check set:

- the program `checks`;
- for every merged workstream and the one being merged, its manifest `checks` and the
  checks its own run replayed and passed (`evidence.check_replay` in its status view),
  with that run's worktree path rewritten to the integration worktree.

The checks run in a scratch copy of the integration worktree, never in it, each limited
to `--check-timeout` seconds (default 900). Receipts are kept under
`.autocode/programs/<key>/verify/NNN-<workstream>/`, numbered by a counter over every
verification of the program; the state file keeps the latest 50 (`verifications`).

- **Pass:** the workstream is `MERGED`, with `verification` (verdict, head, receipts)
  and the pin it was merged under. A skeleton is recorded in `skeleton`; a producer's
  interface version is recorded as delivered.
- **Fail:** the program undoes its own merge of a code workstream (`git reset --hard`
  to the head before the merge) and pauses at `PAUSED_INTEGRATION_CHECK`. The workstream
  stays `COMPLETE` with `integration_check` (verdict, heads, receipts, and the message
  naming the failing command).
  Two things are not undone: the final check's own commits on the integration branch,
  and a conflict resolution you merged by hand (the message says your merge is still on
  the integration branch: undo or repair it there). "The merge was undone" appears only
  when the program reset its own merge. Rerunning with the same workstream
  branch, integration head and check set repeats the pause without merging again:
  follow up the workstream's run, or change the checks through an approved revision.
- **No checks at all:** a skeleton is not accepted as merged
  (`PAUSED_SKELETON_UNVERIFIED`; the program undoes its own merge of it), and
  nothing else starts. A derived manifest has no program checks, so the skeleton then
  depends on the checks its own run replays; add program checks before approving to be
  sure.

Checks are commands you approved in the agreement or that a child run replayed. They
run with your permissions in a scratch copy; that is not a sandbox.

## Final check: journeys

The integration workstream is the final check. It starts only once every code
workstream is merged, on the integration branch itself. It inherits, by id, every
journey and every requirement a workstream other than a deployment workstream lists
(see [Inherited requirements](#inherited-requirements)), and its brief lists each
journey's steps; for a simulated journey, the brief asks its evidence to say what the
journey does not prove.

When its run completes, the program reads each journey's result from the run's status
view (`evidence.acceptance`, by journey id). If any journey is not verified, the final
check is not committed or merged: the program pauses at `PAUSED_JOURNEY_UNVERIFIED` and
the workstream stays `COMPLETE`. Follow up its run:

```sh
autocode --workspace INTEGRATION_WORKTREE --run-dir RUN --follow-up "Verify journey J1 ..."
```

The follow-up reopens the run, and its next completion is checked again. Once every
journey is verified, its changes are committed on the integration branch, the
cumulative checks run, and it is merged.

The summary's `journeys` list each journey by id and name with its steps, its status
(`pending` until the final check merges, then `verified`), the run that verified it,
its evidence and, for a simulated journey, `does_not_prove`. The program is `COMPLETE`
only when every workstream is merged, every journey verified and no change request
open; `final_check` then names the final-check workstream, the journeys by name, and
under `not_proven` what each simulated journey does not prove. The program reports
that statement from the agreement; it does not check what the child's evidence says.

## What each child sees

The composed brief contains, in order:

- `PROGRAM WORKSTREAM <id> (<kind>)` and the program name; for a re-check, the
  `RE-CHECK:` line with the stale reason;
- the program outcome and the approved agreement revision;
- the shared constraints, permission boundaries, technical approach, end-to-end flow
  and deliverables;
- for a derived manifest, the complete parent contract, with the instruction to
  preserve its requirements, exclusions, permission boundaries and human-review
  obligations; approving the parent approves neither the child plan nor any
  human-review obligation in it;
- the shared interfaces with version, producer, consumers, paths, schema and behavior,
  and the instruction to ask the user for any interface change;
- the workstreams already merged on the integration branch when the brief is composed
  (every merged workstream, not only its prerequisites), each with its brief's first
  line;
- for the skeleton, that it is built and verified first and must leave runnable checks,
  with each journey's steps (and what a simulated journey does not prove); for every
  other workstream except a `skeleton_exempt` one, that it extends the verified
  skeleton;
- the workstream's own objective and its acceptance criteria, with their full
  definitions when the agreement has requirements;
- for the final check, each user journey's steps (and what a simulated journey does not
  prove);
- the inherited requirement ids it must keep;
- the exact paths it owns and the paths owned by others (the final check's
  cross-component repair exception instead), and that it must not deploy or reach
  external systems (deployment: only with permission in its own approved plan) and must
  not merge branches.

The brief is composed when the workstream's run starts and saved as
`.autocode/programs/<key>/<workstream>/brief.md`. Resuming the run leaves that file
alone, so it is always the brief that started the current run; a fresh run (a re-check,
or a retry without a run) writes it anew.

Ownership is checked against the actual diff before a non-integration branch is
committed/merged or an existing conflict resolution is adopted. This is an integration
gate, not a filesystem sandbox: it cannot prevent a child from writing an unowned path
during execution. The integration workstream has an explicit cross-component repair
exception and may commit its own tracked-file repairs; it is not rejected merely
because those repairs make its worktree dirty. Its approved scope still applies, and it
may not change a shared interface.

## Boundaries

- No automatic merge into `main`/`master`. The integration branch is yours to review.
- No conflict resolution by the tool. A conflict pauses with both branches preserved.
- No deployment without `--authorize-deployment`, and even then the deployment
  workstream is a normal reviewed run that must not reach external systems unless its
  own approved plan says so.
- Approving the agreement approves no child plan; each workstream asks for its own
  exact approval. No model approves an agreement, a revision or an interface change.
- The child's status view (`autocode --status`, via `autocode_taskrun`) is the only
  source of a workstream's status and plan; the program never reads a child's
  `state.json`. An exit code, elapsed time or a Builder's report never marks a
  workstream complete.
- `STALE` never reverts merged code, and a failing cumulative check undoes only the
  program's own merge of a code workstream. The only other merge the program takes back
  is one an interrupted pass made and never verified.
- Evidence stays per run: each child run's validation and completion records remain
  in its own run directory; the integration workstream is where the whole flow is
  independently validated on the merged code.
- Cumulative checks are not a sandbox, and ownership is checked on the diff, not while
  the child runs.
- `--max-parallel` limits concurrent workstreams, not total model spend. Scheduling
  re-evaluates readiness after each batch finishes. There is no program-wide budget,
  cancellation/eviction API or automatic worktree cleanup in this version.
- A single job never sees any of this: an ordinary run writes nothing under
  `.autocode/programs` and creates no program branch or worktree.

Testing: `.venv/bin/python -m unittest tests.test_program tests.test_program_children
tests.test_program_agreement tests.test_program_agreement_runs` covers manifest and
agreement rules, derivation from an approved contract, approval by exact token,
revisions and `STALE`, inherited requirements, change requests and quiet interface
changes, the walking skeleton, cumulative checks and undone merges, journeys, wave
order, worktree bases, merges, conflict pause and manual resolution, ownership
enforcement, tracked integration repairs, explicit retries, recovery from an
interrupted checkpoint or an unverified merge, deployment gates on start/resume, the
mapping from a child's status view to the workstream status, and a real CLI first wave
with the fake Codex provider.
`tests.test_taskrun` shows that an ordinary job creates no program files, and
`tests.test_live_trial` covers `live_trial.py`'s program mode.

End to end, the `program-notes-cli` scenario drives `program plan`, `derive`, `show`,
`approve` and `run` with real child runs under the scripted model: a walking skeleton,
two parallel workstreams, an accepted interface change request that re-checks the
producer and both consumers, and the final check by journey
([scenarios/README.md, Programs](../scenarios/README.md#programs)):

```sh
.venv/bin/python scenarios/run.py run program-notes-cli --fake
```

The `PROGRAM-01` scenario in [scenarios](scenarios.md) provides the end-to-end oracle
for a live trial (`--mode program`); a live profile stops there at the agreement for a
person. Any trial also stops at the first program-level `PAUSED_*` status, without
rerunning `program run` or serving a child's gate, and reports it as a pause for a
person rather than a stage-budget error.

See also: [Task lanes](task-lanes.md) · [Task-run interface](task-run.md) · [Execution](execution.md) · [Workflow](workflow.md)
