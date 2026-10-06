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
response, the pause status, the frontier binding (source revision, saved
settings with the saved transport identity, task, contract, recovery
accounting, interventions) and the request's evidence receipt. A live
transport change is not part of that binding: the transport check stops an
admitted continuation before any launch. The next explicit resume at that pause without
another recovery flag consumes it once, with no provider call. A plain
relaunch only reports that it is pending, and a run that left the pause
through an explicit control (a grant, a raised bound, an abandoned attempt)
never evaluates it:

- `stale`: something it is bound to changed, or the records were altered. It is
  retired and AutoResolver asks a fresh request for the current run, launching
  nothing. That includes a run whose frontier is unchanged because only the
  request's records or evidence changed; holding on the consumed request there
  was this bug again.
- `held`: the stop needs a control information cannot supply (a spent
  recovery allowance, a reached bound, a repeated failure, a Builder retry
  limit, a stopped parallel member, a stalled milestone, spent report-only
  repairs, an unreconciled attempt). The decision
  names the exact command (`--grant-recovery N` for a spent recovery allowance,
  the bound flag for a reached limit, `--retry-failed-stage`, `--retry-builder`,
  `--abandon-stage ATTEMPT`, `--planning-review-call-limit N`), and only where
  the CLI accepts it. After a content-filter refusal the resume that follows
  `--abandon-stage` names a model change (`--sol-model MODEL`), never the
  refused model again. AutoResolver also runs the automatic-recovery guard
  itself: a spent allowance behind another stop is held as
  `PAUSED_TIMEOUT_RECOVERY`, the status that guard would have set, so the named
  grant is accepted. Later invocations repeat the decision; nothing is
  evaluated or asked again.
- `admitted`: the stop's cause lies outside the run (an allow-list in
  `INFORMATION_CAUSES`: provider capacity, rate limit, quota or refusal, an
  uncertain provider or stage, a busy workspace, a Resolver stop
  (`PAUSED_RESOLVER_OPERATIONAL`, `PAUSED_RESOLVER`), a planning stop with
  reserved recovery left) and no bound
  or operator-only control holds it. The run takes the ordinary resume path;
  every admission check (limits, permissions, transport, source, approval,
  interventions) still runs before a launch. A runner event marks the frontier
  as new, so a stop found next is a new request, never the answered one. A
  quota-stopped or refused parallel Builder member is such a stop: it still
  needs a model only a person can name, so its route question is asked again;
  holding instead would leave no request to answer.

A repeated resume never evaluates a response twice. The decision record is
written before the state that refers to it, so a resume killed between the two
evaluates again, without a provider call; the record the saved state names is
the decision that took effect.

Information never raises a limit, resets a count or clears history. An
admitted continuation takes the ordinary resume path but does not renew the
per-incident AutoResolver attempts or the pending report-repair attempts that
an explicit resume at other pauses renews. The status
view gains `information_review`, and `needs.action` names a held decision's
control; a review the run left behind unevaluated reads `superseded`.
`leave_paused`, requirements answers, plan approval,
`--retry-failed-stage` and `--grant-recovery` are unchanged.

## Still open

- A `blocker`-scope request (an agent's question or a Resolver diagnosis)
  answered with `provide_information` still holds at the same frontier; only
  `operational_exhaustion` is re-evaluated.
- A guard the evaluation does not run itself, such as the validation-round
  limit (`PAUSED_RESOLVER` from autocode_validation_rounds), stops an admitted
  run again before any launch and asks a new request rather than holding.
- A planning stop whose reserved recovery is exhausted
  (`PAUSED_RESOLVER_OPERATIONAL` at a plan review) is held without a command:
  `--planning-review-call-limit` accepts only `PAUSED_PLANNING_BUDGET`.
- Spent report-only repairs (`PAUSED_REPORT_REPAIR_LIMIT`) with an
  operational request are held without a command: no CLI control accepts that
  pause while the request stands (`--retry-report` and `--retry-failed-stage`
  need `PAUSED_REPEATED_FAILURE`). Before this fix the information admitted a
  fresh Builder there, archiving the spent repair and rotating its session.
- Other holds without a command: `PAUSED_MILESTONE_STALLED`,
  `PAUSED_MILESTONE_REPLAN` and any stop not on either list name no control,
  only that the cause must change first.
