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
`--completion-model`, `--resolver-model`, and the matching
`--<role>-reasoning-effort` flags.
`--utility-model` is a cheaper route for the stages that only classify or
reformat ([the utility route](#the-utility-route)).
See [CLI](cli.md). When constructing a new Requirements route from provider
defaults, an explicit `--requirements-model` wins; otherwise `--glm-model` supplies
its initial model. Changing the Planner on resume keeps the saved Requirements
model. Its reasoning effort remains a separate selection.

Conversation handoffs apply their independent profile after this initial
configuration: `--glm-model` selects the profile's Planner while Requirements keeps
its profile default unless `--requirements-model` selects it explicitly.

Accounts that expose only one usable model can start a run with
`--single-model MODEL`. This assigns that model to every role, including the
Planner, Builder, Tester and review roles, while shared or role-specific
reasoning flags still apply. It explicitly relaxes cross-model verification;
the review stages remain separate sessions and gates, but they are not
independent model-family checks. `--single-model` implies joint planning and
cannot be combined with per-role model flags.

## Default models

New OpenCode runs (the default engine) use these routes through the configured
provider connections. The cheaper model
does the volume and the more expensive one judges it: GLM plans and builds, GPT-6 Sol
reviews the plan and checks the build. A verifier never shares its producer's model
family by default. GPT-6 Astra is the Resolver's default; explicit choices may
use it in other roles. The `--single-model` exception is described above.

| Role | Default model | Reasoning | Escalation ladder |
| --- | --- | --- | --- |
| Requirements | `zai-coding-plan/glm-5.3` | medium | None |
| Planner | `zai-coding-plan/glm-5.3` | high | None |
| Plan Reviewer | `openai/gpt-6-sol` | high | None |
| Builder | `zai-coding-plan/glm-5.3` | medium | None; a stuck Builder gets one GPT-6 Sol XHigh attempt ([retry policy](#builder-retry-policy)) |
| Tester | `openai/gpt-6-sol` | high | Sol High → XHigh → Max |
| Completion Reviewer | `openai/gpt-6-sol` | medium | Sol Medium → High → Max |
| Resolver (`--resolver-model`) | `openai/gpt-6-astra` | high | Astra High → XHigh → Max |

A role escalates only while its exact model and reasoning level are on its ladder.
The ladders live in one place (`tools/autocode_route_ladder.py`, shared with the
Builder retry policy), and one function there, `next_route`, decides each climb:
the next rung, or why the role stays where it is (a pinned or custom-provider
route, a route on no rung, the last rung, or a climb already made for the same
failed iteration). A new run persists the ladders under `route_ladders` in its
settings, with rungs removed that its provider does not offer: the provider's
published list, and the model list a new run reads for its routes before its
first model call ([Builder retry policy](#builder-retry-policy) covers the stronger
attempt). Each
escalation, and each ordinary Builder retry, later records how its struggle ended,
and `autocode --status` counts those stamps as `escalation_outcomes`. The runner
stamps every one still open `passed` when the run completes on work a validation
passed, or when the Completion Reviewer or Resolver assigns the next task while the
last validation passed, and `paused` at the Builder's retry-limit pause. At that
assignment it leaves the Builder's own open when the work was sent back since that
validation, by a retry-policy charge or a correction task (the work failed, and a
stronger attempt the charge just made has not run yet), or when the run's records do
not say when that validation's attempt started, so a send-back cannot be ruled out. `autocode --status` also lists
each escalation as a row of `escalations` (a climb, or the Builder's stronger
attempt): the job and stage it was for, the route it climbed from and to, what its
escalated attempts cost, and whether the work then passed, failed again, paused or is
still running, with one line each on stderr ([status view](task-run.md#status-view)).
The row judges each escalation by its own work, so read a row's `outcome` for how one
escalation ended; when its struggle has ended, its stamp agrees. A stamp is the
struggle's, though: a climb a later climb replaced takes the struggle's end too while
its row reads `failed_again`, and so does an ordinary retry, which has no row. Before
the struggle ends the stamp is `open` while the row may already read `passed`;
completing the run or accepting completion via `--accept-completion` stamps open entries `passed`.
The default GLM Builder, any GLM Tester or Completion Reviewer, and the Plan Reviewer
are on no ladder: they keep their configured route, and a verifier that keeps
struggling pauses the run instead. The Builder instead gets its
[retry policy](#builder-retry-policy)'s one stronger attempt. If you configure the
Builder on `openai/gpt-6-sol` (with GLM checkers), it climbs Sol Medium → High → XHigh
→ Max. No role other than the Resolver escalates onto GPT-6 Astra.

The Planner has one stronger attempt of its own (`autocode_planner_escalation`, owner
decision 2026-10-23). In an adaptive run, when the Plan Reviewer's second challenge in a
planning cycle still has a blocking concern about the revised plan, the next revision runs
on Claude Opus 5.5 High, or on GPT-6 Sol High when the Plan Reviewer is a Claude model. Only
a blocking concern counts: schema errors, silences and output caps go through report repair
and recovery as before. A target is used only when the run's provider serves it, the saved
accounts permit it, it is a different model from the Planner's and it is outside the Plan
Reviewer's family; otherwise the Planner stays. The climb is a `reasoning_escalations` row
with trigger `plan_sent_back`, the Planner's session is rotated, and the route stays climbed
for the rest of the run, so a run climbs at most once. A second challenge runs only when the
review allowance leaves room for it (a large plan, `plan_review_call_limit` of 3 or more, or
`0`, in `~/.config/autocode/config.toml`): under the default allowance of two calls the first revision goes straight to the
final review and the Planner never climbs. If the stronger revision is still blocked, the
final review returns the open concerns to you as blocking questions.

Two scripted scenarios check these rows end to end, through `autocode --status --json`
([catalog](../scenarios/README.md#catalog)). In `escalation-converts` the Builder's stronger
attempt fixes what its ordinary attempts could not: one row, from the Builder's route to GPT-6
Sol XHigh, one attempt with its cost, `passed`, and the run completes; `escalation_outcomes`
stamps that attempt and the ordinary retry before it `passed` too. In `escalation-ladder-end`
that attempt fails too: the row reads `paused`, as both stamps do, and the run pauses at
`PAUSED_BUILDER_RETRY_LIMIT` with the stuck-stage Investigator's diagnosis in its stop reason.
`scenarios/test_escalation.py` also checks that a `--builder-strong-model` the tool does not
list refuses the new run before any model call.

The Resolver runs on a route of its own, `resolver`. It starts as a copy of the `astra`
route, which the per-task Planner (`astra_plan`) runs on; `autocode models` lists that
route as "Resolver and per-task Planner (`--astra-model`)". The Resolver's route is
created the first time a Resolver stage runs, or by `--resolver-model`. Which flag sets it:

- On an OpenCode-engine run (the default), `--resolver-model` and
  `--resolver-reasoning-effort` set the Resolver's route alone. `--astra-model` and
  `--astra-reasoning-effort` set the per-task Planner's route, and reach the Resolver
  only while its route has not been created yet.
- A Codex-engine run (`--engine codex`), and a saved run without joint planning, ignore
  `--resolver-model` and `--resolver-reasoning-effort`: the Resolver starts from the
  `astra` route that `--astra-model` and `--astra-reasoning-effort` set.

Only the Resolver's route climbs the Astra ladder. The per-task Planner keeps its
configured effort however often its reports are rejected, so a Resolver route copied
from the `astra` route later starts at the configured effort, not a climbed one. A run
saved before this was fixed kept the Astra ladder under `astra` in `route_ladders`; it
is read as the Resolver's ladder, and the saved state is not rewritten. A climb is not
a settings change for the recovery packet the Resolver works from: the climbed attempt
is held or admitted by that packet's causal-progress check, as the attempt before it was.

The one-stage jobs use the Plan Reviewer's model on a route of their own: the
Investigator (bug fixes), the Analyst (discuss) and the Designer (design reviews),
the last two with effort capped at medium. A run without a Plan Reviewer route falls
back to the Resolver's. The Code Reviewer (code review) runs on the Tester's route.

Other engines keep their own defaults, set where each engine is configured:

| Engine or provider | Defaults |
| --- | --- |
| `--engine codex` | `gpt-5.6-terra` for the Builder; `gpt-5.6-sol` for the Resolver, Tester and Completion Reviewer (`DEFAULT_ROLE_MODELS` in `tools/autocode.py`) |
| `--engine claude` (or `--provider claude`) | `claude-haiku-4-5-20251001` for the Builder; `claude-sonnet-5-5` for Requirements, the Planner and the Tester; `claude-opus-5-5` for the Plan Reviewer, the Resolver and the Completion Reviewer; every role at `medium` (`tools/providers/claude.py`, [Claude Code provider](providers.md#claude-code-provider)) |
| `--provider kilocode` | `openai/gpt-5.6-terra` for the Builder, `zai-coding-plan/glm-5.3` for the Planner, `openai/gpt-5.6-sol` for every other role (`tools/providers/configs/kilocode.toml`) |
| a user-level provider | whatever its `~/.config/autocode/providers/<name>.toml` `[roles]` specify |
| Dashboard Codex console | `gpt-5.6-*` defaults; any valid bare Codex model name can be selected, with configured GLM routes using Z.ai |

Autocode advances exactly one rung after durable evidence that the current role
struggled: an invalid completed response after report repair is exhausted, an
operator-abandoned uncertain response, a Builder batch with no source progress, or
failed validation routed back for implementation or revalidation. The next request
uses the stronger rung and a fresh role session. Autocode does not silently replay
the failed request, advances at most once for the same failed iteration, and never
overwrites an explicit custom model/provider route. Under a [money cap](execution.md#money-cap)
that request is priced before it launches, from the role's last attempt's tokens at the
climbed route's rate; when that projection would take the spend past the cap, the run
pauses `PAUSED_COST_LIMIT`, naming the rung and the arithmetic, instead of launching it.
Under an [active-time budget you set](execution.md#timeouts-and-watchdog) it is timed the same
way, from the role's last attempt's duration, and pauses `PAUSED_TIME_LIMIT` instead. A Builder
climb is also timed against a [milestone budget you set](execution.md#milestone-checkpoints-and-acceptance),
and pauses `PAUSED_MILESTONE_BUDGET` when it would take the milestone past that budget.

> **Note on role names.** The CLI flags keep the older tier names — `--astra-model`
> (the per-task Planner, and the route the Resolver starts from; see above),
> `--terra-model` (Builder), `--sol-model` (Tester) — because those
> names are in saved run state. They are not model choices: `--terra-model
> zai-coding-plan/glm-5.3` selects the model used for the Builder role.

## Choosing models automatically

New CLI and `TaskRun.start` runs choose models automatically from the selected tool's
connected accounts. No model flags or pinned profile are needed. One tool serves the
run by default; explicit per-role tool bindings and model/effort flags still win.
A fully pinned profile keeps its existing preflight and recovery policy because there
is no automatic choice to make. `--single-model` keeps its explicit same-model policy. Saved runs retain their routes
and permitted alternatives without discovering new accounts.

Use `--no-auto-select` to retain fixed defaults. If discovery is unavailable or cannot
field independent checkers, the default launch prints the reason and tries the fixed
routes under the normal catalogue, billing and independence guards. Explicit
`--auto-select` instead refuses before spending. Preview the choices and exclusions
with `autocode models --selection --provider NAME`; this reads local listings only.

Selection also needs the installed OpenCode version to have a row in the
[conformance matrix](providers.md#conformance-matrix) whose result allows ordinary runs
(`known-good` or `accepted-for-ordinary-runs`, #2060). On any other version the default
launch prints why and uses the fixed routes, and `--auto-select` refuses. `autocode doctor`
shows the installed version's row; a new version qualifies once
`tools/provider_conformance.py` has passed on it and its row is added.

OpenCode and Kilo reasoning variants come from their local verbose model listings.
An unsupported qualified level is omitted so the model uses its default. When older
tools cannot report variants, the recorded qualification level is used. Preflight
proves each distinct model/effort on its actual tool within the run's time/cost cap;
receipts survive resume. Recovery checks independence against the current saved routes, including explicit
assignments and catalogue substitutions. It stays within the saved connected accounts, and
exhaustion asks for intervention. A model a flag pinned (`--terra-model`, and the other per-role
flags) is never switched on its own: its stop asks, as before automatic selection. A stuck-run Investigator reuses an independent saved
reviewer route and its supported level; it cannot silently use a fixed paid-gateway
default. Explicit `--investigator-model` remains an override. A newly connected account
is never silently added.

The rules the choice keeps:

- **A minimum standard per role.** A model is only ever chosen for a role when the
  dated qualification table (`tools/autocode_route_qualification.json`) lists it for
  that role, with the evidence that admitted it: AutoCode's own runs qualify a model,
  and a public benchmark admits it only provisionally until such a run confirms it.
  The table changes by pull request, like the price table it is priced from. A model
  not in the table is never chosen automatically, however cheap. After a live run proves
  a route, `tools/qualify_routes.py --run-dir RUN --scenario NAME --profile PROFILE`
  merges that run into the table, refusing anything that did not declare
  `--evidence-provenance live` and finish `TASK_COMPLETE`; it launches no model.
- **Within a role's qualified models, the cheapest route wins.** A subscription route
  counts as cheaper than any pay-per-token one, then the price-table cost orders them,
  and a route whose access is already proven beats one that would need a probe. The
  checkers are never downgraded to save money: they start on their cheapest *qualified*
  model, and only a failure ever moves a role up. A role whose table entries carry a
  `preference` is ordered by it first: the Plan Reviewer is GPT-6 Sol when the ChatGPT
  login is connected, then Claude Opus, and MiMo is not used for review (owner decision,
  2026-10-20: MiMo spent 32-45k reasoning tokens and up to 15 minutes per plan review).
  With neither connected, the run stops before any work and offers those two logins.
  The Builder likewise prefers GPT-6 Luna at medium effort (owner decision, 2026-10-21), then GPT-6 Sol, then MiMo v2.6
  Flash, then Claude Opus, then the cheapest of GLM 5.3, MiMo v2.6 Pro and DeepSeek (owner
  decision, 2026-10-20: on ladder-07 a Luna Builder took 36-66 s and a Sol Builder 66 s
  against MiMo v2.6 Pro's 404-474 s, and the whole task 16-35 min against 34-46 min). A
  small task's first Builder attempt still runs at low effort (`autocode_light_builder`).
  Because a checker never grades its own family, an OpenAI Builder is checked by GLM as
  Tester and Completion Reviewer; when only GPT models could check an OpenAI build, MiMo
  Flash builds instead. The Completion Reviewer prefers GPT-6 Sol only when no GPT model
  built. Requirements, Planner and Tester stay on the cheapest qualified models.
- **Independence is never lowered quietly.** Every producer and checker pair stays on
  different model families (`tools/autocode_verifier_independence.py`); when the
  connected accounts cannot provide that, the run stops before any work and says what
  is missing in account terms ("connect a second model family"), never by suggesting
  `--single-model`. GLM, MiMo, Claude and OpenAI's GPT models are each one family, so no
  Claude model checks another Claude model's work, and no GPT model another GPT model's
  (Luna's build is never tested by Sol), on Kilo, OpenCode or a custom provider (owner
  decision, 2026-10-20). The built-in Claude Code and Codex engines reach only their own
  vendor's models, so there different models of that vendor check each other (for example
  Sonnet's work is reviewed by Opus); a model never checks itself.
- **No invented reasoning levels.** A route gets a reasoning level only when the model
  advertises it; otherwise the model uses its own default.
- **Explicit choices win.** `--<role>-model`, `--provider`, `--role-transport` and
  `--single-model` constrain or replace the automatic choice, and saved runs keep the
  routes they were started with.

The choice is one deterministic function from the discovered candidates and the
accounts you allow to one route per role, or a stop that names what is missing. Each
chosen route is recorded with its origin (`auto_selected`) in the route record the
status view shows. `autocode models --selection` shows the whole reasoning: the
logins the tool reports, the route chosen for each role with its cost, and why every
other qualified model lost (not connected on any login, a preferred or cheaper
qualified route won, or it would check its own family's work).

Before the first stage, the [model-access preflight](#when-a-model-is-not-in-your-plans)
probes exactly the chosen routes that are still unproven, one tiny call each at the
role's own reasoning level, within the run's cost cap — including built-in OpenCode
routes, which saved-run probes never touched. On OpenCode and Kilo a probe runs as a bare
agent with no tools and a one-line system prompt, a few hundred input tokens instead of the
coding agent's ~18k. The Resolver's own model (usually GPT-6 Astra) is probed only before the
first stage that runs it, since most runs never need a Resolver (#1744).

## Builder retry policy

New standard-workflow runs use a persisted Builder retry policy per approved milestone:
the configured Builder gets one ordinary retry, then one stronger attempt
(default `openai/gpt-6-sol` at `xhigh` reasoning, not Astra), then a safety pause. Set
`--builder-strong-model MODEL` when creating a run to select a different model.
A `--single-model` run keeps the stronger attempt and its checkers on the selected
model, changing reasoning effort rather than changing the model.
A tool registered with a TOML file can name its own stronger model, and the model its
checkers move to, in a `[builder_retry]` table
([Providers](providers.md#builder-retry-on-a-tool)); `--builder-strong-model` still wins.
The built-in claude provider declares Sonnet for a Haiku Builder, with Opus checking that attempt.
When the run's provider config lists its models (`models = [...]`) and the strong model is
not among them, the run gets no stronger attempt: the Builder gets one more ordinary retry in its
place, then pauses, and `--builder-strong-model` (or `[builder_retry]`) must name one of the
listed models.
A new run whose provider lists its models (`autocode models`: OpenCode, the claude provider, or a tool
with `models` or `models_command`) checks the stronger attempt against that list, the one it reads for
its routes, before any model call. The attempt needs the strong model and, when a Tester or
Completion Reviewer the attempt would move (see below: not pinned, not on a custom provider) runs
that model or its GLM or MiMo family, the checker model it moves to. When the list lacks either, the
run has no stronger attempt: the Builder gets one more ordinary retry in its place, then pauses with
a stop reason that names the missing model (saved as `not_offered` in the run's `builder_retry`
settings). A `--builder-strong-model` that needs a model the list lacks refuses the new run instead,
so the gap shows at creation rather than as a failed launch partway through the run. A pinned or
custom-provider Builder never makes the stronger attempt, so its policy is left as it is.
A Builder failure whose cause the runner cannot tell from its evidence goes to the
Investigator to be classified before this policy charges it. A milestone may have as many
failures classified as this policy takes (its ordinary retries, the stronger attempt or the
retry in its place, the failure it pauses on, and each failure that did not count, see below),
so classifying never stops a milestone
before the policy would; the next milestone, or a later turn's new plan, starts its own.
Classifications do not count toward the run's three stuck-stage investigations
([Workflow](workflow.md)), and turning those off (`max_calls_per_run = 0`) turns
classification off too: such a failure pauses as `PAUSED_BUILDER_CLASSIFICATION`.
Under a [money cap](execution.md#money-cap) the stronger attempt is priced before it launches,
from the last Builder attempt's tokens at the stronger model's rate; when that projection would take
the spend past the cap, the run pauses `PAUSED_COST_LIMIT` with the arithmetic instead of launching it.
Under an [active-time budget you set](execution.md#timeouts-and-watchdog) it is timed from the last
Builder attempt's duration, and pauses `PAUSED_TIME_LIMIT` when it would take the run past that budget,
or `PAUSED_MILESTONE_BUDGET` when it would take the milestone past a
[milestone budget you set](execution.md#milestone-checkpoints-and-acceptance).
Explicit model pins and custom providers are never overridden. Existing saved runs
without this policy retain their previous routing. Restarting/resuming cannot reset
an exhausted budget. Scope violations, approval requests and transport safety pauses
are not automatically retried by this policy.

Each charged failure is checked against the milestone's earlier ones (its evidence ledger,
`tools/autocode_evidence_ledger.py`). A failure's items are the checks that exited nonzero, the
criteria not met and the open blocking findings in the latest validation and review; its signature
is those items with the files the Builder changed.

- **A repeat**, a failure with the signature of an earlier one, has no new evidence, so it skips the
  remaining ordinary retries. It goes straight to the stronger attempt, or pauses once that is spent
  or the run has none. The stop reason then says the last failure repeated an earlier one.
- **Progress**, a failure whose earlier failing items are all resolved (each check exits 0, each
  criterion passes, each finding is closed) and that fails only on new items, does not count toward
  the limit. The Builder retries on the route it has now, and a pause stays a pause. A run that keeps
  finding new problems is still bounded by its milestone time budget, the stalled-review limit, the
  iteration limit and any money cap.
- **Anything else**, including a failure with no failing items, counts as before.

Each decision in `builder_retry_decisions` records which of these it was as `novelty` (`new`,
`repeat` or `progress`). A failure saved before the ledger existed has no signature, so the next
failure cannot count as progress.

The stronger attempt must not be checked by its own model. The rule is the cross-model
guard's (`tools/autocode_verifier_independence.py`), so its families count: when the Tester or
Completion Reviewer runs the stronger model, or another model of its GLM or MiMo family, it
moves to the policy's checker model
(`zai-coding-plan/glm-5.3` by default, or `checker_model` under `[builder_retry]`) for the
rest of that milestone (keeping its effort, or `high` for a climbed `xhigh`/`max`) and
returns to its route at the next milestone. The decision records the switch as
`checker_models`. Runs saved before this switch existed use the default checker model. Pinned and
custom-provider checkers are never moved; the cross-model guard pauses the run instead. A
new run refuses a `--builder-strong-model` that is the checker model or shares its family, since the
moved checkers would then check their own model's work. On a tool whose Builder model has
no `provider/` prefix (the built-in claude provider), a Tester or Completion Reviewer moves only
when the cross-model guard would refuse it (it runs the strong model in the tool's spelling, or
another model of its GLM or MiMo family) and the checker model is one the run already routes a
role to; one that may only spell the strong model another way (`openai/X` for `X`) cannot move.
When one cannot move, the run pauses (`PAUSED_BUILDER_RETRY_LIMIT`) before any route
changes, and the stop reason says why. `provider/model` names, as KiloCode uses, keep the rule above.

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

## The utility route

Some calls in a run only classify or reformat and need no frontier model.
`--utility-model MODEL` (new runs only) sends those stages to a cheaper model:

```sh
autocode "Your rough idea" --utility-model ollama/qwen3:8b
```

The stages it serves are listed in one place, `UTILITY_STAGES` in
`tools/autocode_roles.py`. Today that is the Job recognizer
(`recognize_workflow`), which picks one of the five workflows on a flow without
a Requirements stage; elsewhere Requirements names the job (#1860). The route copies
the Plan Reviewer's engine, provider and reasoning effort and replaces only the
model (`--utility-reasoning-effort` sets its own effort). A run started without
the flag saves no utility route, so every stage keeps the route it had before.
The flag is refused on a saved run.

A stage that writes code or judges evidence is never put on the utility route:
the Planner, Builder, Plan Reviewer, Tester, Completion Reviewer, Resolver,
Designer, Design and Code Reviewers, and the Investigator. A weak utility model
can therefore misroute a request (the console line after recognition names the
workflow and how to override it with `--workflow`) but cannot produce or accept
work. Builder-failure classification stays on the Investigator because it judges
the failure's evidence, and report-only repair keeps the route of the stage
whose report it repairs.

A local model runs through OpenCode's local providers (for example Ollama or LM
Studio, configured in OpenCode): name it as `provider/model` the way
`opencode models` lists it, and the launch checks it is listed like any other
route. No local model has yet been qualified on the utility route; run the
[conformance probe](provider-conformance.md) for your provider/model before
relying on one, and record a reliability sweep before changing the default.

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
default route; see [CLI](cli.md). `autocode models --prices` shows what each model's
tokens cost instead: the dated price table `tools/autocode_prices.json` (updated by
pull request) and the rate each default route is priced at, or "no rate" for a model
without a row, whose cost is then unknown rather than zero. The money cap
(`--max-cost-usd`, [Money cap](execution.md#money-cap)) counts spend at these rates.

## Reasoning effort

A role runs at one reasoning effort, chosen from the variants its model declares. Kilo
and OpenCode report those variants in their local model listings; when a tool reports
none, AutoCode records the effort as unverified and the tool's own check decides. The
flags are `--reasoning-effort LEVEL` for every role and `--<role>-reasoning-effort LEVEL`
for one (`--glm-reasoning-effort`, `--astra-reasoning-effort`, `--terra-reasoning-effort`,
`--sol-reasoning-effort`, and the matching flag for the other jobs).

An effort AutoCode derives itself — a reduced Builder tier, a climb on the effort ladder,
a single-model run — moves to the nearest declared variant and records the move as
`effort_fits`. An effort a person named never moves. When the model does not declare it,
the run pauses as `PAUSED_EFFORT_UNSUPPORTED` before the stage launches, naming the model,
the level you chose and the levels it does declare. Answer with one of those and continue:

```sh
autocode --answer effort-glm=high
autocode --resume-paused
```

The status view's `needs` carries the same question as `needs.effort` (`question_id`
`effort-ROLE`, `role`, `job`, `model`, `requested`, `declared`, `nearest`), and
`--answer` records the level you name as your own choice. A level the model does not
declare is refused with nothing saved. See [KiloCode](providers.md#kilocode).

## When a role's quota runs out

A provider that reports a used-up quota, usage limit or credits stops the stage with
`PAUSED_BUDGET`, and so does a used-up daily, weekly or monthly plan limit sent as an
HTTP 429 (Z.AI's "Weekly/Monthly Limit Exhausted"); a per-minute rate limit does not.
The stopped attempt is kept and never replayed. Unless the run can switch the role on
its own (see the end of this section), AutoCode asks you which model the role should
continue on, for example:

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
current route under `routes` — its `model`, `engine`, the `job` it runs, the `reasoning_effort` a
launch passes (null for the model's own default), the `provider` tool that launches it, the
`account` its model bills (null when the engine's own login decides) and the `origin`, why the
route is what it is — and the changes under `route_assignments`; while the
question is open, `needs.route` names the role and its current model. The new model
stays on the role until you change it again, including past the next milestone.

A run that chose its models automatically ([Choosing models automatically](#choosing-models-automatically))
first switches the role on its own. It takes the next qualified model on an account the
run already uses, keeps every checker independent of its producer, and prints a line such
as `Builder: MODEL stopped (PAUSED_BUDGET); switched to NEW_MODEL on ACCOUNT without asking`.
The switch sets the stopped attempt aside the same way an answer does, is recorded as a
`route_assignment` whose actor is `runner`, and counts as an automatic recovery. When the new
model bills another way than the stopped one (a subscription route replaced by a pay-per-token
one, by the billing discovery recorded), the record carries `billing: {from, to}`, which the
status view's `route_assignments` shows, and the line ends `; it bills pay per token where
MODEL was subscription` (#2061). AutoCode
asks instead when no such model remains, when you pinned the role's model with a flag,
when a pause was requested, and for native runs and workflow jobs. The same switch applies
to content-filter and output-limit stops.

A run on fixed routes (`--no-auto-select`, or a launch whose automatic selection fell
back to them) has no fallback list or configuration table: its quota stop is always your
decision. Capacity errors retry the same model, rate limits pause as before, and
authentication failures never ask for a model.

### When the stopped role is a workflow job

A workflow job (the Code Reviewer, Designer, Design Reviewer, Analyst, bug-fix
Investigator or Design inventory) that stops on quota pauses differently: its attempt is set aside at
once and the job takes one exact retry, bound to the run's configuration
(`PAUSED_JOB_FAILURE`, `needs.kind` `retry_job`; see [CLI](cli.md#waiting-or-finished)).
The stop names the job, the model and the provider's words, and the status view's
`needs.route` carries the same model question. You answer it with the job's retry
token instead of a resolver token, then retry with the new token it issues:

```sh
autocode --run-dir RUN --answer route-sol=gpt-6-luna --job-retry-token TOKEN
autocode --run-dir RUN --resume-paused --retry-failed-stage --job-retry-token NEW_TOKEN
```

The answer applies the same checks and records the same `route_assignment`. The old
token stops working, so the job never runs again on the model that stopped without
your saying so. `--abandon-stage` and a `--sol-model` flag are refused at this stop
(the flag would make the exact retry stale; only putting back the model the retry is
bound to is saved), and so is the flag next to an `--abandon-stage` that sets the
job's uncertain attempt aside: set it aside on its own, then answer. The answer
itself changes nothing but the job's model; given with another setting it is refused
and nothing is saved, and so is the answer next to the retry (`--resume-paused
--retry-failed-stage`), since the retry needs the new token. Once you have named a
model, the status line and recovery card ask only for the retry on it. The Architect
(Designer, Design Reviewer), Analyst and Investigator routes have no flag at all: the answer is the only
way to move them. After a quota stop the shown token also retries the job unchanged,
once the quota resets. The stuck-stage Investigator (`investigate_stuck`) is the
exception: its route is rebuilt for every investigation, so its stop keeps only the
exact retry (`--investigator-model` pins the model of the next investigation).

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
replayed. AutoCode handles it as a quota stop: a run that chose its models automatically
switches on its own where it can ([above](#when-a-roles-quota-runs-out)); otherwise it asks
the question a quota stop asks, answered the same way:

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
missing or unexpected session ID") and asks no model question. `--resume-paused` keeps it
uncertain ("Recovered response belongs to an unexpected session"), and so does a resume
after AutoCode stopped before it saved the provider's exit or interrupted it. A workflow
job stopped this way names the session, not a refusal. Only a provider that exited with an
error has such a refusal typed, as at its first stop. A stage that timed out stays
`PAUSED_PROVIDER_TIMEOUT` on resume, from any session, as at its first stop, and the
automatic timeout recovery may set it aside.
`--answer route-ROLE=MODEL` is accepted only where the request asks it, so while such a
stop is open (or one a run paused on before a finish-only refusal was typed), the role's
model flag alone (`--terra-model`) is refused and names only the other route:
`--abandon-stage ATTEMPT`, then `--resume-paused --terra-model MODEL`.

A refused workflow job pauses for its exact retry, as a quota stop of a job does
([above](#when-the-stopped-role-is-a-workflow-job)): name another model with
`--answer route-ROLE=MODEL --job-retry-token TOKEN`, then retry with the new token.
That answer refuses the model that was refused. Until you name one, the status
view's `needs.action` is that answer and the recovery card offers no retry. The
job's own retry token still replays the refused model if you retry without naming
one.

## When a response reaches its output limit

An authenticated terminal output-limit stop (`PAUSED_OUTPUT_CAP`) switches models on its
own on a run that chose them automatically, as a quota stop does
([above](#when-a-roles-quota-runs-out)). Otherwise it asks for another model on the same
engine with the existing availability and checker-independence rules.
The answer retains the incomplete attempt and usage; continuation is explicit. Builder
and Requirements never automatically retry this stop on the same model. See [Output cap](providers.md#output-cap)
for cap settings and the existing bounded read-only report repair.

## A parallel Builder stopped on its model

A Builder in a parallel batch that stops on quota, a content-filter refusal or an authenticated
output limit asks the same model question, naming its milestone:

```text
[route-terra] Builder (milestone M1)'s model was refused by its provider's content filter; name another model to continue on
```

Answer it the same way, with `--answer route-terra=MODEL`. The answer sets that member's
stopped attempt aside, runs only that milestone's Builder again on the model you name, and
keeps the other Builders' results. As for a serial Builder, it also moves the run's Builder
route, so later Builders (the next serial milestone, later batches) run on that model too.
The answer is checked against the model that member ran on, even when another member's
answer already moved the route, and it must not share that model's GLM or MiMo family. The
models a refusal's question lists pass the same checks. When two members stop, AutoCode asks
about them one at a time.

Changing another role's settings while a member's question is open withdraws the old
request and asks that member's `route-terra` question again under the new settings.
Use the fresh request's token to answer it.

While the question is open the status view offers no per-member retry for a member stopped
on any of these causes: the question is the next step. AutoCode never reruns a member
stopped by its provider's content filter or output limit on that same model: `--retry-builder M1` is refused while its
route is still that model, so it runs again only on the model your answer names. A member
stopped on quota can still be named with `--retry-builder M1`, which runs the same model again
(for example once its quota has reset).

A settings change beside `--retry-builder` is saved only when the retry is accepted.
The member reruns on its own saved Builder route: a Builder model, provider or reasoning
effort change beside its retry is refused with nothing saved. To change a member stopped
on quota, refusal or an authenticated output limit to another model, answer its `route-terra` question instead.
Saving a different Builder route separately also does not move the member's route. A later
`--retry-builder` refuses the mismatch and names the member's saved settings and the run's
current settings. If that member's model question is open, answer it to choose the model;
the answer keeps its other frozen route settings. Otherwise restore the run's Builder
settings to that member's route in a separate command, then retry it. AutoCode does not
silently copy the run's route into the child.

If you answer the request with corrective information (`--resolver-response
provide_information`) or leave it paused instead, no model is named and no question stays
open. One command continues from there, and the stop reason, the status view's
`needs.action` and the recovery card all name it: `autocode --resume-paused --retry-builder M1`.
For a member stopped by its provider's content filter or output limit, it reads that member's saved stop again
and asks its `route-terra` question again; nothing launches. For a member stopped on quota, it
reruns the member unchanged. A plain `--resume-paused` stays paused there and names that
command; the request itself never advises "then `autocode resume`" at a member's stop.
Changing the Builder's route with `--resume-paused --terra-model MODEL` after the information
moves the run's route, not the member's, so AutoResolver asks that member's question again.

See also: [Providers](providers.md) · [Workflow](workflow.md) · [CLI](cli.md)
