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
above the answer field. This is a separate presentation defect. Neither the
model-format defect nor duplicate presentation is claimed fixed by these PRs.

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
