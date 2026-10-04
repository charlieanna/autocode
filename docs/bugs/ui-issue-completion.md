# UI issue completion ledger

The September Figma build (PR #292) is merged. Its final master CI run
37143840279 passed. That establishes its test gates, not completion of every
open UI issue or pixel identity with Figma.

This follow-up keeps the approved project/conversation layout and human actions
in chat. Existing user data and unrelated running tasks are preserved.
No installed app change or merge is part of the current review branch.

## Current repair batch

- **#289, browser catalogue cleanup:** replace immediate-child-only timeout
  supervision with the existing owned process-tree supervisor. Keep command,
  stdout/stderr, timeout/interruption reason and cleanup receipts. No case or
  deadline is removed. Controlled regressions exercise a normal child, detached
  child, unrelated process, setup failure, timeout and interruption.
- **#32, message intent:** deterministic context-first routing; task status
  questions and typed controls do not become feedback. Proposed corrections
  require chat confirmation bound to the saved text and plan token. Preserve
  idempotent retries, uncertain-delivery reconciliation and answer provenance.
- **Approval browser observation:** wait for both the saved approval and the
  completed action before asserting that Start building is available. The
  separate approval/start assertions remain mandatory.

## Current acceptance status (2026-10-04)

The implementation below has passed local functional and independent visual
acceptance. It is submitted for PR review; this ledger does not close issues or
establish acceptance of unrelated platform work. Later approved product choices
supersede illustrative behavior in older issue descriptions.

| Issue | Delivered behavior | Acceptance and boundary |
| --- | --- | --- |
| #27 / #28 | Persistent chat, grouped projects, scoped creation, Work/artifact panes, responsive drawers and Stop after current step | Desktop/tablet/phone browser matrices; all 18 approved design states independently accepted |
| #29 | Public saved progress, requirement evidence and task/requirement/problem detail | HTTP and real-browser proof; unknown results stay unknown |
| #31 | Exact reviewed-plan approval and separate Start building; stale cards refused | Exact-token, revision race, duplicate and reload tests; separation follows the later user correction |
| #33 | Public recovery projection, per-action guards, semantic role names and saved reason/next step | All mapped lifecycle states and three-size browser matrix; polling preserves focus, scroll and the action hit target |
| #34 | Project-scoped preview, saved-code refresh and requirement-linked screenshots | Browser isolation/refresh and image integrity tests; preview startup remains manual |
| #35 | Stopped-run checkpoint restore to a new branch and paused continuation | CLI/HTTP and browser restore; original history retained and fresh proof required |
| #36 | Setup in chat, dependency guidance, project creation/attachment and safe legacy-folder confirmation | HTTP/Git and three-size browser checks; no automatic credential handling, installation or sign-in |
| #18 / #21 | Server-built transcript and bounded draft updates with exact answer provenance | Python/Node/browser gates and an actual scripted-provider to-do run; no live-model qualification claim |
| #342 | Completed delivered worktrees and their dedicated branches can be deleted with exact confirmation | Real delivery, packed refs, ownership/race/hook refusal tests; desktop/tablet/phone deletion flow with preserved siblings |
| #343 | Legacy and V2 planning reports use the canonical job, including Planner for V2 plan revision | Canonical/browser catalogue consistency, report/clarification tests and JavaScript role rendering |
| #262 | Native sidebar/header geometry and Work hierarchy match the approved frame | Fresh source-bound captures and independent image review; runtime content is not literal Figma placeholder text |
| #30 / #60 | Already closed upstream; question-card and durable deletion behavior retained and extended | Current gates preserve answer provenance, exact scope, partial retry and recreated-resource protection |
| #17 / #19 / #32 / #297 | Already closed upstream; proof, semantic names, message intent and visual comparison retained | Covered by current regression gates |
| #250 / #251 / #256 | Upstream design-manifest and visual-check facilities retained; this delivery covers the approved UI | Broader multi-file/live-connector qualification and efficiency measurements remain separate platform work; UI screenshots do not close these issues |

## Verification rules

- Use the repository venv and actual public CLI/HTTP interfaces.
- Run affected canonical tests, dashboard Python and Node tests, fake scenarios,
  and real-browser checks. Broaden to full canonical tests when shared changes
  require it, and before merging the complete UI delivery.
- Browser checks cover desktop/tablet/mobile, failure and reload states,
  keyboard/focus and usable targets. Inspect captured screenshots.
- Keep failed receipts. A passing retry does not erase a failure or establish
  its cause. Timeout cleanup is separate from diagnosing browser reliability.
- Close an issue only when its own acceptance criteria are supported. A merged
  layout PR, selected passing test, or collection manifest is insufficient.

## Verified first repair batch (2026-10-03)

- 45 chat tests passed, including real CLI delivery, HTTP submission,
  concurrent confirmation, restart/uncertain-receipt reconciliation, and
  byte-identical state for unconfirmed changes and status messages.
- Real Chromium checks passed at 1440×1024, 1024×768 and 390×844. They cover
  typed approval/control safety, confirm/decline, reload, pending questions,
  no horizontal overflow, and minimum 44-pixel confirmation targets. This
  regression also runs as a dedicated CI step under process supervision.
- All 301 dashboard Python tests and standalone Node tests passed before the
  final pending-question and stale-retry guards; all 45 affected chat tests and the three-size
  browser check passed again after those guards. The changed gate passed 23 tests across
  3 modules, including all 14 browser catalogue cases.
- 54 fake scenarios passed. The one live-model Investigator scenario was
  explicitly skipped; no live-model proof is claimed.
- The first full browser gate failed on the mobile approval observation race.
  The corrected observation waits for the actual action to settle and for an
  enabled Start building button. Its subsequent full catalogue gate passed;
  no assertions were removed and no time limits were raised.

This proves the bounded repairs above. It does not prove that every remaining
UI issue is complete or that the rendered app is pixel-identical to Figma.

The final two regressions also establish that a failed correction cannot be
retried against a newer plan and a newly opened question cannot consume a
pending confirmation before delivery is allowed. Both failed before repair.


## Next repair batch: saved progress and question cards

- **#29:** project task counts only from the supported CLI status and matching
  contract acceptance. Preserve unknown completion for legacy plans or absent
  receipts. Requirement results retain failed and unchecked counts separately.
  Linked requirement/problem detail rows preserve focus during polling. The
  expanded list scrolls into view on phones; saved status stays above long lists.
- **#30:** add explicit per-question text submission without classifying literal
  answers as controls. Retain main-composer drafts independently. Group accepted
  answer history with typed/delegated provenance; keep failed sends visible and
  re-asked questions distinct. Existing current-request token checks remain the
  authority boundary. No answer approves a plan.

Focused evidence: all 315 dashboard Python tests and standalone Node tests
passed. The Work matrix passed nine state/viewport combinations. The question
matrix passed partial answers, accepting every suggestion, reload, independent
draft preservation and a stale/re-asked request at three viewport sizes.
Both browser matrices use the real HTTP adapter and page with disposable tasks;
the fixture supplies the external runner responses and never invokes a model.

The broader run exposed real layout and refresh-focus regressions, which were
fixed. The Plan-pane checks caught an unintended
expansion of that pane; new task/problem links are now scoped to Work, preserving
the original Plan-pane assertions and layout. The question test initially clicked
composer text covering an off-screen button; it now scrolls the real target into
view and verifies the hit target before a pointer click. Failed receipts are
retained locally.

The full canonical selection ran 2,998 tests in 221 modules. Its only failed
module was the dashboard catalogue, which had cached the old JavaScript
function-signature assertion before that test was corrected. A fresh complete
14-case catalogue then passed in 333 seconds with its original deadlines. The
corrected test exercises the Work link and compact read-only Plan behavior.
All standalone Node tests passed. The final CI-equivalent browser gates for
chat intent, Work and question cards passed with successful owned-worker cleanup.
All 54 fake scenarios passed; one live-model-only Investigator case was skipped.
These results verify this batch, not the remaining backlog or pixel identity.


## Preview and evidence batch

Project preview preferences are shared across conversations in that project,
with legacy task preferences preserved. A saved code change refreshes the frame;
polling keeps it intact. Screenshot cards cite the recorded requirement and
source. The bounded image endpoint validates recorded fingerprints when available
and refuses changed bytes, stale report selections, traversal, sibling task
paths, symlinks, nonregular files and oversized/non-image payloads.

Eight image projection/HTTP tests and the three-size real-browser preview matrix
passed. The browser matrix verifies persisted addresses, sibling conversations,
project isolation, recorded screenshot rendering, requirement navigation, and
saved-code refresh without a provider or changes to real tasks. The full dashboard
run executed 323 tests and exposed one empty-state regression: the conversation
name was missing. It was restored, and all 15 existing pane acceptance checks
passed unchanged. All standalone Node tests passed before that text repair; the
focused preview check passed again afterward. The first mobile browser attempt
tried to open an already-open drawer; the corrected test observes its open state
and uses native pointer clicks. Original deadlines and failed receipts remain.
The fresh full canonical gate passed 2,999 tests across 221 modules in 549 seconds,
including all 14 browser catalogue cases with their original caps. All 54 fake
scenarios passed; the live-model-only Investigator case was explicitly skipped.
The preview browser matrix passed again after the empty-state repair, including
owned-worker cleanup with no live descendants; its phone capture was inspected.

An earlier full-suite run failed two progressive goal-change cases because the
legacy-worker guard reported an active process. The unchanged seven-test module
passed alone. A deterministic regression reproduced a concrete guard defect:
the same relative run suffix in another workspace, or a longer sibling run name,
was treated as this run. The guard now matches path boundaries. Same-run
controllers, provider output paths and ambiguous bare relative legacy paths
remain blocking. The legacy check was extracted into a lower-level module and
the support-module size limit was reduced. All 100 guard, dispatch, progressive
and architecture tests passed, followed by the full canonical and fake gates
above. Historical failed receipts remain available; no unrelated process was
stopped during diagnosis.

The previously published progress/question-card commit `74a08e6a` also passed
GitHub CI run 37152526823, including the scenario harness, dashboard Python,
standalone Node and all three dedicated browser matrices.

The complete dashboard Python gate was rerun after the empty-state fix: all 323
passed. Final JavaScript review then added two focused regressions and repairs:
failed steps cannot masquerade as a landed candidate for auto-refresh, and a
browser that denies persistent storage still opens the explicitly entered URL
for this session. Standalone Node checks and the real three-size browser matrix
passed after those final changes, including reload after denied storage. The
full canonical/fake results above preceded this final JavaScript-only repair;
CI repeats the broader gates on the pushed revision.


## Server transcript batch

The task page now consumes one server projection over the existing journal,
answers, progress and delivery receipts. Stable source IDs and causal reply
links preserve durable order when clocks disagree. Receipt merging retains the
original request ID for confirmation and retry; failed sends and re-asked
questions remain distinct. Legacy answers without recorded provenance are
labelled explicitly and are not folded into newly delivered answer groups.
Status questions cite unchecked requirements and open findings from saved
records. The projection is display-only and has no runner imports or writes.
Current approval/question controls and checkpoint inspection remain separate
from the transcript; no new execution authority is introduced.

All 335 dashboard Python tests passed, including ten pure projection cases and
an actual HTTP repeat/no-state-write check for completed, waiting and rework
states. The canonical changed selection passed its four architecture tests;
that selection does not cover dashboard modules, so it is not presented as a
substitute for the explicit dashboard gate. All 54 fake scenarios passed; the
live-only Investigator case was skipped.

Real-browser testing caught an incorrect grouping of an old legacy answer with
two new replies. A focused JavaScript regression failed before its repair.
The unchanged question-card browser assertions then passed at desktop, tablet
and phone sizes, including partial/suggested answers, reload and re-asked tokens.
The phone capture was inspected. The chat-intent browser test also passed at
all three sizes after its pointer helper was corrected to scroll the target
into view and verify it is hit-testable. Deadlines and assertions were retained;
failed receipts remain local. Both supervised browser runs reaped their roots
and reported no live owned descendants. The broad Python/fake gates preceded
the final JavaScript-only grouping repair; the focused JavaScript and question
browser gates ran after it. This does not establish the full #16 guided trial
or final Figma visual acceptance.

## Shared role names

The terminal, dashboard stage/model labels and packaged assets use one display
catalogue. Internal stage IDs, saved state, model routes and pins are unchanged.
All dispatched stage IDs and report-repair variants are covered. The two modes
that combine validation and completion review say “Validator / Completion
Owner” regardless of the model selected for that job. README and model docs
use the same job names; historical stored messages are not rewritten.

The first full gate ran 3,003 tests in 222 modules and found one obsolete
“Requirements Gatherer” expectation in the model catalogue. The other modules,
including the 14-case browser catalogue, passed. After correcting that display
expectation, all 43 role/status/catalogue/architecture checks passed. The required
changed-file gate selected the entire suite because package metadata changed:
all 3,003 tests in 222 modules then passed, including the browser catalogue.
All 54 fake scenarios passed; the live-only Investigator case was skipped.

The dashboard Python gate ran 335 tests and found one old model-selector label
expectation. Its corrected 16-test module passed, as did all 31 chat/HTTP tests
including a new check that the actual served script and page use the shared
catalogue. All 24 standalone Node checks passed. A built wheel was extracted
and imported in isolated Python; it includes the catalogue and serves resolved
page labels without installing anything. Final copy-only cleanup removed the
remaining alternate role labels, followed by another passing 24-script Node
gate. The full suite result preceded that final copy-only cleanup. These are
functional naming checks, not Figma visual acceptance or overall backlog closure.

## Historical recovery-card verification (#33)

Stopped tasks now expose a three-part chat card from the additive public
`recovery` view. Specific existing CLI actions are constructed on the server,
bound to the inspected stopped checkpoint, and checked again under the run lock.
Retry preserves the contract, saved routes, limits and failure history. Abandon
and Resume remain separate. Repeated failures retain their source identities.
An internal unpublished question does not become human authority. Unknown stops
still offer inspection and feedback; a deliberate Stop offers a new conversation.
Supported status that finds an absent worker behind a saved RUNNING state offers
inspection without inventing a paused state, execution token or another worker.

Focused projection, adapter and real-CLI recovery tests passed. All 345 dashboard
Python tests, 25 standalone Node checks, 54 fake scenarios (one live-only skip)
and 173 scenario-harness cases passed. Recovery and question-card browser
matrices passed at desktop, tablet and phone sizes. The final absent-worker
follow-up passed 19 Python tests and the recovery browser matrix; its phone
capture was inspected and the supervisor confirmed no live owned descendants.
A subsequent wording-only change calls an absent-worker checkpoint a checkpoint,
not a saved pause; its renderer regression passed.

The first required full changed-file gate ran 3,013 tests in 223 modules and
failed two modules: an exact status-field expectation omitted the additive
`recovery` field, and a scenario cleanup raised EPERM. The field expectation was
corrected. Its 29-test module and the unchanged 41-test scenario-oracle module
passed separately and in the second full gate. The cleanup cause is unresolved;
its historical failure has not been hidden or turned into cleanup success.

The second full gate ran the same 3,013 tests/223 modules in 2,236 seconds and
failed 18 modules. Diagnostics show existing subprocess/browser deadlines and
one missed intermediate worker-activity assertion. Host load exceeded 200 on
10 logical CPUs during that run. This is a failed gate, not proof of a clean
revision. Lower-concurrency failure rechecks and final integrated regression
remain required. No deadlines or assertions were relaxed and no unrelated
workers were interrupted. These receipts do not close #33, the remaining UI
backlog, or final Figma visual acceptance.


## Batch 7 — planned checks and current proof (#17, #29)

Added read-only `--status --inspect-evidence` and additive
`view.verification`. Requirements and their planned methods remain visible
before testing. Current checked/failed/unchecked results require the report to
match the source, task, contract and checklist, with intact pinned project-local
evidence. A changing checkpoint/source or unavailable inspection fails closed.
Authenticated human acceptance remains separate. Existing independent review,
regression proof and completion gates are unchanged.

Selected task detail and status questions in chat use that supported inspection.
The Work and Checks panes keep old reports available but stop displaying their
PASS labels as current checkmarks. Stale recorded completion is clearly labeled
and does not reopen completed-task mutation controls. Binding also rejects a
receipt for another displayed assignment/checklist or during a recorded attempt.

Verification: the changed selection passed 266 tests in 15 modules at two-module
concurrency, including the real CLI test that edits source and corrupts evidence
while requiring the saved checkpoint to remain byte-identical. A further 29
proof-boundary/architecture tests passed, including the existing missing-criterion,
fabricated-event, blocking-finding and changed-human-review-evidence refusals.
All 352 dashboard tests and 26 standalone Node scripts passed. All 54 fake
scenarios passed; the one live-model-only scenario was skipped as designed.

The real HTTP/page browser matrix passed five lifecycle/freshness states at
1440x1024, 1024x768 and 390x844. Desktop and phone captures were inspected. The
supervisor confirmed root reaping and no live owned descendants. These are
fixture-based functional captures, not independent Figma acceptance. A final
caption-only edit clarifies that current evidence supplies the checkmarks.

Failed receipts are retained: a fixture initially changed a report after sealing
its human-request token; the production authority check correctly refused it.
The fixture now seals last. Two older display tests incorrectly counted raw saved
results; they now exercise both inspected and uninspected records. The additional
stale-result assertion exposed a real render-cache bug: the Work pane omitted
verification changes from its fingerprint. That bug was fixed and the full
dashboard/browser checks passed afterward.

The earlier full-branch gate remains failed and requires integrated rechecking.
Its exact unchanged worker-activity assertion passed on isolated recheck; this
does not establish the other failed modules or make that old receipt green.
No merge, install, issue closure or user artifact acceptance is claimed.

## Batch 8 — bounded live draft updates (#21)

The Requirements role still responds to every saved answer. The independent
Planner schedules the first draft and then one update per three additional
answers. A chat-only **Update draft now** action can release the latest held
revision early. It is bound to that exact turn/revision and an idempotent request
ID; it neither adds an invented user message nor approves or starts a build.
The saved hold survives restart without spending a call. A released intent uses
the existing delivery lease, recovery and immutable role rules. Uncertain
provider outcomes remain non-retryable.

Each draft retains the message IDs, requirements revisions and short human
excerpts that produced it. A prior usable draft stays visible while an update
is batched, running or failed. The rail distinguishes these states and labels
the content as a draft; project handoff requires the latest fresh structured
result. Existing independent plan review and approval are still mandatory.

The deterministic 10-answer test produces four draft calls and ten Requirements
replies, retaining every answer and source attribution. Seven new store tests
cover restart, stale targets, archived/attached conversations, duplicate refresh,
safe worker failure and superseded queued work. The prior real CLI regression
for #25 still passes: recorded answers re-evaluate planning readiness without
implicitly approving anything.

The final browser matrix passed at 1440x1024, 1024x768 and 390x844. It exercises
batching, explicit refresh, duplicate clicks, provenance, reload, preserved
unsent text, safe failure/retry and uncertain-delivery refusal. It also caught a
real navigation bug: the previous conversation's draft dialog remained open
when hash navigation selected another conversation. Navigation now closes that
obsolete dialog, and the test keeps the dialog open when switching to verify
the repair. The supervisor confirmed no remaining owned workers. Desktop and
phone captures were inspected; these are functional fixture checks, not Figma
acceptance.

All 359 dashboard Python tests and 27 standalone Node scripts passed on the
final source. The handoff fixtures now explicitly refresh a held draft and bind
their stub Planner report to the latest human turn, rather than the later
assistant reply. The production exact-turn and stale-handoff gates were retained.
Historical failed browser and suite receipts remain saved. The full canonical
and fake gates are still running; no complete-branch PASS or issue closure is
claimed by this entry.

PR #305 was merged externally at 378c9f7a. Its published head ends at 74a08e6a;
the later local batches require a separate follow-up PR and publication approval.

## Batch 9 — durable deletion recovery (#60)

Deletion now records each completed filesystem/Git step durably. A partial retry
cannot delete a path or same-head branch recreated after an earlier removal.
A lost branch-removal receipt is reconciled only when absence is verified; an
existing branch remains blocked. Git inspection failure stays a partial result.
The `deleted` and `remaining` lists describe current reality, while the retained
completion ledger records historical actions. Failed discovery cleanup remains
retryable without claiming that an already removed branch still exists.

The public dashboard snapshot exposes compact incomplete-deletion receipts so
an operator can reach the original request from Archived even after the task
folder is gone and the server restarted. Blocked confirmations remain blocked
across reload. The dialog lists the task, workspace and exact selected paths;
a short per-preview confirmation code replaces retyping every absolute path.
It does not expand scope or weaken stale-preview, ownership or liveness checks.

Nineteen real HTTP/Git tests passed, including seven new interrupted-cleanup and
recreated-resource cases. The integrated real browser matrix passed at desktop,
tablet and phone sizes: Archive cancel/Undo, Delete cancel, exact selected scope,
wrong confirmation, preserved siblings/project, stale/live/uncertain refusal,
server restart, retry, retained recreated branch and no rediscovered deleted
workspace. Cleanup verified no live owned workers. Phone/desktop captures were
inspected. No real user task was deleted. Historical failed browser receipts
remain retained, including test-harness selector/navigation failures.

## Batch 10 — setup inside chat (#36)

A first empty dashboard opens a saved setup chat. A persistent Setup entry
reopens it; prerequisite checks and explicit project actions stay in the chat.
The public `doctor --json` supplies local diagnostics. Missing prerequisites
receive curated instructions, and a failed or malformed check never becomes
ready. Raw diagnostic output is not shown or stored. OpenCode version/catalogue
availability is explicitly not account authentication; accounts remain labeled
unverified and sign-in stays in the provider's own tool. No installer, login,
credential capture or model request is performed by setup.

Explicit new-project creation saves a private folder, initializes Git and makes
an empty first commit. Existing attach requires a clean committed repository.
A durable idempotent request binds the path, mode and validated role settings.
Partial failures preserve the folder and refuse uncertain/changed ownership.
The existing per-role controls supply settings for the new scoped conversation;
existing sessions retain their routes. Navigation restores those controls to
the original new-conversation form, preserving the selected values.

Fourteen HTTP/Git tests passed, including dirty-project preservation, duplicate
requests, partial creation, path escapes, role authority and credential
non-retention. The integrated browser matrix passed at 1440x1024, 1024x768 and
390x844, with missing dependencies, recheck, committed projects, empty scoped
chats, selected model settings, dirty refusal and no secret in dashboard storage.
The supervisor confirmed no remaining owned workers. Initial browser testing
found that default navigation populated the hash before first-run detection;
the fix uses the initial URL. The full dashboard suite also exposed an older
empty-conversation API incompatibility, which was repaired without changing
legacy saved routes. The final dashboard gate passed all 380 tests.

## Integrated test status and existing cleanup repair

The two-worker full gate ran 3,027 tests in 224 modules in 3,825 seconds. Four
modules failed. Four individual CLI cases exceeded their unchanged 60-second
deadlines; all four passed unchanged on serial recheck in 39.962 seconds. The
other failure was `PermissionError` from process-group cleanup after the oracle
leader had been reaped. This branch adopts the existing PR #309 repair at
`bf8e5b85fd572a76e5aea5a5132a494dea94ce04`, including its required unchanged
`autocode_grader_process` supervisor, absent from this older branch. The adopted
repair passed 53 process/oracle/architecture tests. Its source is attributed to
that existing repair; no duplicate defect fix or merge is claimed.

The final integrated dashboard gate passed 380 tests. Twenty-six standalone
Node scripts passed in the aggregate gate; the archive harness then needed to
load the newly called production deletion-history renderer, and passed with all
its original assertions. All 54 offline scenarios passed; the one live-model
case remained skipped as designed. These targeted and fake receipts do not
convert the earlier failed full gate into a passing receipt. A fresh final full
gate and independent visual review remain required before completion/merge.

## Historical visual observations (#262)

The two earlier observations are explicitly reconciled against the retained
later Sol47 review and `m3-visuals-terra03/desktop-building-work.png`:

- Sidebar under an extra full-width header: repaired. The later 1440x900 capture
  starts the sidebar at the top of the page and puts the workspace header beside it.
- Verbose saved-status content above current work: repaired. After the artifact
  switcher the later capture leads with Now working, then the live checklist;
  the larger saved-status block follows them.

The inspected image still matches its manifest hash
`921e45307bb2dfe936ee4960343e8481cd1eef2d8bdecd206377228b9073b6c8`.
The independent Sol47 receipt is retained under iteration 047, archived Sol01,
and has hash `d3bd8eff390eb745dcf00a37f56edda95bd4495e9519b5b3bdfb8469c12c730b`.
Its accepted comparison used the native 1440x900 capture at the reference export
scale of 1024x640. The separate 1024x640 CSS viewport was supplementary only.
These historical repairs were not waivers. Current assets have changed since
that receipt, so it is not current whole-branch Figma acceptance.


## Integration and repair history (2026-10-04)

The follow-up integrates current master without replacing its canonical role,
recovery and visual-check modules. Shared role serialization is deterministic.
The approved sidebar, header, Work hierarchy and six empty-conversation states
are implemented with the exported connection asset. Native capture records bind
served HTML/CSS/JavaScript, source, viewport and references; a 1024px browser
viewport is explicitly supplementary to the 1440px reference canvas.

Independent technical review exposed removed-project and folder-replacement
edges. Empty and linked worktree conversations now honor root removal. New
scoped chats retain a folder identity; older chats require explicit confirmation
inside chat before dispatch. Confirmation is bound to the displayed Git folder,
refuses changed or removed folders and preserves messages. Known replacements
cannot be rebound silently. Both provider paths recheck scope before launch.

Focused proof passed: setup HTTP/Git 19 cases, project/worktree removal 9 cases,
project scoping 15 cases, console/control serialization 38 cases, and architecture
4 cases. The setup browser matrix passed at desktop, tablet and phone, including
legacy confirmation without a provider call. All six scoped-chat screens passed
with draft persistence and zero model dispatch. A real scripted-provider to-do
scenario reached TASK_COMPLETE and passed all ten oracle checks; its 16 saved
transcript messages, including approval and completion, were visible in the real
public dashboard at desktop and phone without inspecting the private save file.
This is offline pipeline/UI evidence, not live-model reliability.

Earlier full candidate gates failed and remain failed receipts. The causes
included a mock DOM missing a newly used method, omitted disclosure keyboard
focus, an exact status-field expectation missing an additive field, and a new
project guard performing slow/full proof inspection before the busy-action
check. Their repairs preserve the assertions. A final test initially sent fields
outside the checkpoint endpoint's schema; its corrected valid payload retains
the project-removal rejection assertion. The completed gates and independent review below supersede that pending
verification status; the failed receipts remain retained locally.


## Final local verification and independent review (2026-10-04)

The tested integration commit is `fa9ea034599293c8c4e0cad44024c46f48ad4902`,
including upstream through `d0cd86c2`. Its full canonical gate passed 3,302
cases across 238 modules, with 20 explicit skips (13 live-model cases, two
optional pytest cases and five separately gated Playwright cases). The scenario
harness passed all 179 cases. The fake catalog reported 54 PASS, one
NOT_EXERCISED resolver path despite passing completion oracles, and one skipped
live-model Investigator. Those two catalog limits are not successful coverage.

Final bounded fixes followed that baseline: a polling focus/scroll repair,
completed-worktree deletion (#342), and canonical planning names (#343).
The final source passed the changed-code gate (20 tests across three modules),
all 405 dashboard Python tests, all 27 standalone Node scripts, and another
complete fake-catalog run with the same explicit coverage limits. Source hashes
were checked before/after each gate; no running test observed a source edit.
The broad baseline is not represented as a full-suite run after these final
fixes. PR CI tests the submitted merge candidate separately.

All 14 real-browser matrices passed on the focus-fixed candidate. After the
last two bugs were repaired, the affected deletion matrix passed again with
actual delivered detached Git worktrees at desktop, tablet and phone sizes.
Fresh full and scoped captures cover all 18 approved states, plus one separate
1024px supplementary viewport. Independent review checked image, reference,
served-asset and source bindings, inspected changed images, and accepted the
final visual inventory. Runtime project names, timestamps, model routes, counts,
diffs and preview content stay truthful rather than copying illustrative text.
An extra exact-revision approval option and a secondary completion Checks action
retain capabilities; this is visual acceptance, not a pixel-identity claim.

The deletion regression first reproduced the refusal after real delivery.
Ownership is now bound to the exact task branch, registered worktree, completion
and delivery provenance, and the displayed preview. Git's branch deletion keeps
its checked-out-worktree guard; a prepared reference-transaction hook checks the
confirmed tip while locked and rechecks ownership, preserving any existing hook
and its refusal. Tests cover changed tips, a competing checkout, packed refs,
custom hook refusal, partial cleanup, lost responses and recreated resources.
The initial hook incorrectly assumed Git always supplies the old OID; the
retained failure led to checking the real ref while the prepared transaction
holds its lock. The corrected 29-case endpoint gate passed. Arbitrary external
Git checkout operations remain subject to Git's normal concurrency limits.

The role regression reproduced wrong speakers in legacy/V2 reports and
clarification replies. Reports and the browser now share the canonical job
mapping; V2 names no longer pass through incompatible legacy aliases. Historical
reports, model settings and execution routes are unchanged. The focus regression
forces a real polling refresh after scrolling to Retry/Resume and verifies that
focus, scroll offset and the clickable action survive DOM replacement.

Independent technical review found no remaining material blocker in these
bounded fixes; independent visual review accepted all 18 approved states.
Evidence and failed/superseded receipts remain local rather than being committed.
The installed app, user projects, active providers and unrelated runs were not
used as destructive test fixtures. Merge and installation remain review steps.

## Merged-source completion checks (2026-10-04)

The review branch incorporates master `40297be3`. The four merge conflicts retain
upstream typed cleanup failures and source-recovery constraints as well as the
UI work. Independent review checked each resolution.

Integration caught a real direct-CLI dispatch regression: a package-only import
of `units.common` failed in 16 Resolver tests. The retained failing gate ran
2,105 cases / 146 modules and isolated that failure. The correction uses the
existing package/direct import pair without changing sandbox policy. All 79
focused Resolver, unit-flow and architecture tests then passed; the fresh
2,105-case / 146-module gate passed on unchanged source.

The new public `recover_source` state now offers inspection and feedback without
Resume. Both paused states are covered, and actual browser requests to forge
Resume are refused without changing the saved fixture. The regression fixture
uses the Investigator stage recorded by the real failure path.

Independent image review also found that project labels could be borrowed from
another task in the same project. Labels now use the stable project identity
already used by sidebar groups and scoped creation. The regression failed before
the fix; multi-task selection, worker-liveness/reordering, removal confirmation
and browser polling checks now verify the distinction between project and task.
All 27 standalone Node scripts pass after this bounded UI correction.

The merged dashboard Python gate passed all 405 tests. Fresh affected browser
flows and native Figma-state captures qualify the final UI source separately;
no collection count or source hash is treated as visual acceptance. Full
canonical/harness baseline, skipped live checks and earlier failures above
remain explicitly recorded. Raw local evidence remains untracked.

The final fake catalog returned zero with 54 PASS, one NOT_EXERCISED Resolver
route (the task passed its completion oracles without entering Resolver), and
one explicitly skipped live-model Investigator. The final bounded UI correction
passed 25 project/sidebar/served-asset Python tests, all 27 Node scripts, and five
affected actual-browser flows including persistent chat with 200% text sizing.
Independent review accepted all 18 approved states after inspecting the new
project labels and recovery-role evidence. The four-file transfer was verified
byte-for-byte across all 1,428 manifest paths, so the accepted candidate captures
bind the submitted implementation. All execution receipts report unchanged
source. CI for the submitted commit remains a separate verification gate.

## CI repair: classic scrollbar space (2026-10-04)

Submitted-head CI run 37189968053 found three failures. Two were stale tests
after the master integration: the standalone CLI test assumed only Builder
could write, although judges now write operational evidence under source guards;
the publication test hand-built a namespace that omitted a new CLI option.
The first now checks the explicit stage policy, and the second uses the real
argument parser. The two complete modules passed all 34 tests locally.

The third was an actual tablet layout bug. Linux scrollbar gutters reduced the
Work pane's available space: the waiting freshness value ended at y774 past its
y756 clipping edge. A new browser regression reserves only missing classic
scrollbar space and reproduced that exact failure locally. Tablet-only spacing
now leaves the value fully visible at y750. Fact text sizes, 44px controls and
existing visibility assertions are preserved; desktop and phone rules stay intact.

Fresh unchanged-source gates passed: 38 cases across three changed modules,
all 14 dashboard catalogue cases including the complete 21-screen matrix and
the new scrollbar regression, all 27 standalone Node scripts, and native
workspace captures (12 primary states plus one supplementary viewport).
Independent review inspected the repaired tablet and native desktop/phone
images and found no material blocker. The repeated full fake catalog returned
54 PASS, one NOT_EXERCISED Resolver route and one live-model SKIPPED case.
Rendered forced-colors behavior remains unverified; no live-model qualification
is claimed. Failed receipts and screenshots remain local. The subsequent PR CI
run separately qualifies the submitted repair on Linux.

## CI repair: actual keyboard focus (2026-10-04)

The next submitted-head CI run, 37192129033, passed the 3,441-case core
gate, 179 scenario-harness cases, all 405 dashboard Python tests, visual
comparison controls and nine browser flows. The last scoped-start flow failed
its composer outline assertion. The unchanged test reproduced that failure in
an owned Debian Linux browser fixture.

The probe combined unknown focus after reload with programmatic focus and an
immediate style read. It now anchors at the textarea, uses actual Shift+Tab to
leave and Tab to return, verifies active keyboard-visible focus, then reads the
outline separately. The existing outline, minimum 2px width and shadow checks
remain. No application CSS or timeout was changed. All six desktop/phone project
cases pass with a solid 2px outline on both local macOS and Debian ARM64 Chromium.
The manifest records each focus measurement and binds unchanged source; there
were no provider calls or browser errors. Independent review accepted the test
correction. GitHub Ubuntu x64 remains a separate submitted-head gate.

The changed-code gate passed all four architecture cases. The full fake catalog
again passed 54 scenarios, with one NOT_EXERCISED Resolver route and one skipped
live-model Investigator; source remained unchanged during both gates. Original
failures and browser evidence remain local. The disposable Linux container was
removed after exporting its evidence; user projects and unrelated containers
were preserved.

## CI repair: deletion versus dashboard polling (2026-10-04)

CI run 37194243063 passed core, harness, dashboard and visual gates, then
refused tablet deletion because a live process referenced its workspace. A
controlled public HTTP reproduction held the dashboard's own real status
subprocess open: exact confirmed deletion returned the same HTTP 400 while
preserving all files. This was a coordination bug, not grounds to weaken the
external-process guard.

Workspace-scoped dashboard JSON commands now share a reentrant gate with
deletion preview and the stopped-check/removal section. Existing reads finish
before those sections; another workspace remains independently inspectable.
Registry-wide reads stay independent. The gate is local to this dashboard
process, while writer locks, live-worker checks, external-process refusal,
exact confirmation, content fingerprints and Git ownership guards remain
unchanged. A same-workspace request can wait behind an existing command.

The public HTTP regression failed before the repair and passed afterward. It
controls the overlap with a socket rather than a sleep, covers both preview
and confirmed deletion, and proves that a separate live process still blocks
both operations. Independent review accepted the lock ordering and regression.
Fresh unchanged-source gates passed all 407 dashboard Python tests, all ten CI
browser flows, four architecture tests and the full fake catalog (54 PASS,
one NOT_EXERCISED Resolver route, one skipped live-model Investigator).
Submitted-head Linux CI remains the separate final delivery gate.
