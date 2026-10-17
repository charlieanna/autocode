# PR #1425 editor and live qualification

The owner requested a Kilo-like sidebar, plan feedback and same-run follow-ups,
and required both an actual editor flow and a live token-plan task before ready.
The editor flow and live greeting intake passed. PR #1425 was merged in another
operation on October 16 at 7:27 PM PDT, before the final greeting result. The
post-merge Requirements fix is tracked in a draft follow-up. **Full live
build/test/completion qualification has not passed; keep that follow-up draft.**

## Implementation evidence

The sidebar exposes the core's additive chat projection. It preserves messages,
completed replies and drafts, renders Markdown safely, and binds actions to the
displayed checkpoint. Selected composer and scroll components are adapted from
Kilo revision `1552877e82ceaecc79a9fd4be7859518745e6f56`; the extension's
`THIRD_PARTY_NOTICES.md` records provenance and modifications. This is component
reuse, not a fork of Kilo's provider/runtime or a claim of complete UI parity.

Focused checks on this change:

- `npm test` in `vscode-extension`: 72 passed, including controller/webview chat,
  stale approval, same-run follow-up, Markdown safety, reading-position retention,
  composer sizing, local undo and IME/newline behavior.
- `npm run typecheck` and `npm run build`: passed.
- All-role model display: `python -m unittest tests.test_roles tests.test_quota_route
  tests.test_usage tests.test_taskrun.RunViewTests tests.test_architecture` passed
  91 tests in 14.378 s. The public status regressions cover roles before launch,
  mixed providers, unspecified variants and actual stage history after a route change.
- Recognition wording: `python -m unittest tests.test_progress_view` passed
  27 tests. The active headline says **Understanding your request**, while
  finished/stopped states and the detailed stage identity remain accurate.
- `tests.test_ci_scope` verifies that model/usage/role/progress contract changes
  select the extension's Node checks as a public CLI consumer.
- Repository venv, `python -m unittest tests.test_chat_view
  tests.test_taskrun.RunViewTests tests.test_ci_scope tests.test_ci_test_selection
  tests.test_run_state tests.test_architecture tests.test_follow_up`: 111 passed
  on macOS; the one supervised CLI case was refused by host admission. That case
  was then run successfully on supported Linux, as below.
- Supported Linux, `BUILD_AUDIT_ARTIFACTS=/tmp/pr1425-fake-chat python3 -m unittest
  tests.test_chat_cli`: passed in 49.183 s. This drives the real public CLI with
  the offline provider: feedback saves without model traffic, the revised plan
  rejects the old token, a fresh approval completes, and a follow-up/reattachment
  retains the same run and earlier reply.
- Same Linux host, `python3 -m unittest
  tests.test_follow_up.FinishedRunCliTests`: passed in 27.039 s.
- Ruff and mypy on the changed Python modules: passed. No new architecture cycle
  or unnamed run-state key was introduced.

The initial offline attempts exposed fixture/setup issues: its fixed requirements
corpus did not cover an arbitrary new feedback clause; adaptive reruns were not
part of that fixture; Git on the macOS bind mount intermittently refused ownership
or `ls-files`; and the test initially used an unsupported `TaskRun.attach`
argument. The final test uses a supported fixture clause, fixed planning and
container-native temporary repositories. Failed artifacts were retained, including
the 7.497 s, 44.364 s and 51.736 s attempts; a 48.432 s pass preceded the final
completed-reply assertion. None is counted as live-provider evidence.

## Actual VS Code flow

VS Code 1.142.0 ran the bundled extension in an Extension Development Host,
launched with `code --new-window --extensionDevelopmentPath=EXTENSION
--disable-extensions PROJECT`. No jsdom substitute was used for these checks.

The native editor checks covered workspace-bound start; actions on another run;
complete plan details rendered as inert text; stale approval refusal; answer
draft/focus retention while polling; replacement-model selection; and cancel/
confirm for person-only recovery. The check found and fixed the incorrect tree
event name and the detail panel's failure to restart polling after being hidden.

The sidebar then completed a fixture conversation: initial request → plan
feedback → revised plan → approval → completion → follow-up in the same run →
fresh approval. The final Kilo-adapted bundle restored the selected conversation,
Markdown reply and unsent draft after closing/reopening the window. Polling showed
789 tokens without losing the draft. Enter sent another follow-up into turn 3;
a stale revision-6 approval was refused after revision 7 arrived, and only an
explicit click after reviewing revision 7 advanced. Actions use environment-bound
approval tokens, never literal tokens on the command line.

This public-contract fixture is deterministic editor evidence. The separate Linux
CLI test establishes real core behavior; neither establishes live-model success.

The owner's follow-up layout report exposed a split-pane problem: the always-open
Runs pane reserved the lower half of the sidebar, placing the composer near the
middle of the window. Chat and History now occupy the sidebar one at a time.
In the actual VS Code host, the composer stayed at the bottom after reloading;
History replaced the conversation, and Back to Chat restored its unsent draft.
A navigation regression verifies that only one pane is visible and browsing
history never advances a run. Extension tests, typecheck and build passed for
this UI-only follow-up; no additional live-model run was needed for the layout.

The model/usage follow-up adds the actual attempt's model and reasoning variant
beside the composer, with run-wide tokens and separate reported/historical cost
figures. It fixes the original scalar-token assumption: the real public view has
structured counters. The 68 extension tests cover partial/unknown quantities,
cache/reasoning inclusion, separate cost bases, actual launch-route variants,
parallel attempts, and polling without disturbing a draft or expanded details.
Typecheck and build passed. In VS Code, a read-only preview using the retained
Kilo public accounting snapshot displayed MiMo/high, at least 149,147 tokens,
the partial historical estimate of $0.0940, and unavailable reported cost. The
expanded breakdown kept the composer at the bottom. This reused snapshot is UI
evidence only, not a new live run or a successful live qualification.

Model details now live in an expanded-by-default **Model & usage** panel pinned
above the composer. Its collapsed summary retains the model, variant and usage;
the full model route and provider remain available without opening run details.
In VS Code, collapsing the panel, opening History and returning to chat preserved
the collapsed state; expanding restored all rows. The saved preference also
survives draft edits, polling and webview recreation in the regression test.

The latest expansion lists every configured role and separately groups actual
attempts by job, model, provider and variant. It retains older models after a
route change and does not imply that an unstarted role has run.

The owner then requested updating the open editor while preserving its chat.
The deployed UI uses runtime revision `62ba45078cc95a2bfb3c91657170cce3a86ab916`
with the open demo's local controller and read-only polling fixes preserved.
That combined deployment passed 72 extension tests, typecheck and build. The
additive model/progress projections were updated in the demo's CLI; existing
requirements-flow edits and saved run data were preserved. Original deployment
files were backed up under ignored scenario artifacts.

In the actual Extension Development Host, History reopened the same conversation
`20261017-192528-vscode-5b9bed` with its original `hi` message and pending
Requirements question. The expanded panel exposed all eight configured roles,
full GLM/MiMo model IDs, Kilo provider, high/medium variants, recorded model
history, 68,263 tokens and a $0.0376 historical estimate. The composer remained at
the bottom. Before/after public status snapshots had identical chat messages and
`WAITING_FOR_USER` status. No message, approval, retry or model launch was issued.
This verifies the new UI on the preserved chat. The controller's greeting
qualification is recorded below; full live build/test/completion remains pending.

## Real greeting conversation

The earlier development window was wired to `fixture-cli.py`, which returned a
sample approval for every new message. That window was closed. The replacement
workspace uses a forwarding bridge to the real CLI in the task-owned Linux
container, with Kilo CLI 7.8.3 and only the owner's ZAI/Xiaomi token-plan logins.
The backend remains connected for the user's next reply; no credentials are
stored in the repository or extension settings.

New sidebar chats pass `--joint-planning --spec-first`. The recognizer sends a
greeting without an engineering outcome to Requirements. A clarification-only
handoff can ask for the goal without inventing behaviors, tests or source
citations. Outstanding questions come before the requirements review; answering
them refreshes Requirements before any plan. Status polling now puts `--status`
first, matching the core's read-only process grammar instead of appearing to be
a competing writer.

**PASS**, actual VS Code Enter → confirmation → Kilo → real Requirements:
run `20261017-192528-vscode-5b9bed`, **214.058 s wall**. Input: `hi`.
The final native sidebar, including the latest model panel, showed:

> Hi! What would you like to build or change? A short description of the outcome you want is all I need to start.

The public checkpoint is `WAITING_FOR_USER`, `needs.kind=answer`, with one
question and `displayed_plan=null`. There are exactly two task-stage event
streams: `recognize-workflow-01.jsonl` and `requirements-gather-01.jsonl`.
Both used `zai-coding-plan/glm-5.3`, medium effort, through provider `kilocode`,
and exited 0. No report repair, Planner, Plan Reviewer, Builder or Tester ran.
The model preflights used MiMo/low (24.389 s) and GLM/low (13.497 s);
recognition took 132.182 s and Requirements 24.581 s. The later-stage model
assignments and role pins remain those in the full command below.

Command: `autocode "hi" --in-place --no-chat --joint-planning --spec-first
--workspace "$PROJECT" --new-run-dir "$RUN"`, with the same provider, per-role
model/effort flags and pins listed below, and bounded overrides
`--max-seconds 900 --max-stage-seconds 300 --max-idle-seconds 300
--max-tool-seconds 300 --max-cost-usd 3`. The operator confirmed the live start
in VS Code. Native accounting records four provider requests, 68,263 inclusive
input/output tokens and a historical comparison estimate of $0.037568;
provider-reported cost and the token-plan invoice are unknown.

Redacted bundle manifest SHA-256:
`98b00a47111ad6dfb7317e849acaa277241f6874a8eced572e1d637b9cb5ce76`.
Artifacts remain ignored under `.scenario-runs/pr1425-real-requirements-20261016/`.

Preserved editor attempts: `20261017-191312-vscode-0f869b` refused before any
model call in 2.527 s because polling looked like a writer;
`20261017-191542-vscode-a46471` reached the question in 116.657 s after a
format repair (the new prompt initially said null instead of the schema's empty
string); `20261017-191916-vscode-2c7689` rejected an empty citation list, then
was explicitly interrupted during investigation at 283.588 s once that defect
was identified. The final run above exercised the corrected prompt and intake
gate with no repairs or source inspection.

Verification for this follow-up: 72 extension tests, typecheck and build passed;
145 focused Python checks passed across the chat, requirements, workflow,
architecture, CI selection, prompt and adaptive-planning modules. Only five
Requirements prompt golden entries changed, verified to differ solely by the
reviewed greeting-policy paragraph. Ruff and mypy passed. Supported Linux
`python3 -m unittest tests.test_chat_cli.ChatCliTests.test_greeting_asks_for_the_outcome_and_a_reply_returns_to_requirements_before_any_plan`
passed in 26.202 s on the integrated PR code and 17.248 s after rebasing the
follow-up onto merged master: a greeting asks for an outcome even
in an existing workspace, a real saved answer refreshes Requirements, and no
plan is displayed or Planner dispatched. The seven existing
`tests.test_requirements_review` checks also passed. Failed offline setup/schema
attempts were retained; the final citation regression first passed in 21.657 s
before integration.

This qualifies greeting intake. Full live build/test/completion remains the
separate unfinished readiness condition below.

## Earlier full-task token-plan attempt

The native VS Code Start action drove the real CLI through a Linux bridge, using
Kilo CLI 7.8.3. The runner source was `51383962c65d66c88364e5b6eb5e00373c7fc491`;
that attempt did not qualify the later chat projection/UI changes. The greeting
check above supplies the bounded intake evidence for this follow-up.
Only the owner's ZAI and Xiaomi token-plan routes were enabled. Kilo Gateway and
ChatGPT routes were not used.

The CLI invocation was `autocode "$BRIEF" --workspace "$PROJECT" --in-place
--new-run-dir "$RUN" --no-chat`, with these advancing options:

```sh
--provider kilocode --joint-planning \
--requirements-model zai-coding-plan/glm-5.3 --requirements-reasoning-effort medium \
--glm-model zai-coding-plan/glm-5.3 --glm-reasoning-effort high \
--plan-reviewer-model xiaomi-token-plan-sgp/mimo-v2.6-pro --plan-reviewer-reasoning-effort high \
--astra-model xiaomi-token-plan-sgp/mimo-v2.6-pro --astra-reasoning-effort high \
--terra-model xiaomi-token-plan-sgp/mimo-v2.6-pro --reasoning-effort medium \
--sol-model zai-coding-plan/glm-5.3 --sol-reasoning-effort high \
--completion-model zai-coding-plan/glm-5.3 --completion-reasoning-effort medium \
--resolver-model xiaomi-token-plan-sgp/mimo-v2.6-pro --resolver-reasoning-effort high \
--investigator-model zai-coding-plan/glm-5.3 \
--pin-model-role astra --pin-model-role terra --pin-model-role sol --pin-model-role completion \
--max-seconds 3600 --max-stage-seconds 900 --max-idle-seconds 300 \
--max-tool-seconds 900 --max-cost-usd 10
```

The brief asked for a standard-library greeting CLI, argument-count error
handling, regression tests and a README. Run:
`20261017-173830-vscode-e36f89`, approximately 21 min 34 s wall time including
the explicit pause, 2026-10-17 00:38:30–01:00:04 UTC.

Job recognizer and Requirements completed on `zai-coding-plan/glm-5.3`.
Plan Reviewer reached `xiaomi-token-plan-sgp/mimo-v2.6-pro`; Builder, Tester and
Completion Reviewer were not reached. Both route preflights had succeeded.

The initial tool bound was 180 s. Reviewer attempts 1 and 2 stopped after about
199 s and 198 s; the third was explicitly interrupted after about 131 s. The
outer Kilo launcher was conservatively charged as a tool. The qualification
configuration was corrected to the existing 900 s stage bound, preserving the
3600 s/$10 task caps and every route. Through the editor, the interrupted stage
was abandoned and the paused run resumed. Attempt 4 then exited 1 with
`APIError: Network connection failed` / `network connection was lost` in
`iterations/001/plan-challenge-04.jsonl`. It stopped at operational recovery;
there is no build/test/completion verdict. No paid baseline or alternate-provider
sweep was launched.

The preceding OpenCode attempt, `20261017-173312-vscode-f5d445`, failed ZAI
authentication after roughly 149 s. It was retained and superseded when the owner
requested Kilo. The original PR's reported 113-minute/162-tool attempt remains
historical failed evidence and was not reclassified as passing.

All task artifacts stay under ignored `.scenario-runs/pr1425-editor-live-20261016/`.
The redacted Kilo bundle's manifest SHA-256 is
`cfec6db8df6503b09002aab519ee4944a360d8f3662a5510d22df184cd33dfd0`.
No logs, credentials or bundles are committed.
