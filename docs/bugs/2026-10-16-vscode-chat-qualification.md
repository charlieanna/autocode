# PR #1425 editor and live qualification

The owner requested a Kilo-like sidebar, plan feedback and same-run follow-ups,
and required both an actual editor flow and a live token-plan task before ready.
The editor flow passed. **Live qualification did not pass; keep the PR draft.**

## Implementation evidence

The sidebar exposes the core's additive chat projection. It preserves messages,
completed replies and drafts, renders Markdown safely, and binds actions to the
displayed checkpoint. Selected composer and scroll components are adapted from
Kilo revision `1552877e82ceaecc79a9fd4be7859518745e6f56`; the extension's
`THIRD_PARTY_NOTICES.md` records provenance and modifications. This is component
reuse, not a fork of Kilo's provider/runtime or a claim of complete UI parity.

Focused checks on this change:

- `npm test` in `vscode-extension`: 59 passed, including controller/webview chat,
  stale approval, same-run follow-up, Markdown safety, reading-position retention,
  composer sizing, local undo and IME/newline behavior.
- `npm run typecheck` and `npm run build`: passed.
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

## Live token-plan attempt

The native VS Code Start action drove the real CLI through a Linux bridge, using
Kilo CLI 7.8.3. The runner source was `51383962c65d66c88364e5b6eb5e00373c7fc491`;
the later chat projection/UI changes have no passing live qualification.
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
