# Running AutoCode on Claude models (Haiku, Sonnet, Opus)

AutoCode drives OpenCode, Codex or a tool registered with one TOML file
([Providers](../../docs/providers.md#add-a-tool)). This example registers the `claude` CLI as
such a tool, so a run can use Claude models with no OpenCode or Codex install. It is trial glue:
it proves the workflow end to end on Claude models; it is not a supported provider.

## How it works

Three pieces, none of which changes AutoCode:

| File | What it does |
| --- | --- |
| `claude.toml` | Registers a provider named `claude`. The command runs one stage through the wrapper. `[roles]` gives the default model per role. |
| `claude_stage.py` | The wrapper. Reads the stage prompt on stdin and runs `claude -p` once with `--json-schema` set to the stage's report schema. The report it returns is written to the path AutoCode gives. It also translates Claude's event stream into the Codex-style events AutoCode's idle watchdog and token accounting read, and puts each call's cost in the event log (`cost_usd`). |
| `trial.py` | Registers a `claude-tiers` profile in memory and calls the scenario harness (`scenarios/run.py`), so the harness starts AutoCode on a scenario and judges the delivery with its oracle. |
| `batch.py`, `qualification.txt` | Runs a list of scenarios several times in parallel, restarts only the runs still missing after an interruption, and summarizes verdicts and cost. With `--hybrid` (also on `trial.py`) a scenario's `[hybrid]` route is scripted and every other stage runs on Claude ([Hybrid runs](../../scenarios/README.md#hybrid-runs)). |

Roles: Sonnet does requirements, planning and validation. Opus is the plan reviewer, the Completion
Owner, the Investigator and the AutoResolver. Haiku is the Builder. AutoCode requires a verifier to
differ from its producer, and this split satisfies that.

## Run it

From a Claude Code cloud session, follow [CLOUD-SESSION.md](CLOUD-SESSION.md): setup, one run, a batch,
watching it, and reading the results.

```sh
mkdir -p ~/.config/autocode/providers
cp examples/claude-provider/claude.toml examples/claude-provider/claude_stage.py ~/.config/autocode/providers/

.venv/bin/python examples/claude-provider/trial.py run bugfix-trivial --profile claude-tiers \
  --i-authorize-live-model-spend --out /path/to/results --timeout-minutes 45
```

The harness then starts AutoCode as it does for any live profile, roughly
`python tools/autocode.py "<brief>" --workspace <project> --provider claude --joint-planning
--requirements-model ... --glm-model ... --plan-reviewer-model ... --terra-model ... --sol-model ...
--astra-model ... --completion-model ... --no-chat`, and answers questions and approves the plan on the
user's behalf. To run AutoCode directly, pass the same `--provider claude` and model flags.

Needs the `claude` CLI signed in on the machine. **It spends real money**: each call costs at least
about $0.06 (Haiku), $0.12 (Sonnet) or $0.25 (Opus), because Claude Code's own system prompt (about 30k
tokens) is sent every time. A ten-stage run cost about $3.

## What the wrapper does about safety

- `--setting-sources project`: the nested agent does not load the user's hooks or settings (a Stop hook
  that says "commit and push" would otherwise fire inside every stage).
- The session-ID variables are removed from its environment, so a nested call is its own session and does
  not attach to the one that launched it.
- Read-only stages get `Read`, `Grep`, `Glob` and `Bash` with `Edit`, `Write` and `NotebookEdit` denied. AutoCode
  compares the workspace before and after and rejects a read-only stage that changed it. Building stages get
  edit tools too. `git commit/push/checkout/reset/stash/branch/rebase/merge` are denied in every stage.
- AutoCode strips credential-looking variables (`TOKEN`, `KEY`, `SECRET`, `AUTH`) from the agent's environment,
  and this wrapper adds none back. The nested `claude` still authenticated in the environment it was tried in.
  If yours needs one, name it with `AUTOCODE_PASS_ENV`, knowing the agent's shell can then read it.
- `Bash` runs on the machine that runs the trial. Use a disposable workspace.

## What the first trial showed (2026-09-29, `bugfix-trivial`, one run)

- The whole workflow ran in 5.5 minutes of model time: job recognition, investigation, planning with plan
  review, the Builder, the runner's regression proof, the Validator and the Completion Owner.
- The delivered fix was correct: it passed the project tests, the hidden tests, and the check that the new
  tests fail on the original code.
- The run ended `ERROR`, not `PASS`. The Investigator wrote a fourth test case for behavior that already
  worked (an exact multiple), and the proof required every case to fail before the fix. The AutoResolver
  correctly refused to force it and asked a human; the harness cannot answer AutoResolver requests
  (`--resolver-response`) yet. Test cases now have a `kind` (`regression` or `guard`) so a guard case only has
  to pass with the fix.
- Discovery took two report repairs (Sonnet left out required contract fields).

## Second trial, after guard cases (2026-09-29, `bugfix-trivial`, one run)

- The run reached `TASK_COMPLETE` in 4.7 minutes of wall time, with no question to the user, for about $2.10.
- The Investigator wrote six cases: four `regression` and two `guard` (an exact multiple, empty input). The
  runner's proof passed, matching each case to its test, and the fix was correct (`-(-total // size)`).
- The Validator's first report was rejected once ("Check command/result differs from receipt") and repaired.
- The harness labels the run `FALSE_COMPLETE` only because `bugfix-trivial` is a known failure while every job
  takes the full planning path: `no_plan_review_rounds` and `stage_budget` fail (10 model stages, not 5). All eight
  correctness checks pass.

Two runs of one scenario show the workflow can get through on these models, not that it reliably does. The
harness approves plans itself, so the deliveries were not approved by a person.
