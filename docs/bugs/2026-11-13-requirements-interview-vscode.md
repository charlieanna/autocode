# Requirements interview: actual VS Code and live model check

On November 13, 2026 (America/Los_Angeles), the installed AutoCode Task Chat
extension was exercised in an actual VS Code window, using native UI automation.
All initial messages and clarification answers were submitted through the extension.
This was not a mock webview or a CLI-only conversation.

## Candidate and setup

The disposable workspace was `/private/tmp/autocode-requirements-ui-test`.
Its workspace setting pointed `autocode.cliPath` at
`/private/tmp/autocode-requirements-ui-cli`, which launched the repository venv
interpreter against `/private/tmp/autocode-requirements-ui-runtime/tools/autocode.py`.
The runtime copied the tools from interview-guidance commit `c84182f07` (#3378),
with `autocode_requirements_handoff.py` from `731c94fd7` (#3377). No provider
or validation bypass was used. The installed extension was the existing master
bundle (the earlier installation receipt named `2c8e96939`), not a changed extension.

The existing Kilo route and saved stage choices were retained. The only model
stage reached was Requirements: `zai-coding-plan/glm-5.3`, reasoning `low`,
through `kilo run --auto --dir WORKSPACE --model zai-coding-plan/glm-5.3
--variant low --format json --agent RUN_AGENT`. Both runs used no cost cap.
The CLI public status and native accessibility tree were checked independently.
No plan was approved, and no Builder was launched.

## Observed conversations

Run `20261113-004106-vscode-237f9a` started with:

> We need to make AutoCode work better. We keep finding bugs with each new run.

The model asked for the most recent disappointing run, what happened and what
was expected. The public view carried one unanswered question with `options=[]`
and `proposed_default=""`. The extension displayed a free-text field and accepted
the answer. This first turn required no report repair.

The answer described premature menus in AutoCode. The next reply reflected that
example, but the run then correctly stopped for repository scope: this disposable
project did not contain AutoCode's implementation. This is evidence of receiving
the answer, not a successful within-repository interview follow-up. The two
Requirements attempts cost an estimated $0.0451 and took 1m38s of model stage time
(1m52s displayed total).

Run `20261113-004431-vscode-c15870` instead asked for a chat in this project that
understands the person's needs, while explicitly leaving its conversation design
unclear. Requirements asked for a previous misunderstanding and what it failed
to ask. The answer said choices sometimes help and sometimes mislead, and
explicitly rejected assuming a blanket ban.

Requirements then asked:

> Can you recall one time a set of options steered you toward the wrong answer,
> and one time having options genuinely helped you decide? What was different
> about those two moments?

Both the final public view and the actual extension showed this saved follow-up
waiting for a free-text answer. Its options and default were empty; planning had
not begun. This demonstrates an answer-dependent probe rather than a forced menu
or immediate confirmation. Final displayed cost was an estimated $0.0464 for
61k tokens, 2m15s of stage time and 2m46s total. Across both chats the extension
showed $0.0914 and 102k tokens; these are model-usage estimates, not invoices.

## Limits and defects exposed

The second run needed two report repairs, one per conversational turn: each
original answer omitted the required `acceptance_tests` field (`$: missing
acceptance_tests`). Each repair succeeded automatically and the run returned to
an answerable question without operator intervention. Thus the interview behavior
passed, but **repair-free intake did not pass**. #3377 fixes a blank outcome's
semantic rejection; it does not fix an omitted required field. No schema check
was relaxed during this verification.

The extension also displayed a question in the assistant message and repeated it
above the answer field. These were defects in the initial candidate. The follow-up
fix and its verification are recorded below; the failed attempts remain intact.

## Retained evidence

The redacted bundles are retained locally in the workspace's `.autocode/bundles/`:

- `initial.tar.gz`: manifest SHA-256
  `ab83f28a186bcc094ba62fc88a93689d8234b9666039ff99e52014d795244f20`.
- `followup.tar.gz`: manifest SHA-256
  `8f59f9e3e627109b0304221499b8abc4dd1c7d850da71754fe3693c65a841599`.

Produced with `autocode bundle RUN --out ARCHIVE --json`. The bundles include
redacted reports, events and state. Replay recordings were unavailable: the
exporter reported that concurrent Builder dispatch recording is unsupported in
v1; no replay result is claimed. The original runs and failed attempts remain
intact. Bundles and raw run output are not committed.

## Follow-up fix

The report loader now supplies `acceptance_tests=[]` only when Requirements has
no existing goal contract, explicitly drafts no behaviors, and asks a nonempty,
unanswered question. It preserves the raw report and records the defaulted field.
An explicit invalid value, drafted behaviors, an answered question, another stage
or another missing required field still fails schema validation. Answering the
question returns to Requirements; this normalization grants no planning authority.

The VS Code answer form omits its visible question only when the current saved
Requirements message already contains that question. The input keeps the question
as its accessible name. Missing, different and still-streaming messages retain the
visible fallback; saving the message updates the form to remove the duplicate.

### Deterministic checks

- The report-loader regression failed before the fix with `missing
  acceptance_tests`. All four regression tests now pass, including default and
  spec-first intake, repair reports, raw-response retention, validation guards
  and return to Requirements after answering.
- 80 focused Python tests passed across `tests.test_requirements_report`,
  `tests.test_requirements_handoff`, `tests.test_requirements_chat`,
  `tests.test_report_schema` and `tests.test_architecture`. One additional
  old-schema/original-stage test was added afterward; the four-test report module
  passed again. No runtime changes followed those passing checks.
- Six focused extension tests passed: saved, streaming, absent and different
  Requirements messages, plus the existing Requirements understanding and fallback
  cases. They run the generated webview script, assert the rendered question and
  accessible input, and send a free-text answer through the named CLI action.
  The new cases fail on the original implementation.
- Five in-memory Python mutations were detected: removing the behavior, goal or
  answered-question guard, disabling normalization, and dropping its evidence
  receipt. Three generated-script mutations were detected: always duplicating the
  question, dropping its accessible label, and omitting the message-completion
  rerender dependency. Mutated code was never packaged or installed.
- TypeScript typecheck, extension build and VSIX packaging passed, as did Ruff
  lint, format checks and `git diff --check`.

Both original failed reports from `20261113-004431-vscode-c15870` were also copied
unchanged to temporary files and loaded through the updated `load_stage_report`
with their recorded schema and unanswered-clarification context. Both supplied
the empty tests list, recorded the normalization and preserved the raw response,
without a provider call. The archived sources remained unchanged (SHA-256
`e21e262f37099a2b638cfe2d47766fd1dd7ca626f0b8e63994bbff16bc8634c7`
and `85c1a265aa097024c8310f9725b6e2b96e4a963a7766fcc545844d4b1e1f9cd2`).
This is captured-report validation, not a full run replay.

### Initial native extension retest blocker (subsequently resolved)

The rebuilt VSIX was installed successfully at 00:58 local time. Its installed
`dist/extension.js` matched the tested build (SHA-256
`cd7b9f402b075a73fca341d607b1ef6dfa65097bbccd24ea12cd77f3e6e950f6`).
The test workspace retained its isolated CLI wrapper and existing model choices.
After invoking Developer: Reload Window in that workspace, the entire VS Code
window became blank. Native UI inspection, closing that test window and
reconnecting to the application timed out, including after resetting UI automation.
No new chat was submitted through the updated native extension at that point. Its
live retest was unverified; the earlier live extension results above were not presented as
evidence that this updated extension passed. Other VS Code workspaces were not
terminated to recover this test.

### Live core follow-up

Because the native UI remained unavailable, a separate public CLI run exercised
the fixed core with the same initial request and nuanced answer as the earlier
second conversation. It retained Kilo and `zai-coding-plan/glm-5.3`/`low`. Start:

```sh
autocode 'I want this project to have a chat that really understands what I need before it starts work. I am not sure what the right conversation should look like.' \
  --joint-planning --spec-first --build-in-worktree --no-chat \
  --new-run-dir /private/tmp/autocode-requirements-ui-test/.autocode/runs/20261113-clarification-fixed-cli \
  --max-cost-usd 0 --max-seconds 240 --max-stage-seconds 120
```

The saved status view's answer command submitted the earlier answer about helpful
and misleading choices, followed by its named Continue command. Two Requirements
attempts completed in 31.51s and 28.87s with **zero rejected reports and zero
report repairs**. The follow-up asked:

> Can you think of a time a set of options or examples actually helped you get
> your point across? What made that moment different?

The final status was `WAITING_FOR_USER`, with `Q2`, empty options/default, and no
goal contract or Planner launch. Both new raw replies explicitly included an
empty tests list, so these live turns establish a repair-free conversation on the
candidate, while the captured-report checks above establish the new omitted-field
normalization itself. Estimated spend was $0.030102 for 26,722 input/output tokens;
no stages were unpriced. This is an estimate, not an invoice. The existing macOS
provider-session boundary remained unavailable; no Linux qualification is claimed.

The redacted `fixed-cli.tar.gz` bundle has manifest SHA-256
`36f02797ff52c15106139d07c30d2cd40a546ff1ed52d8eb7052b09d7548bd98`.
It has the same replay-export limitation recorded above. This CLI result did not
replace the native-extension retest recorded below.

### Completed native extension retest

The test workspace was recovered and reopened in VS Code. A separate temporary
VS Code recovery instance was stopped by its exact process identity; the main
editor process and its other workspaces remained running. Closing and reopening
the disposable workspace activated the installed VSIX without another blank
Developer: Reload Window. No repository change or provider bypass was needed to
recover the editor.

Run `20261113-013251-vscode-5878a7` was started through the actual extension with
the same project-local chat request above. The core's report loader, normalizer,
handoff and three changed prompt resources matched PR revision `7e878a43d`.
Its first question exposed a capitalization edge case: the assistant said
"To ground this, can you walk me..." while the answer field's question began
"Can you walk me...". The visible duplicate check now ignores case. A regression
for this exact difference failed on the previous candidate and passed after the
fix; all five saved/streaming/absent/different variants passed.

The final isolated VSIX was built from that PR plus the case fix. Installed and
tested `dist/extension.js` matched SHA-256
`8e09bc1b9560afdcba8c5f0918df4bf6ce5925d68ad28f10453fd12cb50093d6`.
The restored first question appeared once with a named, editable answer field.
Through that field, native UI automation submitted:

> It offered a menu before I had explained the problem. Sometimes examples or
> choices help me, but sometimes they steer me into the wrong answer. I am still
> unsure when it should offer them, and I do not want it to just assume that
> choices should always be banned.

Requirements reflected the answer and asked:

> Can you think of a time a suggested example or set of options actually helped
> you? What was different about that moment compared to the menu case?

The native UI was observed through completion: the saved message appeared once,
the free-text answer field kept that question as its accessible name, and
"AutoCode is waiting for you" replaced the running state. The public status
independently reported `WAITING_FOR_USER`, empty options and default, no goal
contract, and exactly two Requirements attempts with **zero rejected reports,
zero retries and zero report repairs**. No Planner or Builder was launched.

Crucially, the second raw report omitted `acceptance_tests`. The preserved
`requirements-gather-02.reported.json` lacks that key, its accepted report has
`acceptance_tests=[]`, and its stage receipt records
`defaulted_fields=["acceptance_tests", "access_blockers"]`. This live native run
therefore exercised the normalization itself, as well as the interview and
extension rendering fixes.

The existing Kilo route and Requirements model `zai-coding-plan/glm-5.3`, reasoning
`low`, were retained. The provider command was `kilo run --auto --dir WORKSPACE
--model zai-coding-plan/glm-5.3 --variant low --format json --agent RUN_AGENT`.
The two stages took 34.49s and 33.34s (67.83s combined). The public cost view
estimated $0.030288, with 25,196 input and 1,636 output tokens and no unpriced
stage; this is not an invoice. The run stopped at its second clarification.
This native macOS result does not claim supported Linux provider qualification.

The redacted `fixed-native.tar.gz` bundle is retained beside the earlier bundles,
with manifest SHA-256
`2ce14e58e9a69c94c94fcd9a624b8bc6b4700378604ad780df1dc4c70c493f45`.
It has the same replay-export limitation recorded above.

The seven prompt-rendering snapshots affected by the interview instructions were
updated after comparing the rendered text against master. All 11 prompt-rendering
tests passed. Typecheck, build, VSIX packaging, repository Ruff lint/format checks
and `git diff --check` passed for the final extension fix. The native retest
blocker is resolved; earlier failures remain recorded above.

## Review follow-up: competing inputs, spoken context and prompt guardrails

Follow-up testing of `8e09bc1b9560afdcba8c5f0918df4bf6ce5925d68ad28f10453fd12cb50093d6`
against PR #3378 exposed four remaining UX and prompt-routing issues:

1. **Competing inputs & lost spoken context**: for open-ended Requirements
   questions (`options: []`, `proposed_default: ""`, `chat.input.kind: "feedback"`),
   `vscode-extension/src/panel.ts` still rendered `#app .decision-card`
   (`<input data-question>` + `#take-action`) above `#composer`, submitting
   `--answer` instead of `--feedback` and dropping the model's spoken
   `assistant_reply` turns.
2. **Leaked internal `OUTCOME_QUESTION` on greetings**: when a greeting held chat
   open (`holds_chat_open`), the internal `OUTCOME_QUESTION` was not in
   `chat.messages`, so `panel.ts` rendered a competing `.decision-card` containing
   internal prompt text.
3. **Backticked duplicate question text**: `plain()` in `panel.ts` normalized
   whitespace and case, not Markdown backticks, so finite-option questions
   wrapping identifiers in backticks still repeated above `<select>`.
4. **Prompt/CI guardrails & macOS symlink test portability**:
   - `tools/prompts/requirements-gather.md` now instructs open clarifications to
     supply `acceptance_tests: []` directly when no behaviors are drafted and to
     preserve spoken `assistant_reply` continuity across `brief_feedback` and
     `saved_answers`.
   - `tools/prompts/requirements-triage.md` clarifies that concrete examples inside
     a vague request keep `ready: false` while asking one follow-up about that
     example.
   - `tools/ci_test_selection.py` maps `tools/prompts/fragments/autoplanner/`
     changes to its consumers (`tests/test_ci_test_selection.py`,
     `tests/test_requirements_chat.py`, `tests/prompt_rendering_fixtures.json`).
   - `tests/test_requirements_review.py` resolves `TemporaryDirectory` paths with
     `.resolve()` for macOS `/var/folders` -> `/private/var/folders` symlinks.

### Fix & verification

When `chat.phase === 'requirements'`, `needs.kind === 'answer'`,
`chat.input.kind === 'feedback'`, the question (or a `holds_chat_open` greeting)
is already in the conversation thread, and every decision is an open free-text
answer (`options === []`, empty `proposed_default`, no numeric bound),
`vscode-extension/src/panel.ts` omits `.decision-card`, leaving `#composer`
(`--feedback`) as the single input. Finite `options`, `proposed_default` values,
numeric bounds, route/effort picks, and inputless views keep `.decision-card`,
and `plain()` strips backticks alongside `**` when matching saved messages.

- **Deterministic checks**: `vscode-extension` typecheck, `npm test` (`530`
  tests: `512` passing, `0` failing, `18` skipped, including 3 new cases in
  `vscode-extension/test/editor.test.cjs`), `npm run build`, and a new 3-turn
  real-CLI interview test in `vscode-extension/test/live-walk.test.cjs`
  (`walk: a spec-first Requirements interview answers open questions in the
  composer and keeps spoken context`) pass. Python changed-suite checks,
  `tests.test_requirements_review` (18/18), `tests.test_prompt_rendering`
  (11/11), Ruff lint/format checks on `tools`/`tests`/`scenarios`,
  `git diff --check`, and `check_prompt_evidence.disclosure` pass.
- **Isolated VS Code Desktop live verification (`20261113-020148-vscode-f788c5`)**:
  an isolated VS Code Desktop window (`--user-data-dir
  /private/tmp/autocode-vscode-verify/user-data --extensions-dir
  /private/tmp/autocode-vscode-verify/extensions`) ran the packaged extension
  (`dist/extension.js` SHA-256
  `d7815a735cb6e013dc5fbbd464d0622c6b7bcfe4b5973d9b1ccaaea0ab6449b4`) against
  real Kilo `zai-coding-plan/glm-5.3` (`low`). Turn 1 (`33.01s`, `12,049` in /
  `285` out tokens), Turn 2 (`43.59s`, `12,543` in / `814` out tokens), and
  Turn 3 (`101.36s`, `26,323` in / `1,806` out tokens) completed with `0` report
  repairs and `$0.03` estimated spend, settling at `WAITING_FOR_USER`
  (`draft_plan` confirmation). Live Desktop webview DOM checks on Turns 1–2 and
  compiled `ChatView`/`panel` verification of Turn 3's saved `--status --json`
  confirmed `0` `.decision-card`s, `0` `[data-question]` inputs, `#composer`
  active on Turns 1–2, `#take-action` `"Approve understanding and prepare
  plan"` on Turn 3, and all 7 messages (including all 3 spoken
  `assistant_reply` turns) preserved in `#messages`.
- **Headless CLI triage check (`20261113-triage-live`)**: `requirements-triage.md`
  ran on Kilo `zai-coding-plan/glm-5.3` (`low`, `30.65s`, `7,131` in / `448` out
  tokens; plus one `17.22s` schema repair clearing `"source_refs": ["task
  statement"]`) against a vague example-bearing brief, returned `ready: false`,
  and paused at `WAITING_FOR_USER` with open follow-up question `Q1`
  (`options: []`, `proposed_default: ""`).
