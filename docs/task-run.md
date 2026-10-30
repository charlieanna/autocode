# Task-run interface

[← Back to README](../README.md)

How a program drives one AutoCode task run: start it, read where it stands,
answer what it asks, and let it continue. This is the only supported way for
code outside the runner (the scenario harness, `autocode issue`, the
[coding-agent skill and MCP server](agent-step.md), and the planned
architecture and multi-component layer) to control a run. Such code must not import
runner internals such as `autocode.py` or read `state.json` directly; that file has
about 140 keys and changes without notice.

A task run is bound to one workspace, one Git tree. The multi-component layer drives
several task runs, but all of them come from the same repository; it cannot link runs in
separate repositories. See [Work that spans several repositories](multi-repo.md).

Every step is one CLI invocation, and the run's state lives on disk. A caller
that crashes can reattach to the same run directory with
`TaskRun(workspace, run_dir)`, or with `TaskRun.attach(workspace)` if it never
learned the run directory (it returns the workspace's only run, or `None`). In a
workspace where someone else may also run AutoCode, note `TaskRun.runs_in(workspace)`
before the start and pass it as `attach(workspace, exclude=...)`, so that only a run
created since can be adopted.

## Automatic coordination from chat

For an unresolved bug with grounded external scope, `view.coordination` names
`status_command`, `next_command` and `cancel_command`. The terminal client reads
`autocode coordinate status --run-dir RUN --json` and executes only its named
actions. It does not read child state, select repositories or compose tests.
`/repos` displays the shared local source catalog; configure its roots with
`autocode registry roots add PATH` and standalone repositories with
`autocode registry repositories add PATH`.

Initial Requirements and Investigator requests receive a pinned, bounded inventory
of all configured sources, including external roots. The launch directory is
optional context. Catalog file citations use `catalog/ID/path:line` and are
accepted only when the cited ordinary file matches the pinned Git blob. The task
retains the inventory at `planning/source-catalog.json`; changed catalogs,
revisions or altered receipts cannot silently replace its evidence. Built-in
read-only intake shells receive only the validated catalog directories as extra
read roots; this grants no Builder write authority. Clear source-grounded bug
requirements flow into the complete plan without a separate Requirements
confirmation. Genuine unresolved business questions still pause for an answer.

The version-1 coordination view identifies the anchor, record revision, phase,
member workspaces/runs/PRs, `done`, `needs`, and optional `next_command` and
`approval`. The joint-test baseline and plan are prepared before any Builder.
One approval binds the original request, source-catalog snapshot, repository
membership and pinned baselines, plus every exact member and joint-plan token.
Clients reread before approving. Pass its token privately as `--token -` with
`AUTOCODE_COORDINATION_TOKEN`; providers never receive that variable. A missing
or changed token authorizes nothing. A later revision of the coordinated plans
(after a joint mismatch repair or a replanned joint test) whose every member and
joint plan keeps the approved contract (acceptance criteria, constraints,
permission boundaries, scope exclusions and Git delivery) is approved by the
coordinator under that same approval as the automatic `carry_approval` step,
recorded in the coordination state with the targets it covered; a revision that
changes any of those, or adds a repository the person has not seen, is offered
for approval again. Repository-choice actions exist only for
current catalog candidates. After member runs complete, a named
`coordinate bind` action snapshots exact candidate trees, including deletions,
for the already-approved joint test. Cancellation prevents further dispatch and
submits only current members' named boundary-pause actions.

At a settled shared-cost stop, `needs.kind=budget` names the current finite cap,
known accumulated spend, accounting completeness, `minimum_cap_usd`, and its own
`next_command` argument array. Terminal chat shows these, fills only its
`--max-cost-usd N` slot with the person's finite amount, confirms the whole-set
total, rereads the offer and sends its token privately using
`AUTOCODE_COORDINATION_TOKEN`. `coordinate budget` rejects changed checkpoints,
active workers, uncertain or cancelled operations, insufficient amounts and
zero/unlimited caps. It retains prior member, integration and verifier costs and
all other settings. An existing sufficient shared cap may be reasserted.
The action acknowledges only the displayed member's cost pause through TaskRun,
with that run's current public recovery token and its derived share of the cap.
It grants no plan approval, proof waiver or general recovery retry. Durable
intent precedes the resume; an uncertain reply is retained for inspection and
never automatically replayed. No saved member state is edited by coordination.

The coordinator drives each member using `TaskRun`. `related_profile` is the
public configuration export: selected role routes, efforts, providers, run limits
and policies, without sessions or approvals. Fresh related runs accept a pinned
`--related-profile FILE --related-profile-sha256 SHA256`; this cannot be applied
to a saved run. `TaskRun.assign_linked_scope(identity)` consumes only a current
core-owned changeset assignment. It retires the exact covered scope question;
it neither invents a human answer nor approves a plan.

An integration task starts from original baselines, then receives the candidate
copies only through the named binding action. It runs the approved joint test and
returns differences to members through follow-ups. A revised plan renews the
single coordinated approval. Publication
requires every member's canonical completion evidence, exact matching integration
copies, and the integration run's current authenticated completion report with
independent criterion verdicts and runner fail-before/pass-after proof. Approved
prose methods and named `test:`/`guard:` bindings remain unchanged. A native
verification receipt has `kind: task_run`; its `record` names the canonical report,
and its binding and digests are rechecked through TaskRun's inspected status before
publication and on reopen. Its model costs are already counted in the joint run.
Older completed plans without a native regression-proof record still use standalone
`verify`; their records retain the existing freshness reader. A present but stale
or failing native proof is refused rather than falling back.
The coordinator owns durable publication intent and reconciles branch/PR identity
before any retry. It never merges. Manual change-set completion alone is not
automatic coordination completion.

A coordinated member names the repositories that own its external work by their
exact member names (or a genuinely grounded repository name); its member context
says so, and a role label such as `Checkout display member` is refused by the
report contract, so the normal bounded report repair attempts the correction. A
repair that does not converge leaves the member stopped on that visible failure;
the refused obligation is never silently dropped (ordinary filtering of ungrounded
scope is unchanged). For labels earlier runs already accepted, discovery resolves
the trailing member role noun against the authorized catalog only, and only when
the catalog's own exact, noun, parenthetical and coordinated matches found
nothing: every alias-prefix identity is enumerated, a job word or phrase naming
another repository refuses the label, one identity that is already linked and
still available reuses that member automatically, and anything else stays the
person's repository gate.

Automatic coordination currently runs in owned terminal chats. A `durable_local`
anchor is refused without changing its saved execution policy. See
[multi-repository setup and limits](multi-repo.md).

## Per-attempt execution identity (#2059)

New provider launches add bounded `execution_identity` to their saved attempt and
public `usage.accounting.attempts` row. Schema 1 records the actual route slot,
engine/tool, model, effective effort, runner source commit when known, a saved
selected-route billing classification/basis, and optional configured endpoint
identity (origin, SHA-256, protocol and API provider slot). It contains no key,
credential URL, tenant path or environment dump. An endpoint hash describes the
configured target, not an observed HTTP redirect or invoice. Opaque backend
endpoints and unrecorded billing remain null; account/model discovery is unchanged.

The effective `reasoning_effort` is separate from `launch_route`'s saved route
configuration: existing fit/first-Builder policies may differ. Empty/null effort
can mean the provider default and is not replaced with a guessed level. Invalid
or contradictory identity records project as unknown; old attempts are not
backfilled from the viewer's current settings/runtime. These are additive
measurement fields, not action authority, route qualification or a new client.

## Remote team service proposal (#2023)

The [single-host team-service contract](team-service-contract.md) proposes an
additive authenticated service over this interface. It is **not implemented or
approved**. Current local tokens and Git/`--as` provenance do not authenticate a
remote person. The proposal records opaque remote envelopes, current named-action
bindings, separate admission/execution/delivery states and explicit public core
gaps. Its [synthetic examples](examples/team-service-v1.json) are design data,
not live responses. No existing client, task policy, route, approval or host
configuration changes through this documentation. The owner decision is needed
before implementing the service extension or its dependent runtime slices.

## Python client

`tools/autocode_taskrun.py` wraps the commands below.

`TaskRun.start(..., run_dir=path)` selects an exact fresh run directory, an
immediate child of `workspace/.autocode/runs`. Existing directories and symlinks
are refused before a provider launches. The optional destination is used by
[saved-run replay](replay.md); the run is created there without moving bound
artifacts afterward.

When the workspace is a plain, non-empty folder, the core bootstraps a task
project inside `workspace/autocode-projects/` and runs there. A destination
named under the selected folder's `.autocode/runs` keeps its name: the run is
created under the bootstrapped project instead, and commands may keep using the
selected folder and that name as the run's handle. The status view names the
effective `workspace` and `run_dir`. When the launch does this bootstrapping, the
run also says so once in its chat history (`view.chat.messages`, message id
`workspace-bootstrap`), naming the folder that was selected and the project the
work happens in, so a client shows the move instead of leaving the person to infer
it from `view.workspace`.

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

An AutoCode self-build requires an executing controller outside the task
workspace, so the task cannot edit the code that runs it. With such a controller
(an installed `autocode`, or another checkout), an AutoCode checkout is an ordinary
project and `TaskRun.start` or `--in-place` works in it directly, main checkout
included; an ordinary CLI launch creates its own worktree by default. An unsafe new run
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

To build AutoCode itself with the controller from that same source checkout, keep
that checkout fixed and create a separate workspace:

```sh
git -C /path/to/autocode worktree add --detach /path/to/autocode-self-build HEAD
/path/to/controller-venv/bin/python /path/to/autocode/tools/autocode.py \
  "Fix the selected issue" --workspace /path/to/autocode-self-build --in-place
```

Use the existing provider and model flags for the run. Keep the controller
checkout and its environment unchanged until the run has stopped. An installed
controller is also safe when its package directory is outside the task worktree.

Inputs fixed when a run starts, such as `--ui-run`, `--test-root` and `--package`, belong in `start_options`
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

A caller that keeps each invocation's output, such as its `stdout.log`, `stderr.log`
and exit code, reads it from the client:

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
- A call that outlives `timeout` raises `TaskRunError` naming the call and its limit
  (`status did not finish within 60 s`), followed by the end of each stream that reached
  the caller before the limit, or `no output reached the caller before the limit`.
  `TaskRunError.partial_output` holds that `(stdout, stderr)` whole; it is `None` for
  every other error. It is what arrived, not everything the call printed: unless
  `PYTHONUNBUFFERED` is set, the CLI's stdout to a pipe is block-buffered, and a call
  stopped at its limit can lose what is still in that buffer.
- `TaskRun.cwd` (`cwd=` on `start` and `attach`) is the CLI's working directory, so
  relative paths in `options` and `start_options` resolve against it. `None`, the
  default, keeps the caller's. A continuation from `restore_checkpoint` keeps it.

Operator-declared prerequisites can be supplied once with `--task-preflight`
in `start_options`. A failed prerequisite pauses before paid dispatch and is
visible in the additive `task_preflight` status field. See
[task-preflight.md](task-preflight.md) for phase selection, copied input checks,
receipt reuse and supported correction; readiness does not replace proof.

## Chat projection for ordinary task runs

A clean user-requested pause may set `needs.confirmation_required: false` on
its ordinary `resume` offer. The person's Resume click then needs no second
confirmation dialog. The need remains person-only: clients must not automatically
resume it. Missing or true preserves the client's existing confirmation policy;
failure recovery, held operational pauses, retained attempts, report repairs and
terminal stops do not receive this exception. Numeric allowance changes still
need their explicit confirmation. Named commands and CLI admission checks are
unchanged.

The additive `view.chat` contains `messages` (stable `id`, `role`, `text`),
an opaque `context`, and an optional `input` offer. Messages project existing
human receipts and saved completed replies. At a follow-up, the prior reply is
retained under that turn's historical record; display text grants no approval
and does not become a new requirement. Status reads do not write a transcript.
The Requirements question the run waits on is a message whose `id` starts with
`requirements-question-` and names that question, so each new question is a new
message a client shows once (#1792).

During a running step, optional `progress` is an array of core-authored display
lines: the inspected current activity, saved task/requirement counts and up to
three recent finished attempts in this turn. An attempt finishing does not mean
its report was accepted or its checks passed. Unknown activity stays unconfirmed;
queued and stopped steps are not described as running. Clients render these lines
without deriving stage names or success from private state. Older cores may omit
the field.

While a supervised step works, optional `activity` is an array of at most 20
display events from the running attempt's own provider event file, oldest first:
`{kind, text, status}` with `kind` one of `command`, `edit` or `tool`, `status`
one of `running`, `done` or `failed`, and, for an `edit`, the `paths` it names
(relative to the project when they are inside it, at most 8). `text` is one
bounded line: a command's first line, the files changed, or the tool called.
Model prose, reasoning and tool output are never copied, and only the tail of a
file inside the run directory is read. `done` and `failed` describe the tool
call, not the work: a command that exited 0 is not a passed check, an edit is
not an accepted change, and nothing here is proof, approval or authority to
retry. Activity that is not confirmed as supervised, a finished or stopped step
and a paused run have none, and the field is then omitted. Codex-style items
(also what the built-in Claude provider writes) and OpenCode or Kilo tool parts
are read; other event shapes show nothing. Older cores may omit the field.

While a supervised step or parallel Builder works, optional `log` is the
terminal chat's live agent log (owner decision 2026-10-21): an array of display
entries `{id, agent, kind, text}` from each working attempt's own provider event
file, oldest first per attempt, at most 40 per attempt and 8 parallel Builders.
`agent` is the job's screen name, with the task for a Builder
(`Builder · task M2`); `id` stays the same across reads so a client can keep
entries beyond the newest and update a growing one in place. `kind` is
`thinking` (model reasoning), `message` (model prose), or one of `activity`'s
`command`, `edit` and `tool` with the same `status` and `paths`. Unlike
`activity`, reasoning and message prose are copied: collapsed to one line,
credential-shaped text redacted, and cut to the newest 400 characters. Tool
output is never copied. Each parallel Builder's attempt must itself be confirmed
as supervised, and only a file inside that attempt's run directory is read. The
entries are display only, never a report, proof, approval or authority to retry.
The field is omitted when nothing is working. Older cores may omit the field.

During spec-first Requirements chat, `phase: "requirements"` lets clients show
the conversation without the empty task dashboard. An assistant message may
have `streaming: true`; its text is provisional display content from the active
Requirements attempt, never an answer, approval, report or tool receipt. Kilo
text deltas are filtered to the current assistant message; reasoning and tool
output are excluded. Requirements reuses its own saved provider session while
independent plan reviewers retain separate sessions. Requirements answers
the first message of a spec-first run, whatever it says: a greeting or an
off-topic question gets a short answer and a question about what to build or
change. Until Requirements finds work to do, the person's newest reply becomes
the task; the source log keeps the first message and each reply as separate
sources. Requirements names the job it understood (#1846), as it does on every
run (#1860). A question goes straight to the Analyst without a confirmation; any
other job starts once the person approves what Requirements understood. The Job
recognizer runs, after that approval, only when Requirements named no job. Model access probes for
planning and execution roles wait until the user confirms the requirements.

When the only pending question is the core's current requirements review,
`review` offers its `question_id`, exact `answer`, `label` and bound `summary`.
A client may display that summary and submit the ordinary named answer action.
This confirms the understanding and starts the job Requirements named: plan
preparation and review for a build, the investigation for a bug fix, the review
or the design work. The `label` and `spend_confirmation` say which.
The resulting execution plan still needs its own displayed-token approval.
The optional `review.spend_confirmation` supplies the modal's `title`, `detail`
and confirm-button `label` before submitting that review's planning answer.
`match: "trim_lower"` mechanically matches the answer after whitespace trimming
and lowercasing, as the core does; corrections do not trigger the planning modal.
This spend confirmation never approves an execution plan.
The optional `notice` explains a stop or ongoing work in ordinary language;
chat clients keep diagnostic headings, empty counts and pause categories in
Run details. A notice never grants an action beyond the view's named command.

A build plan can explicitly request local delivery with
`git_delivery: {"kind": "atomic_commit", "message": "…"}`. Use null when no local
commit is requested; older reports may omit the field without granting delivery
authority. This field is part of
the sealed plan and is shown alongside its approval. A fresh passing runner
regression proof allows the controller to create the scoped commit before the
independent Tester inspects it. The committed revision receives fresh runner proof
and independent verification before completion. Plans without executable regression
cases retain the final completion boundary. Existing Git hooks are honored.
Stage workers still cannot commit. Push, merge and deployment are separate actions.

Delivery refuses preexisting dirty launch work, staged changes, changes outside
the approved affected paths, or a moved HEAD. A durable receipt prevents repeating
a successful commit after a lost response. Uncertain Git writes or source changes
in a hook stop for reconciliation. The initial supported route is the ordinary
build review workflow. Older plans promising an atomic commit only in prose require
a revised, explicitly approved structured delivery contract; prose cannot authorize
a Git write and cannot silently satisfy the completion gate.

`input` is null when free text is not an admitted action. Otherwise it names
`kind` (`feedback`, `follow_up`, or `resolver_response`), a display `label`, and `next_command` with
one literal `MESSAGE` placeholder. A client replaces that argument with the
person's text, checks that the command names the same workspace/run, and runs it
as an argument array without a shell. Compare against the view's `workspace` and
`run_dir` fields: after bootstrap relocation they name the run's effective
location, which can differ from the handle the client started with. The headless
conformance client's `message_command()` implements this same parsing boundary.

A client compares the displayed `context` with a fresh status before submitting;
if it changed, retain the draft and ask the person to review the new checkpoint.
The ordinary CLI independently rechecks admission when saving the action.
At a semantic Resolver or completion stop (`RESOLVER_PENDING` or
`PAUSED_COMPLETION_GATE` with `needs.kind=resume`), permitted corrective feedback
uses this same named offer. Terminal chat lets the person type the correction at
the Continue prompt. A held operational pause, blocker or human-review gate does
not gain a feedback bypass; the existing pause-authority policy still decides.
One blocker stop does gain it, as its own decision rather than a bypass: after a
blocker was answered with `provide_information` and the response holds the current
frontier at an approved contract, that hold exists to take the contract decision it
asked about, so the view offers the same `feedback` action and the CLI admits
`--feedback` (and, consistently, the `--edit-goal` path the stop reason names).
The correction invalidates the approval and starts ordinary re-planning toward a
fresh, unapproved draft and a fresh approval — fresh set approval too, for a linked
run. The informational reply alone still changes nothing (#2434).
An operational `resolver_response` offer binds `provide_information` to the current
Resolver request ID and token in `needs`. It supplies corrective information only;
retry grants, scope changes, model routing and resuming remain separate actions.
In spec-first chat runs, a message at plan approval is interpreted by the saved Requirements
route when the client continues. Questions receive an assistant reply while preserving the reviewed
plan and its token; requested or mixed corrections enter normal requirements/review again and need
fresh approval. Free text, including approval-like messages, never approves the plan. The existing
`--feedback` action and `feedback` input kind remain the client contract; no client intent parsing
is needed. Exchanges are stored as `plan_message` user-event receipts (ID, original text, contract
token, return stage, then intent and `reply_after`). They are not requirement sources unless a change
is forwarded once as ordinary brief feedback. Legacy nonchat feedback keeps its correction behavior.

Saving feedback or a follow-up launches no model. A button explicitly offering
to send **and continue** may then follow the ordinary `needs.kind=continue`
action. It may never infer a plan approval, answer another question or release
an operational pause. Approval remains bound to the displayed plan token.
Older cores without `chat` leave free-text input disabled for an existing run.

This projection is separate from the explicit Requirements journal below:
ordinary run clients do not create or drive a second conversation controller.

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

During joint planning, technical decisions with a proposed default are resolved internally and
sent through drafting and review again. They are retained as `user_events` receipts with
`actor=autocode` and `kind=technical_decision`, outside the human `answers` ledger and chat history.
They cannot authorize a protected contract change or approve a plan, and they never renew the
plan-review allowance: the redraft continues the planning cycle that asked, carrying its spent
review calls, its first review and its revision (`planning.inherited_review` in the saved state),
and with one review call left the final review decides on the redraft. Genuine questions about the
requested outcome, behavior, cost, permissions or side effects still require a human answer, as do
unresolved facts and rejected assumptions. A repeated identical technical question goes to report
repair instead of another planning loop. Non-joint runs keep their existing question path.

`handoff_conversation(expected_revision=..., expected_plan_token=...)` records the
reviewed current draft's handoff to that same run. It does not approve the plan or
start a second task. Read the current plan and use the separate `approve_plan()`
action only when the operator authorizes implementation. The original handoff
source digest stays immutable; later human feedback retains its own provenance.
Assistant text and unapproved drafts never become mandatory requirements merely
because they appear in the journal. The browser dashboard and macOS app still use their existing path.

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

A controller exit while applying a returned, accounted report also projects this
pause after fresh inspection confirms all supervised processes stopped with
successful cleanup and the regular report file remains inside the run directory.
`status()["controller"]["completed_stage"]` is `true` at that boundary. Resume
revalidates the retained report without abandoning it or replaying that provider
call. Usage, model routes, limits and plan-approval gates stay in force. An
unaccounted, interrupted or missing result keeps its existing recovery controls.

### Pausing a working run

While a run works (a live stage, a fresh birth-bound controller, or a controller
holding the run's locks), `status()["pause"]` names the boundary pause of
[queued interventions](interventions.md): `label`, `effect`, `requested` and
`next_command`, an argument array following `autocode` for
`intervention submit --kind pause --json`. Its `REQUEST_ID` placeholder is the
client's own id for this request, so a retried submission returns the original
receipt instead of queueing another. The current step finishes and is saved,
then the run pauses with its next stage selected; the ordinary
`--resume-paused` action continues it. While a pause request waits for that
boundary, `requested` is true and `next_command` is null. The field is null when
nothing is working, when the recorded runner cannot consume interventions, and
for a stopped run. It never interrupts a step in progress or stops the run.

### Durable local workers

Durable execution is an explicit new-run choice. `modes list --json` adds an
`execution` object with default `owned` and a `durable_local` availability row.
The latter carries the exact `next_command` argument-array envelope:

```json
["autocode", "worker", "launch", "--workspace", "WORKSPACE", "--run-dir", "RUN_DIR",
 "--checkpoint", "CHECKPOINT", "--"]
```

For a fresh run, fill its absolute selected workspace, fresh immediate run
destination, and `CHECKPOINT` with `-`; append the ordinary literal new-task
arguments. Continuing a durable run uses `view.execution.next_command`, whose
paths and checkpoint are already bound, followed by the existing named action
and its exact human tokens. No shell is involved. The wrapper owns workspace,
run-directory and noninteractive flags; do not repeat those inside the action.
The core checks the checkpoint again under the existing run lock before
configuration, reconciliation, a new controller epoch or provider dispatch.
An admitted human answer, feedback, approval or resume keeps that same controller
in the ordinary loop until the next human boundary; it does not depend on a
viewer taking another Continue action. Explicit unit stops and inspection
actions retain their boundaries. Attaching to an idle saved durable run reads
only: the TUI offers Enter to take its current named Continue action, after
rechecking that exact boundary. It does not silently dispatch on reconnect.

`worker launch` seals a private launch-intent receipt before calling the service
manager. The default request ID fingerprints the exact action, checkpoint and
private authorization-token fingerprints; an explicit `--request-id` may instead
identify the caller's request. Identical repeats reconcile that one receipt;
changed content conflicts. A dispatching receipt can never re-arm itself, even
if the service was collected or its response was lost. `worker inspect ID`
and `worker launches --workspace PATH [--run-dir PATH]` are read-only. The launch
reply's `inspect_command` names the inspection action. `ready` acknowledges a
matching committed run/controller binding; it does not assert worker liveness,
successful provider execution or task completion. Those remain in normal status.

The first adapter uses an already configured Linux systemd user service, `Type=exec`,
without a PTY or inherited input/output pipes, persistent private output, no
automatic restart and existing linger. Admission refuses an unavailable manager,
systemd before 254, missing linger or editable controller/launch storage inside
the task workspace. Only the manager's matching MainPID and InvocationID can
enter the exact receipt on the pinned runtime and host boot. The existing core
controller, task lock, checkout lock and provider supervision retain ownership.
Runtime changes, missing run folders and uncertain persistence refuse adoption;
reboot is interruption, not automatic replay. The launcher never enables a
service or changes host login policy.

`TaskRun.start_durable(workspace, run_dir, brief, ...)` returns `(run, receipt)`
without waiting for model completion. `run.inspect_launch(receipt)` reconciles
its named read and adopts effective bootstrap paths only after binding. Read
the normal status, retain the action and exact human decision tokens, and call
`run.dispatch_durable(named_command, view=inspected_view)` to launch one action.
It returns another receipt. The synchronous `start`/`advance` APIs retain their
ordinary owned-worker semantics; using them to write a durable run is refused.
The client driver reads no private task or launch file. Other clients may observe
a durable run; they need the named worker transport before writing it. An ordinary
root CLI write is refused rather than silently changing worker lifetime.

`view.observation` contains the selected effective paths, a token for canonical
committed checkpoint contents and the read's `sampled_at`; it is neither an
atomic snapshot of every artifact nor action authority. `view.execution` records
the saved mode, launch ID and controller epoch and, for durable mode, the named
transport. Service inspection is separate from the existing controller/liveness
and completion evidence. Legacy absence never implies detach support.

### Asynchronous viewer direction (#1879; implementation pending)

**Owner-approved direction, implementation pending (2026-10-23).** The owner
approved [PR #1985](https://github.com/charlieanna/autocode/pull/1985)'s lifecycle
direction in the Codex chat: "Approve the proposed lifecycle." The source
inventory below was checked against master `348670930`. Current runtime behavior
remains in force until the dependent implementation qualifies. The field examples
describe the agreed semantics; clients cannot assume they are exported yet.
This decision and read-only attachment do not approve an individual task plan or
authorize an additional model call.

Existing guarantees should be extended rather than replaced:

| Foundation | Existing evidence and limit |
| --- | --- |
| Single controller/writer and surviving provider exclusion | `autocode_controller_owner`, the run lock and `autocode_checkout_lock`; `tests/test_controller_owner.py` and `tests/test_checkout_lock.py`. A second viewer is not a second owner. These checks alone do not prove SSH survival. |
| Checked liveness and unknown inspection | `autocode_liveness` separates owner, keeper and provider; `autocode_wait_command` observes controller loss. Access failure is not proof of a dead worker. |
| Boundary pause | The current `pause.next_command` uses a client request ID; intervention tests cover identical retry and conflicting payload, including after application. This is not a blanket guarantee for other actions. |
| Core Requirements conversation | `send_conversation` and `refresh_conversation` bind message IDs/revisions, reconcile saved acknowledgements and hold uncertain advances; `tests/test_conversation_session_cli.py` and `tests/test_conversation_policy.py`. Ordinary/legacy runs need not have this journal. |
| Approval, recovery and restore | Current plan/review/request tokens, recovery token and exact comparison control their own actions; see Commands, Exact stopped-run recovery and Saved code checkpoints. There is no universal retry receipt across all families. |
| Responsive observation | Bounded polling and its cancellation tests already exist. TUI history/verification display is E1; no whole-view freshness or durable detach guarantee follows from it. |

#### Agreed lifetime and authority

The agreed first delivery is **opt-in durable local execution on persistent
Linux**, with a core-owned launcher and the ordinary task controller. The TUI
observes it through this interface. The launcher must establish independent
terminal/session lifetime, persistent output and a reconciliable launch identity
before returning a durable-ready receipt; a separate process group alone is
insufficient. A local user-service arrangement is the preferred first host
adapter, provided its existing configuration survives SSH/session loss. A host
without that demonstrated capability retains the current owned-worker mode.
Enabling a service or changing host login/linger policy is a separate operator
action, never a silent prerequisite installed by the TUI.

The launcher is part of the core runtime, not another task controller. It starts
the existing loop under the existing locks and birth-bound ownership. A service
name is not proof of ownership, and a lost launch receipt is reconciled by the
core before any retry. At a human gate the controller can finish with its ready
receipt; continuing later is another serialized invocation of the same run.
The exact launch action/capability schema and host adapter must be reviewed with
their deterministic qualification before this opt-in mode is exposed.

| Event in the agreed durable mode | Required outcome |
| --- | --- |
| Normal viewer quit, Ctrl-D, SIGHUP, viewer SIGKILL or SSH disconnect | Only the viewer leaves. It neither pauses nor signals the worker. Reattach is read-only and launches no provider. |
| First or repeated Ctrl-C | Request the currently named boundary pause; repeated keys do not escalate to an implicit worker kill. An unavailable pause offer is reported as such. |
| Explicit pause | Submit the existing request-ID-bound pause offer. Save the active step, then wait at the core's boundary. |
| Explicit stop or interrupt | Use a core-named action with its own confirmation and scope. No new immediate-kill control is included in the first delivery; missing controls remain unavailable. Stop, when offered, remains terminal for the conversation. |
| Worker/controller loss while a viewer survives | Show the current controller/liveness inspection and retained attempt. Use the existing reconciliation action only when offered; never infer a paid replay from a quiet log. |
| Lost ordinary supervisor | Fresh status may name private `--reconcile-only -` when owner, keeper and provider are positively dead. It classifies the retained unfinished attempt as uncertain under the run lock without provider setup, artifact adoption, settings changes or model calls. A changed checkpoint, live/unknown role or stop refuses, and status does not offer it while a stop is applied or still pending (the next relaunch applies that stop). Native visual workers and durable service actions remain outside this control. Archiving that uncertain attempt then follows as the core's own named `--abandon-stage` step; any retry is a separate named decision. |
| Host reboot | Work is interrupted, not transparently resumed. After reconnect, inspect ownership and attempts and expose the ordinary recovery boundary. Unknown provider outcomes stay uncertain. |
| Two viewers act on one gate | Both may inspect. The first current action admitted under the core's locks changes the run; a competing stale token is refused and refreshed. No client leader or second controller is introduced. |

The current owned-worker Ctrl-C sequence remains documented for legacy mode
until an explicit migration decision. It must not silently acquire durable-mode
semantics. Multiple viewers initially share the same authorized local OS
identity; this proposal adds no remote authentication service or Jira/RPC API.
Jira/Fargate orchestration remains a separate layer using `TaskRun`.

#### Observation, decision and attention requirements

Keep `controller`, `liveness`, `status`, `needs`, `delivery`, `verification` and
the named actions authoritative for their existing meanings. Add only the
missing capability and observation metadata, for example:

```json
{
  "execution": {
    "version": 1,
    "mode": "durable_local",
    "detach_supported": true,
    "launch_id": "opaque-core-launch-id",
    "controller_epoch": "opaque-owner-receipt-id"
  },
  "observation": {
    "version": 1,
    "workspace": "/project",
    "run_dir": "/project/.autocode/runs/run-a",
    "checkpoint_token": "opaque-committed-checkpoint-token",
    "sampled_at": "2026-10-24T00:00:00Z"
  }
}
```

`checkpoint_token` identifies committed checkpoint bytes; it is not a sequence
number or a token granting an action. `controller_epoch` distinguishes owner
incarnations, not task completion. `sampled_at` describes the read, not meaningful
progress or an atomic snapshot of every external evidence/log file. Existing
attempt/log identities keep their narrower scopes. Clients bind each local read
to its requested identity and request generation, discard cancelled/older local
results and never order checkpoints by a wall clock or a hash. A failed read
retains the last view as unavailable; it does not rewrite worker state.

Before applying a decision, obtain a current view and retain the exact plan,
question, review, recovery or comparison token the person inspected. A newer
view cannot silently answer or approve on their behalf. Each action family
needs its own conflict/lost-receipt contract:

| Action family | Required reconciliation rule |
| --- | --- |
| Read, attach and detach | No run writes, provider dispatch or approval. Detach is local viewer exit, not a new core task-state transition. |
| Pause/intervention | Reuse the existing request ID and exact payload; return its original receipt. A changed payload is a conflict. |
| Core conversation send/refresh | Reuse its existing request-ID/revision and acknowledgement rules. An unresolved advancing outcome holds rather than dispatching again. |
| Plan/review approval and answers | Validate the displayed token under the lock. Preserve existing per-family replay behavior; a missing command receipt requires fresh inspection, not a synthesized success. |
| Feedback, follow-up and plan messages | Verify their existing context/revision/request-ID coverage separately. Add missing conditional receipts in the core before claiming automatic reconnect retry for that family. Unsupported cases remain explicit handoffs. |
| Retry, recovery and restore | Keep the exact attempt/source/recovery/comparison binding and current cleanup evidence. A transport failure or reconnect grants no extra retry, model substitution or limit change. |

Persistent attention (#1885) is exposed in `view.attention` and each `runs --json`
row. Version 1 identifies a pending human boundary using the saved run incarnation
and existing decision context. A completed outcome uses the existing same-chat
turn receipt, so an identical follow-up's completion has a different ID. Missing
saved identity means unknown; it is never invented from text or source equality.
The item contains its workspace/run, kind, fixed label and bounded, redacted task
snippet. It carries no answer or approval authority.

`attention.acknowledge.next_command` is an exact argument array for
`autocode attention ack --workspace W --run-dir R --item-id ID`.
`TaskRun.acknowledge_attention(view)` submits that offer without advancing the run.
An acknowledgement never changes the task checkpoint, current question, plan
token, delivery acceptance, provider attempts or budgets. New acknowledgements
require the item still be current. Repeating an acknowledged ID returns its
original receipt even after the task resolves; a different run cannot inherit it.
A lost reply is uncertain until that same ID is inspected or explicitly retried.
This reconciliation applies to acknowledgement only, not other action families.

The authorized local OS identity shares receipts across viewers. Storage defaults
to `$XDG_STATE_HOME/autocode/attention` (otherwise
`~/.local/state/autocode/attention`); `AUTOCODE_ATTENTION_HOME` selects an isolated
profile. It must be a private owned directory outside the editable task workspace;
receipts are private atomic files bound to the local UID and run paths. Reads
create nothing. Unavailable, corrupt, unowned, symlinked or unsupported storage
projects unknown and offers no acknowledgement action. There is no remote
identity service, notification service or workspace-owned acknowledgement file.

The TUI's F8 or `/attention` drawer restores current saved alerts, including after
a viewer restart. Cold-start inventory is silent; a newly observed unread ID is
announced once in that viewer's current bounded cohort. An acknowledged item
stays inspectable while current but does not announce again. The selected run
uses its own prompt and the global unread badge. Background runs and the prior
run while composing a local new-chat draft receive the notice. Independently
running viewers may each announce a newly observed item; delivery is not a
universal exactly-once alarm guarantee.
Resolved/superseded items disappear from current inventory; this is not an alert
history journal. Unread storage is durable, while delivery of a notice is an
observation. Completion says to inspect evidence, not that current delivery passed.

Inventory uses `runs --all --json` in the existing two-child bounded read pool,
normally every ten seconds. Per-run status reads take precedence over an inventory
started before them. The UI retains at most 256 rows and reports overflow;
an empty `runs --json` inventory returns success with `runs: []` rather than prose.
`/runs` remains available for the full public list. Failed discovery retains a
labelled last snapshot and shows unavailability. Drawer rows stay frozen until
explicit refresh so incoming items cannot change which ID a queued key targets.
Acknowledgements are asynchronous with a five-second viewer timeout; a slow
receipt cannot block draft editing. Closing the drawer restores the draft/cursor
and viewport without switching runs. Opening an item reads current status;
submitting an answer or approval still uses the exact token the person inspected.

The terminal's `/runs` also uses that bounded pool for its project and global
listings. The command returns immediately while a refresh is outstanding; its
completion notice invites `/runs` to inspect the new snapshot. Displayed numbers
appear automatically for the first requested list, then
stay fixed until that explicit inspection, so a later read cannot retarget an
`/open N` being typed. Listing a chat does not open a tab or subscribe to its
individual status; only chats explicitly opened in this viewer join that set.
An uncached `/open N` finishes its viewer navigation after fresh listings arrive.
A newer navigation or a changed source chat/question cancels that pending open;
a failed listing cannot complete it.

#### Acceptance and rollout before implementation is enabled

| Design case | Required deterministic and supported-host proof |
| --- | --- |
| A1: one worker, two viewers | Both observe; competing starts/continues and token decisions admit one writer. Receipts reconcile the exact launch/action without a second provider call. |
| A2: viewer, worker and host loss | Kill each separately with an in-flight fake provider. Real PTY/SSH loss preserves a healthy durable worker; worker loss projects recovery; reboot never claims continued execution. Uninspectable ownership remains unknown. |
| A3: stale/out-of-order data | Hold an old read across a newer decision, refresh, run switch and controller epoch. Release it; it cannot replace the current view or authorize an action. Stale human tokens are rejected by the core. |
| A4: old/new pairs | Omit every proposed field independently. Legacy clients ignore additive fields; new clients show unknown and retain legacy lifetime semantics without advertising detach. A source/controller version change cannot rewrite a held provider attempt. |
| A5: accepted request, lost response | Lose launch, action and provider receipts at each persistence boundary. Inspect the same launch/request/attempt; no blind provider replay. Identical supported retries return their existing receipt, conflicts refuse, unsupported uncertainty holds. |

Use fake providers and controlled clocks for authority/race cases, then actual
terminal and SSH/service tests for host lifetime. No paid model is needed.
Measure keyboard-command response and healthy-read completion while another read
hangs; propose a controlled-host input target of 250 ms, report actual samples
and load, and derive fairness from the configured subscription/read bounds rather
than promising a fixed 25-run freshness number. No spinner, poll or notification
acknowledgement counts as work progress.

Implementation order after owner agreement: #1881 owns durable launch/reattach
and host capability; #1882 D2 owns active switching and per-run draft/cursor/view
context; #1883 E2 consumes observation/identity metadata; #1885 owns durable
attention and conditional decisions; #1884 F2 consumes separately approved stage
policy; #1886 owns the integrated fault/terminal/SSH matrix. Existing E1/F1 and
D1 results remain regression evidence, not proof of these new outcomes.

The consolidated owner decision above covers these lifetime/authority, metadata,
first-host and response-target choices. Dependent issues should not ask the same
policy questions again. Exact additive
schema/actions, compatibility fixtures, host adapter and measured A1–A5 results
are readiness evidence before enabling the opt-in capability, not a second owner
approval gate unless the agreed semantics change. Keep current defaults and all
saved routes, efforts, usage, limits, histories and approvals unchanged. Cross-host
delivery, host administration and stage-selection policy remain separate decisions.

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
attempt durations, reported usage, regression proof and remaining gaps. The report of a build,
bug-fix or design run (the run's `workflow`, or none for a run saved before workflow
recognition) also has `approvals`: the status view's `approvals` rows when the report was
written (who approved each plan and review, accepted completion, edited the plan or resumed a
pause, with its recorded provenance, never verified), shown in the Markdown as an Approvals
table after the intended outcome, or as `None recorded` for a run with none
([Who approved](execution.md#who-approved)). Review and discuss reports, which have no approval
step, have no `approvals` field and no such section, even when the status view lists a resume.
Neither do verification reports, nor reports written before AutoCode listed approvals, so
older pairs still authenticate. A components report lists its child runs' approvals
(below). An older build, bug-fix or design report
gains the section when AutoCode saves the completed run again; reading its status does not.
A task report whose approved plan is in the status view also has `plan.goal_hash` and
`plan.plan_file`, copied from `approved_contract` (#720); the Markdown says them after the
agreed plan's token. Reports written before AutoCode cited them have neither field and still
authenticate; a completed run saved again rebuilds its pair with them.
When the proof's mutation check left survivors, `regression_proof.mutation_survivors` lists
them per test case, keyed like `case_tests`, and the Markdown names each one under
the regression proof. For current task proofs, `verification.changed_code_evidence`
binds execution, mutation observations and independently checked per-line reasons
to the exact source/proof context. `verification.changed_code_repairs` retains
the bounded Builder returns and Investigator handoffs. Legacy report-only proofs
without this field grant no new changed-line evidence. Review-only mutation reports
are kept separately in `workflow_result.mutation_check`, with survivors keyed
by the reviewed tests' actual IDs. Local
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

Completed component builds publish it beside `.autocode-components/manifest.json`;
the component CLI summary and `MultiComponentBuild.evidence_report()` expose it.
Aggregate documents reference authenticated child `TaskRun.evidence_report()`
documents and retain simulated runtime/deployment scope and unknown totals.
Their existing inheritance and integration gates authorize accepted historical
child deliveries; the aggregate reader does not claim fresh child verification.
Aggregate completion timestamps and elapsed wall time remain unrecorded when the
coordinator has no owned timing record. A child's completion time does not stand
for later integration checks or runtime smoke.
An aggregate also lists, under `approvals`, the approvals each child's own report lists
(#722): child by child, in its `children` order, each row as that report has it plus `child`,
the child's id. Its Markdown shows them as an Approvals table with a Child column, and says
what it leaves out: a components run does not list approvals of the architecture it was built
from. That run's own report lists them. A program report an older checkout wrote (before
`autocode program` moved to the `archive/program` branch) also says that its agreement records
no approver and that it does not list the approvals of the plan run it was derived from. A child whose
report lists no approvals, because it was written before AutoCode listed them, is named in the
aggregate's not-verified list rather than taken to have recorded none. When no child's report
lists approvals, the aggregate has no `approvals` and no such section, as before.
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
| Export the plan as a plan file (`TaskRun.export_plan(path)`) | `autocode plan --export PATH`: the plan shown for approval, or the approved one, as the file a team reviews in a pull request ([Workflow](workflow.md#reviewing-a-plan-in-a-pull-request)); changes nothing in the run | 0 written, 2 refused (no plan yet, or the file cannot be written) |
| Continue | `autocode --no-chat [options]` | 0 complete, 2 stopped for input |
| Resume a pause | `autocode resume --no-chat [options]`; after editing the design at `PAUSED_DESIGN_CONFLICT`, `autocode --resume-paused --no-chat [options]` | 0 complete, 2 stopped for input |
| Grant N recoveries after resolving the cause | `autocode --resume-paused --grant-recovery N --no-chat [options]` | 0 complete, 2 stopped for input |
| Accept a changed OpenCode transport | `autocode --resume-paused --accept-transport-change --no-chat [options]` | 0 complete, 2 stopped for input |
| Adopt an inspected interrupted Investigator report | `autocode --recover-job-report TOKEN [options]` | 0 applied, 2 rejected; no model launched |
| Answer | `autocode --answer QUESTION_ID=TEXT --resolver-token TOKEN` (`--answer` repeatable); a question with `options` takes only one of its option labels or its `proposed_default`, exactly | 0 saved, 2 rejected |
| Respond to an operational Resolver request | `autocode --resolver-request ID --resolver-token TOKEN --resolver-response provide_information --resolver-message TEXT`; the next resume re-evaluates it once ([below](#operational-information)) | 0 saved, 2 rejected |
| Name the model a role stopped on quota, a content-filter refusal or an authenticated output limit continues on | `autocode --answer route-ROLE=MODEL --resolver-token TOKEN`, then resume the pause | 0 saved, 2 rejected |
| Name the model a stopped workflow job continues on (`retry_job` with `route`) | `autocode --answer route-ROLE=MODEL --job-retry-token TOKEN`, then retry the job with the new token | 0 saved, 2 rejected |
| Choose an effort the model declares for a role whose named effort it does not (`PAUSED_EFFORT_UNSUPPORTED`, `TaskRun.assign_effort(role, effort)`) | `autocode --answer effort-ROLE=EFFORT`, then resume the pause | 0 saved, 2 rejected |
| Approve the plan | `autocode --approve-goal TOKEN` | 0 saved, 2 rejected |
| Approve a review | `autocode --approve-review CRITERION --review-token TOKEN` | 0 saved, 2 rejected |
| Reject a review (`TaskRun.reject_review(criterion, token, note)`) | `autocode --reject-review CRITERION --review-token TOKEN --review-note TEXT` | 0 saved as feedback for revision, 2 rejected |
| Plan feedback | `autocode --feedback TEXT` | 0 saved, 2 rejected |
| Follow up a finished run | `autocode --follow-up TEXT` | 0 saved, 2 rejected |

Answers and approvals never launch a model; continue afterwards. A follow-up
is the next request in the same conversation: on a finished run it records a
new turn (`turn` in the status view) and reopens the run at Requirements,
which names the kind of job again from the new message, with the earlier turn
as context (its job, a review's verdict and finding counts, a design turn's
documents), and continues in the same run directory (#1860). After a review, a
follow-up that asks to act on the findings is a build planned from them: the
review's blocking findings are the task list, so no requirements questions are
asked, and the plan still goes to the user for approval. The rewritten task names what the previous turn wrote (its report or
note, then the files its stages changed; at most eight paths). After a design
turn, a follow-up that asks to build the design names that document, so the
build goes from Requirements to checking it against the repository
(`check_design`). The build is planned afresh from the design: the design
turn's contract and requirements move to the run's history rather than being
revised, and the new plan still needs approval. After a design review, a
reply to it (an answer, a correction) is named design and the Architect
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
At a cross-model independence stop, `TaskRun.answer()` and `assign_model()`
use the current status view's route question without a resolver token: the core
derives that decision from saved routes and checks the replacement again. Other
resolver questions still require their current request token.

`TaskRun.assign_model(role, model)` answers `route-ROLE` with a model a person
named, updates every existing `--ROLE-model MODEL` or `--ROLE-model=MODEL`
occurrence in the client's `options`, and leaves the run paused for
`resume_paused()`. A refused model raises and leaves the run paused with the
question open. At a stopped workflow job (`retry_job` with `route`, see
[Failed workflow jobs](#failed-workflow-jobs)) the same call answers with the job's
`job_retry_token` instead of a resolver token and leaves the run paused for
`retry_job()` with the new token.
That stopped job's view also names the answer as a command, `needs.route.command`, in the same
shell-quoted form as `next_command` and carrying the job's token, beside the retry `next_command`
names; a client that shows the retry as a yes/no takes a typed model through it (#2005).
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
also prints the run's cost line there (`Cost: ...`, from the view's `cost`), then, when the run has
escalated, one `Escalations: ...` line and a line per escalation (from the view's `escalations`);
the status JSON on stdout and the returned TaskRun view keep their existing shape.
`TaskRun.accept_transport_change()` uses the explicit transport-change command
after a person inspects the new route and the saved run reports
`PAUSED_TRANSPORT_CHANGED`.
At an eligible OpenCode/Kilo pause with no retained attempt or report repair,
`needs.action` and `next_command` name `--resume-paused --accept-transport-change`;
`needs.recovery_hint` explains that confirming accepts the changed connection
settings after model/billing checks. It requires a person's confirmation and does
not approve a plan or replace selected role models. Separately selected per-role
tools retain their immutable identities and receive no acceptance offer. Other
engines, uncertain attempts and unrelated pauses keep their existing recovery;
clients must use the current named action rather than add this flag themselves.
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

Each card action that runs a control names its exact flags in `args`, for
example `["--resume-paused", "--retry-builder", "M3"]` or
`["--abandon-stage", "004/builder-02"]`. A repair paused on an edited source
offers `accept_source_edit` with `["--resume-paused", "--accept-source-edit"]`,
the same command `needs.action` names. At a recovery novelty hold, where `needs`
is the AutoResolver request with a `retry_action`, the card also offers
`retry_failed_stage` with `["--resume-paused", "--retry-failed-stage"]`: the
one explicit retry of the held step. Before that request is published
(`RESOLVER_PENDING`), a plain resume would only hold again, so `needs` is a
`resume` whose `action` and `retry_action` both name that retry, and the card
offers the same `retry_failed_stage` (#1770). When the hold cannot take it yet, the
card offers no retry and `needs.retry_unavailable` names what must happen first.
The view and `--retry-failed-stage` apply one shared admission rule
(`autocode_recovery_limits.scoped_retry_refusal`), so the view never offers a
retry the CLI refuses on saved state. A client runs
`autocode --run-dir RUN --expected-recovery-token TOKEN ARGS...` and never
derives flags from the action's `kind`, so a new action needs no client change.
Inspection, feedback, decision and conversation actions have no `args`: they
open a view or ask the person for input instead of running a command.

An operator changing the Resolver's model at its no-progress hold supplies
`--resolver-model MODEL` (and optionally `--resolver-reasoning-effort EFFORT`)
together with `--resume-paused --retry-failed-stage`. This records the exact
Resolver route change alongside one invocation-local retry. The original incident,
usage and spent allowances remain intact; changing another role, engine, source,
contract or scope is refused. A model change alone cannot authorize a call, and a
rejected combined change saves no settings. Clients continue to use only named
view actions; this operator control does not add a client-side route override.

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
meaning must change. [schemas/status-view.schema.json](schemas/status-view.schema.json)
describes the status payload `--status` and `--json` print, with this view under `view`:
every field it requires is always present, and a consumer must ignore fields it
does not know.

`action()` is the same composition of `needs`, `pause_category` and `next_command`
that `view()` returns those three fields from, without the evidence, usage and
completion accounting around them. A caller that only needs the next action takes
it from here; a caller that needs any other field takes `view()`. Nothing else may
derive a recovery command from saved state.

`autocode events` is this contract's streaming form: one JSON line per event the
run recorded, and `--follow` ends the stream where `action()` says the run ended —
exit 0 on a completed run, exit 2 with `next_command` named on stderr for a run
that needs a person. The envelope is
[schemas/run-event.schema.json](schemas/run-event.schema.json).

`autocode wait` is the contract's blocking form: it ends where `action()` says the
run reached a person, or where `COMPLETE` says it finished — and, because a saved
status is only a claim, it also ends when a fresh controller inspection finds
nothing owning the saved `RUNNING` checkpoint. Every other read in this family
surfaces that gap only after the fact.

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

`needs.command_inputs` describes the one number a pause leaves to a person, so a
client can ask for it instead of handing the command off (#2044). Every bound a
budget pause names is described, with the same row shape:

| Pause holding the run | `id` | `flag` | `label` | `used` reports |
| --- | --- | --- | --- | --- |
| `PAUSED_TIME_LIMIT` | `max_seconds` | `--max-seconds` | Total active time (seconds) | `elapsed`: active seconds |
| `PAUSED_COST_LIMIT` | `max_cost_usd` | `--max-cost-usd` | Cost cap (whole USD) | known spend in USD |
| `PAUSED_ITERATION_LIMIT` | `max_iterations` | `--max-iterations` | Total iterations | the current iteration |
| `PAUSED_MILESTONE_TIME_LIMIT`, `PAUSED_MILESTONE_BUDGET` | `max_milestone_seconds` | `--max-milestone-seconds` | Milestone active time (seconds) | the milestone's counted seconds |
| `PAUSED_MILESTONE_STALLED` | `max_milestone_replans` | `--max-milestone-replans` | Milestone replans | replans used |
| `PAUSED_NO_PROGRESS` (its unchanged-batch bound only) | `no_progress_limit` | `--no-progress-limit` | Unchanged implementation batches allowed | unchanged batches counted |

For each, `minimum` is one more than the larger of `current` (the saved bound;
the cost cap may be fractional) and the count, `maximum` is the largest safe
integer, and the command is `--resume-paused FLAG N --expected-recovery-token -`
with the current `recovery.token` in `AUTOCODE_EXPECTED_RECOVERY_TOKEN`. The row
is offered wherever that bound holds the run: at its own pause, after
AutoResolver published its operational request for it, and once corrective
information has been reviewed and held (not while that review is pending). It is
not offered while the run is active, while an attempt or report repair is
unreconciled, or where the need names another control (a retry, an edit, a
model question, a new run).

Where `next_command` is that raise, the row binds to it. Where it is not, because
the need is a plain `--resume-paused` or AutoResolver's operational response, the
row also carries `command`: the raise itself, a complete command with the run's
paths and the private token channel. A client fills the row's `arg_index` in
that command and takes it, never in `next_command` or in a reply. The terminal
chat offers it at the pause's yes/no and at the operational reply. The VS Code
run details and sidebar chat show it as its own number and **Raise limit and
continue** button beside the plain resume or the recovery reply, confirmed with
the bound it changes. The browser dashboard's recovery card shows the one row
`needs.command_inputs` describes, attached or separate, as a number entry once
the saved pause is inspected; its backend re-reads the status, checks the
recovery token the card showed and types the number only into the command the
view names for it. The headless conformance client, the VS Code client and the
dashboard validate the row the same way
(`tools/autocode_client_actions.check_input`).

For the finite total-time recovery, for example:

```json
{"id":"max_seconds","kind":"integer","flag":"--max-seconds","placeholder":"N",
 "arg_index":2,"label":"Total active time (seconds)","current":2700,"elapsed":2800.5,
 "minimum":2801,"maximum":9007199254740991}
```

The field is an array containing this one descriptor, and is offered only when
the named action requires `--resume-paused --max-seconds N`. `arg_index` counts
the arguments after removing the executable and owned `--run-dir`/`--workspace`
pairs. The complete remaining command must be `--resume-paused --max-seconds N
--expected-recovery-token -`, with the current `recovery.token` in
`AUTOCODE_EXPECTED_RECOVERY_TOKEN`. A client validates the exact command,
descriptor and token binding before substituting that one argument. A person
must enter a whole number between `minimum` and `maximum`; there is no default
and no extraction of numbers from chat. The minimum exceeds both the saved cap
and consumed active time. An empty, zero, fractional, unsafe or out-of-range
value grants nothing. An `N` with no row is a client gap.

This choice changes only the total active-time cap. It neither changes other
limits or model routes nor approves a plan. Recovery freshness is rechecked
under the run lock, including the saved active-time accounting. Ordinary CLI
callers retain the existing `--max-seconds 0` behavior; this finite input does
not expose disabling the guard. Corrective information still authorizes no
allowance: its pending deterministic review must first reach the held decision.

At a reconciled `PAUSED_PLANNING_BUDGET` checkpoint, the same array can instead
describe the existing finite planning-review allowance:

```json
{"id":"planning_review_call_limit","kind":"integer","flag":"--planning-review-call-limit",
 "placeholder":"N","arg_index":2,"label":"Total plan-review calls","current":2,"used":2,
 "minimum":3,"maximum":9007199254740991}
```

Only two exact remaining command shapes are supported, each followed by
`--expected-recovery-token -` with the current token in the same private channel:
`--resume-paused --planning-review-call-limit N` (index 2, explicit resume requested) and
`--planning-review-call-limit N` (index 1, save only). The latter is the held
operational-information action; a client must not insert Resume. Its next fresh
status determines whether a separate continuation is available. Both inputs say
"Set review allowance": even the explicit-resume form can save first and leave
a separately named Resume. `current` is
the saved finite allowance and `used` is the charged review-call count. Both
are integers; the minimum exceeds both, and there is no default or unlimited
choice. Planning accounting participates in the recovery token's freshness.

The person confirms the exact total number of calls. This uses the existing
allowance setter and preserves charged calls, recovery history, other limits,
routes and plan approval. It does not reset a cycle, change allowance policy or
approve the plan. Ordinary CLI callers retain their existing explicit zero
semantics.

An exhausted `PAUSED_TIMEOUT_RECOVERY` can name one additional finite choice:

```json
{"id":"grant_recovery","kind":"integer","flag":"--grant-recovery","placeholder":"N",
 "arg_index":2,"label":"Additional recoveries","current":3,"used":3,"minimum":1,"maximum":3}
```

Its only remaining command is `--resume-paused --grant-recovery N
--expected-recovery-token -`, using the same current private token channel.
`current` is the existing fixed automatic-recovery allowance; `used` is counted
spend, including the existing legacy fallback. The finite input accepts 1 through
`used`, validated as safe integers, and never supplies a default. Pending
corrective information must first reach its deterministic held decision.

This takes the existing grant action: choosing 1 replenishes one spent slot
(for example, 3 counted becomes 2). It does not add to the fixed allowance or
bank an excess grant. The existing action clears the consecutive-timeout streak,
retains historical attempts and appends its ordinary grant receipt. Accounting,
legacy fallback inputs and prior grant receipts are bound into recovery freshness.
Routes, global time/spend/stage limits and plan approval remain unchanged.

`evidence` is what the run agreed to deliver and what supports it, for reports
made outside the runner (such as a pull request body, docs/issues.md):
the approved contract's intended outcome, the base commit, one row per
acceptance criterion with its latest recorded outcome and evidence, the
findings ledger, and, for bug fixes, the runner's own fail-before/pass-after
regression proof (`verdict`, `fail_to_pass`, `failures`, `unverified`,
`commands`, `source_revision`, and `case_tests`: each English test case's
proving tests; `null` otherwise). Optional
`follow_up_baseline`, when present on the proof, identifies the immutable completed-turn
source freshly checked for new follow-up guards. Inherited regressions still use the
original run reference; this field does not reuse a previous proof or approve a plan.
Each acceptance row also carries
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
Reviewer) or `runner` (a failed runner lifecycle observation or, in a
`--package` run, the files changed outside the package, one blocking row naming
each path ([Monorepo packages](workflow.md#monorepo-packages))). No model
report closes a `runner` finding: the runner closes it when a later check of its
own passes, and a person can close one with `--close-finding`. Closing the
package finding accepts nothing: the next check before review opens a new one,
and `--accept-completion` refuses while the change is there.
`unverified_risk_claims` lists the person's own sentences that
promise durability or concurrency but that no runner protocol exercises (`kind`
`durability` or `concurrency`, `source_id`, `quote`, `reason`); `[]` when there
are none. It is a disclosure, not a check: a PASS never verifies them.
For a completed code-review workflow, `workflow_result.outcome` and its artifact
hash name the runner-derived verdict. Its optional `review_attribution` object
contains `pre_existing` and `inconclusive` counts, `submitted_verdict` and
`verdict_adjustment`. The summary distinguishes nonblocking pre-existing
observations from blockers with inconclusive attribution. The findings artifact
preserves each executable blocker's `disposition` (`kind`, `reason`, `runner`,
`inputs`, `base_proof`, `changed_proof`); proof references carry `path` and
`sha256`. Only a validated runner disposition of `pre_existing` exempts a
blocking finding. Identical failures only support that exemption when the cited
file and line range are verified unchanged in the actual applied scratch source;
changed, unknown and out-of-range locations retain the blocker. Legacy findings
and reviews without an explicit patch retain blocking behavior. This attribution
applies to review findings, not the task's acceptance criteria. See [patch attribution](bugs/2026-10-19-review-patch-attribution.md).

`proof_stability` is the proving tests' repeats: `repeats`, `unstable` (each
test, which side, its kind and the run outcomes), `unrepeated`, `next_step` when
a test disagreed, and `note`; `null` when the proof did not repeat tests.
`diff_coverage` is the same proof's line coverage of changed product hunks
(`collected`, `tool`, `note`, `files`, `uncovered`, `unmeasured`); `null` when the
proof did not record it. The raw coverage report alone grants no completion
authority. Current task proofs combine authenticated producing receipts with
mutation evidence in `verification.changed_code_evidence` (#2474).
`mutation_check` is the same proof's mutation check (#699, #2474, see
[Named test proof](named-test-proof.md#mutation-check)): `collected`, `note`,
`limit_per_file`, `budget_seconds`, `files` (each `path`, `notes`, and `mutants`
with `line`, `operator`, `before`, `after`, `outcome` `killed`, `survived`,
`timeout` or `not_run`, and `survived_tests`) and, once collected, `survivors`,
keyed by the same English test-case ids as `regression_proof.case_tests` (a
diagnosis's `T1` for a bug fix, the acceptance criterion's id for a planned build): each
case's proving tests that still passed with a product line changed (`test`,
`path`, `line`, `operator`, `before`, `after`, `text`), `{}` when every mutant
that ran was killed. Every proof that did not pass, including one refused before
any test ran, has the note `not run: the proof did not pass` and no survivors;
`null` when an older passing proof had no due test case, or there is no proof.
Current task proofs enumerate eligible Python sites within the existing command
budget and require observed named-test failures; setup, collection, timeout and
unrun outcomes never demonstrate a kill. `verification.changed_code_evidence`
lists exact gap IDs, file hashes/lines, `tested`, `reason_required` or `excepted`,
the reason/evidence, and the chosen fallback. Missing tools never trigger a
project install: demonstrated per-line mutation evidence is the deterministic
fallback, otherwise an explicit independent per-line judgment is required.
Two direct repairs precede the existing Investigator path within saved run
limits; no person question is added for these gaps. A review can instead report the original tests in its
explicit patch: `subject` is `reviewed_patch_tests`, `patch_path` and
`patch_sha256` identify that patch, `selected_files` and `selected_tests` record
the independently run pool, and `survivors` is keyed by actual test IDs. These
are separate from reviewer-delivered finding tests, which must still fail on
the reviewed code. Not-run notes never mean that all possible mutants were
killed. The same report appears in `workflow_result.mutation_check` and the
canonical document's **Review test mutation check** section; no named proof is
manufactured for a review.
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

`cost.breakdown` groups that same finished-stage ledger in three alternative ways:
`by_stage`, `by_process_outcome` (`completed`, `failed`, `interrupted`, `unknown`) and
`by_billing` (`subscription`, `pay per token`, `unknown`: how the stage's model bills, from the
billing discovery saved for an automatically selected run, else the model ID's account; an OpenAI
model's login is not recorded, so it is `unknown`). A subscription stage's `estimated_usd` is the
API-equivalent figure, not the plan's bill.
Each row has `stages`, `spent_usd`, `reported_usd`, `estimated_usd`, `unpriced_stages`,
`partial_stages`, `failed_stages`, `interrupted_stages` and `complete`; stage rows
also name the `stage` and routing-aware `job`. The groupings overlap and must
not be added together. `cost.breakdown.unpriced` says why the finished unpriced stages have no
cost (#1713): `usage_missing` (no input or output token count recorded), `model_unknown` (counts
but no model identified) and `rate_unavailable` (counts, but the price table has no rate for the
model), with those models in `models_without_rate`. They are counts of stages, never an amount.
`cost.breakdown.price_bases` lists the dated rates the estimated stages were priced at, one entry
per model and rate: `model`, `input`, `output`, `cached_input` where published, `source`,
`retrieved`, `until` (the UTC date the rate stopped applying; null for the current rate) and
`stages`. Each stage is priced at the rate in force when it finished.
Each grouping plus `inherited_usd` reconciles with the known
run total, subject to six-decimal rounding. Inherited spend has no stage attribution.
Active attempts and unaccounted workers are excluded, just as in the money-cap
ledger; the overall `cost.complete` remains false while they run. Unknown cost is
not zero. A completed process may return a rejecting review, and another attempt
may be required work: these labels do not claim acceptance or wasted spend.
The cost line calls out repeated stages and failed/interrupted attempts. For
observed repetition reasons, consult the existing `efficiency.repeats` projection.

`escalation_outcomes` counts the run's escalations and Builder retries by the outcome the runner
stamped on them (`passed`, `paused`, `open`; `tools/autocode_route_ladder.py`). The runner stamps
every one still open `passed` when the run completes on work a validation passed (including when
completion is accepted via `--accept-completion`), or when the Completion Reviewer or Resolver assigns
the next task while the last validation passed, and `paused` at the Builder's retry-limit pause. At that
assignment it leaves the Builder's own (its climbs, its retries and its stronger attempt) open when the
work was sent back after that validation's attempt started (a retry-policy charge or a correction task
anywhere in the run; `escalations` takes the same two as a failure after a PASS of the escalation's own
milestone), or when that attempt's start is not in the run's records, so a send-back cannot be ruled out.
A stamp is the struggle's: every entry still open when the struggle ends takes its end, so a climb a
later climb replaced reads `passed` there and `failed_again` as a row, and before the struggle ends a
stamp is `open` while its row may read `passed`. Read a row's `outcome` for how one escalation ended.
`escalations` lists each escalation the run made, oldest first (`tools/autocode_escalation_view.py`, #612):
a role's climb on its effort ladder (`kind` `effort_climb`) and the Builder's stronger attempt
(`stronger_attempt`; see [Models](models.md#builder-retry-policy)). Each row has the route `role` that moved,
the `stage` that struggled and its `job` as the screen names it (from the role when the stage is not
known; null, never a code name, when neither names a job), `at`, `trigger` (`validation_rework`,
`rejected_output`, `no_progress` or `abandoned_attempt` for a climb, `execution_failure` for the stronger
attempt), `detail`, `struggle_id`, `task_id` (for the stronger attempt, the task of the failed attempt its
decision names), the route it climbed `from` and `to` (`model`, `reasoning_effort`, `profile`),
`deferred_by_parallel_builder` (a parallel Builder left the stronger attempt to this run, which made it
serially), `inherited`, `attempts`, `cost` and `outcome`:

- `attempts` are the role's stage records launched on the route it climbed to, from the escalation
  until the first of: a validation of its work that passed (that attempt included), unless one of its
  attempts was then charged as a failure (see `outcome`), or the role's next escalation of either kind
  (its next climb, the Builder's stronger attempt). The running one is included (`active`); dry runs
  and the runner's own stages are not attempts. A climb lasts for the rest of the run, so a later
  milestone's attempts on the climbed rung count until one of those ends it. A stronger attempt's
  attempts are this run's own Builder records: a parallel Builder's records the parent holds
  (`worker_milestone`) are its worker's, whose own stronger attempt is recorded only in its worker
  run. A validation judged an escalation's work when one of its attempts produced it, or, for the
  Builder, whose work another role validates, when the Builder's last attempt before it is one of its
  attempts. No other role is judged by another role's validation: a Tester PASS of the Builder's work
  is not the work of a Completion Reviewer's or Resolver's climb. Nor is a validation of another
  milestone's work, or of work on a route the role was moved to. The runner assigns every task, a
  rework included, a new id, so an attempt's milestone is its task's contract hash and milestone ids
  (`current_task`, `task_archive`): an attempt whose task is of another milestone than the attempt the
  escalation was made from (a later milestone, or a replanned contract's) is not its work, and for the
  Builder neither is a validation whose own task is: a replanned contract's first task may be a
  validate task, whose Tester runs before any Builder attempt. When either task is not recorded, they
  are not told apart. The Builder's retry policy's charge names the record of the attempt that failed:
  the Builder's own, or the Tester's or Completion Reviewer's that sent its work back, whose work is
  the Builder's last attempt for that record's task before the decision; when no Builder attempt of
  that task started before it (a validate task: unchanged work sent back to the Tester; a record that
  names no task), the Builder's last attempt of that task's milestone (of any, when it names none)
  before that record. A review that sends the work back also assigns a correction task (an implement
  task whose `decision` is `REWORK`), which charges the Builder's last attempt of that task's
  milestone before it was assigned: with the retry policy off (`settings.workflow` set, or
  `builder_retry` disabled), nothing else records that a review sent passed work back.
- `cost` prices those attempts as `cost` prices a stage: `usd` (reported plus estimated),
  `reported_usd`, `estimated_usd`, `unpriced_attempts`, `unrouted_attempts` and `complete`. An attempt
  with no usage record is unpriced, never zero: `usd` is then a floor (`complete` false), or null when
  no attempt is priced. A stage record saved without a launch route (before 2026-10-01) cannot be
  shown to have run on the route it climbed to: the role's such records in that span are counted in
  `unrouted_attempts`, not listed in `attempts`, and no validation of them decides the outcome; while
  there are any, the cost is unknown the same way (`complete` false, `usd` null when no listed attempt
  is priced). An escalation whose attempt never launched (a cost-cap or time-budget stop before it)
  cost `0`.
- `inherited` is true for an escalation made before this run was created: in a checkpoint
  continuation (`autocode checkpoint --restore`), one the restored run made. Its attempts there are not
  in this run's stage records, so its `cost` is unknown, never zero: `usd` is null (a floor of its
  attempts since the restore, when it has any) and `complete` false. What that run spent is in `cost`'s
  `inherited_usd`.
- `outcome` is `passed`, `failed_again`, `paused` or `running`, and `outcome_basis` says what decided
  it, the first that applies: the first decisive record after it (`validation`: a validation of its
  work passed, unless one of its attempts of its milestone was then charged as a failure: by the
  Builder's retry policy, as it charges a review that sends passed work back, or by any correction
  task of its milestone assigned after that PASS, also one that sends back a later attempt that
  failed its own check; `later_escalation`: the role climbed again, the milestone made another
  stronger attempt, or the Builder made its stronger attempt after climbing, or climbed after it,
  from this escalation's attempt), the Builder's retry-limit pause (`recorded`: the runner's `paused`
  stamp, or the `pause` decision that charged one of its attempts of its milestone, since the runner
  does not stamp an escalation it already stamped `passed`), a validation of its work failing or blocked
  (`validation`), else such a charge (`recorded`: a retry, the stronger attempt, the retry-limit pause
  or a correction task charged to one of its attempts of its milestone), the runner's `passed` stamp
  (`recorded`), for a role other than the Builder, only when every attempt of its role since it, until
  the role's next escalation, ran on the route it climbed to and there was at least one, else the
  run's status now (`run_status`: complete is `passed`, running is `running`, any pause, wait or stop
  is `paused`). The runner stamps another role's open climb `passed` where the Completion Reviewer or
  Resolver assigns the next task while the last validation passed, whoever's work passed, so that
  stamp is not taken over a failure of its own work, once a person or the Builder's retry policy
  moved the role to another route, or when the role ran nothing since.
  Validations judge the Builder's work, and a run saved by AutoCode before this change (#612) may hold
  a `passed` stamp the runner wrote on the Builder's stronger attempt in the handler that made it, at the PASS the review
  just sent back, before that attempt ran, so the Builder's stamp is never taken. It is a projection of saved records, so it can change as the run
  continues.

A parallel Builder's own climbs and stronger attempts stay in its worker run's state, which the view
does not read; a stronger attempt it leaves to the parent is listed once the parent makes it.

`progress` is how far the run has got, counted only from saved records
(`tools/autocode_progress_view.py`; added by `autocode --status`, which supplies
the accepted milestones, the valid review receipts and the state of the recorded
workers). `line` is the one line a person reads, for example
`Builder working · 1 of 3 tasks done · 1 of 3 requirements checked, 1 failed · 1 open problem · nothing needed from you`;
its wording may change, so programs read the fields beside it:

- `headline`: `Complete`, `Waiting for you`, `Paused`, `Waiting for another run`,
  `<job> working`, `<job> finished` (its report is saved but not applied yet),
  `<job> finished · Next: <job>` when the run already knows the job after that
  finished one (#1711), `Next: <job>`, `<job> stopped without saving a report` (the
  recorded workers are
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
modified or stale contracts do not expose it. Criterion `verification_method`,
`human_review` and the optional `current_behavior` (what happens today, from the
bound diagnosis; #2133) values are copied without interpreting or coercing model-authored
text. Where the body carries them it also includes `milestones` (each with at least
`id` and `objective`) and `end_to_end_flow`: the completion order the approval binds,
so a person approving a member plan can see a coordinator-owned joint-proof milestone
in the milestone list instead of only in criteria summaries (#2434); sealed legacy
bodies predate both fields and simply omit them, keeping every earlier field's shape.
Automation must check these structured fields and bind the token to
`needs.token`; do not infer approval authority by parsing headings or review labels
embedded in `--show-goal` prose. This projection is not approval, execution permission
or completion proof; the existing CLI approval checks remain authoritative.

`approved_contract` is the plan in force: the contract that was approved, for as long
as that approval holds. It has `revision`, `hash`, `token` (the `r<revision>:<hash>`
that was approved), `task_id`, `approved_at`, `approval_source` (`skip_to_build` when the
person chose on starting the run to approve the reviewed plan, otherwise `person`) and `body`, the full approved contract
body (outcome, deliverables, acceptance criteria, constraints, permission boundaries,
end-to-end flow, technical approach, milestones and the rest) copied as saved. It also has
`goal_hash`, the SHA-256 of that body (what `autocode plan --check FILE` prints for a file
holding exactly this plan), and `plan_file`: null, or, when the plan was loaded with
`--goal-file` and not approved, changed and approved again since, the file's `path` and
`goal_hash`, the `revision` it was loaded as and the parts of the plan that `changed` since
then, apart from the runner's records of the plan's review (#720). A malformed record gives
null, never an absent `approved_contract`. It is
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
was approved. A coordinating layer reads a child run's approved criteria here, never
from `state.json`. Like `displayed_plan`, it
is neither execution permission nor completion proof.

`approvals` lists every plan and review approval, completion acceptance, goal edit and
pause-releasing resume the run recorded, oldest first (#722): `kind` (`plan`, `review`,
`completion` for `--accept-completion`, `edit` or `resume`), `at`, `actor` (`user_cli` for a
person, `workflow_policy` for a small bug fix approved under the workflow policy),
`contract_token` (the plan token approved, or the draft an edit installed; null for a review,
a completion acceptance or a resume), `criterion` (the accepted criterion, null otherwise),
`pause_status` (the pause a resume took the run out of, such as `PAUSED_TIME_LIMIT`, or the
one a pause intervention interrupted; null otherwise), `source` (`skip_to_build` for a plan
approved because the run was started with `--skip-to-build`; null otherwise) and `provenance`: `git_user_name`,
`git_user_email`, `host`, `command` (`autocode`, the `resume` command word when typed, and
the invocation's option names, never their values) and `claimed_as` (the `--as` name, or
null). None of it is verified ([Who approved](execution.md#who-approved)). `provenance` is
null for a record saved before AutoCode kept it and for a workflow-policy approval. It is a
history: an approval stays listed after a newer plan or a rejection replaces it, so read what
is in force from `approved_contract` and `evidence.acceptance[].human_reviewed`. It is empty
until the first record.

`routes` maps every configured role to its saved `model` and `engine`, plus
`reasoning_effort` (null when unspecified), resolved per-role `provider`, and
`job` (its primary job's screen name from `autocode_roles`). These are configured
assignments, not evidence a stage ran or a prediction of conditional routing or
escalation. Several slots can serve the same job. The actual history is in
`usage.accounting.attempts`: each attempt now also includes `job`, derived from
its stage and workflow, alongside its recorded model and `launch_route`, and
`route_role`, the slot whose route the stage launched when that differs from its
unit `role` (null otherwise; #2114).
Clients must keep configured assignments separate from actual attempts; changing
a saved route does not rewrite an earlier attempt's model or reasoning effort.

The top-level `provider` names the provider those OpenCode-engine routes launch through
(`opencode`, `claude` for the [built-in Claude Code provider](providers.md#claude-code-provider),
or a configured tool's name); it is null when every role runs on Codex. `route_assignments` lists, oldest first, every model a person named for a
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

`tool_containment_backend` names the kernel boundary the latest contained launch ran
in, read from that launch's own stage record (the running stage first), never from
the machine reading the status: `backend` (`seatbelt` or `bubblewrap`; `null` if the
record names neither), `backend_version` (the macOS release, or `bwrap --version`), and
that launch's `stage`, `iteration` and `started_at`. A launch saved before backends were
recorded reads as `seatbelt` with a `null` version, since Seatbelt was the only backend
then. Launches without a boundary (planning, uncontained, configured providers, Codex)
are passed over, so after a run turns uncontained this still names the last boundary
that applied; `tool_containment` says whether the run's stages are contained now. It
is `null` until a stage launches contained.

`package` is the repository directory a run started with `--package` works in,
or `null` for a run of the whole repository; see
[Monorepo packages](workflow.md#monorepo-packages).

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

`workflow` is the kind of job AutoCode recognized from the request, named by
Requirements, the first stage of every new run and follow-up turn (#1846,
#1860): one of `build`, `bugfix`, `review`, `design` or `discuss` (see
`scenarios/README.md`, "Workflows"). A flow without a Requirements stage (the
v2 planning flow, a run without a Requirements role) still begins with the Job
recognizer (`recognize_workflow`). It is `null` until that stage has run, and
for runs that predate it. `workflow_source` is `"model"` when a model decided
it (Requirements or the recognizer) and `"user"` when `--workflow` named it;
`workflow_reason` is `Requirements understood: <outcome>` (`Requirements named
no job; planned as a build` when its report named none), or the recognizer's
one-sentence reason. Both are `null` whenever `workflow` is.

`planning_depth` shows how much planning the run chose and why
([adaptive planning](adaptive-planning.md)). It is always present:

| Field | Meaning |
| --- | --- |
| `adaptive` | Whether the run plans adaptively (`--adaptive-planning`, the default where it applies); `false` on the fixed sequence |
| `clarity` | `"clear"` or `"vague"`: the judgment of the request text by the stage that names the job, Requirements (or the recognizer on a flow without Requirements); `null` on the fixed sequence, under `--workflow`, and before the job is named. It no longer skips Requirements; a small clear brief keeps its reduced plan-review depth |
| `requirements_skipped` | `true` only on a run saved before 2026-10-22 whose recognizer judged a build request clear and sent it straight to the Planner, `false` where such a run's judgment kept the Requirements stage; `null` on every newer run, which always runs Requirements first (#1860), and wherever clarity decided nothing |
| `plan_size` | `"small"`, `"medium"` or `"large"`, measured from the draft's milestones and the files they touch at its first review; `null` before that review |
| `size_signals` | The counts behind `plan_size`: `milestones`, `affected_paths`, `acceptance_criteria`, `dependency_edges`; `null` before the first review |
| `review_limit` | The plan-review call allowance set at the first review (`0` is unlimited); a large plan gets one more call. A later `--planning-review-call-limit` changes the allowance in force, not this record |
| `challenges` | How many first-style reviews the Plan Reviewer has made |
| `approved_early` | `true` when the Plan Reviewer approved the Planner's draft at a first-style review, with no revision or final review |
| `uncited_sentences` | How many source sentences the latest Planner draft's criteria leave uncited, counted by the runner; `0` when none or unknown |

The Plan Reviewer still reviews every build plan and you still approve it; these
fields only report the decisions.

`stage_policy` records which planning path a bug-fix run took and why
([compact planning](adaptive-planning.md#compact-planning-for-bug-fixes)). It is `null` until a
bug-fix run's Investigator has reported (and for every other job); then:

| Field | Meaning |
| --- | --- |
| `version` | `1` |
| `path` | `"compact"` (the runner wrote the draft; the Plan Reviewer reviews it) or `"full"` (the Planner's draft runs) |
| `reasons` | Every trusted signal that kept the full path, in words (`the run did not opt in (--compact-bugfix-planning)`, `the project declares no sensitive-paths, so every path counts as sensitive`, `the fix touches sensitive paths: ...`, `the compact draft was refused: ...`); `[]` on the compact path |
| `basis` | The records the decision rests on: `diagnosis_output_hash`, `diagnosis_source_revision` and `requirements_output` |
| `decisions` | One row for `astra_discovery` (the Planner's draft): `decision` is `"omitted"` or `"included"`, with its `reason`, and `covered_by` names what covers an omission (`runner:compact_draft`, `astra_challenge`, `person:approve_goal`, `runner:regression_proof`) |
| `fallback` | `null`, or `{"from": "astra_challenge", "reason": ..., "review_call_refunded": true/false}` once a review could not approve the compact draft and the run returned to the full path (the compact review's call is given back to the full path's allowance) |

The record reports a decision the core made from trusted inputs; it grants nothing, and a
client renders it without acting on it.

`proxied_requirements` lists the requirements the current plan proves only on a stand-in
(#2068): the user-visible result is rendered by code outside this checkout, so the plan's
criteria prove an internal value the result is believed to depend on. Each row has
`requirement_id`, `evidence` (the criteria it cites) and `proxy_reason`. A plan may mark a
requirement `proxied` only with the person's word: a saved answer cited in `proxy_reason`, or
until then a blocking decision question about that requirement, which the run asks before the
plan can be approved. It is `[]` when there are none or no plan yet, and it grants nothing; the
approval screen lists the same rows under "Proven only on a stand-in".

`needs` is `null` when the run is complete. Otherwise its `kind` says what the
run is waiting for:

| `kind` | Waiting for | Extra fields | Answer with |
| --- | --- | --- | --- |
| `approve_plan` | approval of the plan AutoCode displayed | `token` | Approve the plan |
| `answer` | answers to clarifying questions or a decision | `questions` (id, question, why, options, proposed_default), `request_kind`; for a question Resolver published, also `resolver_request_id`, `resolver_token` and `resolver_scope`; with `resolver_scope` `blocker` or `operational_exhaustion` (and no `route`), `action` is the response command; when `pause_origin` is `PAUSED_NO_PROGRESS` because a repeated incident was held, also `retry_action` (`--resume-paused --retry-failed-stage`), the one scoped retry the hold admits after inspection | Answer, with the current `resolver_token`; the questions left return under a new one. A question with `options` takes exactly one of those labels or its `proposed_default`; free text answers only a question without options. A `blocker` or `operational_exhaustion` request refuses `--answer` and takes `action` (`respond_operational()`), or, after inspection, `retry_action` (`retry_failed_stage()`) |
| `review` | a person to accept specific acceptance criteria | `criteria`, `token`, `question` | Approve a review, per criterion |
| `planning_budget` | more plan-review calls | `reason` | Plan feedback, or `--planning-review-call-limit N` |
| `recover_source` | an attempt without a saved original source identity | retained retry metadata, `recovery_hint`; `action` is null | Inspect the archive and current changes before a new run |
| `retry_job` | inspection of a stopped workflow job | `job_retry_token`, `archive`, `write_diagnosis`, `recovery_hint`; `route` after quota, a content-filter refusal, an authenticated output limit, authentication failure or a fully stopped execution timeout | Exact retry after restoring original source; with `route`, name another model first |
| `resume` | a person to inspect a pause and resolve its cause | `reason`; `new_run_required` with `action=fresh_run` preserves an immutable caller-binding failure and requires a new run; `edit_required` at a repairable component-plan stop prescribes `action` (`--edit-goal FILE`) or `feedback_action` (`--feedback TEXT`), then fresh approval; at `PAUSED_ACCEPTANCE_TESTS` (the Tester's pause) `action` is `--feedback 'WHAT TO CHANGE'`, which revises the plan and returns the run to plan approval, `chat.input` offers the same `feedback` message, and `guidance` says that `--resume-paused` reruns the Tester unchanged and helps only once the code changed; otherwise `action` when one command continues, such as `--resume-paused --no-progress-limit N` at a `PAUSED_NO_PROGRESS` its unchanged-batch limit caused, with `no_progress_batches`, the retained count N must exceed (or N is `0`), or `--resume-paused --retry-builder M` at a parallel Builder member's quota, content-filter or output-limit stop whose request was answered without a model, or `--resume-paused --retry-failed-stage` (also as `retry_action`) at a recovery novelty hold whose request is not yet published, where a plain resume would hold again; when that hold cannot take the retry yet, `retry_unavailable` instead says why (an owned or uncertain attempt to reconcile, or a different pending decision), the same reason the CLI gives; at a `PAUSED_REPEATED_FAILURE` whose active attempt, uncertain artifacts or pending report repair its retry gate will not authorize over, `retry_unavailable` carries that CLI refusal verbatim and `action` is the plain `--resume-paused`, never the rejected `--retry-failed-stage` (#1897) | Start a fresh run when required; otherwise correct and freshly approve an edit-required plan, or resume once resolved |
| `dependency` | a bound producer run's delivery (see [Dependencies between existing runs](#dependencies-between-existing-runs)) | `reason`, `producer_run`, `producer_workspace` | `autocode wait --run-dir <producer_run> --workspace <producer_workspace>`: returns when the producer needs a person or ends. Nothing to answer on this run: the dependency worker (`python tools/autocode_dependencies.py ... --watch`) calls `--receive-dependency MANIFEST` (`TaskRun.receive_dependency`) when the producer's verified delivery is ready |
| `continue` | no person input; the core may name a deterministic recovery in `action` (for example, setting aside an uncertain attempt, or resuming bounded recovery when the saved progress ledger proves another slot is unnecessary) | `action` when present | Run only the named `next_command`, then read status again |

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

When a person named a role's reasoning effort and the role's model does not declare
that level (GLM 5.3 declares `low`, `high` and `max`, not `medium`), the run pauses as
`PAUSED_EFFORT_UNSUPPORTED` before any stage launches (#2057). Its `answer` need carries
`effort`: `question_id` (`effort-ROLE`), `role`, `job`, `model`, `requested`, `declared`
(the levels the model offers) and `nearest` (the declared level nearest the requested
one; advice, never a default). Its one question lists `declared` as its `options`, has no
`proposed_default` and is never delegable. `next_command` is `--answer effort-ROLE=EFFORT`
with no token: a client fills `EFFORT` with one of `declared` a person chose, and refuses
anything else. The answer saves that level as the person's own choice and launches
nothing; the run then needs a resume. An effort the runner chose is never asked about:
the launch moves it to the nearest declared level and records the move.

Approving a plan or a review is a real user decision. Automated callers should
do it only when a person has delegated that decision to them, as the scenario
harness does for test runs.

Resolver scopes preserve separate decision authority even when they share a
needs kind. The fast execution matrix in `tests/test_needs_execution_matrix.py`
checks these scopes through the CLI's saved-action pipeline and the public
TaskRun methods; adding a scope or a needs-table row requires an execution case.

| Resolver scope | Need and decision | Authority retained after the decision |
| --- | --- | --- |
| `clarification` | `answer`: answer with the displayed Resolver token | Intent is saved for fresh planning; execution is not approved |
| `goal_approval` | `approve_plan`: approve the displayed sealed plan token | Only that current plan gains approval |
| `permission` | `answer`: select an offered scoped permission answer | The existing approved contract is preserved |
| `goal_change` | `answer`: answer the proposed change with its current token | Fresh planning and approval remain necessary |
| `human_review` | `review`: accept a named criterion with its current artifact token | Source and technical-evidence pins must still match |
| `blocker` | `answer`: use `respond_operational`, not an ordinary answer | Corrective information does not edit the contract or authorize execution |
| `operational_exhaustion` | `answer`: use `respond_operational`, not an ordinary answer | Existing limits hold; information is considered at the next explicit resume |
| `intake` | `answer`: answer the Resolver intake question with its current token | Intent is saved; no project plan or execution approval is implied |

AutoResolver never changes the approved contract itself. The resolver policy defines a `replan`
action (`autocode_resolver.ACTIONS`), but every decision the runner asks of it admits only
`continue`, `retry` and `escalate`, and a diagnosis only `retry` and `escalate`. A contract change
always goes through fresh planning and the person's approval of the new plan token. This is
deliberate: the Resolver adapter does not authorize contract replanning, implementation replay, new
permissions or completion.

## Supervision timing in status

`view.liveness.timing` reports an authenticated supervision receipt's
`monotonic_elapsed_seconds`, `wall_elapsed_seconds`, `admitted_stage_limit_seconds`,
`excess_wall_seconds`, `suspension_tolerance_seconds` and
`likely_host_or_vm_suspension`. Legacy or malformed timing is `null`. A runner
check exposes the same evidence at `view.runner_check.liveness.timing`. This is
passive evidence: stage, idle and tool limits and supervision triggers continue
to use monotonic awake time only.

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

## Work in another repository

`view.scope` reports work the request places in another repository (#2066). Requirements
names each part, and the runner keeps it only when its quote is verbatim in the request or
a saved answer, or when it cites a file in this workspace that shows it.

- `spans_repositories` and `external_parts` (`target`, `work`) list the kept parts.
- `dropped` names the targets of the parts the runner refused.
- `decision` is `this_repository` once the person chose it, else `null`.
- `uncovered` lists the targets this run leaves out.
- `linked` lists the other runs of this run's change set: the source run and each run
  linked to it (`changeset`, `target`, `role` `source` or `member`, `workspace`,
  `run_dir`, which is `null` while a linked run is starting). It is `[]` for a run in no
  change set.
- `briefs` holds a ready brief (`repository`, `brief`) for each repository the request
  spans, this one first, for a person who stops here and runs them separately.

A kept part stops the run before any plan, with one nondelegable question
(`workspace-scope-<identity>`, kind `answer`). The question binds the current human
turn and feedback to the exact external work; clients use its offered ID. The same
gate applies to a reproduced Investigator diagnosis and a follow-up with an existing
goal. Its only option is `This repository only`. Other
text is refused, and `--feedback TEXT` changes the request instead. After that answer the
plan must record each uncovered part in `scope_exclusions` as
`Not covered: changes in TARGET: WORK`, and `displayed_plan.scope_exclusions` shows it on
the approval screen. The Planner's packet names the exact entries
(`scope_exclusions_required`). A saved answer may correct one authenticated boundary's
exact wording: a user-backed `reworded` declaration whose replacement still names the same
peer moves that entry to the corrected text in every later draft and packet, while the
scope and assignment it was recorded under stay the same; a removal, a moved boundary or an
unbacked declaration keeps the original entry (#2467).

### Linked runs

`autocode changeset link --run-dir RUN --target TARGET --workspace PROJECT [--json] [-- OPTIONS]`
starts a run in another repository for one of the source run's `scope.external_parts`.
It is a layer above this interface: it reads the source run's status view and starts the
linked run with `TaskRun.start`, nothing else.

- `TARGET` must be a target in the source run's `scope.external_parts`.
- `PROJECT` must be an available project in the AutoCode registry (`autocode registry
  list`), named by its path or registry id. A path from the task text or a model's report
  is never used. An operator can register a fresh committed local checkout with
  `autocode registry add PATH`, without starting a task. Linking runs directly in
  that selected checkout; choose a dedicated Git worktree if needed.
- The linked run's brief is the source run's `scope.briefs` entry for `TARGET`. Arguments
  after `--` are the linked run's run options, such as its engine and models.
- The link is recorded in `changesets.json` beside the registry, so both commands need
  the same `AUTOCODE_HOME`. A target is linked once. A link whose run never started is
  dropped, so it can be made again. Linking from an existing member extends its canonical
  set, preserving the original source. The same target or workspace cannot be added twice.
  Ambiguous overlapping records from older versions are refused rather than choosing a
  different set for status and inventory.
- `autocode changeset status --run-dir RUN [--json]` shows the change set of any of its
  runs, each with the status, `needs` kind and `next_command` of its own status view.
  `autocode changeset list [--json]` lists every change set.

Linking does not answer the source run's scope question: its plan still records the linked
target as not covered there. Adding a member withdraws any shared execution release, so
the enlarged set needs a fresh approval of all current plans.

#### Approving the set

`changeset status` shows each run's plan (`runs[].plan`, the complete `displayed_plan` its
token approves) and `approval`:

- `set_token` is a digest of every run's identity and own plan token. It is `null` until
  every run offers a complete plan for approval, including a previously approved plan
  still held after an interrupted set approval. `waiting_on` names runs without an offer.
- `next_command` is the argument array that approves the set, or `null`. It is also `null`
  when every run has already completed under its approved plan: an unchanged completed run
  joins a revised set with that plan, but a set with no new plan has nothing to approve.

`autocode changeset approve --run-dir RUN --set-token TOKEN` reads every run's view again.
It refuses a token that no longer matches, then records a durable hold for the exact
membership before approving any member. Each approval uses the public
`TaskRun.approve_plan(plan_token, changeset_token=set_token)` operation, transported as
`--approve-goal - --changeset-token -` with `AUTOCODE_APPROVE_GOAL_TOKEN` and
`AUTOCODE_CHANGESET_TOKEN`. Each core publishes only its own exact approval at its durable
checkpoint; the coordinator never reads another run's private state.

The shared release is committed only after every member checkpoint has accepted the
same set. A failed/interrupted approval leaves all members held; inspect the current
plans and repeat the offered set approval to finish it. A later plan, task turn or
membership change withdraws the release before that changed checkpoint can commit.
The core checks the receipt before execution, including resumed and parallel Builder
work; an already dispatched attempt retains its normal safe-boundary lifecycle.

An explicit individual `--approve-goal` remains available and releases only that exact
run plan and turn. Reapproval after a held set uses the ordinary AutoResolver plan
request boundary and cannot clear a different pause, pending question or uncertain
attempt. An ordinary resume cannot release a held set. `changeset status.record_revision`
is the monotonic coordination-record revision used to reject stale record mutations.

#### Checking the set together and delivering it

Each run still passes its own completion gate. `changeset status` adds `verification`:

- `waiting_on` lists the runs not yet `TASK_COMPLETE`. Nothing is checked together before then.
- `check` is the integration command the person named, `result` its last run and `waiver` the
  person's reason to accept the set without it. A result or waiver carries `check_identity` and
  `current`. It is current only while the named command and execution target, member run/workspace
  identities, every run's status and every workspace's code (commit, diff and untracked files,
  AutoCode's own excluded) match the recorded evidence. Historical results and waivers without
  the check identity remain readable but are not current; run the check again or record a fresh
  explicit waiver rather than inferring approval for an unknown command.
- `done` is true once every run is complete and either no command was named, a current result
  passed, or a current waiver exists.
- `merge_order` and `next_command` (the `verify` argument array, while a check is due).

The summary's additive `record_revision` identifies the inspected change-set record. Changing
the check, verifying and waiving use a conditional write against that revision. If a command,
membership, waiver or competing check result changes the record while verification runs, its old
result cannot overwrite the new record or certify a replacement command. The action refuses and
asks for fresh inspection.

The actions:

- `changeset check-command --run-dir RUN --in TARGET -- COMMAND...` names the command, run
  with no shell in that run's workspace. `AUTOCODE_CHANGESET_WORKSPACES` holds a JSON object
  from each run's repository name to its workspace, so the consumer's tests can run against
  the producer's checkout. This is where the shared interface is checked. Naming a command
  clears both the previous result and its waiver, including when changing only the target.
- `changeset verify --run-dir RUN` runs it. A check that changes a workspace does not pass.
- `changeset waive --run-dir RUN --reason TEXT` records a waiver for the named check, current
  member runs and code. It grants no waiver for a replacement check.
- `changeset reopen --run-dir RUN --target TARGET [--message TEXT]` sends that run a
  follow-up, by default the failed check's command and output.
- `changeset order --run-dir RUN TARGET...` sets the merge order. By default the source
  run merges first.
- `changeset pr-body --run-dir RUN --target TARGET` prints the Markdown section that run's
  pull request carries: the change set id, every run, the merge order, the check's outcome or
  the waiver, and the parts no run of the set covers.

#### Change sets in clients

Each `runs --json` row carries `changeset`, the id of the change set its run belongs to, or
`null`. A client (owner decision 2026-10-25) may read `autocode changeset status --run-dir RUN
--json` for a run whose status view lists `scope.linked`, and take only the two argument arrays
it names, after showing what they act on:

- `approval.next_command`, after showing every run's `plan`. It carries the `set_token` of those
  plans, so a revision in any run makes the core refuse it.
- `verification.next_command`, the joint check.

Approval displays include each plan's `scope_exclusions` under **Not covered**, including work
left to another repository. The terminal, GitHub bridge, dashboard and VS Code show these terms
before approval; a set approval shows them for every member. Recorded replay approval matching
also includes exclusions and Git delivery, so changing only those terms still requires a new match.

The shared rule is `autocode_client_actions.changeset_action`, mirrored by
`vscode-extension/src/core/next-command.ts`. The terminal chat's side panel lists the linked
runs, `/changeset` shows the set and offers these actions, and `/runs` groups the chats of one
change set. VS Code's Run details shows the set with one button for the action named now. The
channel bridge posts the set's plans at a plan gate and takes `approve set` only with the set
token of the plans it posted (`docs/channels.md`). The other `changeset` commands stay the
person's own.

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

A person can change only the total time/accounting caps of that exact stopped job with
`autocode --run-dir RUN --job-retry-token - --max-seconds SECONDS --max-cost-usd USD --no-chat`
(`AUTOCODE_JOB_RETRY_TOKEN` supplies the displayed token; either total flag may be omitted).
This standalone settings action checks the original source and captured configuration before
saving anything. It preserves usage and failed-attempt evidence, keeps the job paused and issues
a fresh retry token; it grants no retry or plan approval. Read status again and explicitly retry
with that fresh token. The old token, mixed actions, model/effort changes and other limits are
refused. Clients may use this operator action only when offered by their public status contract;
its availability as a CLI command does not authorize a client to invent a cap increase (#2156).

A job stopped by its provider's content filter, on quota, or by its stage time limit also carries
`needs.route` (the shape of the `answer` need's `route`: `question_id`, `role`,
`job`, `current_model`, `engine`, `cause`, `stopped_model`, and `candidates` for a
refusal). The same model is likely to refuse again, so before retrying, a person
can name another model: `run.assign_model(role, model)` answers
`--answer route-ROLE=MODEL --job-retry-token TOKEN` with the view's token. The spec-first acceptance-test
writer uses its own
`route-acceptance_tester` question; an explicit answer changes only that saved
writer route, leaving the later Tester (`sol`) and Completion Reviewer unchanged.
A retained acceptance-writer timeout without a saved question is projected only
from its matching archived timed-out attempt, source identity and launch configuration.
This read changes no evidence and grants no retry authority. A model the
launch would refuse, or the refused model itself, raises and changes nothing. An
accepted model is saved as a `route_assignment` and the need comes back with a new
`job_retry_token`; the old one is rejected. Its `route` is asked again against the new
configuration: `current_model` is the named model, and `candidates` never lists it
(answering it again is refused). Then call
`run.retry_job(view["needs"]["job_retry_token"])`. `assign_model` updates every existing
split or equals-form `--ROLE-model` in the client's options, so the retry never
passes the old model back; the CLI refuses a `--ROLE-model` change for the stopped role at this stop (it would
make the exact retry stale), unless it puts back the model the retry is bound to.
The answer carries no other setting: given with a limit or another role's model it
is refused and nothing is saved, and the CLI refuses it next to
`--resume-paused --retry-failed-stage` (the retry needs the new token). After a
refusal, output-limit or timeout stop, until a different model is named, the need's `action` is that answer
(`--answer route-ROLE=MODEL --job-retry-token TOKEN`) and the recovery card offers
no `retry_job` action for a refusal or output-limit stop, since the exact retry would replay the stopped model; the
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
code-changing Builder steps. A parallel batch's verified integration is one
saved step: its `milestone_id` is `null` and `members` names each milestone it
served with the files that milestone's Builder changed (`members` is `null` on
other steps). Historical step metadata without source objects
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

While a Builder step runs, `code_checkpoints.working` (otherwise null) names one more action in the
same shape, `autocode checkpoint --working`. It is display only: it prints `{since, changed_files,
patch, truncated, compared_at}`, the files as they are now against the newest saved step (`since` is
its id) or, before any step is saved, against the checked-out commit (`since` is `"start"`), limited to
the run's own scope. It carries no token and restores nothing. The Builder may still be writing, so a
file changing during the read makes the action refuse; ask again.

Each row of `code_checkpoints` that still has verified source objects also
names the actions a client may take, in the same shape as a connection
(`actions` with `id`, `label` and `next_command`: an argument array following
`autocode`). A client executes only offered actions. The `compare` action is
always named on an available row. The `restore` action is named only where
restoration is currently allowed (`blocked_reason` is null); it carries
`--expected-token -`, which reads `AUTOCODE_CHECKPOINT_EXPECTED_TOKEN` and is
supplied only by a fresh `compare` of that same checkpoint (a stale token is
refused), and the `REQUEST_ID` placeholder, which the client replaces with its
own stable id for this confirmation — replaying the same id reconciles its
candidate instead of creating another. Without the run's paths the array uses
the `WORKSPACE` and `RUN_DIR` placeholders; the status view supplies the real
ones.

`status()["changes_by_criterion"]` attributes those saved steps to the approved
plan's acceptance criteria. Each criterion lists the plan milestones that cover
it, the checkpoint ids of their saved Builder steps and the union of the files
those steps changed; a parallel batch's step counts only each member's own files
toward that member's milestone. `overlapping` is true when a step outside the criterion's
milestones also changed one of those files, so the file's diff may hold other
work. `unattributed` lists steps whose milestone is not in the plan. It is
attribution, not proof: it says where to look, not that the changes satisfy the
criterion. It is `null` until a plan is approved.

`status()["dev_server"]` is the run's development server
([Development server](cli.md#development-server)): `configured` says whether the
run saved a `serve` command, `status` is `off`, `starting`, `running`, `stopped`,
`exited`, `failed` or `lost`, and `url` is set only while it is `running`.
`code_checkpoint` names the code checkpoint the server was started on and
`restarts` counts the code changes that restarted it, and `waiting` is true while
a server that exited or failed waits for the run's next code change to start
again; a client can compare
`code_checkpoint` with the latest `code_checkpoints` row to tell whether the
preview shows the newest code. The
status command inspects it on every call; a direct `run_view.view()` without that
inspection leaves it `null`. Starting and stopping the server is
`autocode serve`, not a TaskRun method, and it never changes the run.

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

## Connections for clients

Connection setup is a core interface alongside task runs. A client may read
`autocode connections list --json` before starting a task. The version-1 object
contains `connections`, each with `id`, `label`, `connection_status`, `detail`, and `actions`.
The connection new runs use without `--provider` (`AUTOCODE_PROVIDER`, then `default_provider`
in `~/.config/autocode/config.toml`, else `opencode`) also has `default: true`; it is display
only, so a client can say what a new chat runs on.
An action has `id`, `label`, and `next_command`: an argument array following
`autocode`, not shell text. Clients execute only offered actions. Listing reads
user-level provider configuration and executable availability; it never launches
models, reads credential stores, or runs backend commands. The plain view carries
`routes: null` and `routes_next_command`
(`["connections", "list", "--routes", "--json"]`).

That named action adds the route each role would use, chosen from the connected
tool's own model listing (#2057): `tool`, `routes` (each with `role`, `job`, `model`,
`tool`, `account`, `billing`, `reasoning_effort`, `origin`), the same text as
`routes_lines`, and `routes_stop` when no connected account can field a qualified
route for a role. It reads a listing and never launches a model. A client shows
`routes_lines` beside the connection; it does not choose a route itself.

`connections check --provider NAME --json` checks the backend without inference.
Its result is one connection object. `credentials_present` means the backend
reported credentials, not that every model, billing route or live task passed.
Other statuses are `not_checked`, `unavailable`, `sign_in_required`, and
`check_failed`. Status-command output and credential values are never returned.

`connections login --provider NAME` delegates interactive login to the backend
in a terminal, inheriting its input and output. The core resolves the offered
backend command again; the client supplies no credentials or executable. Login
does not alter a task or start inference. After login the person checks again.
A remote CLI wrapper must forward a terminal for interactive login (for example,
a container transport needs a TTY). Connections are checked in the CLI's own
execution environment, which may differ from the editor host.

Changing a client's selected backend applies to new chats. Existing task actions
use their saved provider, models and budgets. Backend-specific role overrides
must not be copied onto a different backend.

The always-present `autocode` connection advertises a `setup` action with
`next_command=["connections", "setup"]`. Execute it interactively in a terminal,
with no credential arguments or webview input. Its additional setup states are
`setup_required` and `configured`; clients must not describe
`configured` as authenticated live access. The core setup action neither chooses
a client's default backend nor launches a model. Discovery reads its nonsecret
metadata; an explicit check validates its private local credential setup. When
several API providers are configured (issue #2055), the same row instead offers
an `add` action with `next_command=["connections", "add"]`, executed the same
interactive way; it adds or rotates one provider's key without touching the
others.

## Stage choices for clients

A client may let a person choose the model and reasoning level of each stage, as one dropdown per
stage ([Choosing each stage's model](models.md#choosing-each-stages-model)).
`autocode stages list --json [--backend NAME]` returns the version-1 view: `backend`, `label`, `file`
(where the choices are kept), `options_stop` (why no model could be listed, or null) and `stages`, in
the order a task runs them. Each stage has `id`, `label`, `does` (what the stage does), `advice` (the
kind of model and level that suits it), `flag` and `effort_flag`, `default` (`model`, `effort`, and
`offered`: whether the backend offers that model), `chosen` (the saved `model` and `effort`, or null),
`options`, `set` and `reset`. Each option has `model`, `levels` and `levels_listed` (whether the
levels come from the tool's own listing), `effort_choices` (the levels a person may pick, or
`["default"]` for a model that lists none), `family`, `context`, `account`, `billing` and
`unavailable`: the reason the stage may not run that model beside the others, or null.

A client shows `label`, `does` and `advice`, lists the options, and greys out an option whose
`unavailable` is set, showing its reason. To save a choice it takes `set.next_command`, an argument
array following `autocode`, replacing `MODEL` with one of `options[].model` and `EFFORT` with one of
that option's `effort_choices`, and refuses anything else. `reset.next_command` (null until a choice
is saved) forgets the stage's choice. Both print the updated view, and a refused choice exits 2 with
the reason on stderr and saves nothing. The view launches no model and reads no credential. A new
task reads the choices when it starts; a running task keeps its own.

## Run modes for clients

A client that starts runs may offer a choice of how much planning a new run does
before the person approves it. `autocode modes list --json` returns the
version-1 list: `schema_version`, `default` (a mode id) and `modes`, each with
`id`, `label`, `description` and `start_args`, an argument array of start flags
following the brief. A client shows the label and description and passes the
chosen mode's `start_args` unchanged when it starts a run; it never builds its
own list or adds flags to a mode. The flags apply only to the new run: a saved
run keeps the flow it started with. Listing launches nothing and reads no run.
Optional `spend_confirmation` is `"planning"` for Full mode: start the Requirements
conversation without a spend dialog, then honor the core's review confirmation
before preparing the plan. Requirements conversation still uses the selected
model allowance. A mode with `"start"` (none today) can plan immediately, so a
client confirms at launch. Missing or unknown metadata retains confirmation at
launch for older cores.

| Mode | Start flags | What it changes |
| --- | --- | --- |
| `full` (default) | `--joint-planning --spec-first` | Requirements talks with the person and confirms what it understood before the plan is prepared ([Spec-first runs](spec-first.md)). |

Every chat starts with the Requirements conversation (owner decision,
2026-10-22), so `full` is the only mode listed. The `quick` mode
(`--joint-planning --no-spec-first --adaptive-planning`), which let a clear
request skip Requirements, was removed; the `--adaptive-planning` flag itself
remains. A client still reads the list rather than assuming one mode, and a
choice it saved that the list no longer names, such as `quick`, starts the
default instead of failing.

Every mode keeps the plan approval, the Tester and the Completion Reviewer: a
mode changes how much planning happens before approval, never what counts as
done. A CLI without `modes` (exit 2 with a usage error) offers no choice; a
client then starts the full flow.

### Automatic routes for new runs

New runs automatically select qualified models from the chosen tool's connected
accounts, preferring subscriptions and preserving independent checking. No per-role
model flags are needed, including with `TaskRun.start(workspace, brief)`.
Explicit model, effort and tool overrides win; saved runs keep their choices.
`--no-auto-select` retains fixed defaults; conversation creation accepts both selection
switches too. `--auto-select` requires selection to
succeed instead of falling back when discovery or an independent checker is unavailable.
See [automatic model selection](models.md#choosing-models-automatically) for discovery,
reasoning levels, bounded preflight, recovery and the read-only selection preview.

### Repeated permission handoffs

`PAUSED_PERMISSION_LOOP` stops a fourth permission handoff after three authenticated
material decisions at the same task, contract, source and validation result. Changed
wording, report paths, elapsed time, budget settings and previous answers do not renew
that allowance. This is a conservative bound on unchanged work, not a semantic claim
that every question has identical wording or intent. Actual source, contract or
validation progress admits new decisions; corrective `--feedback` can establish a new
scope. No prior permission or completion gate is silently bypassed.

The public `needs` includes `permission_decisions`, `permission_decision_limit`, the
saved blocker reason, and a named feedback action. Plain resume remains held without
another provider call. All earlier requests, answers and usage remain retained.

### Authentication during an exact job retry

A retained workflow job that failed provider authentication offers the same token-bound
model answer as other model-related job failures: inspect `view.needs.route`, call
`TaskRun.assign_model(role, model, job_retry_token=token)`, and then use the newly
issued `view.needs.job_retry_token` with `TaskRun.retry_job`. The model answer launches
nothing and preserves effort, other roles, limits and recorded usage. Alternatively,
repair the existing provider login and retry that exact job unchanged.

If billing or authentication preflight blocks an authorized job retry before the job
launches, the public view retains that exact job's retry and model-choice actions.
It does not offer an ordinary Resume that would discard the job's retry boundary.
An old token, consumed authorization, different next stage or active attempt cannot
revive a previous failed job. No automatic provider substitution or credential copy
is performed.

### Model choice after a stopped job timeout

A fully stopped workflow job that exceeded its stage limit can name another model through the
same exact job-token answer. `needs.route.cause` is `timeout`; choosing a model changes only that
job's model, rotates its retry token and launches nothing. It preserves other roles (including a
bug Investigator's Plan Reviewer), effort, limits, usage and the archived attempt. A same-model
exact retry is still accepted, but can time out again; no model or time-limit change is automatic.

When the stopped model declares a lighter reasoning variant than the one the job launched at
(the tool's listing, or a level a preflight probe passed), the question offers that variant first
(#1902): `needs.route.lower_effort` is `{model, from, to, answer}`, `needs.route.candidates` is
`[answer]`, and `next_command` is that answer, `--answer route-ROLE=MODEL@LEVEL --job-retry-token -`.
This covers the deadline and the Investigator's exploration-budget stop alike. The answer form
`MODEL@LEVEL` names the effort with the model; a level the model does not declare is refused and
changes nothing, and with no listing the launch's own check decides. The named level is saved as
the person's, bound into the rotated retry token, and the retry launches at it. A model without a
lower declared variant gets the question as before.

A saved bug-Investigator timeout without a route question can expose one only when the archived
rejected, timed-out attempt matches its source capture, launch route, configuration and archive.
An active/uncertain attempt or a mismatch cannot gain that model action. The answer then rechecks
source and configuration before it is saved, and the explicit retry checks the newly bound token.

### Explicit review intake and result freshness (#2113, #2167)

A new chat explicitly started with `--workflow review` goes directly to its
Reviewer; it does not gather build requirements or request implementation-plan
approval. Unpinned chat intake and build approvals are unchanged. Only the
review's Validator route is due for model preflight; a saved unused Builder route
is not probed. Probe receipts still bind to the exact tool, model and effort.

For completed reviews, `view.evidence.workflow_result` preserves the recorded
outcome, summary, report path and digest. New reviews also record `source_revision`
after the core writes its report. Status exposes a separate `view.review_result`
inspection with `freshness` (`current`,
`stale_or_unverified`, or `unknown`) and `reasons` after comparing that binding
and report digest with the current workspace. Old reviews without a source
binding remain unknown; status does not upgrade them or rerun a model. A review
outcome is not build-delivery acceptance. The authenticated
`evidence.workflow_result` stays unchanged by read-time inspection. The terminal renders these public
review facts in its header, Review result panel and `/details`.

### Original-test command recovery

At an idle validation pause, the core can name `--resume-paused --revise-protected-tests @approved-suite` when an approved empty package-marker deletion stranded the original root unittest collector for nested repositories. The `needs.reason` displays both exact commands and the retained original-test guarantee; clients take only that named action after confirmation. This revises only the suite command, capturing the same sealed original files from their archive, never the current modified tests. The old binding and explicit user event remain. The proposal starts from a unique approved, unfiltered pytest collector covering every original path. If restoring the sealed empty root marker would collide with an empty nested test package, the explicit offer moves the joint `tests` root first and adds `--import-mode=importlib`; it displays this exact change for confirmation and retains both markers. Executable nested package setup cannot trigger this correction. Active, uncertain, unapproved, filtered or ambiguous cases offer no correction. Fresh original-test replay and independent validation remain required.
