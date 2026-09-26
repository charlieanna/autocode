# Models, roles, and escalation

[← Back to README](../README.md)

## Roles

Every stage of a run has a **role**. Role names describe responsibilities, not
mandatory models — each role can select any provider/model from `opencode models`
(or a Codex-compatible provider):

| Role | Job |
| --- | --- |
| **Requirements Gatherer** | Read-only requirements handoff, no task DAG |
| **Planner** | Draft the task DAG and evidence-backed revision |
| **Plan Reviewer** | Challenge the draft; owns final planning decisions |
| **Builder** | Implement one bounded task |
| **Validator** | Independent validation, separate session |
| **Completion Owner** | Complete/rework decision, separate session |

Model overrides use the role names: `--requirements-model`, `--glm-model`,
`--plan-reviewer-model`, `--astra-model`, `--terra-model`, `--sol-model`,
`--completion-model`, and the matching `--<role>-reasoning-effort` flags.
See [CLI](cli.md).

## Escalation ladders

| Role | CLI and billing route | Automatic escalation ladder |
| --- | --- | --- |
| Requirements Gatherer | OpenCode / Z.ai Coding Plan | Provider default |
| Planner | OpenCode / Z.ai Coding Plan | Provider default |
| Plan Reviewer | OpenCode / ChatGPT login | Sol High → Sol XHigh → Astra High |
| Builder | OpenCode / ChatGPT login | Terra Medium → Terra High → Terra XHigh → Terra Max |
| Validator | OpenCode / ChatGPT login | Sol High → Sol XHigh → Astra High |
| Completion Owner | OpenCode / ChatGPT login | Sol Medium → Sol High → Astra High |

> **Note on ladder names.** The ladders still use the older model-tier names —
> **Terra** (implementation), **Sol** (review/validation), **Astra** (strongest) —
> because those names appear in the underlying CLI flags (`--terra-model`,
> `--sol-model`, `--astra-model`) and in saved run state. They are not separate
> roles. A flag like `--terra-model openai/gpt-5.6-terra` simply pins the model
> used for the Builder role.

Autocode advances exactly one rung after durable evidence that the current role
struggled: an invalid completed response after report repair is exhausted, an
operator-abandoned uncertain response, a Builder batch with no source progress, or
failed validation routed back for implementation or revalidation. The next request
uses the stronger rung and a fresh role session. Autocode does not silently replay
the failed request, advances at most once for the same failed iteration, and never
overwrites an explicit custom model/provider route.

## Builder retry policy

New standard-workflow runs use a persisted Builder retry policy per approved milestone:
the configured Builder gets one ordinary retry, then one stronger-model attempt
(default `gpt-6-sol` with `high` reasoning), then a safety pause. Set
`--builder-strong-model MODEL` when creating a run to select the stronger model.
Explicit model pins and custom providers are never overridden. Existing saved runs
without this policy retain their previous routing. Restarting/resuming cannot reset
an exhausted budget. Scope violations, approval requests and transport safety pauses
are not automatically retried by this policy.

An implementation attempt with no source changes is no progress, not a build candidate.
The dashboard's named milestone checkpoints distinguish recorded implementation/tool
activity from independent verification; tool completions alone never verify criteria.

## Explicit model selection

Explicit `provider/model` choices use OpenCode for every role. For example:

```sh
autocode "Your rough idea" \
  --glm-model openai/gpt-5.6-sol \
  --astra-model zai-coding-plan/glm-5.3 \
  --terra-model openai/gpt-5.6-terra \
  --sol-model openai/gpt-6-astra
```

This overrides four routes through OpenCode; the Completion Owner keeps its default.
No Codex login is required for the default workflow. Bare model names for the legacy
`astra` and `sol` CLI roles are expanded to `openai/model` on OpenCode. Explicit
legacy `--engine codex` runs retain their separate Codex routing.

Before launching an OpenAI role, Autocode checks the nonsecret `opencode auth list`
summary for OAuth; missing/unknown/API authentication or API environment overrides
pause without fallback. This also applies before a project-free planning reply.
Other providers retain their configured connections. No credentials are copied.
Models in a catalogue are not proof of entitlement. Usage/provider failures pause
without silently switching to separately billed API access. Actual subscription
entitlements are managed by the CLIs.

The dashboard shows the live `opencode models` catalogue in all four pickers, grouped
by provider, plus explicit reasoning selectors for the Plan Reviewer, Builder,
Validator, and Completion Owner. Each unchanged default route is labeled explicitly.

Resuming keeps the saved engine, models and separate role sessions. Start a new run
when switching between Codex and OpenCode; their session IDs cannot be reused across
engines. Saved runs retain their original role engines and sessions; no existing run
is migrated by a dashboard selection.

See also: [Providers](providers.md) · [Workflow](workflow.md) · [CLI](cli.md)
