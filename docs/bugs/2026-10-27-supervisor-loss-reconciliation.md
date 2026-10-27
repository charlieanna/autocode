# Lost supervisor recovery (#2230)

A real host reboot interrupted the native full-screen TUI review on master
`dd042e5c0`. The saved run `20261027-173510-chat-d3680c` retained RUNNING and an
unfinished Reviewer attempt. Fresh public status positively found the owner,
keeper and provider dead, with no final cleanup receipt. It exposed neither a
next command nor a safe way for a conforming client to reach the uncertainty
boundary. This is not evidence that the provider completed, failed or cost zero.

Status now offers one checkpoint-bound metadata-only `--reconcile-only` action
for ordinary foreground supervised attempts. Under the existing run lock it freshly checks
all three process identities and records PAUSED_UNCERTAIN_STAGE. It does not
configure a provider, migrate saved settings, adopt an output, launch inference,
reset usage, replenish recovery or retry the attempt. Native visual execution,
finished attempts, concurrent runner checks and live/unknown process identities
remain outside the offer. Pending/applied stops refuse the action. A subsequent
human discard/retry is a separate existing action; this fix grants none.

Regression evidence: the original public view lacked the action. Real CLI tests
exercise a saved provider that is not installed, unchanged usage/route/recovery
records and source, stale token/settings refusals, and a now-live process. Pure
view tests cover unknown/live/finished/visual/controller states. The Linux CLI
checks launch no model. The interrupted run is retained for post-merge recovery;
a paid replay has not been authorized by this metadata-only fix.
