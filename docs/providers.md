# Providers and adapters

[← Back to README](../README.md)

OpenCode is the default engine. Codex: `--engine codex`. Claude Code: `--engine claude`
([built in](#claude-code-provider)). Any other tool registers with one TOML file.

## You do not need to choose a model

Connecting one account is enough. Automatic selection reads the tool's own model and
login listings, prefers subscriptions, and keeps the producer and checker roles on
independent models ([automatic model selection](models.md#choosing-models-automatically)).

`autocode connections setup` ends by naming the route each role will use, from that
local listing and without launching anything:

```text
Routes for a new run, from kilocode's local listing:
  Requirements: zai-coding-plan/glm-5.3 at medium (Z.AI Coding Plan subscription; qualified for Requirements)
  Planner: openai/gpt-6-sol at high (OpenAI subscription; qualified for Planner)
  Builder: openai/gpt-6-sol at medium (OpenAI subscription; qualified for Builder)
```

A new run with no model flag prints the same lines for its roles before it spends
anything. A client reads them from `autocode connections list --routes --json` as
`routes[]` and `routes_lines`; `autocode models --selection` shows the same choice with
the alternatives it rejected and why. When no connected account can field a qualified
route for a role, `routes_stop` and that preview say which role and which accounts would.

Every `--<role>-model`, `--single-model` and `--<role>-reasoning-effort` flag is an
override on top of that choice, not a required setup step. An override is checked at the
same launch boundary as an automatic route: an effort the model does not declare pauses
as `PAUSED_EFFORT_UNSUPPORTED` and names the variants it does
([Reasoning effort](models.md#reasoning-effort)).

## Provider process ownership

Supervised task-stage providers, including core TaskRun conversations and
`autocode ui`, require a native little-endian Linux x86_64 or aarch64
runtime, matching `/proc` PID namespace, and working seccomp user notifications
with syscall continuation. A Linux host without them, and any other unsupported
host except macOS, refuses the launch before provider spawn. The refusal
identifies the unavailable boundary; it does not send a model request. There is
no CLI opt-out.

macOS has no seccomp, so providers there run without this boundary (#1195):
the keeper's lifeline and sampled process-group and session ownership, as for
runner commands, with no kernel proof that every descendant exited. The run
prints one notice on stderr, and the supervision receipt carries no
`session_boundary`. A provider child that calls `setsid()` and reparents
between samples can outlive the run there; use Linux when that matters.

Automatic model-access probes started by a task use the same provider boundary
and retain attempt-local admission and cleanup receipts. Admission or cleanup
failure pauses the task. A probe timeout remains uncertain and can be retried;
it does not become a cached access pass.

Each automatic probe counts toward the run's recorded spend and active time.
The runner checks the remaining money/time allowance and pending human input
before each request, limits its timeout to the remaining active time, and saves
its usage before admitting another probe or workflow stage. Stop is acknowledged
only after the previous probe's cleanup is verified. Interrupted attempts retain
their evidence and usage; they require verified cleanup before a retry. Older
marker-only access passes remain readable, but their historical probe usage
cannot be reconstructed from the marker.

The dashboard's separate conversation/intake transports and explicitly invoked
standalone provider probes use other launch paths; this task-owned boundary does
not qualify them.

The owner chose this macOS fallback in
[#1195](https://github.com/charlieanna/autocode/issues/1195) (delivered by
[#1204](https://github.com/charlieanna/autocode/pull/1204)). #1265 replaced it
with strict refusal on 2026-10-14; the owner restored the fallback on
2026-10-15, so local live qualification can run on macOS.

The same holds for Kilo's read-only checkers (Tester, Completion Reviewer). On Linux they
run a qualified Kilo 7.8.3 kernel shell (#1739). On macOS, which has no such shell, they
keep Kilo's read tools and its own shell with edits denied, intersected with your Kilo
permissions. Any source change a checker still makes is put back after the stage, and the
run prints a one-line notice. Owner decision of 2026-10-21, in
[#1766](https://github.com/charlieanna/autocode/issues/1766).

Normal detached shell tools remain allowed: the independent keeper records a
prospective session before permitting `setsid()`. Its receipt preserves that
ownership through controller or keeper loss. Cleanup is verified only when the
kernel reports that no task retains the inherited boundary. Permission errors,
missing receipts and unverified cleanup remain failures.

Providers inherit `no_new_privs`, so privilege elevation through exec is
unavailable. Additional ordinary seccomp filters remain allowed, but installing
a replacement notification listener is refused. Launches with permitted or
effective `CAP_SYS_PTRACE` or `CAP_SYS_ADMIN` are also refused. The keeper is
non-dumpable to protect its listener from same-user descriptor access. This is
a process ownership boundary, not a filesystem or network sandbox.

Offline native tests cover detached spawning, thread callers, double forks,
descriptor closure, owner loss and keeper loss. They do not establish live-model
or provider-version conformance. This policy changes no role models or routes.

Runner-owned verification commands use their existing portable lifeline and
sampled SID guard, including nested preparation workers. They do not install a
provider listener or claim its kernel family-extinction proof. Commands already
inside a provider family continue to inherit that family's filter. The model
adapters always use the mandatory provider boundary; there is no CLI opt-out.

Ordinary Kilo write stages also use an effective native tool policy. Read, coding,
shell, todo, language-server and search tools are intersected with the operator's
rules; asks remain denied in headless mode. Delegation, provider-owned interactive
questions/planning gates, external-directory access and unknown tools are denied.
The stage starts a fresh restricted session so earlier session grants cannot
restore those tools. Mixed-session streams remain invalid; AutoCode never merges
subagent reports or commands into the primary session's evidence.

If the selected Kilo agent has a positive `steps` limit, its restricted AutoCode
agent keeps that bound. Kilo then forces a text-only response after that many
tool rounds; the response still has to pass AutoCode's report and completion
gates. AutoCode adds no default step limit and does not inherit another model
or broader tool grants with the bound.

## OpenCode adapter

The Requirements Gatherer, Planner and default Builder use the connections already configured in OpenCode. `--engine opencode`
is accepted but is optional for new runs:

```sh
python3 tools/autocode.py "Your rough idea" --workspace /path/to/project
```

Override the planning roles with `--requirements-model`, `--glm-model`, and
`--plan-reviewer-model` (plus their reasoning-effort flags). For a new task without
a conversation-handoff profile, `--glm-model` also selects the initial Requirements
model unless `--requirements-model` selects it separately. Conversation handoffs
apply an independent profile: Requirements keeps that profile's default unless
`--requirements-model` selects it explicitly. Resuming keeps the saved Requirements
model unless `--requirements-model` explicitly changes it.
Override execution roles with `--astra-model`, `--terra-model`,
`--sol-model`, or `--completion-model` using a provider/model ID from `opencode models`, including
`openai/…` with an OpenCode ChatGPT OAuth connection. Saved runs retain their original role
engines and sessions; no existing run is migrated by a dashboard selection.
If an account offers only one model, `--single-model MODEL` assigns it to all
roles and permits same-model verification. The roles still run in separate
sessions and all normal evidence, approval and completion gates remain active,
but model-family independence is intentionally unavailable in this mode.
OpenCode reasoning variants can be selected in the browser or with the existing
role-specific reasoning-effort flags. New joint runs start with separate GLM requirements
and planner sessions, GPT-6 Sol High for the Plan Reviewer, GLM Medium for the Builder, GPT-6
Sol High for the Validator, and a separate GPT-6 Sol Medium session for the Completion Owner.
Escalation then follows the ladders and Builder retry policy documented in
[Models](models.md). Provider
credentials remain with OpenCode: Autocode does
not read its auth file or change your global configuration.

The same approval, task, independent-evidence and completion gates apply. The adapter
uses OpenCode's [non-interactive JSON event interface](https://opencode.ai/docs/cli/#run),
validates the final report against the stage schema, and verifies command evidence
against actual completed bash events; the runner then re-runs a passing Validator's
checks itself in a clean copy ([Execution](execution.md#the-runner-re-runs-the-validators-checks)). Raw events, session IDs and stage-local
permission overrides are saved alongside the checkpoint. Token limits include cache
reads/writes and reasoning tokens. Malformed or uncertain results pause; the runner
does not automatically replay an ambiguous provider request. An explicitly retryable
native `APIError` with `ECONNRESET`, `ETIMEDOUT`, `EPIPE`, or HTTP 500/502/503/504 may
receive one fresh same-stage, same-model retry when its entire event stream contains
only that error, its collected failed process exited normally with verified owned
cleanup, and its source, Git index, task, settings and route are unchanged. The
failed attempt, raw events and usage remain charged and retained. The existing
run-wide automatic-recovery limit still applies; restarts, abandonment and route
changes do not renew a failed chain's allowance. Session continuations, partial
turns/tools/reports, uncertain cleanup, human gates, quota/auth failures and output
limits keep their ordinary recovery policy. Saved attempts without the launch
binding and non-native transports remain unsupported by this automatic retry.
The supported native error fields follow [OpenCode 1.18.33's conversion](https://github.com/anomalyco/opencode/blob/v1.18.33/packages/opencode/src/session/message-v2.ts#L600)
and [HTTP error metadata](https://github.com/anomalyco/opencode/blob/v1.18.33/packages/opencode/src/provider/error.ts#L160);
unknown metadata keeps the ordinary hold.

When a Validator or Completion Owner response is cut off by the output-token limit and the process
exited cleanly with the source unchanged, the runner archives the attempt and queues a bounded
report-only repair from the saved partial response and check evidence rather than
replaying the provider request.

OpenCode has a different isolation boundary: Requirements Planner sessions and other
read-only OpenCode roles have edit tools denied and their workspace snapshots
checked, but OpenCode tool permissions are **not an OS sandbox**. Shell commands
and configured external tools retain OpenCode's native permission policy. Autocode
also rejects a read-only report when native OpenCode step snapshots record a
workspace change during the attempt, even if the final files were restored. This
check applies when loading saved reports and when reusing review evidence for
report repair. Before a native OpenCode launch, the runner adds local Git
exclusions for `.autocode/`, `.autocode-ui/`, `__pycache__/` and `*.pyc`, matching
its existing source-snapshot boundary and preserving other exclusion rules.
This prevents runtime evidence writes from looking like source drift. The check
cannot detect writes outside the workspace, ignored files, or
transient changes between snapshots, and is unavailable when the transport omits
snapshots. Use an OS sandbox when filesystem prevention is required.
The built-in OpenCode adapter does not enable `--auto` or override user-level
permission rules with blanket allows.
A denied required operation is reported back as a blocker.
See OpenCode's [permission documentation](https://opencode.ai/docs/permissions/).

### Output cap

OpenCode derives a requested response limit from the model's listed output limit
and `OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX`, with a 32000-token default. Provider routes
can override it. On routes that honor the setting, reasoning counts against the same
limit; a planning report can exceed it and stop with finish reason `length` and
truncated JSON. AutoCode therefore launches OpenCode with
`OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=64000` unless you set a positive whole number
yourself, which wins:

```sh
OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=100000 autocode "..."
```

The setting is a request, not a verified provider limit. For example, OpenCode 1.18.33's
[OpenAI Codex plugin clears `maxOutputTokens`](https://github.com/anomalyco/opencode/blob/v1.18.33/packages/opencode/src/plugin/openai/codex.ts#L569-L573)
after OpenCode [derives it from this setting](https://github.com/anomalyco/opencode/blob/v1.18.33/packages/opencode/src/session/llm/request.ts#L114-L131).
A real Codex check returned responses exceeding the requested setting. Increasing this
variable cannot be assumed to fix truncation on that route.

AutoCode does not default to the model's whole listed limit: OpenCode's context planning
uses this setting, and some APIs refuse a request whose input plus maximum output
exceeds the window. Each stage record keeps the requested setting (or OpenCode's default
when absent) under `output_token_cap` (`tokens`, and `set_by`:
`operator`, `autocode`, or `opencode` when the variable did not reach it). A length stop's
pause distinguishes that setting from how many tokens the last response used. A collected, terminal
`length` stop offers a `route-ROLE` question (`PAUSED_OUTPUT_CAP`): answer it with a
different model on the same engine, then resume. The answer retains the incomplete
attempt, work and usage; it never launches a provider. Workflow jobs also require
the displayed job retry token and issue a new token for the explicit retry.
On a route that honors the setting, another recovery option is to request a larger limit,
set the attempt aside with `--abandon-stage`, and `--resume-paused`. Model and provider
limits still apply. Automatic report repair remains bounded to the existing read-only
Tester and Completion Reviewer stages; Builder and Requirements never retry
an output-limit stop automatically. Ambiguous or interrupted output remains uncertain.
The cap-setting rules are in `tools/autocode_output_cap.py`. Ordinary Kilo 7.8.3 uses
`KILO_EXPERIMENTAL_OUTPUT_TOKEN_MAX`, not the OpenCode variable. AutoCode sets that
native variable to 64000. A valid Kilo-specific operator value wins; otherwise a
valid legacy OpenCode value is carried across for existing callers. Kilo records
also name `variable`, and length-stop guidance names that same native setting.
A network-disabled local API qualification observes `max_tokens: 64000` with the
native default and the chosen operator override. This proves request construction,
not that every remote provider honors it. Other configured providers and the
separate native-isolation path retain their existing cap behavior.

Ordinary Kilo planning and read-only verification both use Kilo 7.8.3's qualified
kernel-constrained shell on Linux, with a writable verification copy and source writes
denied; a planning shell also cannot read the run's private state. On macOS, which has no
kernel shell for Kilo, both keep Kilo's read tools and its own shell with edits denied, and a
source write is put back after the stage. Planning report recovery has no tools. Every other native tool is disabled there. Effective operator
denies are retained; headless asks are denied rather than auto-approved. Restricted
stages start fresh sessions so previous session grants cannot override their policy.
Builder permissions and the separate native-isolation route keep their existing contract.

For an isolated native run paused before plan approval, an OpenCode authentication
failure can also be set aside explicitly with the displayed `--abandon-stage`
attempt. This requires a collected nonzero exit containing only one terminal
401/403 `APIError`, no response or report, and the original stopped supervisor,
unchanged source, settings and runtime. Other uncertain output remains refused.
The command preserves the failed attempt and pauses without launching a model.
Refresh the provider credential before `--resume-paused`; resume retains the
same models, review gates and time limits. An older run cannot adopt a changed
runtime to gain this recovery operation.

### Environment variables agents see

Every provider process, and every test command the runner executes itself, gets
the runner's environment minus variables that look like credentials: a name word
such as `TOKEN`, `SECRET`, `PASSWORD`, `KEY` or `AUTH` (`GITHUB_TOKEN`,
`AWS_SECRET_ACCESS_KEY`, `SSH_AUTH_SOCK`, `OPENAI_API_KEY`), or a URL value with
an embedded password. Proxy variables and AutoCode's ordinary `AUTOCODE_*` settings are
kept. The eight [controller token variables](cli.md#keeping-authorization-tokens-out-of-argv)
are always withheld, including when named in `AUTOCODE_PASS_ENV`. Token counts are kept:
a `TOKEN` word followed by `MAX`, `LIMIT`, `COUNT` or
`BUDGET`, or after `MAX`, holding a whole number
(`OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=100000`). Each stage record lists the withheld
names (never values) under `withheld_env`. The rules are in `tools/autocode_agent_env.py`.

Providers sign in from their own stored logins (OpenCode, Codex and Kilo auth
files, and the Claude Code login), so the default routes need none of these. If
your provider or your project's tests genuinely need one, name it:

```sh
AUTOCODE_PASS_ENV=ZHIPU_API_KEY,DATABASE_URL autocode "..."
```

Configured providers' version, model and authentication preflights use the same
filtered ambient environment as their stage processes. A check that needs an
explicitly named variable therefore needs the same `AUTOCODE_PASS_ENV` opt-in.
An authentication listing can still describe a stored route without proving the
request will authenticate. When a configured stage returns a native HTTP 401 or
403 API error, AutoCode names any withheld candidates and points to the opt-in;
those names do not establish which variable the provider requires. Pass only
variables you intend to expose to that provider and its agents. Private controller
tokens remain withheld even when listed.

Withholding variables is not a sandbox: an agent with shell access can still read
files such as `~/.aws/credentials` or `~/.config/gh/hosts.yml`.

Resuming preserves the saved engine, models and separate role sessions. Start a new
run when switching between Codex, OpenCode and Claude Code; their session IDs cannot be reused
across engines. A response with an unexpected session ID pauses the run.
OpenCode version or configuration drift pauses the saved run, including changes to
custom config-directory files, agent definitions and local plugin/tool definitions.
This adapter was live-checked with OpenCode **1.18.31**. OpenCode 2.x is accepted: the stage runs in the workspace directory with `--standalone`, and reasoning effort is `provider/model#effort` rather than `--variant`. A saved run records the OpenCode version, so resume refuses a silent major-version change. Strict tool containment and verified visual delivery stay qualified only for OpenCode **1.18.33**.

## Check provider compatibility

Before relying on a new tool/model combination, run the shared
[provider conformance probe](provider-conformance.md). It checks the same workspace,
JSON report, command evidence, usage and session rules through Codex, OpenCode,
KiloCode and Claude Code. Passing applies to that recorded tool/model/configuration combination.

## Codex provider overrides

Pass `--engine codex` to start a Codex-engine run. Roles can use different
**Responses-compatible Codex providers** in one run, e.g. planning on the ChatGPT
subscription while the Validator audits and the Builder codes via another compatible provider:

```sh
python3 tools/autocode.py "Build a greeting CLI" --workspace /path/to/project \
  --engine codex \
  --astra-model gpt-6-astra \
  --sol-model audit-model --sol-provider other_provider \
  --terra-model implementation-model --terra-provider other_provider --reasoning-effort high
```

Each role can also have its own reasoning effort. For example, use the Astra route at
extra-high (`xhigh`) for read-only discovery and planning:

```sh
python3 tools/autocode.py "Build a greeting CLI" --workspace /path/to/project \
  --engine codex --astra-model gpt-6-astra --astra-reasoning-effort xhigh
```

Role-specific effort overrides the shared `--reasoning-effort` value. All selected
models and providers are saved in the run checkpoint, so resumed runs retain this
assignment.

An interactive terminal starts in chat mode by default. The Requirements Gatherer presents a few material
questions at a time; type replies directly or `/default` to accept a proposed default.
When the build brief is displayed, type feedback to revise it, `y` to approve that exact
revision, or `/pause` to save and exit. After approval the implementation, validation
and review loop runs automatically within the configured limits. During questions,
`/feedback TEXT` sends a broader correction to the Requirements Gatherer.

Use `--chat` to select this mode explicitly, or `--no-chat` for one command per turn.
Non-interactive invocations default to the command-per-turn interface.

`--<role>-provider` is saved per role like models and is passed to Codex as a
`model_provider` override; roles without a provider keep the local Codex login
(ChatGPT auth). The named provider must implement the OpenAI **Responses** API.
For the connected Z.ai Coding Plan, use the OpenCode engine above. Changing the global
provider/auth in local Codex config still pauses a saved Codex-engine run.

### A final message that is only a shell command

Some models served through a Codex transport sometimes end a stage with the
arguments of a shell tool call instead of the stage's JSON report
([#512](https://github.com/charlieanna/autocode/issues/512)):

```json
{"cmd": "ls docs/ && wc -l docs/*.md"}
```

AutoCode treats this as a known provider defect, never as a report, and never
runs the command. A final message counts as only a command when its keys are
`cmd` or `command` (holding a command) and, at most, the other arguments of a
Codex shell call (`workdir`, `timeout_ms`, `yield_time_ms`, `max_output_tokens`,
`shell`, `login`, `with_escalated_permissions`, `justification`), and none of
them is a field of the stage's schema. A command whose text embeds a JSON report
is still a command: AutoCode does not unwrap it, though a wrapper in your
provider command may before AutoCode reads the report. The stage is rejected
with `Provider final message is a shell command ({"cmd": ...}) instead of the
JSON report; ...` and then:

1. On a transport that keeps sessions (`--engine codex`, OpenCode, or a tool with
   `output = "opencode_events"` and `resume`), the runner resumes the stage's own
   session once and asks for the report alone, with no command. This correction
   spends no report-repair attempt. It applies to every stage, planning stages
   included, but only when it would run on the route (engine, provider, model,
   effort) that started the session: a Plan Reviewer attempt that ran on its
   one-use fallback route goes straight to step 2.
2. Otherwise, or when that correction fails, the usual report-only repairs run
   (`report_repair.max_attempts`, two by default), told that the rejected report
   is a command with nothing to repair.
3. A stage that keeps doing it stops there, usually at `PAUSED_REPEATED_FAILURE`,
   with that reason in `stop_reason`. As for any stuck stage, the Investigator
   looks at it first.

**Roles affected.** Every stage's final message is checked, so every role is
covered. The defect was seen (2026-10-05) on the roles one profile ran through
Codex with the `gocode` provider: the Plan Reviewer (`astra_challenge`,
`astra_finalize`) and the Tester (`sol`). Its Builder and Requirements ran
through another tool and did not show it. No role is qualified on a route that
shows the defect: if a role keeps stopping this way, run it on another model,
and check a new tool/model pair with the
[conformance probe](provider-conformance.md) first.

## GoCode

Register GoCode as `~/.config/autocode/providers/gocode.toml` (the command wraps
`gocode env` then `codex exec`). The provider sets API credentials for its own
child process. Keep `OPENAI_API_KEY`, `CODEX_API_KEY`, and `OPENAI_BASE_URL` out
of the AutoCode process itself: ambient keys trip joint-planning's billing-route
gate (`PAUSED_BILLING_ROUTE`) even when GoCode would have supplied the right
route for the child. Unset them in the shell that launches `autocode` or
`scenarios/run.py`, or put them in the provider's `forbid_env` so AutoCode
strips them before the stage. `autocode doctor` names this gate on a Codex
setup before any run pauses: the `billing:codex` check lists the exported
variables or the `model_provider`/`openai_base_url` lines in Codex's own
config, with the exact `unset` command and `autocode connections setup` as the
API-key alternative (#2055).

Put `-o {report}` (or `--output-last-message {report}`) in the **command list**
as its own argv slot, not only inside a `sh -c` string. When Autocode sees that
slot, the stage prompt asks the model to return the JSON report as its final
response (Codex persists it). If the flag is buried in a shell string, Autocode
asks the model to write the report with a tool, which fails under
`--sandbox read-only` and yields empty Requirements contracts with
`REPORT_WRITE` blockers (docs/bugs/2026-10-12-gocode-cross-product-fixes.md).

```toml
name = "gocode"
command = [
  "sh", "-c", "eval \"$(gocode env --shell bash)\" && exec \"$@\"", "gocode",
  "codex", "exec",
  "-C", "{workspace}",
  "--sandbox", "{sandbox}",
  "--model", "{model}",
  "-c", "model_reasoning_effort=\"{effort}\"",
  "--output-schema", "{schema}",
  "-o", "{report}",
  "-",
]
prompt = "stdin"
models_command = ["gocode", "models"]
version_command = ["gocode", "--version"]

[roles]
astra = { model = "gpt-5.6-luna", effort = "high" }
terra = { model = "gpt-5.6-luna", effort = "low" }
sol = { model = "gpt-5.6-luna", effort = "high" }
completion = { model = "gpt-5.6-luna", effort = "high" }
glm = { model = "gpt-5.6-luna", effort = "low" }
plan_reviewer = { model = "gpt-5.6-luna", effort = "high" }
```

In `~/.codex/config.toml`, under `[model_providers.gocode.auth]`, set
`timeout_ms = 45000` so a slow `gocode api-key-helper` refresh does not end the
stage as a bare exit 1. Autocode names that stop as a provider auth-helper
timeout when the trailing log lines match.

The scenario harness profile `gocode-cross` (scenarios/harness/profiles.py) is
the reliability scorecard route: Luna for requirements and planning, Terra for
the Builder, Sol for the Plan Reviewer, Validator and Completion Reviewer, Astra
for the Resolver. Batch the three RELIABILITY.md cases with three repeats each:

```sh
unset OPENAI_API_KEY CODEX_API_KEY OPENAI_BASE_URL
for scenario in bugfix-iso-weeks greenfield-todo-cli feature-timesheet-by-project; do
  for n in 1 2 3; do
    .venv/bin/python scenarios/run.py run "$scenario" --profile gocode-cross \
      --i-authorize-live-model-spend --out .scenario-runs/gocode-cross-scorecard
  done
done
.venv/bin/python scenarios/run.py stats --out .scenario-runs/gocode-cross-scorecard
```

Any `FALSE_COMPLETE` blocks the change under test. A drop in pass rate is
investigated by re-running the same case on `origin/master`.

## Claude Code provider

`--engine claude` (or `--provider claude`, or `engine = "claude"` in the [project
settings](cli.md#project-settings)) runs every role through Claude Code (`claude -p`) with your
Claude Code login: no OpenCode or Codex install, no API key. It is built in
(`tools/providers/claude.py`); `--engine claude` is the OpenCode engine's facade with the provider
`claude`, so the status view shows `provider: "claude"` and every route's engine `opencode`.

```sh
claude auth login                     # once, with a Claude subscription
autocode doctor --engine claude       # claude on PATH and logged in, in the agents' environment
autocode --engine claude "Add a --json flag to the export command"
```

Default routes: Sonnet (`claude-sonnet-5-5`) drafts requirements and plans and tests, Opus
(`claude-opus-5-5`) reviews plans, resolves and completes, Haiku (`claude-haiku-4-5-20251001`) builds;
every role at effort `medium`. A stuck Builder's stronger attempt runs Sonnet and its checkers move
to Opus. The usual `--*-model` and `--*-reasoning-effort` flags take these bare ids and the efforts
`low`, `medium`, `high`, `xhigh` and `max`.

**How a stage runs.** Each stage is one process:
`python -I tools/providers/claude_stream.py … -- claude -p --output-format stream-json …` with the
stage prompt on standard input. The translator inserts the report schema (`--json-schema`), reads
Claude's stream and writes Codex-shaped events to the stage log, so sessions, usage, cost, command
evidence, the idle watchdog and the output cap work as for any stage. Claude's raw rows (stream deltas
left out) are kept beside the log as `<stage>.claude.jsonl`.

- **Sessions.** Every stage starts a fresh Claude session: its stage record has
  `expected_session: null` and its command no `--resume`. When a role's earlier session is set aside
  for this, the record says so (`fresh_session_reason`); a role's first stage and every
  joint-planning stage have no earlier session to set aside. Only a same-stage correction (a report
  that did not parse, or a shell command returned as the report) resumes the stage's own session.
- **Reports.** The report is the JSON object Claude Code's StructuredOutput tool accepted. A stage
  that ends without one gets the same-session format correction, then the bounded report repair.
  A report Claude Code rejected is never used.
- **Evidence.** Every Bash call is a `command_execution` event with Claude Code's real exit code,
  cited as `event:item_N`; capture receipts work too. A command whose exit code Claude Code did not
  report (moved to the background, interrupted, timed out, denied, or an unrecognized result) has
  exit code `null` and is never evidence: run it again in the foreground.
- **Tools and permissions.** The same `--tools Bash,Read,Edit,Write,Glob,Grep` on every stage, no MCP
  servers (`--strict-mcp-config`), no skills (`--disable-slash-commands`), project settings only
  (`--setting-sources project`). Edit and Write are pre-approved unless the stage's sandbox is
  read-only (planning stages); judging stages keep them, and the after-stage snapshot catches a
  judge's source edit. `git commit`, `push`, `checkout`, `reset`, `stash`, `branch`, `rebase` and
  `merge` are denied.
- **Login.** Before every stage of a joint-planning run, after `--accept-transport-change`, and in
  doctor, `claude auth status --json` runs in the agents' environment and must report a first-party
  Claude subscription login (`claude.ai` or `oauth_token`). An API key (`ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, an `apiKeyHelper`), Bedrock or Vertex is refused
  (`PAUSED_BILLING_ROUTE`); a variable only in your own shell is fine, because the agent scrub withholds
  it. `auth status` does not apply an untrusted project's settings, while `claude -p` trusts them, so
  before every stage, at run start and in doctor the project's `.claude/settings.json` is read too: one
  that sets `apiKeyHelper`, `awsAuthRefresh`, `awsCredentialExport` or `gcpAuthRefresh`, or an `env`
  entry for an Anthropic API key, auth token or base URL, `CLAUDE_CODE_USE_*` or
  `CLAUDE_CODE_OAUTH_TOKEN`, is refused, and so is one that is not valid JSON. A stage whose Claude
  Code still reports an API key source (`apiKeySource` in its first row) is stopped at once and pauses
  `PAUSED_BILLING_ROUTE`. A setup token reaches Claude Code only through
  `AUTOCODE_PASS_ENV=CLAUDE_CODE_OAUTH_TOKEN`. The account's email and organization are never stored
  or printed. `auth status` is necessary, not sufficient: the stage's own call proves it authenticates.
- **Environment.** The agent environment, minus ten variables that would tie the child to a parent
  Claude Code session (`CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_REMOTE_SESSION_ID`,
  `CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_CODE_DEBUG`, `CLAUDE_CODE_DIAGNOSTICS_FILE`,
  `CLAUDE_CODE_TEE_SDK_STDOUT`, `CLAUDE_CODE_MESSAGING_SOCKET`, `CLAUDE_EFFORT`,
  `CLAUDE_AUTO_BACKGROUND_TASKS`, `CLAUDE_CODE_ENTRYPOINT`), plus `DISABLE_AUTOUPDATER=1`.
- **Identity.** A saved run records the launch policy (hashed), the resolved `claude`, its version, a
  hash of `<workspace>/.claude/settings.json` and hashes of `ANTHROPIC_BASE_URL` and
  `CLAUDE_CONFIG_DIR`. A change pauses it with `PAUSED_TRANSPORT_CHANGED`; after a `claude` update,
  `autocode resume --accept-transport-change` re-checks the login and continues.
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS` is not part of it.
- **Cost.** Each stage's reported cost is that call's share of Claude Code's `total_cost_usd`
  (list price, not your subscription's spend). Claude reports a session's total, so a resumed call
  subtracts what the session had already cost (a ledger under the run's `claude-sessions/`); when
  that is unknown the stage falls back to the dated price estimate.

**Stops.**

| Claude Code says | The run |
| --- | --- |
| Output token limit (`max_output_tokens`) | `PAUSED_OUTPUT_CAP`; a truncated Tester or Completion report is repaired automatically. Raise `CLAUDE_CODE_MAX_OUTPUT_TOKENS` or name another model. |
| Context window limit (`prompt_too_long`) | `PAUSED_CONTEXT_WINDOW` (never an output cap): name a model with a larger window, or give the job a smaller task |
| Conversation compacted (`compact_boundary`) | `PAUSED_CONTEXT_WINDOW`; the report that followed the summary is not accepted |
| Subscription usage limit, spend limit, billing | `PAUSED_BUDGET` (with the reset time when Claude gave one) |
| Rate limit (429) | `PAUSED_RATE_LIMIT` |
| Overloaded (529) | `PAUSED_PROVIDER_CAPACITY` |
| Refusal | `PAUSED_CONTENT_FILTER` |
| Authentication (401/403) | `PAUSED_PROVIDER_UNCERTAIN`, naming any withheld variables; run `claude auth login` |
| An API key source in its first row (`apiKeySource`) | `PAUSED_BILLING_ROUTE`; Claude Code is stopped before the stage runs |
| Anything else, a cut stream, a start failure | `PAUSED_PROVIDER_UNCERTAIN`, no automatic replay |

The model's own words never classify a stop, and nothing is retried automatically (Claude Code
retries API errors itself). Rejections of a report that did not parse read "OpenCode final message is
not a JSON report" for every event-mode provider.

**The name `claude` is reserved.** `--provider claude` always selects the built-in provider; a user
`~/.config/autocode/providers/claude.toml` (the retired example) is ignored and doctor names it. A run
started with that example resolves to the built-in on resume and pauses `PAUSED_TRANSPORT_CHANGED` at
its next stage; resume it with `--accept-transport-change`.

**Limits.** No kernel tool containment (Claude Code's permission flags and the workspace snapshot
checks apply); no verified visual evidence; no `--task-preflight`; no hybrid split by stage; no replay
of recorded Claude runs; no Investigator report adoption; `autocode models` lists nothing for the bare
ids. `components` and `verify` take `--provider claude` rather than `--engine claude`;
`tasks` has neither and runs its lanes on Claude Code through `AUTOCODE_PROVIDER=claude`,
`default_provider = "claude"` or the project's `engine = "claude"`. Claude Code loads `CLAUDE.md` files of the workspace and its ancestors, so keep a
scenario's `--out` outside this repository. In a root cloud container, prefix runs with
`setpriv --bounding-set=-sys_admin,-sys_ptrace` ([cloud sessions](../examples/claude-provider/CLOUD-SESSION.md)).

## Add a tool

OpenCode and Claude Code are built in. Any other tool registers with one TOML file, not a Python
package. `--provider <name>` loads `~/.config/autocode/providers/<name>.toml` and,
if that file is absent, the bundled example at `tools/providers/configs/<name>.toml`.
Configs inside a project are not loaded. The names `opencode` and `claude` are reserved for the built-in
providers; a user config with either name is never read. [KiloCode](#kilocode) is a worked example.

```toml
name = "kilocode"
command = ["kilo", "run", "--auto", "--dir", "{workspace}", "--model", "{model}", "--variant", "{effort}", "--format", "json"]
prompt = "stdin"                        # or "file" (uses {prompt_file})
output = "opencode_events"              # or "report_file"; see below
resume = ["--session", "{session}"]     # optional; enables saved sessions
models_command = ["kilo", "models"]     # optional; or a static list: models = [...]
version_command = ["kilo", "--version"]

[roles]
astra = { model = "openai/gpt-5.6-sol", effort = "high" }
terra = { model = "openai/gpt-5.6-terra", effort = "medium" }
sol = { model = "openai/gpt-5.6-sol", effort = "high" }
completion = { model = "openai/gpt-5.6-sol", effort = "medium" }
glm = { model = "zhipuai-coding-plan/glm-5.3", effort = "high" }
plan_reviewer = { model = "openai/gpt-6-sol", effort = "high", light_effort = "medium" }
```

A connection whose `command` runs `kilo` under another name (a copy of this file in
`~/.config/autocode/providers/NAME.toml` with your own roles and routes) is a Kilo connection:
it gets the same launch as `kilocode`, with the activity plugin, the output cap, the native
variant check and the read-only checker policy, rather than the plain command launch (#2010).

For clear, small tasks the Plan Reviewer may use `light_effort` (or one lower
standard effort tier) only when a new run used an automatic/default effort.
An explicit reviewer effort, including a project effort setting, wins. Saved
runs without effort provenance retain their existing effort on upgrade; model
pins alone do not establish effort provenance. The review-call allowance still
applies independently of this effort choice.

`[roles]` must keep each verifier on a different model family from what it checks
(Planner/Plan Reviewer, Builder/Validator, Builder/Completion Owner); a run whose
roles break that pauses with `PAUSED_CROSS_MODEL` before any agent is launched.

Placeholders are `{model}`, `{effort}`, `{workspace}`, `{report}`, `{schema}`,
`{prompt_file}`, `{run_dir}`, `{role}`, and `{sandbox}`. The opt-in Codex
artifact adapter below adds the standalone `{sandbox_args}` argv splice. `{sandbox}` is
`read-only` for planning, `workspace-write` for the builder, and
`workspace-write` for the judging stages (Validator, Completion Owner,
milestone checkpoint): their contract requires writing runner-owned
operational files under `.autocode/` (capture receipts, reports), which
`read-only` forbids. Their source contract stays enforced by the runner's
after-stage workspace snapshot, not by the sandbox.
`{effort}` reaches the tool only if the command uses it.
Use `{{` and `}}` for literal braces in a command argument, such as
`${{VAR}}` or `{{"key":1}}`; single braces are reserved for placeholders.
Changing the config file or the tool version pauses a saved run.

### A different tool for one role

For a **new** run on the OpenCode facade engine (`--engine opencode` with any
provider, or `--engine claude`), repeat `--role-transport ROLE=PROVIDER` to run
only those roles on another connected tool. `ROLE` is the existing CLI code name,
such as `sol` or `completion`; `PROVIDER` names a tool with its own identity: a
registered TOML config such as `kilocode`, the built-in `claude` provider, or the
`autocode` direct connection. It cannot name the run's own tool, and `--engine
codex` keeps Codex's own transport for every role. Each selection requires an
explicit corresponding model flag. Select its exact model ID from that tool's
catalogue, including a bare ID when the tool publishes one:

```sh
autocode models --provider opencode
autocode models --provider kilocode
# Replace the placeholder with an ID actually offered by the second catalogue.
autocode "Your task" --engine opencode --provider opencode \
  --role-transport sol=kilocode --sol-model '<MODEL_FROM_KILO_CATALOGUE>' \
  --sol-reasoning-effort high
# A run on Claude Code with the Tester on a Kilo account (#2049).
autocode "Your task" --engine claude \
  --role-transport sol=kilocode --sol-model '<MODEL_FROM_KILO_CATALOGUE>'
```

The other roles retain their routes selected by the ordinary model flags and
OpenCode defaults. Roles derived mid-run (such as Analyst, Investigator, Architect,
Acceptance Tester, Resolver, Stuck Investigator, or Utility) inherit the external
tool transport identity bound to their source role when their own route is on that
tool. A derived role whose own route stays on the run's tool, such as a Resolver
given its own `--resolver-model` on OpenCode, inherits nothing and runs there. Keep the GLM and MiMo role flags when using a profile that
assigns them requirements, planning, building, plan review or resolution; a
role selection does not assign every role to the new tool. Each tool checks
only its own selected models and auth routes. At build start, the model-access
probe covers every tool whose config declares `[auth]` billing routes, including
a selected tool when the run's own tool declares none. It sends each role's model
only through the tool that runs that role. Before the first request, it checks
the login of each of those tools that still has a model to probe; a tool whose
models all passed earlier in the run is not probed again. A pass on one tool
never counts for the same model on another. Independent verification and all
ordinary approval, evidence and completion gates still apply.

The binding is immutable once the run starts. Do not repeat `--role-transport`
when advancing or reattaching with `TaskRun`; pass it in `start_options` only.
A missing or mismatched saved identity cannot fall back to the default tool.
Changes to a bound config fingerprint, executable path or version pause rather than move its
session to another tool; `--accept-transport-change` does not replace these
bindings. This selection cannot combine with `--single-model` or recorded
replay.

Ordinary transport compatibility does **not** qualify a tool for native role
isolation or image acceptance. Those flows still require their exact supported
native owner and independently verified containment/image transport. Registered
Kilo roles remain refused by those guards until such qualification exists; a
model appearing in its catalogue is insufficient. Check the concrete tool/model
pair with [provider conformance](provider-conformance.md) before relying on it.

### Builder retry on a tool

A Builder that keeps failing a milestone gets one ordinary retry and then one stronger
attempt ([Models](models.md#builder-retry-policy)). By default that attempt runs GPT-6 Sol,
and a checker on GPT-6 Sol moves to GLM; a tool whose model list (`models` or `models_command`)
lacks GPT-6 Sol, or lacks GLM while a checker would move to it, gets only one more ordinary retry
instead. A tool that serves other models names its own in an optional
`[builder_retry]` table (the built-in claude provider uses the same policy: Sonnet, checked by Opus):

```toml
[builder_retry]
strong_model = "claude-sonnet-5-5"   # the Builder's stronger attempt
checker_model = "claude-opus-5-5"    # a Tester or Completion Reviewer on strong_model moves here
strong_effort = "xhigh"              # optional; xhigh when left out
```

Both models are the tool's own spelling. When the config lists `models`, both must be in
it. `strong_model` must differ from the `[roles]` Builder's model, and `checker_model` must be
neither `strong_model` nor another model of its GLM or MiMo family; a config that breaks either
is refused when it loads. A new run takes the
table when it is created (`--builder-strong-model` still wins). A Builder that already runs
the strong model makes its stronger attempt on that model at `strong_effort`. When the stronger attempt
starts, a Tester or Completion Reviewer that runs the strong model moves to `checker_model`
until the next milestone, as does one on another model of the strong model's GLM or MiMo family.
When the Builder's model has no `provider/` prefix it moves only when it runs the strong model
in the tool's spelling (or such a family member), is neither pinned nor on another provider, and
`checker_model` is a model the run already routes a role to; otherwise the run pauses with
`PAUSED_BUILDER_RETRY_LIMIT` before changing any route. A config without the table keeps the
default models, and `provider/model` names such as KiloCode's keep the earlier rule (a checker
that cannot move is left for the cross-model guard). Adding or changing the table
changes the config file, so saved runs on that tool stop with `PAUSED_TRANSPORT_CHANGED` at
their next stage: change it between runs.

### Output modes

`output` says how the result comes back:

- `output = "report_file"` (the default): the tool writes exactly one JSON object
  to `{report}`, as `codex exec -o` does. Every stage starts fresh. Command
  evidence is a `capture_command` receipt file, not an `event:` id. These tools
  need supported usage events for token accounting; otherwise those counts
  remain unknown in the usage ledger.
- `output = "opencode_events"`: the tool prints OpenCode-format JSON events, as
  `opencode run --format json` and `kilo run --format json` do. Autocode reads
  the final report, token usage and command exit codes from those events, and
  resumes each role's session with the required `resume` template, for example
  `resume = ["--session", "{session}"]`.

Every structured handoff for these output modes includes an executable
`capture_command`, including standalone review, design, discuss and investigation
jobs. Models append `--output .autocode/evidence/<unique-name>.json -- <command>`
to that value. It uses the public `autocode.py capture` entry point; the
`autocode_capture_command.py` implementation module is not a standalone command.
An existing handoff command and its explicit output mode are preserved; the
supplied default inherits the runner's output-mode environment. This supplies
tool context without granting additional filesystem permissions.

The runner can fill an omitted check exit code from a unique executed event or
the cited, verified capture receipt before checking the unchanged report schema.
It never replaces a supplied exit code, guesses from output text, or resolves
ambiguous executions. The original report is retained as `.reported.json`, and
the stage records the derived metadata. Nonzero exits remain failures.
For `report_file` tools, capture commands must inherit `AUTOCODE_CAPTURE_CONTEXT`
from the tool process: the capture helper records it automatically and the
validator checks it against the saved attempt. Receipts from another attempt,
missing capture context, or changed output hashes are rejected. Event providers
continue to require their independent tool-event attestation.

A stage log holds the tool's stdout and stderr. When a tool exits with an error,
AutoCode reads the provider's reason from its `error` and `turn.failed` JSON
events and from the plain-text error lines (`ERROR: …`, `Error: …`) that end the
log, as `codex exec` without `--json` prints them. So
`ERROR: exceeded retry limit, last status: 429 Too Many Requests` stops the stage
as a rate limit, and a used-up plan or a busy model as a quota or capacity stop
([Models](models.md#when-a-roles-quota-runs-out)). Tool output earlier in the log
is never read as the provider's error, even when it mentions 429.

### Codex commands that write capture receipts

A registered `report_file` command that passes `--sandbox read-only` to Codex
cannot create AutoCode's required capture receipts. For that command, opt in to
`sandbox_adapter = "codex_artifacts"` and replace the legacy sandbox flag and
`{sandbox}` value with the standalone `{sandbox_args}` argument:

This adapter permits `-C`/`--cd` only with the exact `{workspace}` binding; literal
or derived destinations and other sandbox/worktree overrides are refused before
launch. Config arguments must use a valid `key=value` override, with policy and
project-trust keys reserved for the adapter. JSON blobs are not config overrides.

```toml
name = "codex_receipts"
sandbox_adapter = "codex_artifacts"
command = ["codex", "exec", "--ephemeral", "{sandbox_args}", "-C", "{workspace}", "--model", "{model}", "-c", "model_reasoning_effort=\"{effort}\"", "-c", "approval_policy=\"never\"", "-c", "forced_login_method=\"chatgpt\"", "--output-schema", "{schema}", "-o", "{report}", "--json", "-"]
prompt = "stdin"
output = "report_file"
version_command = ["codex", "--version"]

[roles]
astra = { model = "gpt-6-sol", effort = "medium" }
terra = { model = "gpt-6-luna", effort = "medium" }
sol = { model = "gpt-6-sol", effort = "medium" }
completion = { model = "gpt-6-sol", effort = "medium" }
glm = { model = "gpt-6-luna", effort = "medium" }
plan_reviewer = { model = "gpt-6-sol", effort = "medium" }

[auth]
command = ["codex", "login", "status"]
forbid_env = ["OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"]
[[auth.routes]]
models = "gpt-"
pattern = "(?m)^Logged in using (ChatGPT)$"
expect = "ChatGPT"
```

Save this as `~/.config/autocode/providers/codex_receipts.toml`, then select it
with `autocode "Your task" --provider codex_receipts`. Choose models available
to your login. Registered command providers synthesize Requirements defaults
from the config's own `glm` model and effort. For a new provider-configured task,
`--glm-model` overrides the model for both Planner and Requirements;
`--requirements-model` wins for Requirements when given. The provider's Requirements reasoning default stays
independent of `--glm-reasoning-effort`; use `--requirements-reasoning-effort` to
change it. OpenCode keeps its separately defined Requirements defaults when
neither model override is given.

An existing Requirements route keeps its saved model when `--glm-model` changes
the Planner. Enabling joint planning at a supported migration boundary uses the
same fallback only when creating a missing Requirements route. Pass
`--requirements-model` to explicitly change that role, or `--single-model` on a
new run to move every role.

This adapter requires **Codex CLI 0.160.0 or later**, checked before a model
request. For planning and review it selects a named permission profile derived
from `:read-only`, adding writes only to the workspace's `.autocode/evidence/`,
`.autocode/output/`, and the exact assigned stage JSON report. The Builder keeps
`workspace-write`. Codex's `-o` option saves the final JSON response; the model
still runs actual capture commands to produce independently checked receipts.
No permission answer automatically changes this profile.

**Opting in passes `--ignore-user-config` to each Codex invocation.** Global files
are untouched and the stored login is reused, but user-level configuration,
plugins, MCP servers and provider defaults are omitted. Supply any required,
compatible options explicitly in the command. Managed requirements still apply.
Do not combine the adapter with legacy sandbox flags, additional writable roots,
configuration profiles or permission-policy overrides. The adapter rejects those
combinations instead of silently overriding a policy.

Operational paths must be canonical and unaliased. Pre-existing symlinks,
hard-linked files and special files in the writable operational directories are
rejected before launch; AutoCode does not delete or rewrite them. A stage report
must be a JSON file under the run's `iterations/` directory. Existing source
snapshot, receipt identity, hash, regression-proof and completion checks still
apply. Language tools that need additional caches must use an allowed operational
path or a separately configured provider; this adapter does not grant arbitrary
cache directories.

Existing provider configs and `--engine codex` keep their configuration semantics.
Changing a saved run's provider config triggers its existing transport-change
pause; inspect and accept that change through the public recovery command.
Permission-profile behavior has been exercised on macOS with Codex 0.160.0;
other platform/version combinations need their own conformance check.

### Auth checks

A tool can declare how its subscription login is checked. Without an `[auth]`
table Autocode does not inspect the tool's login. With one, before an OpenAI
role starts, Autocode runs `command`, strips terminal color codes, and requires
every match of `pattern` to equal `expect`. A mismatch, a failed listing, or a
variable in `forbid_env` pauses the run with `PAUSED_BILLING_ROUTE` before any
model is called:

```toml
[auth]
command = ["kilo", "auth", "list"]
forbid_env = ["OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"]

[[auth.routes]]
models = "openai/"
name = "OpenAI"
billing = "subscription"
pattern = "^\\s*[●•]\\s+OpenAI\\s+(\\S+)\\s*$"
expect = "oauth"
```

A route may also carry `name` (the login name the tool itself prints; defaults to
the prefix) and `billing` (`"subscription"` or `"pay per token"`; unknown when
unset). These feed automatic route selection (#1426): the discovery step reads the
same listing to learn which accounts are connected and which models each one can
bill, so a model no connected login serves is never offered to the selector. A
command template whose `{effort}` arguments (like Kilo's `--variant {effort}`) get
no reasoning level omits those arguments instead of passing an empty value, and the
model uses its own default level.

A tool that reports a subscription usage limit or runs out of pay-as-you-go
credit pauses the run with `PAUSED_BUDGET`.

### KiloCode

`tools/providers/configs/kilocode.toml` is bundled. It runs `kilo run --auto`,
and on Linux a launch refuses any release but the qualified **7.8.3** (`npm install -g
@kilocode/cli@7.8.3`): restricted read-only and planning stages need its kernel
shell, so a newer Kilo stops before any spend instead of running uncontained. macOS
has no such shell and no pin; its checkers run as described above (#1766).
Its default routes use ChatGPT OAuth for `openai/...`, Zhipu AI Coding Plan for
`zhipuai-coding-plan/...`, and Alibaba Token Plan for `alibaba-token-plan/...`.
Optional `zai-coding-plan/...` and `kilo/...` routes require their separate
Z.AI Coding Plan `api` and Kilo Gateway `oauth` logins. Check `kilo models` and
`kilo auth list` for your installed version and configured account; distinct
provider names are not credential aliases. Connect the routes you select with
`kilo auth` first. The plan reviewer is GPT-5.6 Sol here because Kilo has no
Cursor ACP route:

```toml
name = "kilocode"
command = ["kilo", "run", "--auto", "--dir", "{workspace}", "--model", "{model}", "--variant", "{effort}", "--format", "json"]
prompt = "stdin"
output = "opencode_events"
resume = ["--session", "{session}"]
models_command = ["kilo", "models"]
version_command = ["kilo", "--version"]

[roles]
astra = { model = "openai/gpt-5.6-sol", effort = "high" }
terra = { model = "openai/gpt-5.6-terra", effort = "medium" }
sol = { model = "openai/gpt-5.6-sol", effort = "high" }
completion = { model = "openai/gpt-5.6-sol", effort = "medium" }
glm = { model = "zhipuai-coding-plan/glm-5.3", effort = "high" }
plan_reviewer = { model = "openai/gpt-5.6-sol", effort = "high" }

[auth]
command = ["kilo", "auth", "list"]
forbid_env = ["OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"]

[[auth.routes]]
models = "openai/"
pattern = "^\\s*[●•]\\s+OpenAI\\s+(\\S+)\\s*$"
expect = "oauth"

[[auth.routes]]
models = "alibaba-token-plan/"
pattern = "^\\s*[●•]\\s+Alibaba Token Plan\\s+(\\S+)\\s*$"
expect = "api"

# Each coding plan keeps its own login and quota; the gateway is an explicit paid route.
[[auth.routes]]
models = "zai-coding-plan/"
pattern = "^\\s*[●•]\\s+Z\\.AI Coding Plan\\s+(\\S+)\\s*$"
expect = "api"

[[auth.routes]]
models = "zhipuai-coding-plan/"
pattern = "^\\s*[●•]\\s+Zhipu AI Coding Plan\\s+(\\S+)\\s*$"
expect = "api"

[[auth.routes]]
models = "kilo/"
pattern = "^\\s*[●•]\\s+Kilo Gateway\\s+(\\S+)\\s*$"
expect = "oauth"
```

Copy it to `~/.config/autocode/providers/kilocode.toml` to change models or
reasoning levels. Select an ID published by your `kilo models` whose prefix is
declared in `[auth]` and whose login passes the check. Available model prefixes
and login rows depend on your Kilo installation and configured account.
If a selected model matches no configured route, `PAUSED_BILLING_ROUTE` lists
those routed prefixes before running the login check. Choose a published model
with one of those prefixes; add a route only when you intend to configure it.
The message lists your loaded configuration, including user-defined routes.
`kilo/...` IDs bill the Kilo Gateway pay-as-you-go account instead of a
subscription. The `[auth]` table above runs `kilo auth list` before a role on any declared route and pauses with
`PAUSED_BILLING_ROUTE` if its own login is absent or has the wrong mode:

| Model prefix | Kilo login | Required mode |
| --- | --- | --- |
| `openai/` | OpenAI | `oauth` |
| `alibaba-token-plan/` | Alibaba Token Plan | `api` |
| `zai-coding-plan/` | Z.AI Coding Plan | `api` |
| `zhipuai-coding-plan/` | Zhipu AI Coding Plan | `api` |
| `kilo/` | Kilo Gateway | `oauth` |

Each coding plan uses its separate login; a gateway login does not satisfy a
coding-plan route. A missing `Kilo Gateway oauth` row refuses Gateway calls.
For these routes the check also refuses a set
`OPENAI_API_KEY`, `CODEX_API_KEY`, or `OPENAI_BASE_URL`. When `[auth]` declares
routes, every selected model must match a declared prefix; add an
`[[auth.routes]]` entry for another vendor before using it.

Before a Kilo launch, AutoCode checks an explicit reasoning effort against that
model's native `kilo models PROVIDER --verbose` variants. An unsupported or
unverifiable effort stops before inference; it is never silently remapped.
For example, GLM-5.3 declares `low`, `high` and `max`, not `medium`; an unrecognized
variant can otherwise be ignored by Kilo and use the provider's default.
The new bundled Kilo GLM default is High. Existing runs and operator-owned configs
retain their selections and must explicitly correct an unsupported one using the
corresponding `--ROLE-reasoning-effort` flag. A deliberately unspecified effort
still uses the model's default.

The bundled command uses Kilo's `--auto` for noninteractive stages: ordinary
permission requests are approved once; effective explicit `deny` rules in
Kilo's configuration and selected agent still block the tool. AutoCode does
not replace that permission configuration or enable Kilo's permission-bypass
flags. Without `--auto`, Kilo rejects the first request and exits with an
auto-rejected-permission error. If you copied an older provider config to
`~/.config/autocode/providers/kilocode.toml`, add `--auto` to its command too;
the bundled update does not replace your custom file. AutoCode's plan approvals,
human reviews and recovery choices still require their own user input.

**Restricted stages keep your effective policy — asks included, as denials.**
A stock Kilo 7.8.3 install resolves its default agent with `bash` set to *ask*,
allowing only a fixed list of benign command prefixes (`cat *`, `ls *`,
`grep *` and similar; no `test`, `git` or compound shell lines). Every ordinary
Kilo stage — the kernel-contained read-only planning and review stages, the
read-only checks that use Kilo's own shell where no kernel sandbox is
qualified, and the writer stages — rebuilds the selected agent's effective
permission into a fresh per-launch agent: explicit allows pass through, and
every `ask` or `deny` stays a `deny`, because a headless stage must not widen
an ask into an execution grant. AutoCode's own model-free conformance probes
run compound shell lines that match none of the stock allowed prefixes, so on
a stock install the first kernel-contained read-only stage pauses
`PAUSED_TOOL_CONTAINMENT` with "Native tool permissions deny the model-free
conformance probe" before any model request. `--auto` cannot grant these: it
answers permission requests Kilo raises while running, and the per-launch
agent already carries the ask as an explicit deny, so no request is ever
raised.

**Selecting the shell policy for a trusted task.** When you deliberately trust
a task with a shell, start `autocode` with a Kilo configuration whose effective
permission allows `bash`. One explicit example, for a process that sets no
inline configuration of its own:

```sh
KILO_CONFIG_CONTENT='{"permission":{"bash":{"*":"allow"},"external_directory":"deny"}}' \
  autocode chat "Fix the flaky login test" -- --provider kilocode
```

The `bash: {"*": "allow"}` rule is broad command authority scoped to this
process and its stages, not a minimal grant; a narrower operator-defined
allowlist may serve a given task instead — nothing here requires the blanket
shape. Inline configuration is appended after Kilo's defaults, so the last
matching rule wins, and your `~/.config/kilo/kilo.jsonc` file is untouched;
that on-disk config merge is a separate mechanism from this environment
variable. If a `KILO_CONFIG_CONTENT` value already exists, merge these
`permission` fields into that object and keep every other key — never replace
the whole value with this example — and where the existing policy holds
explicit `deny` or `ask` rules, review them deliberately rather than copying
an allow over them. AutoCode adds its own per-launch agent, activity plugin
and compaction settings by merging into the object it receives, and the
restricted agent always carries `external_directory` denied. This is a
per-task decision, not a setup step: skip it when your Kilo configuration
already gives the effective agents explicit `bash` rules (those pass the
intersection unchanged), and do not use it for repositories you do not trust.
On the kernel-contained read-only stages the bubblewrap boundary, not this
permission line, is what confines the shell; the permission line is what lets
AutoCode prove that boundary model-free before launching anything.

**Context compaction is off.** Kilo summarizes a conversation on its own once it
nears the model's window (its `compaction.auto`, at the window minus the larger of
the output cap and a 20k buffer). A stage must see its handoff whole, so every
ordinary launch sets `compaction.auto` to `false` in `KILO_CONFIG_CONTENT`. A request
that still exceeds the window is rejected by the API (`ContextOverflowError`,
`context_length_exceeded`); Kilo then compacts anyway and carries on, so the runner
refuses that response: the stage pauses `PAUSED_CONTEXT_WINDOW`, naming the request
size, and asks `route-ROLE` for a model with a larger window (or set the attempt aside
and give the job a smaller task, for example a narrower `--package`). The activity
plugin forwards any compaction part Kilo writes as an `autocode_compaction` row, and
a request whose input is less than half of the previous one's (which held at least
32k) is treated as the same signature, so a compaction the stream did not announce
is still refused. The native-isolation launches keep disabling Kilo's compaction
agent as before. The rules are in `tools/autocode_context_window.py`.

Where each stage's boundary comes from: on Linux, read-only planning and review
stages are kernel-contained — AutoCode prepares a per-launch bubblewrap boundary
(fresh user, PID, IPC and network namespaces, a fresh `/proc`, every capability
dropped, the workspace mounted read-only) and proves it with model-free probes
before any launch, including the readonly planning shell's denial of private
run-state reads where that boundary is supplied. On hosts where no kernel
sandbox is qualified, such as macOS, read-only checks instead run with Kilo's
own permission-restricted shell and edits denied, and any source change is put
back by the workspace snapshot check. Writer stages get no kernel sandbox and
no filesystem boundary from this command: the workspace snapshot checks
inspect workspace changes afterwards and are not an OS boundary around the
writer process, and `external_directory: "deny"` is a native tool policy, not
a filesystem sandbox for bash. Use explicit Kilo permission restrictions for
operations that must be blocked before execution. See Kilo's
[permission reference](https://kilo.ai/docs/code-with-ai/platforms/cli#permissions).

The `alibaba-token-plan/` route serves that login's own plan rather than the
Gateway: Qwen (`qwen3.8-max`, `qwen3.7-plus`, `qwen3.6-flash` and the rest of
`kilo models`' `alibaba-token-plan/` list), and on the same login DeepSeek, GLM,
Kimi and MiniMax. Because one login reaches several vendors, a Builder and the
Tester that checks it can be different model families, which is what the
cross-model guard is for.

### Default provider

New runs use OpenCode unless you choose otherwise. When OpenCode is not
installed and nothing chooses (no `--engine` or `--provider`, no
`AUTOCODE_PROVIDER`, no `default_provider`, no project `engine`), a new run
takes the first connected backend in this order: Claude Code signed in to a
subscription, Codex signed in, the AutoCode direct connection with a configured
API key, then Kilo with a connected account (#2049; the order is the proposed
"default engine" answer in #2054). The run prints `Engine for this run: …` with
the reason, `doctor` names the same choice in its `engine` row, and a saved run
keeps the engine it started with. Each check is the backend's own login listing,
as `autocode connections check` reads it; no model is called and no credential
is read. `--provider <name>` picks the
tool for one run. To change the default for every new run and for the dashboard,
set `AUTOCODE_PROVIDER=kilocode` or add this to `~/.config/autocode/config.toml`:

```toml
default_provider = "kilocode"
```

A saved run keeps the provider it started with, and `--engine codex` runs still
use Codex. `autocode-dashboard --provider <name>` overrides the default for the
dashboard; its model pickers list that tool's models.

See also: [Models](models.md) · [Install](install.md) · [CLI](cli.md)

## Conformance matrix

<!-- BEGIN provider conformance matrix -->

Generated by `python tools/provider_matrix.py --write` from
[tools/provider-matrix.json](../tools/provider-matrix.json). Do not edit by hand.
A version the offline conformance suite has not been run against is
`untested` — never guess. Run the suite with
`python tools/provider_conformance.py --fake` (no model calls).

| Engine | Version | Transport | Containment | Visual | Result | Date | Commit | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| opencode | 1.18.33 | opencode | qualified | qualified | known-good | 2026-10-08 | `a8852cf4` | The version the glm53-mimo three-case reliability sweep ran on (docs/bugs/2026-10-06-opencode-2.md). |
| opencode | 1.18.31 | opencode | untested | untested | known-good | 2026-10-01 | `68e89aa4` | Live-checked in the 2026-10-01 glm53-openai sweep; containment/visual stay qualified only for 1.18.33. |
| opencode | 2.0.20 | opencode-standalone | refused | refused | accepted-for-ordinary-runs | 2026-10-06 | `9a31a8e8` | Uses --standalone and a provider/model#effort id. A 2.x binary fails the containment and visual checks closed until it has its own conformance pass (docs/bugs/2026-10-06-opencode-2.md). |
| codex | native | codex | n/a | n/a | known-good | 2026-10-03 | `b96a633e` | Native Codex probes in docs/provider-conformance.md; ChatGPT login required. |
| kilocode | registered-adapter | kilocode | n/a | n/a | known-good | 2026-10-03 | `b96a633e` | Registered KiloCode adapter; see docs/provider-conformance.md. |
| autocode | direct-1 | direct-compatible | qualified | n/a | known-good | 2026-10-26 | `ed2a01957` | The direct engine's own runtime, kernel-contained shell and event stream against a loopback model fixture (#2055); covers the OpenAI/OpenRouter-compatible API-key transport. Sessionless engine: build and validate boundaries. The Anthropic-messages transport and Google remain pending. |

<!-- END provider conformance matrix -->

### Connection setup and company backends

`autocode connections list --json` lists built-in Kilo, OpenCode, Codex and Claude Code
and user-registered providers (Codex is an engine, `--engine codex`; its row says whether it is
installed and signed in). `connections check --provider NAME --json` checks
credentials without model traffic. `connections login --provider NAME` opens the
backend's own interactive sign-in. AutoCode does not store a second copy of keys,
SSO sessions, or AWS credentials. Kilo/OpenCode login lets the backend choose its
supported API-key or browser flow; existing route/billing checks still apply
before a task launches.

A company GoCode adapter can keep its corporate authentication and Bedrock
routing. AutoCode does not require it to go through Kilo or OpenCode. In a
registered provider's user-level TOML, optionally add:

```toml
[connection]
login_command = ["your-company-cli", "its-documented-login-command"]
status_command = ["your-company-cli", "its-documented-login-status-command"]
```

These are argument arrays executed without a shell. Use the company's documented
commands; the names above are placeholders, not GoCode commands. The status
command must make no inference requests: exit 0 for credentials present, 1 for
sign-in required, and another code for a failed check. Its stdout/stderr is
withheld from clients. The interactive login command owns its terminal output.
Without these optional commands AutoCode reports login as unchecked and offers
no guessed sign-in command. Provider files are trusted user configuration; files
in a project are never discovered as connection definitions.

### AutoCode direct connections (issue #1493)

AutoCode includes its own text/tool runtime. It does not require Kilo, OpenCode,
Codex, Claude Code, or another coding-agent executable. In VS Code open
**Connections → AutoCode · Direct connection → Set up AutoCode**, or run:

```sh
autocode connections setup
autocode connections add openai
autocode connections check --provider autocode --json
autocode "hi" --provider autocode --joint-planning --spec-first
```

Setup is interactive in a terminal and launches no model. Choose **mimo**,
**zai**, **openai**, **openrouter**, **claude**, or a custom **compatible** API.
`connections setup` keeps one provider per connection; `connections add
PROVIDER` (issue #2055) configures several providers at once, each with its own
key in the same private store, and assigns each role to its provider — for
example Planner/Builder/Requirements on an OpenAI key and the independent
reviewer/tester roles on a Claude key, so verifier independence is satisfied
without flags. `add` reports immediately when the resulting routes are not
independent; re-adding a provider with a blank key rotates nothing and keeps
the other providers' keys. Google's Gemini API is refused until its own
transport ships; openrouter reaches many vendors today through one key. A
version-1 single-provider connection keeps working and upgrades in place the
first time a second provider is added.

| API choice | Protocol | Authentication | Response limit field |
| --- | --- | --- | --- |
| MiMo | Chat Completions | `api-key` | `max_completion_tokens` |
| Z.ai | Chat Completions | Bearer key | `max_tokens` |
| OpenAI / OpenRouter / custom compatible | Chat Completions | Bearer key | `max_completion_tokens` |
| Claude | Anthropic Messages | `x-api-key`, version `2023-06-01` | `max_tokens` |

MiMo and Z.ai require an explicit base URL, including its version path. Use the
exact endpoint and matching key for your billing route: ordinary API access and
Token/Coding Plan subscriptions have separate endpoints. Setup shows the API
examples and requires you to choose; it never falls back to another billing
route. OpenAI defaults to `https://api.openai.com/v1`; OpenRouter defaults to
`https://openrouter.ai/api/v1`; Claude defaults to
`https://api.anthropic.com/v1`. Endpoint overrides require HTTPS (HTTP is allowed
only for loopback fixtures). This setup accepts API keys, not ChatGPT or Claude
subscription logins. Kilo continues to own its separate sign-in and routes.

MiMo/Z.ai assistant messages retain `reasoning_content` across tool turns.
Claude uses native `tool_use`/`tool_result` blocks and replays signed thinking
blocks in order. Unsupported content and incomplete responses fail closed.
Nonempty Chat Completions effort overrides are sent as `reasoning_effort` and
must be supported by that model. Claude effort overrides are currently refused;
its default reasoning is used. Redirects are refused, with no automatic
parameter change, retry or provider fallback. Bedrock is outside this scope.

Model IDs are those the endpoint accepts, not Kilo/OpenCode routing aliases.
Setup groups Planner/Builder/Requirements and independent reviewers/testers; the
existing verifier-independence rules still apply. Reopening setup with blank
model answers preserves every saved role assignment. `models --provider autocode`
lists the configured roster; unconfigured model escalation is refused.

The API key lives in a separate `auth.json` file, mode `0600`, under
`$XDG_CONFIG_HOME/autocode/direct/` (default `~/.config/autocode/direct/`), whose
mode is `0700`. This is a private local file, not an OS keychain. The credential
is bound to the API endpoint: changing the endpoint requires an explicit key for
that destination, and in a multi-provider connection each provider's key is
bound to that provider's endpoint alone. Status and launch arguments never
contain key values. Listing
connections reads metadata only; **Check setup** validates local setup, not live
model access. `configured` and `setup_required` are distinct.
Key rotation for the same endpoint preserves transport identity; changes to the
API provider, endpoint, models or runtime change it and trigger the saved
run's existing drift checks. Configuration is never read from project files.

Select **Use for new chats** after setup. This does not replace an existing
Kilo/GoCode connection or change its model routes. CLI callers explicitly pass
`--provider autocode`; setup does not silently change the global default.

The existing supported-Linux provider supervisor owns each launch and cleanup.
Model-requested commands run only through AutoCode's existing kernel shell
boundary, with credentials removed, no network, protected run files, and the
stage's read/write authority. Read-only execution uses the existing verification
copy mechanism. There are at most 32 model responses per stage and eight tool
calls per response, an 8192-token requested response cap, a 60-second HTTP read
bound, and a 90-second tool bound; outer task/stage/spend limits still apply.
Tool output is bounded at 65536 bytes and explicitly marked when truncated.
Actual tool output and exit codes supply evidence; model prose cannot manufacture
command events. Reported input/output usage is retained across requests, including failed turns;
output counts include reasoning. A separate reasoning-token breakdown is not yet
reported by this runtime. Missing token fields and prices remain unknown.

This initial runtime supports text and shell tools with a fresh context per
stage. It does not provide native image qualification, in-process file tools,
MCP tools, or external-agent session resumption. Local HTTP fixtures and
supported-Linux task tests verify protocol and safety behavior; they do not
establish live model access. The implementation stays draft
pending live qualification with an explicitly configured connection.

Protocol references: [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[MiMo API and Token Plan setup](https://mimo.mi.com/docs/en-US/quick-start/summary/first-api-call),
[MiMo Chat Completions](https://mimo.mi.com/docs/en-US/api/chat/openai-api),
[Z.ai Chat Completions](https://docs.z.ai/api-reference/llm/chat-completion),
[Z.ai thinking replay](https://docs.z.ai/guides/capabilities/thinking-mode),
and [Claude Messages](https://platform.claude.com/docs/en/api/messages/create).

### Automatic routes for new runs

New runs automatically select qualified models from the chosen tool's connected
accounts, preferring subscriptions and preserving independent checking. No per-role
model flags are needed, including with `TaskRun.start(workspace, brief)`.
Explicit model, effort and tool overrides win; saved runs keep their choices.
`--no-auto-select` retains fixed defaults. `--auto-select` requires selection to
succeed instead of falling back when discovery or an independent checker is unavailable.
See [automatic model selection](models.md#choosing-models-automatically) for discovery,
reasoning levels, bounded preflight, recovery and the read-only selection preview.
