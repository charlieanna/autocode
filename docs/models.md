# Models, roles, and escalation

[← Back to README](../README.md)

## Roles

Every stage of a run has a **role**. Role names describe responsibilities, not
mandatory models — each role can select any provider/model from `opencode models`
(or a Codex-compatible provider):

| Role | Job |
| --- | --- |
| **Requirements** | Read-only requirements handoff, no task DAG |
| **Planner** | Draft the task DAG and evidence-backed revision |
| **Plan Reviewer** | Challenge the draft; owns final planning decisions |
| **Builder** | Implement one bounded task |
| **Validator** | Independent validation, separate session |
| **Completion Owner** | Complete/rework decision, separate session |
| **AutoResolver** | Diagnose and resolve a failed step |

These names come from `tools/autocode_role_names.json`, shared by the terminal
and browser. Internal IDs and saved routes remain compatible. Report-format
repair keeps the original job's name. In the Builder-led review modes, the
`astra_checkpoint` call performs both jobs and is labelled **Validator /
Completion Owner**, independently of the model selected for that call.

Model overrides use the role names: `--requirements-model`, `--glm-model`,
`--plan-reviewer-model`, `--astra-model`, `--terra-model`, `--sol-model`,
`--completion-model`, and the matching `--<role>-reasoning-effort` flags.
See [CLI](cli.md).

## Default models

New OpenCode runs (the default engine) use these routes. Every OpenAI model goes
through the ChatGPT login; GLM goes through the Z.ai Coding Plan. The cheaper model
does the volume and the more expensive one judges it: GLM plans and builds, GPT-6 Sol
reviews the plan and checks the build. A verifier never shares its producer's model
family. GPT-6 Astra is reserved for the Resolver.

| Role | Default model | Reasoning | Escalation ladder |
| --- | --- | --- | --- |
| Requirements | `zai-coding-plan/glm-5.3` | medium | None |
| Planner | `zai-coding-plan/glm-5.3` | high | None |
| Plan Reviewer | `openai/gpt-6-sol` | high | None |
| Builder | `zai-coding-plan/glm-5.3` | medium | None; a stuck Builder gets one GPT-6 Sol XHigh attempt ([retry policy](#builder-retry-policy)) |
| Validator | `openai/gpt-6-sol` | high | Sol High → XHigh → Max |
| Completion Owner | `openai/gpt-6-sol` | medium | Sol Medium → High → Max |
| Resolver (`--astra-model`) | `openai/gpt-6-astra` | high | Astra High → XHigh → Max |

A role escalates only while its exact model and reasoning level are on its ladder.
The default GLM Builder, any GLM Validator or Completion Owner, and the Plan Reviewer
are on no ladder: they keep their configured route, and a verifier that keeps
struggling pauses the run instead. The Builder instead gets its
[retry policy](#builder-retry-policy)'s one stronger attempt. If you configure the
Builder on `openai/gpt-6-sol` (with GLM checkers), it climbs Sol Medium → High → XHigh
→ Max. No role other than the Resolver escalates onto GPT-6 Astra.

The one-stage jobs use the Plan Reviewer's model on a route of their own: the
Investigator (bug fixes), the Analyst (discuss) and the Architect (design reviews),
the last two with effort capped at medium. A run without a Plan Reviewer route falls
back to the Resolver's. The Reviewer (code review) runs on the Validator's route.

Other engines keep their own defaults, set where each engine is configured:

| Engine or provider | Defaults |
| --- | --- |
| `--engine codex` | `gpt-5.6-terra` for the Builder; `gpt-5.6-sol` for the Resolver, Validator and Completion Owner (`DEFAULT_ROLE_MODELS` in `tools/autocode.py`) |
| `--provider kilocode` | `openai/gpt-5.6-terra` for the Builder, `zai-coding-plan/glm-5.3` for the Planner, `openai/gpt-5.6-sol` for every other role (`tools/providers/configs/kilocode.toml`) |
| a user-level provider | whatever its `~/.config/autocode/providers/<name>.toml` `[roles]` specify |
| Dashboard Codex console | `gpt-5.6-*`, or `glm-5.3` / `glm-5.3-flash` per role through Z.ai |

Autocode advances exactly one rung after durable evidence that the current role
struggled: an invalid completed response after report repair is exhausted, an
operator-abandoned uncertain response, a Builder batch with no source progress, or
failed validation routed back for implementation or revalidation. The next request
uses the stronger rung and a fresh role session. Autocode does not silently replay
the failed request, advances at most once for the same failed iteration, and never
overwrites an explicit custom model/provider route.

> **Note on role names.** The CLI flags keep the older tier names — `--astra-model`
> (Resolver), `--terra-model` (Builder), `--sol-model` (Validator) — because those
> names are in saved run state. They are not model choices: `--terra-model
> zai-coding-plan/glm-5.3` selects the model used for the Builder role.

## Builder retry policy

New standard-workflow runs use a persisted Builder retry policy per approved milestone:
the configured Builder gets one ordinary retry, then one stronger attempt
(default `openai/gpt-6-sol` at `xhigh` reasoning, not Astra), then a safety pause. Set
`--builder-strong-model MODEL` when creating a run to select a different model.
When the run's provider config lists its models (`models = [...]`) and the strong model is
not among them, the run gets no stronger attempt: the Builder gets one more ordinary retry in its
place, then pauses, and `--builder-strong-model` must name one of the listed models.
Explicit model pins and custom providers are never overridden. Existing saved runs
without this policy retain their previous routing. Restarting/resuming cannot reset
an exhausted budget. Scope violations, approval requests and transport safety pauses
are not automatically retried by this policy.

The stronger attempt must not be checked by its own model. When the Validator or
Completion Owner runs the stronger model, it moves to `zai-coding-plan/glm-5.3` for the
rest of that milestone (keeping its effort, or `high` for a climbed `xhigh`/`max`) and
returns to its route at the next milestone. The decision records the switch as
`checker_models`. Runs saved before this switch existed use the same model. Pinned and
custom-provider checkers are never moved; the cross-model guard pauses the run instead. A
new run refuses a `--builder-strong-model` that is `zai-coding-plan/glm-5.3`, since the
moved checkers would then check their own model's work.

A parallel Builder cannot move the checkers that will check its batch. When its stronger
attempt would run on the checkers' model, it stops with `SERIAL_ESCALATION` instead
(decision `defer`) and leaves that attempt to the run. The run integrates the Builders
that finished, and the unchanged checkers validate them. The deferred milestone is never
run in parallel again. When it is next assigned, the run makes the stronger attempt
itself, serially, with the checker switch above and without a human. The retry budget
carries over from the Builder, so this is still the milestone's last attempt before the
safety pause. If every Builder in a batch defers, nothing is integrated and the run starts
on the first of them straight away. A Builder route pinned while the milestone waited is
not overridden; the run pauses instead.

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

## When a model is not in your plans

A new run first asks its provider which models your plans offer (`opencode models`).
If a role's model is not listed, the run stops before any model call and shows:

- every model your plans offer, grouped by plan, each marked **subscription** or **pay
  per token** and with its tier: **cheap worker** (plans and builds), **strong judge**
  (checks) or **Resolver only**. A model AutoCode has no tier for says "tier unknown";
  free, flash and MiMo routes are not offered.
- a replacement for each missing role. It prefers a subscription over per-token billing,
  then the tier the role wants, and never shares a model (or GLM family) with the role it
  checks or is checked by. Only the Resolver is offered GPT-6 Astra. OpenAI routes are
  not offered when OpenCode signs in to OpenAI another way, such as an API key.
- the flags to start the run with, for example `--sol-model openai/gpt-6-luna`.

In `--chat`, you can accept all the replacements and the run continues with them, as if
you had passed the flags. AutoCode never changes a model without you. A saved run is not
re-routed this way: resuming keeps its models, so start a new run instead.

A listed OpenAI route while OpenCode is signed in with an API key is not a missing model:
the run pauses at the billing check, as above, and resumes once the ChatGPT login is
connected. `autocode models` shows the same list at any time and checks every role's
default route; see [CLI](cli.md).

See also: [Providers](providers.md) · [Workflow](workflow.md) · [CLI](cli.md)
