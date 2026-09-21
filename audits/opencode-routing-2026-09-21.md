# OpenCode routing — 2026-09-21

The user requested OpenCode across Autocode projects and confirmed that OpenCode
already has the intended ChatGPT account. No authentication credentials were changed.

The default GLM/Astra/Terra/Sol workflow now uses OpenCode for every role. Astra and
Sol use `openai/gpt-6-astra` and `openai/gpt-5.6-sol`; GLM and Terra retain
`zai-coding-plan/glm-5.3`. Bare Astra/Sol aliases in new conversations are expanded
to OpenCode identifiers. Explicit legacy `--engine codex` remains an opt-in mode.

Existing mixed OpenCode/Codex runs migrate before their next provider launch, after
recovery and under the workspace writer lock. Migration validates the installed
models, existing OpenAI OAuth route, and saved OpenCode configuration. It backs up
the checkpoint and archives Codex session IDs while retaining OpenCode sessions,
approved goals, evidence, task history, and limits. Unresolved stages block migration.

Validation used temporary projects and fake providers:

- 52 routing/planning/transport tests: 50 initially passed. Two stale fixture
  expectations were corrected (OpenCode model IDs and quota error forwarding), and
  both passed on rerun alongside a new real-subprocess checkpoint migration test.
- 44 dashboard model-selection and console tests passed.
- Planning UI and model-picker JavaScript checks passed.
- The five migration invariants and five OAuth-route tests also passed together.
- `git diff --check` passed.

The installed pipx command resolves to this editable checkout. The existing
dashboard on port 5191 had no active actions or conversations and was restarted;
HTTP verification confirmed the new OpenCode default labels. Project workers were
not interrupted.

Runtime state when this record was written:

- DDIA Tutor already uses OpenCode for every role and remains running.
- IdleCampus is currently running Terra through OpenCode. Its Astra/Sol switch is
  queued through pause receipt `opencode-routing-20260921` at the next saved stage
  boundary. A detached watcher resumes that exact run through the updated runner
  once this pause is applied, provided no new user intervention supersedes it.
- Watcher PID at launch: `59524`. Status:
  `/private/tmp/autocode-opencode-switch-20260921.status.json`. Log:
  `/private/tmp/autocode-opencode-switch-20260921.log`. The watcher is scoped to
  IdleCampus's `20260918-113408-complete-the-adaptive-dsa-mapping-and-validation` run.

The queued IdleCampus migration was not yet observed complete. Read the watcher
status and current checkpoint before reporting it as complete or launching another
resume command.
