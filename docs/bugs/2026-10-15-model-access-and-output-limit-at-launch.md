# 2026-10-15: model access at launch, and output-limit stalls on plan review

Measured while comparing AutoPlanner on GLM+MiMo through the Kilo Code provider
(`glm-mimo-kilo` profile, `scenarios/run.py plan-compare`, 2026-10-14). Evidence
directories under `.scenario-runs/20261014T21*plan-compare-glm-mimo-kilo*`.

## 1. Billing failure discovered mid-run, after paid work (fixed)

`kilo/xiaomi/mimo-v2.6-pro` (Kilo Gateway) passed `check_models` and
`check_subscription_routes`, ran ten minutes of GLM stages, then failed the first
MiMo stage with HTTP 402 `usage_limit_exceeded` ("Add credits to continue").
Roster and login checks cannot see a zero balance. Fix: `autocode_model_preflight`
probes each billing-routed role model once per run at build start (one tiny call,
`run_dir/model-preflight.json` records it). Access-class failures (budget, auth,
billing route) refuse the build before any stage dispatch; transient noise is
reported and the run continues. Only providers whose config declares `[auth]` are
probed, so scripted test providers keep zero-call launches.

The `xiaomi-token-plan-sgp/` route in `kilocode.toml` serves MiMo on the
operator's own plan and avoids the paid gateway; `kilo/xiaomi/...` needs prepaid
credits.

## 2. Output-limit stop on the Plan Reviewer stalled unattended runs (fixed)

MiMo's Plan Reviewer exhausted its response cap entirely on reasoning
("32003 output tokens, 32003 of them reasoning", finish reason `length`), so the
run stopped on a person-only `route-plan_reviewer` question. The bounded
report-only repair (`automatically_recover_truncated_review`) already covers this
failure shape — the attempt passed every gate (exit 0, no `turn.completed`,
length-stop reason, unchanged source, schema and `before_ref` intact) except its
stage whitelist (`sol`, `astra_review`, `astra_checkpoint`). The whitelist now
includes the Plan Reviewer stages (`astra_challenge`, `astra_finalize`), so a
truncated plan review gets one bounded report-only repair before a person is
asked to change models. Measured case: `tiny-greeting-adaptive` in the run above.

## 3. Claims the evidence reversed (measured again, not fixed)

- **Idle watchdog vs silent reasoning**: no stall occurred. MiMo went silent for
  724 s+ and the per-family floor (`autocode_idle_policy.LONG_SILENCE_FLOORS`,
  mimo 900 s) held. The visible anomaly is attribution: a provider helper process
  makes the monitor report `running_tool` (tool budget 1800) for pure reasoning,
  so the idle budget is bypassed and a 1800 s kill would be labelled "tool
  execution exceeded". Three tests pin that fallback semantics deliberately
  (#1098, `tests/test_activity.py`), so changing it needs a demonstrated failure,
  not just a mislabel. Candidate follow-up: fallback-only expiry names the
  inference in its reason text, and a new family floor is added when a measured
  silence exceeds one.
- **Questions without defaults**: the only unanswerable question in these runs was
  the person-only quota/route question, which is person-only by design. Model
  authored clarifying questions carried `proposed_default` and the driver answered
  them. No contract gap shown; revisit only with a run that stalls on a
  model-authored question lacking a default.

## Measurement caveats

Planning was slow: `tiny-greeting` (fixed pipeline) needed 45 minutes to reach
plan approval against the 720 s approval-ready target in `RELIABILITY.md:150`
(different provider mix, so not a like-for-like regression claim). The harness
budget, not AutoCode's, ended that run. Size the per-case budget accordingly
before drawing latency conclusions from `plan-compare`.
