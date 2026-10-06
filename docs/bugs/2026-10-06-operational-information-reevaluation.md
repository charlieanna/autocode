# Accepted operational information was never re-evaluated (#486)

AutoResolver's operational request advises "send corrective information, then
`autocode resume`". The CLI accepted `--resolver-response provide_information`
and reported that work, approvals and budgets were unchanged, but nothing ever
evaluated the response. `review_operational_response` stored it as a terminal
hold bound to the current frontier, and every later `--resume-paused` printed
"AutoResolver retained the human guidance" without a provider launch or a new
evaluation. The status view named only `--resume-paused`. Nothing short of
editing `state.json`, changing the source or starting a new run moved the run.

Both rules behind it were right (information is not permission; a bare resume
must not bypass an operational pause). The missing piece was a transition from
"response received" to "response evaluated".

## Fix

`tools/autocode_operational_information.py` owns one record per answered
`operational_exhaustion` request (`resolver.information_reviews`).
`provide_information` schedules it, bound to the request ID and token, the
response, the pause status, the frontier binding (source revision, settings and
transport identity, task, contract, recovery accounting, interventions) and
the request's evidence receipt. The next explicit resume without another
recovery flag consumes it once, with no provider call:

- `stale`: something it is bound to changed, or the records were altered. It is
  retired and the existing fail-closed path runs (hold, or a fresh request for
  the changed run).
- `held`: the stop needs a control information cannot supply. The decision
  names the exact command (`--grant-recovery N` for a spent recovery allowance,
  the bound flag for a reached limit, `--retry-failed-stage`, `--retry-builder`,
  `--abandon-stage ATTEMPT`, `--planning-review-call-limit N`), and only where
  the CLI accepts it. AutoResolver also runs the automatic-recovery guard
  itself: a spent allowance behind another stop is held as
  `PAUSED_TIMEOUT_RECOVERY`, the status that guard would have set, so the named
  grant is accepted. Later invocations repeat the decision; nothing is
  evaluated or asked again.
- `admitted`: the stop's cause lies outside the run (an allow-list: provider
  capacity, rate limit, quota or refusal, a busy workspace, an unproven
  recovery, an exhausted report repair, a planning stop with reserved recovery
  left) and no bound or operator-only control holds it. The run takes the
  ordinary resume path; every admission check (limits, permissions,
  transport, source, approval, interventions) still runs before a launch. A
  runner event marks the frontier as new, so a stop found next is a new
  request, never the answered one.

Information never raises a limit, resets a count or clears history. The status
view gains `information_review`, and `needs.action` names a held decision's
control. `leave_paused`, requirements answers, plan approval,
`--retry-failed-stage` and `--grant-recovery` are unchanged.

## Still open

- A `blocker`-scope request (an agent's question or a Resolver diagnosis)
  answered with `provide_information` still holds at the same frontier; only
  `operational_exhaustion` is re-evaluated.
- A planning stop whose reserved recovery is exhausted
  (`PAUSED_RESOLVER_OPERATIONAL` at a plan review) is held without a command:
  `--planning-review-call-limit` accepts only `PAUSED_PLANNING_BUDGET`.
