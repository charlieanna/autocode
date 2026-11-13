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

### Native extension retest limitation

The rebuilt VSIX was installed successfully at 00:58 local time. Its installed
`dist/extension.js` matched the tested build (SHA-256
`cd7b9f402b075a73fca341d607b1ef6dfa65097bbccd24ea12cd77f3e6e950f6`).
The test workspace retained its isolated CLI wrapper and existing model choices.
After invoking Developer: Reload Window in that workspace, the entire VS Code
window became blank. Native UI inspection, closing that test window and
reconnecting to the application timed out, including after resetting UI automation.
No new chat was submitted through the updated native extension. Its live retest
remains unverified; the earlier live extension results above are not presented as
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
It has the same replay-export limitation recorded above. This CLI result does not
replace the pending native-extension retest.
