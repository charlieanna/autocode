# Running AutoCode live from a Claude Code cloud session

How to run AutoCode's scenarios on Claude models (Sonnet, Opus, Haiku) from a Claude Code session in the
cloud, the way the #110 qualification batches were run (2026-09-29 and 2026-09-30). [README.md](README.md)
explains the provider files; this page is the procedure.

A cloud session's container is fresh each time: the `claude` CLI is installed and signed in, and Python 3.11,
Git and Go are on the machine. There is no virtualenv, no provider registration, and nothing from an earlier
session. Nested `claude` calls bill to the account that runs the session.

## 1. Set up (every new session)

From the repository root:

```sh
python3 -m venv .venv && .venv/bin/pip install psutil
mkdir -p ~/.config/autocode/providers
cp examples/claude-provider/claude.toml examples/claude-provider/claude_stage.py ~/.config/autocode/providers/
.venv/bin/python scenarios/run.py run --fake bugfix-trivial     # plumbing check, no spend: expect PASS
```

- AutoCode runs the **installed** copy of `claude_stage.py`. Copy it again after changing it, before a live run.
- `go` is needed only for `port-policy-go`.
- This venv does not install the `autocode_cli` package, so `tests.test_configure_module` fails locally with
  `No module named 'autocode_cli'`. CI installs the package; the failure is the environment, not a change.

## 2. Run one scenario

```sh
.venv/bin/python examples/claude-provider/trial.py run bugfix-trivial --profile claude-tiers \
  --i-authorize-live-model-spend --out /tmp/live/one --timeout-minutes 60
```

The harness starts AutoCode on the scenario, answers its questions and approves its plan, then judges the
delivered workspace with the scenario's oracle. One run takes 1 to 15 minutes and costs $0.10 to $5.
`bugfix-trivial` takes about 3.5 minutes and $1.70.

## 3. Run a batch

```sh
.venv/bin/python examples/claude-provider/batch.py run examples/claude-provider/qualification.txt \
  --out /tmp/live/batch --repeat 3 --jobs 4 --i-authorize-live-model-spend
```

- `qualification.txt` is the #110 ladder: 17 scenarios. `--repeat 3` makes 51 runs, which took about an hour
  at `--jobs 4` and cost $84 (2026-09-30).
- To run a few scenarios, write their ids one per line in your own list file.
- `--fake` rehearses the same batch with the scripted model, at no cost.
- Start it in the background: `run_in_background` on the Bash tool, or `setsid nohup ... &`. A foreground
  command times out long before the batch ends.
- Keep `--out` outside the repository (the session's scratchpad or `/tmp`). Never commit run output.

## 4. Watch it

```sh
.venv/bin/python examples/claude-provider/batch.py status /tmp/live/batch
```

`status` prints one line per scenario (verdict, oracle score, seconds, cost) and the running total.

- **A container restart kills every running trial.** Run the same `batch.py run` command again: it starts
  only the runs still missing a `result.json`. A killed run's directory stays behind and shows as `running`
  in `status`. Delete it or ignore it.
- To be told when the batch ends, wait on the trial processes with a pattern that cannot match your own
  waiting command, for example `while pgrep -f "[t]rial.py run" >/dev/null; do sleep 60; done`. A plain
  `pgrep -f "trial.py run"` also matches the loop itself, and the loop never ends.
- For a batch longer than an hour, schedule a check-in (`send_later`) as well, so a restart does not go
  unnoticed.

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
| `result.json` | `verdict`, `checks` (each oracle check with `ok` and `detail`), `metrics.model_stage_names`, `wall_seconds`, `summary` |
| `project/` | The delivered workspace |
| `project/.autocode/runs/<run>/state.json` | `status`, `stop_reason` (why a run paused), `stages[]` with `rejected` and `rejection_reason` |
| `project/.autocode/runs/<run>/iterations/NNN/<stage>-NN.prompt.md` | The exact prompt a stage got |
| `project/.autocode/runs/<run>/iterations/NNN/<stage>-NN.jsonl` | The stage's events: token `usage` and `cost_usd` per call |

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
2. Reproduce it offline in a unit test that fails.
3. Fix it, then run `tools/run_suite.py --changed` and `scenarios/run.py run --fake`.
4. Re-run that scenario live three times (a list file with the one id and `--repeat 3`) before calling it fixed.

## Known limits

- **Runs are bounded by time, not tokens**: 20 minutes per stage (`trial.py`) and the harness's timeout per
  run (`--timeout-minutes`). Claude reports every cache read as input,
  and a Builder re-reads its conversation on each tool call, so a token cap stops cheap runs: a 6M cap
  stopped 4 of 24 ladder rungs (2026-09-30) that had cost about $4 each, all with correct code. The real
  spend is `cost_usd` in each stage's event log, which `batch.py status` adds up.
- **The harness approves plans and answers questions itself**, so a passing run was not approved by a person.
- **Every stage costs at least $0.06 to $0.25**, because Claude Code sends its own system prompt with each call.
- **Results on Claude models do not qualify the OpenCode route** that AutoCode uses by default.
- Live runs need `--i-authorize-live-model-spend` and are never part of a routine test run (AGENTS.md).
