# Running AutoCode live from a Claude Code cloud session

How to run AutoCode's scenarios on Claude models (Sonnet, Opus, Haiku) from a Claude Code session in the
cloud, the way the #110 qualification batches were run (2026-09-29 and 2026-09-30). Claude Code is a built-in
provider ([Providers: Claude Code provider](../../docs/providers.md#claude-code-provider)); [README.md](README.md)
explains this directory; this page is the procedure.

A cloud session's container is fresh each time: the `claude` CLI is installed and signed in, and Python 3.11,
Git and Go are on the machine. There is no virtualenv and nothing from an earlier session. Nested `claude`
calls bill to the account that runs the session.

## 1. Set up (every new session)

From the repository root:

```sh
python3 -m venv .venv && .venv/bin/pip install psutil
rm -f ~/.config/autocode/providers/claude.toml ~/.config/autocode/providers/claude_stage.py   # the retired example
.venv/bin/python tools/autocode.py doctor --engine claude --workspace /path/to/any/git/repo   # engine:claude ok
setpriv --bounding-set=-sys_admin,-sys_ptrace .venv/bin/python scenarios/run.py run --fake bugfix-trivial \
  --out /tmp/live/fake                                                     # plumbing check, no spend: expect PASS
```

- **Prefix every run and batch with `setpriv --bounding-set=-sys_admin,-sys_ptrace`.** The container runs as
  root with `CAP_SYS_ADMIN` and `CAP_SYS_PTRACE`, and AutoCode refuses to launch a provider while it holds them
  (`PAUSED_PROCESS_CLEANUP`, [Testing](../../docs/testing.md)).
- Doctor checks `claude auth status --json` in the environment AutoCode gives its agents: a first-party
  Claude subscription login passes; an API key, Bedrock or Vertex that would reach Claude Code is refused. It
  also warns about a stale `~/.config/autocode/providers/claude.toml`, which is ignored.
- `go` is needed only for `port-policy-go`.
- This venv does not install the `autocode_cli` package, so `tests.test_configure_module` fails locally with
  `No module named 'autocode_cli'`. CI installs the package; the failure is the environment, not a change.

## 2. Run one scenario

```sh
setpriv --bounding-set=-sys_admin,-sys_ptrace .venv/bin/python scenarios/run.py run bugfix-trivial \
  --profile claude-tiers --i-authorize-live-model-spend --out /tmp/live/one --timeout-minutes 60
```

The harness starts AutoCode on the scenario with `--provider claude` and the `claude-tiers` models, answers its
questions and approves its plan, then judges the delivered workspace with the scenario's oracle. One run takes
1 to 15 minutes and costs $0.10 to $5. Keep `--out` outside the repository: Claude Code loads the `CLAUDE.md`
files of a workspace's ancestors, and this repository's would steer the stages.

## 3. Run a batch

```sh
setpriv --bounding-set=-sys_admin,-sys_ptrace .venv/bin/python examples/claude-provider/batch.py run \
  examples/claude-provider/qualification.txt --out /tmp/live/batch --repeat 3 --jobs 4 \
  --i-authorize-live-model-spend
```

- `qualification.txt` is the #110 ladder: 17 scenarios. `--repeat 3` makes 51 runs, which took about an hour
  at `--jobs 4` and cost $84 on the retired example wrapper (2026-09-30).
- To run a few scenarios, write their ids one per line in your own list file.
- `--fake` rehearses the same batch with the scripted model, at no cost.
- Start it in the background: `run_in_background` on the Bash tool, or `setsid nohup ... &`. A foreground
  command times out long before the batch ends.
- Keep `--out` outside the repository (the session's scratchpad or `/tmp`). Never commit run output.

### Hybrid batches

A scenario's `[hybrid]` route ([scenarios/README.md](../../scenarios/README.md#hybrid-runs)) needs a live tool
registered with a TOML file. The built-in claude provider cannot be split by stage, so live `--hybrid` runs
report `SKIPPED`; `--hybrid --fake` still rehearses a route with the scripted stand-in.

## 4. Watch it

```sh
.venv/bin/python examples/claude-provider/batch.py status /tmp/live/batch
```

`status` prints one line per scenario (verdict, oracle score, seconds, cost) and the running total.

- **A container restart kills every running run.** Run the same `batch.py run` command again: it starts
  only the runs still missing a `result.json`. A killed run's directory stays behind and shows as `running`
  in `status`. Delete it or ignore it.
- To be told when the batch ends, wait on the harness processes with a pattern that cannot match your own
  waiting command, for example `while pgrep -f "[s]cenarios/run.py run" >/dev/null; do sleep 60; done`.
- For a batch longer than an hour, schedule a check-in (`send_later`) as well, so a restart does not go
  unnoticed.
- **Watch the subscription window.** A stage that finished (`turn.completed`) carries Claude Code's own
  diagnostics in its terminal row, including `claude.rate_limit.utilization` (the share of the current window
  used). A failed stage's `turn.failed` row leaves the window out, so its text never reads as a rate limit:
  read the `rate_limit_event` row (`rate_limit_info.utilization`, or `unifiedWindows`) in the stage's
  `<stage>.claude.jsonl` beside its event log instead. Stop starting runs when it is high; a used-up window
  pauses runs with `PAUSED_BUDGET` and is never retried automatically.
- **A `claude` update pauses saved runs** with `PAUSED_TRANSPORT_CHANGED` (the version is part of the run's
  identity; AutoCode sets `DISABLE_AUTOUPDATER=1` for its own calls). Resume one with
  `autocode resume --accept-transport-change`.

## 5. Read a result

Verdicts:

| Verdict | Meaning |
| --- | --- |
| `PASS` | AutoCode reported done, and the oracle agrees. |
| `HONEST_BLOCKER` | AutoCode stopped and said why. Check whether the delivered code was right anyway (the oracle score). |
| `FALSE_COMPLETE` | AutoCode reported done and the oracle disagrees: the case that matters most. |
| `ERROR` | The harness could not finish the run. |

Each run's directory, `<stamp>-<scenario>-claude-tiers-<id>/`, holds:

| Path | What it holds |
| --- | --- |
| `result.json` | `verdict`, `checks` (each oracle check with `ok` and `detail`), `metrics.model_stage_names`, `wall_seconds`, `cost` |
| `project/` | The delivered workspace |
| `project/.autocode/runs/<run>/state.json` | `status`, `stop_reason` (why a run paused), `stages[]` with `rejected` and `rejection_reason` |
| `project/.autocode/runs/<run>/iterations/NNN/<stage>-NN.prompt.md` | The exact prompt a stage got |
| `project/.autocode/runs/<run>/iterations/NNN/<stage>-NN.jsonl` | The stage's Codex-shaped events: commands with exit codes, the report, token `usage`, the call's `cost_usd` and the `claude` diagnostics |
| `project/.autocode/runs/<run>/iterations/NNN/<stage>-NN.claude.jsonl` | Claude Code's own rows for that stage (stream deltas left out), its stderr and exit code |

To see why a run stopped:

```sh
d=/tmp/live/batch/<run directory>
python3 - "$d" <<'EOF'
import glob, json, sys
state = json.load(open(glob.glob(sys.argv[1] + "/project/.autocode/runs/*/state.json")[0]))
print(state["status"], "|", (state.get("stop_reason") or "")[:2000])
for stage in state["stages"]:
    if stage.get("rejected"):
        print("-", stage["stage"], "|", (stage.get("rejection_reason") or "")[:500])
EOF
```

## 6. When a run fails

This is the loop that took the batch from 44 to 49 of 51 runs passing:

1. Find the cause in `stop_reason`, the rejected stages and the prompts. Decide whether it is a model error
   the runner should catch, or a runner bug.
2. Reproduce it offline in a unit test that fails (`tools/fake_claude.py` stands in for Claude Code).
3. Fix it, then run `tools/run_suite.py --changed` and `scenarios/run.py run --fake`.
4. Re-run that scenario live three times (a list file with the one id and `--repeat 3`) before calling it fixed.

## Known limits

- **Runs are bounded by time, not tokens**: 20 minutes per stage (the `claude-tiers` profile's
  `--max-stage-seconds 1200`) and the harness's timeout per run (`--timeout-minutes`). Claude reports every
  cache read as input, and a Builder re-reads its conversation on each tool call, so a token cap stops cheap
  runs: a 6M cap stopped 4 of 24 ladder rungs (2026-09-30) that had cost about $4 each, all with correct code.
  The real spend is `cost_usd` in each stage's event log, which `batch.py status` adds up.
- **The harness approves plans and answers questions itself**, so a passing run was not approved by a person.
- **Every stage costs something even when idle**, because Claude Code sends its own system prompt with each
  call; the restricted tool set keeps that prompt small.
- **Results on Claude models do not qualify the OpenCode route** that AutoCode uses by default.
- Live runs need `--i-authorize-live-model-spend` and are never part of a routine test run (AGENTS.md).
