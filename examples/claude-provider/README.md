# Running AutoCode on Claude models (Haiku, Sonnet, Opus)

Claude Code is a built-in provider: `--engine claude` (or `--provider claude`) runs every role through
`claude -p` with your Claude Code login (`tools/providers/claude.py`,
[Providers: Claude Code provider](../../docs/providers.md#claude-code-provider)). Nothing needs to be
installed into `~/.config/autocode/providers/`; a `claude.toml` there is ignored, and `autocode doctor`
names it.

This directory is the cloud-session guide for running the scenario catalog on Claude models:

| File | What it does |
| --- | --- |
| [CLOUD-SESSION.md](CLOUD-SESSION.md) | Setup, one run, a batch, watching it and reading the results from a Claude Code cloud session. |
| `batch.py`, `qualification.txt` | Runs a list of scenarios several times in parallel through `scenarios/run.py` with the `claude-tiers` profile, restarts only the runs still missing after an interruption, and summarizes verdicts and cost. |

Roles (the `claude-tiers` profile in `scenarios/harness/profiles.py`, and the provider's own defaults):
Sonnet does requirements, planning and validation. Opus is the Plan Reviewer, the Completion Reviewer,
the Investigator and the Resolver. Haiku is the Builder. AutoCode requires a verifier to differ from its
producer, and this split satisfies that. A Builder that still fails a milestone after its ordinary retry
gets one attempt on Sonnet at `xhigh` effort; for the rest of that milestone the Tester, which runs Sonnet,
moves to Opus ([Models](../../docs/models.md#builder-retry-policy)).

```sh
claude auth login                                   # once, with a Claude subscription
.venv/bin/python tools/autocode.py doctor --engine claude
setpriv --bounding-set=-sys_admin,-sys_ptrace .venv/bin/python scenarios/run.py run bugfix-trivial \
  --profile claude-tiers --i-authorize-live-model-spend --out /path/outside/the/repo --timeout-minutes 45
```

**It spends real money** (Claude Code's list price is what the run reports; a subscription is billed by its
own plan). Each stage is one `claude -p` call with a restricted tool set (`Bash`, `Read`, `Edit`, `Write`,
`Glob`, `Grep`), no MCP servers and no skills, which cut a Haiku call's cost by about 60% against the 44
host tools the example wrapper exposed. Keep `--out` outside this repository: Claude Code reads the
`CLAUDE.md` files of a workspace's ancestors.

Live hybrid runs (`--hybrid`) report `SKIPPED`: the built-in provider cannot be split by stage.

## Trial history

The trials below ran on the retired example wrapper (`claude.toml`, `claude_stage.py` and `trial.py`,
removed in #717), which registered the `claude` CLI as a TOML-configured tool. They are kept as dated
records.

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
