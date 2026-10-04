# Operational stops advertised commands that the CLI refused (#288, #301)

A timeout-exhausted run advertised `--resume-paused --grant-recovery N`, but
the grant is bound to the request that was shown. Two ordinary operator steps
made that request out of date, after which nothing returned the run to RUNNING:

- **Fixing the cause in the workspace.** The grant was refused, and a plain
  `--resume-paused` or the `--no-chat` refresh that `stale_request_message`
  recommends kept the stale request ("retained the operational request").
- **Correcting `--max-idle-seconds` or `--max-stage-seconds` without a grant.** The
  settings write read the stale request as a legacy blocker and queued a
  Resolver diagnosis. The recovery guard then stopped it as `RESOLVER_PENDING`,
  and the advertised grant was refused there too.

At a time-limit stop, answering the request with `--resolver-message` and then
running `--resume-paused --max-seconds N` published a second request instead of
continuing. The bound-change supersede in `run_setup` only looked for a live
request, and the answer had already consumed it.

## Change

- A stale operational request is asked again, with its recorded pause, by any
  plain invocation (`republish_stale_operational`). Persistence no longer reads
  it as a legacy blocker. A refused grant names that refresh command. The grant
  itself still validates the request that was shown, so a source change is
  never excused (#306).
- An explicit `--max-seconds` or `--max-iterations` at the pause left by an
  answered request carries the same authority as at a live one.
- The advice names the exact next command: `--grant-recovery N` for timeout
  recovery, `--max-seconds N` for time limits and `--max-iterations N` for
  iteration limits.

`tests/test_recovery_advice_cli.py` reads each advertised command from the
published request and runs it. A fixture launch must follow. One test covers
an external-directory exhaustion, then an explicit time cap, then another
recovery exhaustion. All of it fails on the previous revision.

## Not established

Other `operational_exhaustion` categories (for example `PAUSED_RESOLVER`
"attempt budget exhausted", provider capacity, `PAUSED_NO_PROGRESS`) still
offer only corrective information or leaving the run paused. A published
request holds a plain `--resume-paused` there. Giving those stops a resume
path would change what `--resume-paused` authorizes, and is left for a
separate decision.
