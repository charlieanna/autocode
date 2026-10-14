# Task-run interface

[← Back to README](../README.md)

How a program drives one AutoCode task run: start it, read where it stands,
answer what it asks, and let it continue. This is the only supported way for
code outside the runner (the scenario harness, `autocode program`, and the planned
architecture and multi-component layer) to control a run. Such code must not import
runner internals such as `autocode.py` or read `state.json` directly; that file has
about 140 keys and changes without notice.

Every step is one CLI invocation, and the run's state lives on disk. A caller
that crashes can reattach to the same run directory with
`TaskRun(workspace, run_dir)`, or with `TaskRun.attach(workspace)` if it never
learned the run directory (it returns the workspace's only run, or `None`). In a
workspace where someone else may also run AutoCode, note `TaskRun.runs_in(workspace)`
before the start and pass it as `attach(workspace, exclude=...)`, so that only a run
created since can be adopted.

## Python client

`tools/autocode_taskrun.py` wraps the commands below.

`TaskRun.start(..., run_dir=path)` selects an exact fresh run directory, an
immediate child of `workspace/.autocode/runs`. Existing directories and symlinks
are refused before a provider launches. The optional destination is used by
[saved-run replay](replay.md); the run is created there without moving bound
artifacts afterward.

This example accepts the displayed plan and chooses question defaults or the first
listed option. An interactive client should collect the person’s answer instead,
using the token from the view they saw. Operational recovery needs a separate decision.

```python
from autocode_cli.autocode_taskrun import TaskRun, TaskRunError

run = TaskRun.start(workspace, brief, options=("--engine", "codex"))
view = run.advance_until_input()
while not view["done"]:
    need = view["needs"]
    if need["kind"] == "answer" and need.get("resolver_scope") in ("blocker", "operational_exhaustion"):
        break  # Inspect recovery guidance; respond_operational() handles these requests.
    if need["kind"] == "approve_plan":
        view = run.approve_plan(need["token"])
    elif need["kind"] == "answer":
        # One question per pass: an answer consumes the request, and the questions
        # left come back in the returned view under a new resolver_token.
        question = need["questions"][0]
        # Decision questions may have no default; choose among their options.
        view = run.answer(question["id"], question["proposed_default"] or question["options"][0],
                          resolver_token=need.get("resolver_token"))
    elif need["kind"] == "continue":
        view = run.advance_until_input()
    else:
        break   # review, planning_budget or resume: a decision for the caller or a person
```

`TaskRun.start` works directly in the given workspace (`--in-place`): the caller
owns the workspace, typically a worktree it created, so start one run per
workspace at a time. Files uncommitted or untracked there at the start are the
code the run starts from (`base_commit`), not part of its change. `options`
(engine and model flags) are passed whenever the run starts or advances. Any
rejected command raises `TaskRunError` with AutoCode's message.

AutoCode self-builds require a linked Git worktree and an executing controller
outside that workspace. `TaskRun.start` accepts a caller-owned linked worktree;
an ordinary CLI launch creates its own worktree by default. An unsafe new run
or advancing invocation is refused before starting a provider. Saved status,
explanations and dry runs remain readable without changing the checkpoint.

`view.runner` and `view.evidence.runner` identify the controller that last saved
the run: its package directory (`path`) and source revision (`commit`, with
`+modified` for local source edits). `view.evidence.base_commit` identifies the
Builder's starting code separately. Legacy runs without a saved identity report
`runner: null`; an installed package without source revision metadata reports
`commit: null`. Status never substitutes the current installation or Builder
revision for this saved identity. The existing resume warning names a change
in the controller revision.

To build AutoCode itself from a source checkout, keep that checkout fixed and
create a separate workspace:

```sh
git -C /path/to/autocode worktree add --detach /path/to/autocode-self-build HEAD
/path/to/controller-venv/bin/python /path/to/autocode/tools/autocode.py \
  "Fix the selected issue" --workspace /path/to/autocode-self-build --in-place
```

Use the existing provider and model flags for the run. Keep the controller
checkout and its environment unchanged until the run has stopped. An installed
controller is also safe when its package directory is outside the task worktree.

Inputs fixed when a run starts, such as `--ui-run` and `--test-root`, belong in `start_options`
instead of `options`: `TaskRun.start(workspace, brief, options=("--engine", "codex"),
start_options=("--ui-run", str(design_run)))`. They are passed once; later advances
and reattachment use the saved settings. For scoped Python proof, use
`start_options=("--test-root", "components/api/")`; the root selects tests, not
generic write ownership. Generated component runs bind their original ownership
declaration to this caller-selected root before plan approval and execution.
An older shipped component run with no saved root uses only its exact, single
original generated ownership declaration to derive a transient root for admission
and regression proof. It does not rewrite the original task, settings, approval,
user events or historical evidence. An explicit root must still match; generic
rootless tasks gain neither a test root nor write authority from model text.

At `PAUSED_COMPONENT_PLAN`, `needs.kind` remains `resume` for compatibility.
First check `needs.new_run_required`: when true, `needs.action` is `fresh_run`,
`needs.edit_required` is false, and `needs.feedback_action` is null. The original
caller declaration is missing/ambiguous or the saved root disagrees with it.
These immutable inputs cannot be repaired by editing the model's plan. Preserve
the stopped run and use `TaskRun.start` for a fresh component run in a separately
caller-owned workspace, with the correct generated declaration and matching
launch-only root. That new plan still requires approval. Feedback at this stop
is rejected before recording an event or scheduling a new model draft. Never
change the original task or saved root to make the old approval usable.

With a valid caller binding, `needs.edit_required` is true instead: incorrect
body paths or deferred/prohibited mandatory flow can be repaired. Follow
`needs.action` (`--edit-goal FILE`) or `needs.feedback_action` (`--feedback TEXT`),
then inspect and approve the new draft with its newly displayed token. The old
approval token cannot approve it. Plain resume leaves the checkpoint unchanged
and launches no Builder; adding `--test-root` on resume cannot repair a
launch-only setting. A valid older rootless run needs no root flag to edit its
plan. The recovery card, progress line and issue/finder guidance distinguish
these two cases without promising that an unchanged resume repairs either one.

A caller that keeps each invocation's output, as `autocode program` keeps a
workstream's `stdout.log`, `stderr.log` and exit code, reads it from the client:

- `TaskRun.last_advance` is the `subprocess.CompletedProcess` of the latest call that
  starts or advances the run (start, advance, resume and retries), also when AutoCode
  rejected it. Status reads and user actions never replace it.
- `TaskRunError.process` is the `CompletedProcess` of the CLI call that failed. It is
  `None` when the error is not a failed call: a call that could not run or did not
  finish (a missing working directory, a timeout), `attach` finding several runs, or a
  guard such as `advance_until_input`'s no-progress check, which is raised after its
  calls finished; `last_advance` still holds the latest advancing call. A start that
  fails may still have created its run: `TaskRunError.run_dir` names it when it
  created exactly one.
- `TaskRun.cwd` (`cwd=` on `start` and `attach`) is the CLI's working directory, so
  relative paths in `options` and `start_options` resolve against it. `None`, the
  default, keeps the caller's. A continuation from `restore_checkpoint` keeps it.

Operator-declared prerequisites can be supplied once with `--task-preflight`
in `start_options`. A failed prerequisite pauses before paid dispatch and is
visible in the additive `task_preflight` status field. See
[task-preflight.md](task-preflight.md) for phase selection, copied input checks,
receipt reuse and supported correction; readiness does not replace proof.

## Core Requirements conversations

`TaskRun.start_conversation(workspace, text, request_id="first-message")` starts a
core-owned journal and one ordinary task run. `attach_conversation(workspace,
run_dir=...)` validates an existing conversation; `read_conversation()` reads its
journal and current public run view without changing either or invoking a model.
Provider, model, effort and budget selections use the same ordinary runner
settings as `start()`, through a creation allowlist shared by Python and the CLI.
The core session always uses joint planning, stops for explicit plan approval,
and accepts `--planning-v2` for separate Planner and Plan Reviewer stages.
The default joint flow presents a Requirements planning draft and an independent
Plan Reviewer. Ordinary planning review limits still apply: a budget pause remains
a pause, and conversation refresh grants no extra review calls or approval.
Creation inputs belong in `start_options`; later advances use saved settings.

`send_conversation(text, request_id=..., expected_revision=...)` queues a human
message only. Use the revision returned by the inspected conversation and retain
the request ID for a retry of that same message. It does not call feedback,
answer, or a model. `refresh_conversation(request_id=..., expected_revision=...)`
explicitly commits the pending human batch through the ordinary saved-action
boundary, then advances the same controller to its next question or approval
stop. A question refresh also supplies its `question_id` and current
`resolver_token`; exact answer and operational-recovery rules still apply.
Stale revisions, conflicting request IDs and stale question tokens are refused.

`handoff_conversation(expected_revision=..., expected_plan_token=...)` records the
reviewed current draft's handoff to that same run. It does not approve the plan or
start a second task. Read the current plan and use the separate `approve_plan()`
action only when the operator authorizes implementation. The original handoff
source digest stays immutable; later human feedback retains its own provenance.
Assistant text and unapproved drafts never become mandatory requirements merely
because they appear in the journal. The browser dashboard and macOS app remain on their existing frozen path.

The additive `view.conversation` is `None` for ordinary and legacy dashboard
runs. A core conversation exposes actionable `status`/`needs` (including current questions and resolver tokens),
`messages`, `pending_message_ids`,
`revision`/`applied_revision`, saved human actions and request acknowledgements,
accepted `planning_records`/history, `current_draft`/draft history, and Reviewer
findings under `review`. Each accepted record identifies its actual saved role,
model and effort; `declared_seed_routes` records only explicit creation context
and uses `runner-default` when no model was named. A queued message makes the
shown draft stale until refresh completes its ordinary independent review.

A completed identical refresh or an unchanged revision with no pending human
messages dispatches nothing. If a client exits after saving a request, its
runner-owned acknowledgement reconciles that exact source event on restart.
If it exits after marking planning delivery as advancing, an unresolved outcome
is held for ordinary inspection/recovery; repeating refresh never blindly
replays a paid call. The immutable initial handoff and all accepted/rejected
stage artifacts remain evidence on the same run. `read` and `attach` save
nothing and cannot approve it.

The public CLI has the same operations:

```sh
autocode conversation start --workspace "$PROJECT" --text "Build a greeting CLI" \
  --request-id initial -- --provider opencode --joint-planning
autocode conversation read --workspace "$PROJECT" --run-dir "$RUN"
autocode conversation send --workspace "$PROJECT" --run-dir "$RUN" \
  --text "Use standard-library Python" --request-id language --expected-revision 1
autocode conversation refresh --workspace "$PROJECT" --run-dir "$RUN" \
  --request-id draft-2 --expected-revision 2
```

Every conversation command prints JSON on stdout. Provider/model/effort/budget
options follow `--` on `start`; unit overrides, approval and automatic approval
are refused. Attach/read are read-only. Sending several messages does not
silently refresh between them. Only an explicit refresh asks the ordinary
controller for its next result.

A rejected conversation answer can be corrected without replaying it. Read the
current question and token first. If the runner retained an input rejection,
queue the corrected text with `send_conversation(...,
supersedes_request_id="old-message-id")` or CLI `send --supersedes-request-id
old-message-id`, at the current revision. The original message and sealed
rejection remain visible. Only that rejected, unapplied message leaves the
pending batch; saved actions, active or unknown delivery, and unattempted inputs
cannot be replaced. Refresh the correction with a new request ID and the current
question/token. A stale supplied token is refused before a request is sealed.

## Controller ownership and recovery

`status()["controller"]` reports the current invocation's birth-bound owner,
last saved checkpoint and completed stage, declared CLI return code, and ready
or stopped disposition. A hard exit has no collected return code; that field
stays `null`. Deliberate answers, approvals and unit handoffs retain an explicit
ready receipt and keep their ordinary continuation behavior. A rejected user
command records its actual exit code `2` while retaining the ready checkpoint;
it does not imply a lost controller or a successful action.

If the controller disappears between stages, status projects
`PAUSED_CONTROLLER_EXIT` with a concrete `--resume-paused` command. Reading
status or attaching does not rewrite the checkpoint. The next advancing CLI
saves that pause under the existing run lock; `resume_paused()` acknowledges it
and continues the saved stage. The checkpoint digest must still match before
the receipt identifies that stage or return code as the current boundary.
Retained provider attempts and runner checks keep their existing recovery
actions. A live owner or provider and a busy exclusion lock remain active;
unverifiable ownership is reported precisely without guessing absence.
Existing approval, source and provider cleanup gates still apply.

## First interaction timing

`status()["interaction_timing"]` reports the saved first useful interactions:

| Field | Meaning |
| --- | --- |
| `launched_at` | UTC timestamp at CLI main entry, before workspace setup and provider preflight. Interpreter startup and module imports precede this boundary. |
| `first_question_at` | First published question or decision request rendered for the caller. |
| `first_plan_at` | First approval-ready plan rendered for the caller. |
| `first_builder_at` | First successful Builder dispatch: provider supervisor launch in serial execution, or Builder worker process launch in parallel execution. |
| `first_question_seconds`, `first_plan_seconds`, `first_builder_seconds` | Wall seconds from `launched_at` to the corresponding event. |

Builder dispatch measures the runner's job handoff; it does not claim the provider
has accepted a request or emitted its first token. A parallel worker can still
fail its own preflight after dispatch. Failed process creation, report repairs,
dry runs and scheduling alone do not count as Builder dispatch.

The first timestamps are retained across repeated displays and resumes. Durations
include human answer/approval time and time between invocations; compare automated
runs with automated runs rather than treating human wait as model latency. Status
reads do not stamp an event or perform a model listing. An unreached event is
`null`, and all seven fields remain `null` for older runs without a recorded
launch. A later resume does not fabricate their history. Canonical completion
reports bind these facts and show them in JSON and Markdown. The generated
[reliability table](reliability-table.md) lists dated samples and medians of
recorded values, keeping fake and live profiles separate.

## Canonical completion evidence

Every newly completed task publishes `evidence.json` and `evidence.md` beside
its `state.json`. The version 1 JSON schema is
[`tools/autocode-schemas/evidence-report.schema.json`](../tools/autocode-schemas/evidence-report.schema.json).
Markdown is rendered deterministically from that document. The report records
the approved plan, checked source snapshot, criterion results, independent
commands and their exit codes, artifact paths and hashes, roles/models and
attempt durations, reported usage, regression proof and remaining gaps. When the
proof's mutation check left survivors, `regression_proof.mutation_survivors` lists
them per test case, keyed like `case_tests`, and the Markdown names each one under
the regression proof; they are reported, not gaps. Local
artifact paths are references, not hosted evidence. Unknown usage stays unknown;
historical price estimates remain separate from provider-reported cost.
Usage records the public accounting snapshot captured at the completed checkpoint,
including its unknown quantities. It does not attest every later raw event-log byte.
Inspection compares the report's bound evidence and accounting facts with the
supplied current public projection; a changed finding, reproduced case, base
revision, attempt or usage fact makes the report stale. Log timestamps and other
inspection bookkeeping that do not change those facts do not rebind the report.

```python
view = run.status()                         # small additive evidence_report metadata
report = run.evidence_report()              # authenticates current completion and both files
markdown = report["markdown"]               # exactly the bytes in evidence.md
document = report["document"]               # parsed evidence.json
historical = run.evidence_report(require_current=False)
```

`status(inspect_evidence=True)` includes the authenticated document and Markdown
without writing files, launching a provider or replaying checks. Availability is
`missing`, `export_failed`, `recorded` (historical), `current`, `stale` or `invalid`.
The default `evidence_report()` raises `TaskRunError` unless the report pair,
approved contract, completion and current source are authenticated. Historical
inspection permits an intact old delivery record after source or approval changes;
it conveys no authority to publish that record as current. A missing old report
is never reconstructed by a status reader. Reports do not add approval or relax
the existing completion gate. Read-only workflows can record job completion with
explicitly unverified code checks.

Declare provider evidence when starting: `--evidence-provenance live`, `fake`,
`mixed` or `unknown`. This is a caller disclosure, fixed at launch, not a model
attestation. A fixture executable named `codex` remains fake when declared fake;
provider names and model names cannot distinguish fixtures from real inference.
Without a declaration, custom configurations and older runs are unknown. A resume
cannot relabel their prior attempts live. The scenario harness declares fake,
live or mixed according to the route it actually launches. Tests using a fixture
must pass `--evidence-provenance fake`. Human plan approval remains a separate
decision.

Completed programs publish the pair beside their owned program state; their
public `program run`/`program status` summaries expose `evidence_report` metadata.
Completed component builds publish it beside `.autocode-components/manifest.json`;
the component CLI summary and `MultiComponentBuild.evidence_report()` expose it.
Aggregate documents reference authenticated child `TaskRun.evidence_report()`
documents and retain simulated runtime/deployment scope and unknown totals.
Their existing inheritance and integration gates authorize accepted historical
child deliveries; the aggregate reader does not claim fresh child verification.
Aggregate completion timestamps and elapsed wall time remain unrecorded when the
coordinator has no owned timing record. A child's completion time does not stand
for later integration checks or runtime smoke.
For component `--run-local`, `--runtime-evidence-provenance fake|live|unknown`
separately discloses the runtime smoke route. Its HTTP results and sanitized local
receipt are included in the aggregate; simulated smoke does not prove real
containers or database behavior. The harness supplies this known declaration.

`autocode issue pr` requires a current canonical report before staging source and
embeds its exact Markdown in the PR body. Oversized reports are refused rather
than truncated. The common writer masks text matching the shared credential policy
before binding canonical JSON and Markdown; it discloses masking and preserves
raw artifact/source hashes and plan bindings. Raw evidence is not rewritten.
Readers refuse unsafe historical pairs, and issue delivery refuses any wrapper
that the GitHub transport would change, including unsafe Git-quoted source names,
before staging or pushing. Committing tested dirty source changes Git HEAD: the issue adapter
records an owned delivery receipt with the tested manifest, plan/report bindings
and resulting commit/tree. A later `pr --open` requires that exact clean tree and
intact historical pair; changed bytes, modes, deletions, untracked source, approval
or report digests refuse publication. The body distinguishes the tested snapshot
from the later delivery commit and does not call historical evidence current.
The owned `evidence_export` state field stores only paths, hashes,
binding and publication error; the export adapter owns writes, while status and
TaskRun consume it. Cross-run coordinators use public child reports, never child
`state.json`.

## Commands

All commands take `--workspace WORKSPACE`; commands on an existing run add
`--run-dir RUN`.

| Step | Command | Exit status |
| --- | --- | --- |
| Start | `autocode "BRIEF" --in-place --no-chat [options]` | 0 complete, 2 stopped for input |
| Status | `autocode --status` | 0; prints JSON, the view is under `"view"` |
| Display brief | `autocode --show-goal` | 0; prints the current brief for human review |
| Continue | `autocode --no-chat [options]` | 0 complete, 2 stopped for input |
| Resume a pause | `autocode resume --no-chat [options]`; after editing the design at `PAUSED_DESIGN_CONFLICT`, `autocode --resume-paused --no-chat [options]` | 0 complete, 2 stopped for input |
| Grant N recoveries after resolving the cause | `autocode --resume-paused --grant-recovery N --no-chat [options]` | 0 complete, 2 stopped for input |
| Accept a changed OpenCode transport | `autocode --resume-paused --accept-transport-change --no-chat [options]` | 0 complete, 2 stopped for input |
| Adopt an inspected interrupted Investigator report | `autocode --recover-job-report TOKEN [options]` | 0 applied, 2 rejected; no model launched |
| Answer | `autocode --answer QUESTION_ID=TEXT --resolver-token TOKEN` (`--answer` repeatable); a question with `options` takes only one of its option labels or its `proposed_default`, exactly | 0 saved, 2 rejected |
| Respond to an operational Resolver request | `autocode --resolver-request ID --resolver-token TOKEN --resolver-response provide_information --resolver-message TEXT`; the next resume re-evaluates it once ([below](#operational-information)) | 0 saved, 2 rejected |
| Name the model a role stopped on quota, a content-filter refusal or an authenticated output limit continues on | `autocode --answer route-ROLE=MODEL --resolver-token TOKEN`, then resume the pause | 0 saved, 2 rejected |
| Name the model a stopped workflow job continues on (`retry_job` with `route`) | `autocode --answer route-ROLE=MODEL --job-retry-token TOKEN`, then retry the job with the new token | 0 saved, 2 rejected |
| Approve the plan | `autocode --approve-goal TOKEN` | 0 saved, 2 rejected |
| Approve a review | `autocode --approve-review CRITERION --review-token TOKEN` | 0 saved, 2 rejected |
| Reject a review (`TaskRun.reject_review(criterion, token, note)`) | `autocode --reject-review CRITERION --review-token TOKEN --review-note TEXT` | 0 saved as feedback for revision, 2 rejected |
| Plan feedback | `autocode --feedback TEXT` | 0 saved, 2 rejected |
| A program's correction of the displayed plan (`TaskRun.guide_plan(text, token)`) | `autocode --program-guidance TEXT --review-token -` (reads `AUTOCODE_REVIEW_TOKEN`); saved as the program's, never as user feedback | 0 saved, 2 rejected |
| Follow up a finished run | `autocode --follow-up TEXT` | 0 saved, 2 rejected |

Answers and approvals never launch a model; continue afterwards. A follow-up
is the next request in the same conversation: on a finished run it records a
new turn (`turn` in the status view) and reopens the run, which recognizes the
kind of job again from the new message and continues in the same run
directory. After a review, a follow-up that asks to act on the findings is
planned from them: the review's blocking findings are the requirements, so no
requirements questions are asked, and the plan still goes to the user for
approval. The rewritten task names what the previous turn wrote (its report or
note, then the files its stages changed; at most eight paths). After a design
turn, a follow-up that asks to build the design names that document, so the
build starts by checking it against the repository (`check_design`) instead of
gathering requirements. The build is planned afresh from the design: the design
turn's contract and requirements move to the run's history rather than being
revised, and the new plan still needs approval. After a design review, a
reply to it (an answer, a correction) is recognized as design and the Architect
revises the same review: concern ids are kept, settled concerns stay as resolved,
and `review/design-review.json` records every revision
([CLI](cli.md#replying-to-a-design-review)). Only a finished run takes
a follow-up, and a finished run takes no answer, feedback or edited plan: either
mistake exits 2 and changes nothing (see [CLI](cli.md#waiting-or-finished)). A usage error
also exits 2, with a message starting `usage:` on stderr; the client checks for
it so a mistyped flag is not mistaken for a pause. A rejection also exits 2,
starting `Input rejected:`, and startup can exit 2 before any run exists;
`TaskRun.start` raises with the tail of the CLI's output so a startup failure
is never mistaken for a pause.
`TaskRun.respond_operational()` uses the separate Resolver response command;
an operational request cannot be answered with `TaskRun.answer()`, except the
model question of a quota stop or a content-filter refusal (`needs.route`, see [Models](models.md#when-a-roles-quota-runs-out)):
`TaskRun.assign_model(role, model)` answers `route-ROLE` with a model a person
named, updates a `--ROLE-model` in the client's `options`, and leaves the run
paused for `resume_paused()`. A refused model raises and leaves the run paused
with the question open. At a stopped workflow job (`retry_job` with `route`, see
[Failed workflow jobs](#failed-workflow-jobs)) the same call answers with the job's
`job_retry_token` instead of a resolver token and leaves the run paused for
`retry_job()` with the new token.
Each answer consumes the Resolver request whose token it carries, and the
questions still open come back under a new `resolver_token`, so a token read
before an earlier answer is rejected. Without `resolver_token`,
`TaskRun.answer()` reads the status view and uses the current request's token
after checking that the request lists the question. When a person chose the
answer, pass the token of the view they saw, so it cannot apply to a request
they never saw. A question that lists `options` is a choice AutoCode acts on, so its
answer must be one of those labels or the question's `proposed_default`, matched exactly
apart from surrounding spaces (#1013). Any other text is rejected with the valid labels
(`Input rejected:`, exit 2; `TaskRun.answer()` raises): nothing is saved, the token stays
current, and something the options do not cover goes in `--feedback` (`/feedback` in
`--chat`, which asks again after a rejected answer). A question without options takes any
nonempty text. The terminal CLI also shows the remaining question IDs and
current Resolver token on stderr after each successful answer and on `--status`, and `--status`
also prints the run's cost line there (`Cost: ...`, from the view's `cost`);
the status JSON on stdout and the returned TaskRun view keep their existing shape.
`TaskRun.accept_transport_change()` uses the explicit transport-change command
after a person inspects the new route and the saved run reports
`PAUSED_TRANSPORT_CHANGED`.
`TaskRun.accept_source_edit()` uses `--resume-paused --accept-source-edit` when
a repair is paused at `PAUSED_STALE_HANDOFF` because a person edited the source.
The approved contract, task, budget, proof and evidence pins stay.

## Reviewed Figma input changes

Start with native references (`--figma-file` and repeatable `--figma-additional-file`)
or a complete exported bundle (`--figma-manifest`) in `start_options`. Both inputs
produce the same durable coverage and plan ownership, exposed in `view.design`.
See [figma.md](figma.md) for collection, responsive derivation and source receipts.

At a stopped, reconciled boundary, `run.revise_design(manifest_path,
view["design"]["manifest_hash"], reason)` proposes a new complete bundle. It keeps
the old references, approvals and independently recorded evidence, pauses for a
new plan review, and launches no model. A changed hash, active worker or pending
control rejects the correction. Resume through `TaskRun.resume_paused()` to
review the updated coverage; existing plan and completion gates still apply.

## Exact stopped-run recovery

The additive `view.recovery` projection describes a saved pause: what happened,
what is retained, recorded failure groups and specific next actions. Its `token`
binds the run, task, scope, settings, attempts and failure history. It does not
grant approval, increase limits or confirm that a human request is authorized.
Current AutoResolver request and approval gates remain authoritative. Running
and complete tasks have no recovery card. Unknown pause types retain inspection
and corrective feedback rather than offering a guessed execution command.

A caller displaying this card can append `--expected-recovery-token TOKEN` to
an existing `--resume-paused` or `--abandon-stage` command. The runner rechecks
the token under its run lock before applying settings or writing the checkpoint.
If the pause changed, it refuses: refresh and inspect the new card. Existing
CLI callers can omit the flag; all ordinary liveness, scope and approval gates
still apply. Abandoning an interrupted attempt preserves partial work and does
not resume automatically. Stop remains terminal for that conversation.

## Inspecting current verification

`autocode --run-dir RUN --status --inspect-evidence` performs a read-only source
and evidence inspection. The additive `view.verification` field lists every
planned criterion and method, its recorded result and current checked / failed /
unchecked state. A normal status projection labels saved results `not_inspected`;
a saved PASS alone never establishes current proof.

The inspection checks the report's source, task, plan and criterion identity,
then hashes its pinned project-local evidence. It rereads the source and checkpoint
to detect work changing during inspection. Missing, stale or unavailable evidence
keeps current requirements unchecked while retaining the old report. Human
acceptance remains a separately authenticated requirement. The inspection never
runs tests, changes the checkpoint, approves work or substitutes for the full
completion gate. The dashboard uses this supported inspection for selected task
detail; list polling does not hash every project checkout.

## Status view

Produced by `tools/autocode_run_view.py` from the saved state. Fields may be
added; existing fields keep their names and meanings, and `schema` changes if a
meaning must change.

`action()` is the same composition of `needs`, `pause_category` and `next_command`
that `view()` returns those three fields from, without the evidence, usage and
completion accounting around them. A caller that only needs the next action takes
it from here; a caller that needs any other field takes `view()`. Nothing else may
derive a recovery command from saved state.

`task` is the exact saved request text, including literal shell syntax and line
breaks. `task_sha256` is its UTF-8 SHA-256, also recorded when a new run starts or
a follow-up rewrites its request. The read-only view computes the digest from
saved text for legacy runs too; absent task text gives null for both fields.
A legacy request that cannot encode as UTF-8 remains visible with a null hash.
These fields identify what AutoCode received; they cannot recover text expanded
by a shell before CLI invocation. Use `--brief-file PATH` or `--brief -` to read
the original UTF-8 bytes without shell expansion.

```json
{
  "schema": 2,
  "pause_category": "your_decision",
  "pause_category_label": "Your decision",
  "status": "AWAITING_GOAL_APPROVAL",
  "next_command": "AUTOCODE_APPROVE_GOAL_TOKEN=... autocode --run-dir RUN --approve-goal -",
  "done": false,
  "needs": {"kind": "approve_plan", "token": "..."},
  "phase": "AWAITING_GOAL_APPROVAL",
  "next_stage": "orchestrator",
  "iteration": 1,
  "stop_reason": null,
  "current_task": {"id": "task-1", "objective": "...", "milestone_id": "M1"},
  "workflow": "build",
  "turn": 1,
  "evidence": {
    "outcome": "...",
    "base_commit": "...",
    "acceptance": [{"id": "AC1", "criterion": "...", "status": "passed", "evidence": "...",
                    "validator_status": "PASS", "human_reviewed": false}],
    "validator_source_revision": "...",
    "findings": [{"id": "F1", "status": "resolved", "severity": "minor", "finding": "...", "source": "sol"}],
    "regression_proof": null,
    "unverified_risk_claims": []
  }
}
```

`pause_category` groups a stopped run's cause as `your_decision`,
`your_environment`, `model_or_provider` or `autocode`; `pause_category_label`
is its readable label. The exact `status` and every existing field retain their
meaning. Both category fields and `next_command` are null for active or completed
runs. A typed quota/refusal route retains the provider category; an operational
request uses the additive `needs.pause_origin` when its published request names
that cause.

`next_command` formats the current `needs` and existing recovery controls. It
contains the owned run/workspace paths, and issued tokens travel through their
paired environment variables with the `-` selector. Replace `ANSWER`, `MODEL`,
`N` or `GOAL_FILE` with a decision; presentation never chooses one or grants
approval. An uncertain attempt offers abandonment first; inspect status again
before resuming. Missing issued tokens offer a noninteractive display, and a
missing original source or immutable caller binding offers inspection. A terminal
stop or dependency wait offers no recovery command. CLI gates still validate
freshness and authority when a person executes any displayed command.

`evidence` is what the run agreed to deliver and what supports it, for reports
made outside the runner (such as a pull request body, docs/issues.md):
the approved contract's intended outcome, the base commit, one row per
acceptance criterion with its latest recorded outcome and evidence, the
findings ledger, and, for bug fixes, the runner's own fail-before/pass-after
regression proof (`verdict`, `fail_to_pass`, `failures`, `unverified`,
`commands`, `source_revision`, and `case_tests`: each English test case's
proving tests; `null` otherwise). Each acceptance row also carries
`validator_status`: the latest saved validation's result for that criterion
(`FAIL`, `PASS` or `NOT_VERIFIED`), or `null` when that validation has no row
for it. A failed criterion must be distinguishable from an unchecked one; the
decision report's own `status` is a separate field and a separate vocabulary
(`verified` / not). `validator_source_revision` is the source revision that
validation checked, or `null` before one. The view does not read the
workspace: after rework, `validator_status` still reports that validation
until a newer one replaces it, so compare `validator_source_revision` to the
workspace before treating the status as current. `test_cases` lists a
reproduced bug's regression tests in plain English (`id`, `given`, `when`,
`then`; empty otherwise; see [Bug fixes](workflow.md#bug-fixes)). `check_replay`
is the current validation's checks as the runner itself re-ran them in a clean
copy: `verdict`, `source_revision` and one row per command (`command`,
`exit_code`, `timed_out`, `output`); `null` before a PASS validation and for
validations that predate it (see [Execution](execution.md#the-runner-re-runs-the-validators-checks)).
It also carries the runner's own observations of promises a person declared:
`brief_acceptance` (exact CLI output formats) and `risk_acceptance` (process
recovery of a declared lease queue or transactional outbox, and a race between
three interpreters when the request also promises atomicity under contention:
`verdict`, `source_revision`, `summary` and one row per observation with its
pinned supervisor transcript), each `null` when the request declares none. A failed
`risk_acceptance` makes the replay's `verdict` `FAIL` even though the Tester's
own checks passed (see [Execution](execution.md#process-recovery-proof-for-declared-lifecycle-apis)).
Each `findings` row also has `source`: `sol` (Tester), `astra` (Completion
Reviewer) or `runner` (a failed runner lifecycle observation, which only the
runner closes). `unverified_risk_claims` lists the person's own sentences that
promise durability or concurrency but that no runner protocol exercises (`kind`
`durability` or `concurrency`, `source_id`, `quote`, `reason`); `[]` when there
are none. It is a disclosure, not a check: a PASS never verifies them.
`proof_stability` is the proving tests' repeats: `repeats`, `unstable` (each
test, which side, its kind and the run outcomes), `unrepeated`, `next_step` when
a test disagreed, and `note`; `null` when the proof did not repeat tests.
`diff_coverage` is the same proof's line coverage of changed product hunks
(`collected`, `tool`, `note`, `files`, `uncovered`, `unmeasured`); `null` when the
proof did not record it. It never blocks completion.
`mutation_check` is the same proof's report-only mutation check (#699, see
[Named test proof](named-test-proof.md#mutation-check)): `collected`, `note`,
`limit_per_file`, `budget_seconds`, `files` (each `path`, `notes`, and `mutants`
with `line`, `operator`, `before`, `after`, `outcome` `killed`, `survived`,
`timeout` or `not_run`, and `survived_tests`) and, once collected, `survivors`,
keyed by the same English test-case ids as `regression_proof.case_tests` (a
diagnosis's `T1`, a plan case's id; not `evidence.acceptance` criterion ids): each
case's proving tests that still passed with a product line changed (`test`,
`path`, `line`, `operator`, `before`, `after`, `text`), `{}` when every mutant
that ran was killed. Every proof that did not pass, including one refused before
any test ran, has the note `not run: the proof did not pass` and no survivors;
`null` when a passing proof had no due test case, or there is no proof. It never
blocks completion. The review workflow runs no named proof and has no mutation
check.
`regression_proof.unstable` lists the same unstable tests.
`finding_scope_moves` appears once an approved revision moved a criterion that
open findings cite to another milestone: one row per finding it re-attributed
(`finding`, `from` with the old `milestone_id` and `criteria`, `to` with every
resulting finding's `id`, `milestone_id` and `criteria`, `contract_token`,
`at`). A finding split across milestones appears in `findings` once per part
(see [Execution](execution.md#open-findings)).

`usage` is the run's tokens and cost so far: `stages` (finished), `active_stage` (the stage
running now, or null), `tokens`, `cost_usd` (`reported`, `estimated`, `complete`), `unknown_stages`
and `by_role` (see [Cost reporting](cost-reporting.md#every-task-continuously)). Unknown cost is
not zero: `complete` is false while a stage has none or is running.

`cost` is the run's spend against its money cap ([Money cap](execution.md#money-cap)), priced at the
dated table `autocode models --prices` prints: `estimated_usd` (stages priced from the table),
`reported_usd` (stages whose provider reported a cost), `inherited_usd` (the spend a restored
checkpoint carried from the run it was restored from), `spent_usd` (their sum, which the cap is
compared with), `cap_usd` (the saved `--max-cost-usd`, or null for no cap), `unpriced_stages`
(stages on a model without a rate, or with no token counts recorded; not counted), `complete` and `prices_as_of`. Each stage counts
once. A null amount is unknown, never zero: `estimated_usd` and `reported_usd` are null when no stage
has that basis, and `spent_usd` is null when no stage is known at all; while `complete` is false,
`spent_usd` is a floor. `usage.cost_usd` keeps the frozen comparison rates its reports are bound to,
so its `estimated` can differ from `cost.estimated_usd`.

`progress` is how far the run has got, counted only from saved records
(`tools/autocode_progress_view.py`; added by `autocode --status`, which supplies
the accepted milestones, the valid review receipts and the state of the recorded
workers). `line` is the one line a person reads, for example
`Builder working · 1 of 3 tasks done · 1 of 3 requirements checked, 1 failed · 1 open problem · nothing needed from you`;
its wording may change, so programs read the fields beside it:

- `headline`: `Complete`, `Waiting for you`, `Paused`, `Waiting for another run`,
  `<job> working`, `<job> finished` (its report is saved but not applied yet),
  `Next: <job>`, `<job> stopped without saving a report` (the recorded workers are
  gone; see the top-level `stale` and `next_action`; a stopped runner check is named
  `Runner check (...)`), or the status in words when no stage is known. Job names
  come from `autocode_roles`; `stage` is that name.
- `needs_you`: what a person has to do, in words: what `needs` asks for, the
  top-level `next_action` when the workers are gone, or `plan approval needed`
  while a saved plan waits to be shown for approval.
- `tasks`: one item per milestone of the current plan, proposed or approved (`number`,
  `id`, `label`, `state`, `current`), with `done` and `total`. `state` is `done`
  (accepted under the current contract, or the run is complete), `working` (the run is
  on it now), `waiting`, or `unknown` when milestone checkpoints are off (`known` is
  then false until the run completes).
- `requirements`: one item per acceptance criterion. Its state comes from the newest
  `PASS` or `FAIL` for it across the Tester results under the current contract (the
  current validation and the ones before it); `NOT_VERIFIED` and a missing row leave
  an earlier verdict standing. `checked` is a pass in the current validation with no
  different source recorded since; `checked_earlier` a pass from an earlier
  validation, or from one the source has changed since (`rebuilt_since_check`);
  `failed`; `awaiting_review` (a human-review criterion the Tester passed but no
  person has accepted yet); `reviewed` (a person's review receipt is valid for the
  current validation); `unchecked` otherwise, never `checked`. Each state has a
  count. Results under another contract, or with no contract hash under a contract,
  do not count. The view does not read the workspace.
- `problems`: `open` counts the open findings (`items`: `id`, `finding`, `source`,
  `severity`, `blocking`) plus, for a code or design review and a design-conflict
  stop, the findings that job saved instead (`reports`: `kind`, `blocking`,
  `advisory`, `report_path`; a design review also has `questions`, the number of
  questions its report asks, and `revision`, 1 until a reply revises it). A design review's
  `blocking` and `advisory` count its open concerns: a concern a reply resolved
  is not a problem.
- `for_earlier_request`: true after a `--follow-up` until the follow-up's own plan is
  drafted; the previous request's tasks, requirements and review findings are then
  left out.

No field is a percentage or an estimate from elapsed time.

`displayed_plan` is an optional structured approval projection: `revision`, `hash`,
`token`, `acceptance_criteria`, `constraints` and `permission_boundaries`. It appears
only when the last displayed token matches the current sealed contract. Missing,
modified or stale contracts do not expose it. Criterion `verification_method` and
`human_review` values are copied without interpreting or coercing model-authored
text. Automation must check these structured fields and bind the token to
`needs.token`; do not infer approval authority by parsing headings or review labels
embedded in `--show-goal` prose. This projection is not approval, execution permission
or completion proof; the existing CLI approval checks remain authoritative.

`approved_contract` is the plan in force: the contract that was approved, for as long
as that approval holds. It has `revision`, `hash`, `token` (the `r<revision>:<hash>`
that was approved), `task_id`, `approved_at` and `body`, the full approved contract
body (outcome, deliverables, acceptance criteria, constraints, permission boundaries,
end-to-end flow, technical approach, milestones and the rest) copied as saved. It is
absent until the current sealed contract carries an approval of exactly its token,
once a newer draft revision replaces the approved one (a goal change, or the plan a
`--follow-up` drafts), and while the contract itself records an open blocking
question (approval refuses one); a question the run asks after approval does not
remove it. An edited or stale contract does not expose it, and a completed run keeps
it. Until a follow-up's own plan is drafted, and for a follow-up answered by a
review, design or discussion, which drafts none, it is still the earlier request's
approved plan; compare `turn` or `progress.for_earlier_request`. A follow-up that
builds the design the previous turn proposed is the exception: when the design
check passes, the design turn's plan is archived, so `approved_contract` is absent
until the build's own plan is approved. The approval is
normally the user's, but a small bug fix approved under the workflow policy the user
agreed to (the short path for small fixes, off for now; see
[Bug fixes](workflow.md#bug-fixes)) shows it too, and the view does not say which.
`displayed_plan` is what a person is asked to approve; `approved_contract` is what
was approved. A coordinating layer such as
`autocode program` reads a child run's approved criteria here (`program derive` and
the inherited-requirement check), never from `state.json`. Like `displayed_plan`, it
is neither execution permission nor completion proof.

`routes` maps every configured role to the `model` and `engine` its next launch
uses. `route_assignments` lists, oldest first, every model a person named for a
role after its quota ran out or its provider's content filter refused it: `kind` (always `route_assignment`), `role`, `job`,
`from`, `to`, `engine`, `stage`, `attempt_id`, `events`, `pause_status`, `at`,
`actor`, `via` (`answer` or `resume_flag`) and, when `via` is `answer`, the
`request_id` it answered. It is empty for runs that never stopped on quota or a refusal.

<a id="operational-information"></a>
For a diagnosed criterion contradiction, an answer need may include
`recommended_revision` with `acceptance_criteria: [{id, criterion}]` and
`recommended_revision_response: "Accept recommended revision"`. Present the
request's current/proposed wording before submitting that exact response through
`TaskRun.respond_operational(request_id, token, text)`.
Acceptance installs a draft; it does not advance the run or approve the plan.
Follow the next published need through configured plan review and explicit
`approve_plan(new_token)`. The correction is limited to existing criterion text:
at most 32 rows, with IDs up to 128 characters and wording up to 8,192 characters.
All verification, human-review, source and permission fields are preserved.
Replaying the identical response is idempotent. A stale request or changed
diagnosis is refused, and other information keeps the ordinary hold.

`information_review` describes Resolver's one re-evaluation of the corrective
information last sent to an operational request (`--resolver-response
provide_information`), or is `null`: `request_id`, `status`, `cause` (the pause
the request was for), `scheduled_at`, `evaluated_at`, `decision` (`hold` or
`continue`), `reason`, `action` and `receipt` (the runner-owned decision record).
`status` is `pending` until the next resume at that pause (`autocode resume` or
`--resume-paused` with no other recovery flag) evaluates it once, with no provider
call. A run that left the pause first (through another control, or to a newer
request) never evaluates it, and `status` reads `superseded`. Otherwise
it is `held` (still paused, `action` and `needs.action` name the control it
requires, such as `--resume-paused --grant-recovery N`, a raised bound,
`--abandon-stage ATTEMPT` or, at a parallel Builder member's quota or
content-filter stop, `--resume-paused --retry-builder M`, which `action` also names
while the review is `pending`; at `PAUSED_PLANNING_BUDGET` that is the
`planning_budget` need's `action`), `admitted` (the run continued through the normal
admission checks) or `stale` (the run, request, response or evidence changed after
the response, so it was not evaluated and Resolver asks a fresh request). Information never raises a limit, resets
a count or approves anything; a later resume repeats a held decision without
evaluating again.

`tool_containment` says how built-in OpenCode stages other than planning run their
tools: `contained` (inside the kernel tool boundary, see
[Execution](execution.md#native-tool-containment)) or `uncontained_user_accepted`
(the run was started or resumed with `--allow-uncontained-tools`; those stages have
OpenCode's own permission checks only, and each such stage record says
`uncontained_tools: true`). It is `null` for runs that launch no such stage: the
native Codex engine (unless its Investigator is pinned to an OpenCode model) and
configured providers.

`direct_rework_assignments` records a repair assigned directly from a Completion
Owner's accepted REWORK report. Each entry binds the original and assigned tasks,
contract, source, report and evidence hashes, and the ordinary retry charged by
the runner. It is assignment provenance, not a Resolver diagnosis or completion
proof. The list is empty for runs that have never used this path.

Direct assignment is limited to the first ordinary repair of a single serial
milestone, with an independent Tester's executed failure and a complete task
within the same approved scope. Ambiguous or incomplete tasks, repeated failures,
parallel/integrated work and recovery cases retain the Resolver path. Pending
human decisions remain intact and hold the handoff before either route. Modified
sealed evidence pauses before dispatch. Every assigned repair still requires
fresh verification and independent completion acceptance.

`workflow` is the kind of job AutoCode recognized from the request, decided by
the first stage of every new run (`recognize_workflow`): one of `build`,
`bugfix`, `review`, `design` or `discuss` (see `scenarios/README.md`,
"Workflows"). It is `null` until that stage has run, and for runs that predate
it. `workflow_source` is `"model"` when the recognizer decided it and `"user"`
when `--workflow` named it; `workflow_reason` is the recognizer's one-sentence
reason. Both are `null` whenever `workflow` is.

`needs` is `null` when the run is complete. Otherwise its `kind` says what the
run is waiting for:

| `kind` | Waiting for | Extra fields | Answer with |
| --- | --- | --- | --- |
| `approve_plan` | approval of the plan AutoCode displayed | `token` | Approve the plan |
| `answer` | answers to clarifying questions or a decision | `questions` (id, question, why, options, proposed_default), `request_kind`; for a question Resolver published, also `resolver_request_id`, `resolver_token` and `resolver_scope`; with `resolver_scope` `blocker` or `operational_exhaustion` (and no `route`), `action` is the response command; when `pause_origin` is `PAUSED_NO_PROGRESS` because a repeated incident was held, also `retry_action` (`--resume-paused --retry-failed-stage`), the one scoped retry the hold admits after inspection | Answer, with the current `resolver_token`; the questions left return under a new one. A question with `options` takes exactly one of those labels or its `proposed_default`; free text answers only a question without options. A `blocker` or `operational_exhaustion` request refuses `--answer` and takes `action` (`respond_operational()`), or, after inspection, `retry_action` (`retry_failed_stage()`) |
| `review` | a person to accept specific acceptance criteria | `criteria`, `token`, `question` | Approve a review, per criterion |
| `planning_budget` | more plan-review calls | `reason` | Plan feedback, or `--planning-review-call-limit N` |
| `recover_source` | an attempt without a saved original source identity | retained retry metadata, `recovery_hint`; `action` is null | Inspect the archive and current changes before a new run |
| `retry_job` | inspection of a stopped workflow job | `job_retry_token`, `archive`, `write_diagnosis`, `recovery_hint`; `route` after quota, a content-filter refusal or an authenticated output limit | Exact retry after restoring original source; with `route`, name another model first |
| `resume` | a person to inspect a pause and resolve its cause | `reason`; `new_run_required` with `action=fresh_run` preserves an immutable caller-binding failure and requires a new run; `edit_required` at a repairable component-plan stop prescribes `action` (`--edit-goal FILE`) or `feedback_action` (`--feedback TEXT`), then fresh approval; otherwise `action` when one command continues, such as `--resume-paused --no-progress-limit N` at a `PAUSED_NO_PROGRESS` its unchanged-batch limit caused, with `no_progress_batches`, the retained count N must exceed (or N is `0`), or `--resume-paused --retry-builder M` at a parallel Builder member's quota, content-filter or output-limit stop whose request was answered without a model | Start a fresh run when required; otherwise correct and freshly approve an edit-required plan, or resume once resolved |
| `dependency` | a bound producer run's delivery (see [Dependencies between existing runs](#dependencies-between-existing-runs)) | `reason`, `producer_run`; `action` is null | Nothing to answer: the dependency worker (`python tools/autocode_dependencies.py ... --watch`) calls `--receive-dependency MANIFEST` (`TaskRun.receive_dependency`) when the producer's verified delivery is ready |
| `continue` | nothing; the run can simply proceed | | Continue |

A `resolver_scope` of `operational_exhaustion` or `blocker` means Resolver
stopped the run because it could not continue safely (for example, the
run time limit was reached). That question is for a person who has looked at
the run; a caller must not answer it with a proposed default. Its `action` is
the response command. A `blocker` is often a decision about the approved
contract (a criterion Resolver proved contradictory): the response is retained
as information and never edits the contract, so after it the run holds and
`needs.reason` names the path that applies such a decision, `--edit-goal
body.json` followed by `--approve-goal`.

When a role's quota ran out, its provider's content filter refused it, or its authenticated response exhausted the output limit, the `answer`
need also carries `route`: `question_id` (`route-ROLE`), `role`, `job`, `current_model`,
`engine`, `cause` (`quota`, `content_filter` or `output_limit`), `stopped_model` and, for a refusal,
`candidates` (configured models that would pass the launch rules; advice only). Its
question has no default and is never delegable; only a model a person names
answers it (`--answer route-ROLE=MODEL`), and the run then needs a resume. A
stopped workflow job carries the same `route`, with the same fields, on its
`retry_job` need ([Failed workflow jobs](#failed-workflow-jobs)).

Approving a plan or a review is a real user decision. Automated callers should
do it only when a person has delegated that decision to them, as the scenario
harness does for test runs.

## Runner checks in status

`view.runner_check` describes a local check in progress before the Tester:
its `stage`, plain-language `summary`, `started_at`, `updated_at`, `command` and
`output` path. It is `null` when no such check is active. These checks do not
consume a model turn. A resumed run saves its running state before the first
test starts, so it does not continue to look paused throughout a long suite.

The CLI also returns `runner_check_workers` with `checked`, `alive` and
`live_pids`, using the controller's recorded process birth identity. An exited
controller makes the checkpoint `stale`; an inaccessible process is unknown,
not assumed dead. Inspect retained test output before restarting a stale check.
The check activity never substitutes for a passing proof or independent review.

## Dependencies between existing runs

An already authorized delivery should not become a request for a person to assemble
hashes and review logs. At an exact stopped permission request, an operator can bind
the consumer to a producer using `--bind-dependency SPEC.json` (also
`TaskRun.bind_dependency`). The specification names `producer_workspace`,
`producer_run`, `question_id`, the current `request_token`, a human-readable `label`,
an explicit `files` list, and a `destination` beneath the consumer run's `evidence/`.
This action delegates only that delivery, records the authorization, and presents
`WAITING_FOR_DEPENDENCY` / `needs.kind=dependency`, with no question to answer.
It cannot consume a plan approval or artifact acceptance request.

Run `python tools/autocode_dependencies.py --workspace CONSUMER --run-dir RUN --watch`
to supervise this binding. The worker uses `TaskRun.status`, never another run's
private state. The additive `view.delivery` exists only for runner-verified current
completion, with the source snapshot, approved contract and independently recorded
Tester/completion review pins. The worker waits without model calls, copies only
the declared regular files into an atomic evidence bundle, checks the producer again,
then calls `--receive-dependency MANIFEST` and continues the consumer. It never imports
source into the consumer itself or accepts the consumer's integration result.

Both the binding and delivered receipt survive restarts. A single worker lock prevents
duplicate transports; a crash after publishing the bundle is safely replayable.
Changed producer source, missing review pins, modified deliveries and changed consumer
source/contracts block transport or resume. Operator feedback or an edited plan cancels
the wait so the new decision can be reviewed. Keep the worker running while waiting;
if it exits with an error, reconcile the reported cause and restart it against the same
run. A failed/incomplete producer cannot release the consumer.

`TaskRun.grant_recovery(N)` explicitly grants a positive number of additional recoveries after the operator inspects saved work and fixes the cause. It preserves recovery history and uses the CLI checkpoint guards. `resume_paused()` and operational guidance do not grant an allowance.

### Failed workflow jobs

When a sessionless `report_file` bug Investigator lost its controller after
writing a report but before the exit/result checkpoint, `status()` may expose
`job_report_recovery`: the exact owned `report` path, its `sha256`, `attempt_id`,
original `source_identity`, and a bound `token`. Read the report and inspect the
attempt before calling `run.recover_job_report(token)` (CLI:
`--recover-job-report TOKEN`). This is explicit adoption of those exact bytes,
not a claim that the provider completed successfully. The raw exit remains
unknown and stage history records operator recovery provenance. The action
loads and applies the report normally, including the reproduction probe, and
launches no model; continue separately afterwards.

The offer is read-only and disappears if the report, source capture, original
source, configuration, or cleanup evidence is invalid or changed. All recorded
workers and the independent keeper must be conclusively dead, with an authenticated
owner-loss receipt proving cleanup. Timeouts, uncertain cleanup, rejected or
malformed output, foreign/symlink paths, session/event reports, writers and
report-repair attempts are not eligible. There is no durable provider report
finalization seal, so presence/schema validity alone never authorizes automatic
adoption. Plain attach/resume retains a valid offered attempt without applying
or replaying it. No offer is inferred for an already archived job failure.

A stopped Reviewer, Architect, Analyst or Investigator publishes
`needs.kind = "retry_job"`, retaining its owning stage and archived transcript.
Inspect `needs.archive`, `needs.reason` and `needs.write_diagnosis`; then call
`run.retry_job(view["needs"]["job_retry_token"])` for one fresh attempt under the
saved source, route and limits. The CLI equivalent is
`--resume-paused --retry-failed-stage --job-retry-token TOKEN`. A plain resume
keeps the pause. Stale source/configuration, a token for a different attempt,
or unresolved restoration is rejected before any model request.

A job stopped by its provider's content filter or on quota also carries
`needs.route` (the shape of the `answer` need's `route`: `question_id`, `role`,
`job`, `current_model`, `engine`, `cause`, `stopped_model`, and `candidates` for a
refusal). The same model is likely to refuse again, so before retrying, a person
can name another model: `run.assign_model(role, model)` answers
`--answer route-ROLE=MODEL --job-retry-token TOKEN` with the view's token. A model the
launch would refuse, or the refused model itself, raises and changes nothing. An
accepted model is saved as a `route_assignment` and the need comes back with a new
`job_retry_token`; the old one is rejected. Its `route` is asked again against the new
configuration: `current_model` is the named model, and `candidates` never lists it
(answering it again is refused). Then call
`run.retry_job(view["needs"]["job_retry_token"])`. `assign_model` updates a
`--ROLE-model` in the client's options, so the retry never passes the old model back;
the CLI refuses a `--ROLE-model` change for the stopped role at this stop (it would
make the exact retry stale), unless it puts back the model the retry is bound to.
The answer carries no other setting: given with a limit or another role's model it
is refused and nothing is saved, and the CLI refuses it next to
`--resume-paused --retry-failed-stage` (the retry needs the new token). After a
refusal or output-limit stop, until a different model is named, the need's `action` is that answer
(`--answer route-ROLE=MODEL --job-retry-token TOKEN`) and the recovery card offers
no `retry_job` action, since the exact retry would replay the stopped model; the
CLI still accepts the shown token. A quota stop keeps the retry in both, for once
the quota resets. Once a model
is named, `progress.needs_you` and the recovery card's `what_happened` ask only for
the retry on it, and `action` and the card's `retry_job` carry that retry. The
same holds when the job's uncertain attempt was set aside with `--abandon-stage`:
the reason keeps the refusal or quota, the model and the provider's words. The Architect, Analyst
and Investigator routes have no flag; only this answer moves them (an
`--investigator-model` in the options pins the stuck-stage Investigator and is left
as it is).

A missing or corrupt capture file does not prevent retry if the current source
exactly matches the identity saved before the attempt, including file modes and
Git HEAD. After manual restoration, the same token can be used; the old
`unrestored`/`write_diagnosis` fields remain historical evidence and the CLI
rechecks the full identity. It does not reconstruct an original identity from
the current checkout.

In status schema 2, an older attempt that never recorded its original identity
exposes `needs.kind = "recover_source"` and `action = null`, with
`recovery_hint`. Its token, archive and diagnosis remain available for inspection,
but cannot authorize an exact retry. Inspect the retained work and current changes
before starting a new run. No automatic restart or budget reset occurs.

### Saved code checkpoints

`status()["code_checkpoints"]` adds immutable source receipts for recorded,
code-changing Builder steps. Historical step metadata without source objects
is inspectable but cannot be restored. `recorded_check` is historical, never
current proof. This interface does not silently materialize snapshots for old runs.

Use `compare_checkpoint(id)` to inspect all changed paths and a bounded textual
diff (including uncommitted additions). `restore_checkpoint(id, expected_token,
request_id)` requires that exact comparison and returns a new `TaskRun` on a new
managed branch. The original branch, staged changes, run and later work remain
intact. Restoration is supported at an explicit reconciled pause of an ordinary
build run with the same approved contract. Active workers/checks, pending
interventions, human decisions, terminal Stop, progressive contracts and other
workflow kinds refuse with an explanation; no worker is interrupted.

The new continuation is paused, with the exact plan approval, saved settings,
model routes, limits and consumed allowances preserved. All milestones require
fresh proof. Implementation/validation/completion and human artifact acceptance
are not inherited as current. Earlier finding dispositions are restored; later
open findings are marked rolled back, with complete original history retained
in `restoration-history.json` and on the original run. Explicit `resume_paused()`
uses the normal Builder/Validator/completion gates. No approval or provider call
happens during restoration. Replaying the same request reconciles its owned
candidate; changed or uncertain partial candidates are preserved and refused.

The CLI equivalent is `autocode checkpoint --workspace WORKTREE --run-dir RUN
--compare CHECKPOINT`, followed by the mutually exclusive `--restore CHECKPOINT
--expected-token TOKEN --request-id ID`. The dashboard uses this supported
interface, places confirmation in chat and comparison in the Changes pane.

### Conversation task provenance

New dashboard attachments, including saved legacy conversations, carry a canonical
`autocode.conversation-task` envelope. The full validated handoff remains in the
task presented to workers: human and assistant messages, titles, structured drafts,
routes and receipts are retained. Only its human messages are requirement sources;
assistant suggestions and unapproved drafts do not become mandatory requirements.
Later human feedback and answers retain their existing source rules. A human can
adopt a suggested literal by requesting it in a message, feedback or answer.

Source projection requires the complete canonical envelope and valid handoff digest.
Extra instructions, altered envelopes and ordinary CLI tasks retain whole-task
source semantics. Existing saved flattened tasks are not rewritten or reinterpreted.
This format changes neither model routes nor the legacy/continuous handoff controls,
and conveys no approval to implement.
## Current verification ownership

`status()` includes additive `verification_obligation` metadata when a scheduled
clean replay is pending. It names the current obligation separately from the
saved `runner_check`, including its phase, exact preparation admission and current
native command custody. `runner_check_matches_current` tells whether the saved
check describes that same command. Status binds the current attempt, admission,
phase/owner and command hierarchy before inspecting liveness, including genuinely
live workers; it does not require a stopped result merely to display them.
An admitted or post-execution phase with no current custody borrows no earlier
command's liveness. A legacy unbound marker or unavailable
ownership remains held; status does not reconcile it, grant a retry or establish
test success. Use the displayed supported recovery action and retain interrupted
receipts separately from fresh verification.
