# A retained stopped attempt was omitted from spend (#3352)

The runner records `finished_at` and usage, then accounts the attempt, before
applying its report. A failed or uncertain response can remain in `active_stage`
while recovery decides what to do. The cost projection previously charged only
`stages`, while live accounting treated everything in `active_stage` as running.
Consequently the stopped attempt's known cost was displayed as running and was
absent from the amount compared with the cap.

The usage ledger now selects finished attempts from both places, deduplicating a
retained record against the saved ledger by artifact identity. A finish timestamp
or the runner's `accounted=True` marker identifies the stopped boundary; a paused
status alone does not establish that a process finished. Both the legacy usage
summary and request accounting use the same selection. The cost view, breakdown,
cap check, next-attempt projection and checkpoint-carried spend all consume that
ledger. No state migration, report acceptance, recovery decision or provider-log
read was added to the pure cap calculation.

Partial usage remains a floor; unknown cost remains unknown. A still-running
attempt stays outside the boundary cap and contributes to `running_usd` as before.
The caller cannot make a stopped attempt appear running by supplying accounting
sampled before its finish. Archiving an attempt does not charge it again.

## Verification

Base: `0cd2928f0d` (master). The final eight-test
`tests.test_cost.RunningStageSpendTests` class was copied to an isolated base
checkout. It failed seven assertions, including the public CLI regression:
`(spent_usd, running_usd, cap_usd)` was `(0.0, 0.75, 0.5)` instead of
`(0.75, None, 0.5)`. Existing running-stage controls passed. The fixture uses
scripted Kilo request-finish events and a retained stopped checkpoint, without
launching a provider.

On the candidate, 130 tests passed across:

```sh
PY=/Users/ankurkothari/Documents/workspace/autocode/.venv/bin/python
$PY -m unittest tests.test_cost tests.test_cost_breakdown tests.test_usage \
  tests.test_autocode_request_usage tests.test_partial_cost_reconciliation \
  tests.test_chat_cost tests.test_command_inputs tests.test_run_metrics
```

The CLI regression invokes `tools/autocode.py --status --json`, verifies the
amount and floor in both JSON and the cost line, and checks that the checkpoint
bytes did not change. The cap and next-attempt checks include the retained spend;
the recovery transition test verifies it is counted once with a duplicate saved
copy and after the retained slot is removed. Full terminal amounts, partial
token estimates, unknown models and genuine running attempts remain distinct.

Ten in-memory mutations were detected by assertion failures: dropping either
finish marker, dropping the retained charge or identity deduplication, charging
running attempts early, reverting request-accounting liveness, removing the stale
live-accounting guard, and reverting cost completeness or the legacy summary's
liveness/completeness. Mutated source was never written to the checkout.

Coverage.py was unavailable in the existing venv. A standard-library line trace
over the same 130 tests executed every changed executable product line, identified
using Python bytecode line tables. The other changed lines are documentation or
continuations without their own executable line. This is line-execution evidence,
combined with the behavioral mutation checks, not a coverage.py report.

No live-model run was needed for this deterministic accounting fix. The public
CLI check launches no provider and runs on macOS; supervised provider scenarios
remain Linux checks. No supported-Linux provider qualification is claimed here.
