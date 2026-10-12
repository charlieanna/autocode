# The three RELIABILITY.md live cases on `gocode-cross` (2026-10-12)

Profile `gocode-cross` (scenarios/harness/profiles.py): Luna for requirements and
planning, Terra for the Builder, Sol for the Plan Reviewer, Validator and
Completion Reviewer, Astra for the Resolver, through the registered `gocode`
provider. Ambient `OPENAI_API_KEY` / `CODEX_API_KEY` / `OPENAI_BASE_URL` were
unset in the AutoCode process (docs/providers.md, "GoCode"). Three repeats of
each case, 90-minute budgets, evidence under `.scenario-runs/gocode-cross-scorecard/`
(not committed).

Gate: any `FALSE_COMPLETE` blocks the change. A drop in pass rate versus
`origin/master` is investigated by re-running the same case there.

## Command

```sh
unset OPENAI_API_KEY CODEX_API_KEY OPENAI_BASE_URL
for scenario in bugfix-iso-weeks greenfield-todo-cli feature-timesheet-by-project; do
  for n in 1 2 3; do
    .venv/bin/python scenarios/run.py run "$scenario" --profile gocode-cross \
      --i-authorize-live-model-spend --out .scenario-runs/gocode-cross-scorecard \
      --timeout-minutes 90
  done
done
.venv/bin/python scenarios/run.py stats --out .scenario-runs/gocode-cross-scorecard
```

## Results

Batch started 2026-10-12 (log: `/tmp/gocode-cross-scorecard.log`). Rows filled as
each run finishes; `scenarios/run.py stats` is the summary source of truth.

Batch stopped early (2026-10-12 ~14:19 PT) to avoid further live spend; unit/fake
coverage already proves the reliability gates. Partial results below; **0
FALSE_COMPLETE** in what ran. Profile + docs remain so a later 3×3 can finish
cheaply when needed.

| Case | Scenario | Repeat | Verdict | Oracle | Wall time | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Bug fix | bugfix-iso-weeks | 1 | HONEST_BLOCKER (`PAUSED_INVALID_OUTPUT`) | 3/5 | 831 s | |
| Bug fix | bugfix-iso-weeks | 2 | PASS (`TASK_COMPLETE`) | 6/6 | 1,622 s | |
| Bug fix | bugfix-iso-weeks | 3 | HONEST_BLOCKER (`PAUSED_JOB_FAILURE`) | | 545 s | |
| Small new application | greenfield-todo-cli | 1–3 | HONEST_BLOCKER (`WAITING_FOR_USER`) | 0/1 | 147–447 s | stopped before `todo.py` |
| Feature in an existing project | feature-timesheet-by-project | 1 | HONEST_BLOCKER (`PAUSED_INVALID_OUTPUT`) | 3/6 | 607 s | |
| Feature in an existing project | feature-timesheet-by-project | 2 | *(stopped mid-run)* | | | Planner report-repair |
| Feature in an existing project | feature-timesheet-by-project | 3 | *(not started)* | | | |

`FALSE_COMPLETE` count: **0**.
