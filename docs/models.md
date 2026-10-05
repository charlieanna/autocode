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
| **Tester** | Independent validation, separate session |
| **Completion Reviewer** | Complete/rework decision, separate session |

Model overrides use the role names: `--requirements-model`, `--glm-model`,
`--plan-reviewer-model`, `--astra-model`, `--terra-model`, `--sol-model`,
`--completion-model`, and the matching `--<role>-reasoning-effort` flags.
See [CLI](cli.md).

## Default models

New OpenCode runs (the default engine) use these routes through the configured
provider connections. The cheaper model
does the volume and the more expensive one judges it: GLM plans and builds, GPT-6 Sol
reviews the plan and checks the build. A verifier never shares its producer's model
family. GPT-6 Astra is the Resolver's default; explicit choices may use it in other roles.

| Role | Default model | Reasoning | Escalation ladder |
| --- | --- | --- | --- |
| Requirements | `zai-coding-plan/glm-5.3` | medium | None |
| Planner | `zai-coding-plan/glm-5.3` | high | None |
| Plan Reviewer | `openai/gpt-6-sol` | high | None |
| Builder | `zai-coding-plan/glm-5.3` | medium | None; a stuck Builder gets one GPT-6 Sol XHigh attempt ([retry policy](#builder-retry-policy)) |
| Tester | `openai/gpt-6-sol` | high | Sol High → XHigh → Max |
| Completion Reviewer | `openai/gpt-6-sol` | medium | Sol Medium → High → Max |
| Resolver (`--astra-model`) | `openai/gpt-6-astra` | high | Astra High → XHigh → Max |

A role escalates only while its exact model and reasoning level are on its ladder.
The default GLM Builder, any GLM Tester or Completion Reviewer, and the Plan Reviewer
are on no ladder: they keep their configured route, and a verifier that keeps
struggling pauses the run instead. The Builder instead gets its
[retry policy](#builder-retry-policy)'s one stronger attempt. If you configure the
Builder on `openai/gpt-6-sol` (with GLM checkers), it climbs Sol Medium → High → XHigh
→ Max. No role other than the Resolver escalates onto GPT-6 Astra.

The one-stage jobs use the Plan Reviewer's model on a route of their own: the
Investigator (bug fixes), the Analyst (discuss) and the Designer (design reviews),
the last two with effort capped at medium. A run without a Plan Reviewer route falls
back to the Resolver's. The Code Reviewer (code review) runs on the Tester's route.

Other engines keep their own defaults, set where each engine is configured:

| Engine or provider | Defaults |
| --- | --- |
| `--engine codex` | `gpt-5.6-terra` for the Builder; `gpt-5.6-sol` for the Resolver, Tester and Completion Reviewer (`DEFAULT_ROLE_MODELS` in `tools/autocode.py`) |
| `--provider kilocode` | `openai/gpt-5.6-terra` for the Builder, `zai-coding-plan/glm-5.3` for the Planner, `openai/gpt-5.6-sol` for every other role (`tools/providers/configs/kilocode.toml`) |
| a user-level provider | whatever its `~/.config/autocode/providers/<name>.toml` `[roles]` specify |
| Dashboard Codex console | `gpt-5.6-*` defaults; any valid bare Codex model name can be selected, with configured GLM routes using Z.ai |

Autocode advances exactly one rung after durable evidence that the current role
struggled: an invalid completed response after report repair is exhausted, an
operator-abandoned uncertain response, a Builder batch with no source progress, or
failed validation routed back for implementation or revalidation. The next request
uses the stronger rung and a fresh role session. Autocode does not silently replay
the failed request, advances at most once for the same failed iteration, and never
overwrites an explicit custom model/provider route.

> **Note on role names.** The CLI flags keep the older tier names — `--astra-model`
> (Resolver), `--terra-model` (Builder), `--sol-model` (Tester) — because those
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

The stronger attempt must not be checked by its own model. When the Tester or
Completion Reviewer runs the stronger model, it moves to `zai-coding-plan/glm-5.3` for the
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

This overrides four routes through OpenCode; the Completion Reviewer keeps its default.
No Codex login is required for the default workflow. Bare model names for the legacy
`astra` and `sol` CLI roles are expanded to `openai/model` on OpenCode. Explicit
legacy `--engine codex` runs retain their separate Codex routing.

AutoCode accepts every well-formed model route listed by the configured provider,
including `mimo-token-plan/`, free, Flash, highspeed and newly listed models.
Model defaults and tier labels guide selection; they are not allowlists. Explicit
conversation handoff choices retain their model and reasoning effort. Visual
review also accepts an explicit model instead of requiring Astra.

Before launching an OpenAI role, AutoCode accepts either OAuth or API authentication
reported by `opencode auth list`, or explicit API environment configuration. Missing
or ambiguous authentication pauses without choosing another connection. Other
providers retain their configured connections. No credentials are copied. Actual
model entitlement is managed by the provider CLI; a listed model can still fail at
request time. Producers and their reviewers must keep independent routes.

The dashboard shows the live `opencode models` catalogue in all four pickers, grouped
by provider, plus explicit reasoning selectors for the Plan Reviewer, Builder,
Tester, and Completion Reviewer. Each unchanged default route is labeled explicitly.

Resuming keeps the saved engine, models and separate role sessions. Start a new run
when switching between Codex and OpenCode; their session IDs cannot be reused across
engines. Saved runs retain their original role engines and sessions; no existing run
is migrated by a dashboard selection.

## When a model is not in your plans

A new run first asks its provider which models your plans offer (`opencode models`).
If a role's model is not listed, the run stops before any model call and shows:

- every model your plans offer, grouped by plan, each marked **subscription** or **pay
  per token** and with its tier: **cheap worker** (plans and builds), **strong judge**
  (checks) or **Resolver tier**. A model AutoCode has no tier for says "tier unknown";
  MiMo token-plan, free and Flash routes are included.
- a replacement for each missing role. It prefers a subscription over per-token billing,
  then the tier the role wants, and never shares a model (or GLM family) with the role it
  checks or is checked by. Any listed model can be offered, including GPT-6 Astra
  for other roles and OpenAI routes authenticated with an API key.
- the flags to start the run with, for example `--sol-model openai/gpt-6-luna`.

In `--chat`, you can accept all the replacements and the run continues with them, as if
you had passed the flags. AutoCode never changes a model without you. A saved run is not
re-routed this way: resuming keeps its models, so start a new run instead.

`autocode models` shows the same list at any time and checks every role's
default route; see [CLI](cli.md).

## When a role's quota runs out

A provider that reports a used-up quota, usage limit or credits stops the stage with
`PAUSED_BUDGET`. The stopped attempt is kept, never replayed, and AutoCode asks you
which model the role should continue on, for example:

```text
[route-sol] Tester's quota is exhausted; name the model to continue on
```

The question names the job on screen (Tester, Builder, Completion Reviewer, …). It has
no proposed default, is never delegable (`--delegate-all` refuses it) and the scenario
driver never answers it for you. Answer it with a model:

```sh
autocode --run-dir RUN --answer route-sol=gpt-6-luna --resolver-token TOKEN
autocode --run-dir RUN --resume-paused
```

The answer sets the stopped attempt aside exactly as `--abandon-stage` does (partial
work and evidence retained, the role's session dropped), saves the model on the role,
and records the change. `--resume-paused` then runs the role again on a fresh session
with the new model. The same thing without answering the question:
`--abandon-stage ATTEMPT`, then `--resume-paused --sol-model gpt-6-luna` (or both in
one invocation). `--sol-model` alone is refused while the stopped attempt is still
uncertain. Setting the attempt aside never raises the role's reasoning effort: a quota
stop is not a sign the model struggled.

A model is refused, and the run stays paused with the question open, when a launch
would refuse it:

- it is for a different engine: a Codex run takes a bare name (`gpt-6-luna`), an
  OpenCode run a `provider/model` (`openai/gpt-6-luna`); sessions never move between
  engines;
- OpenCode does not list it (`opencode models`);
- it breaks the cross-model rule: a checker never shares its producer's model, or
  its GLM or MiMo family (Builder/Tester, Builder/Completion Reviewer,
  Planner/Plan Reviewer);
- it is the model that just ran out (or was refused, below).

Every change, through the answer or the resume flag, is appended to the run's
`user_events` as a `route_assignment` (role, job, from, to, engine, stage, attempt,
events path, time, `via` answer or resume_flag). A resume flag the launch refuses
(another engine's format, a cross-model clash) is not recorded. The status view shows each role's
current route under `routes` and the changes under `route_assignments`; while the
question is open, `needs.route` names the role and its current model. The new model
stays on the role until you change it again, including past the next milestone.

There is no fallback list, configuration table or automatic switch: a quota stop is
always your decision. Capacity errors retry the same model, rate limits pause as
before, and authentication failures never ask for a model.

## When a provider's content filter refuses a response

A provider whose content filter refuses a stage's response (OpenCode's
`ContentFilterError`, a `content_filter` error code, or an OpenCode or Kilo stream
whose last step finished with reason `content-filter`) stops the stage with
`PAUSED_CONTENT_FILTER`, whether the provider exited with an error or exited 0. The
stop names the job, the refused model and the provider's words:

```text
Builder: the provider's content filter refused the response on xiaomi-token-plan-sgp/mimo-v2.6-pro (ContentFilterError: The response was blocked by the provider's content filter); the same model is likely to refuse it again.
```

The same model is likely to refuse the same request again, so the attempt is kept and never
replayed, and AutoCode asks the question a quota stop asks, answered the same way:

```text
[route-terra] Builder's model was refused by its provider's content filter; name another model to continue on
```

The question also lists the run's other configured models, from other providers, that
would pass the launch rules above, or says that none does (in the GLM/MiMo profile the
only other model is the Tester's GLM, which the cross-model rule refuses for the
Builder). The list is advice, never a default. Setting the attempt aside never raises
the role's reasoning effort, and the `route_assignment` records `PAUSED_CONTENT_FILTER`
as its `pause_status`. Only the provider's error event or a final `content-filter`
finish reason classifies the stop: the model's own text ("The request was rejected ...")
never does. When no error event follows the finish, the provider's words in the stop are
`content_filter: OpenCode's last step finished with reason content-filter`.

A refusal in a response the provider returned with exit 0 from a session the run did not
expect, or one that names no session, stays `PAUSED_UNCERTAIN_STAGE` ("Provider returned a
missing or unexpected session ID") and asks no model question; `--resume-paused` keeps it
uncertain ("Recovered response belongs to an unexpected session"), because the session is
checked before the saved response is read. `--answer route-ROLE=MODEL` is accepted only
where the request asks it, so while such a stop is open (or one a run paused on before a
finish-only refusal was typed), the role's model flag alone (`--terra-model`) is refused and
names only the other route: `--abandon-stage ATTEMPT`, then
`--resume-paused --terra-model MODEL`.

See also: [Providers](providers.md) · [Workflow](workflow.md) · [CLI](cli.md)
