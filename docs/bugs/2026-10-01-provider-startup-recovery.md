# General provider startup recovery

An OpenCode launch in the PR #209 live verification exited with only `Error:
Unexpected error` / `database is locked`, before reporting a session. AutoCode
treated it as uncertain and required a person, although the unchanged scenario
passed when started alone.

The main CLI loop and unit controller now share a backend-neutral startup
recovery policy. A recognized whole local database-lock diagnostic can receive
at most two automatic retries per run, with one- and two-second delays. This is
a controller recovery policy, not a patch to any provider's database engine.
It uses neither model selection nor a backend name to decide eligibility.

Eligibility requires a nonzero exit within five seconds, complete process
cleanup, unchanged source, no output report and no observed session, model or
tool activity. Unknown/mixed output, partial reports, changed source, existing
questions, report repairs, live workers, timeout/interrupts, quota and rate
limits stay on their existing pause/reconciliation paths. A log alone is never
accepted as a completion report.

Each failed attempt is archived before a fresh attempt. The receipt is saved
on the existing `stages` record as `startup_recovery`; the provider recovery
module reads those receipts for the run-wide limit, and AutoResolver reads them
when explaining exhaustion. Existing `user_events` and the aggregate automatic
recovery counter record the retry. No new run-state keys are added. Retry
history survives CLI restarts and stage changes. Sessions, contracts, planning
call charges, transport identity, permission policy, billing route, time limits
and model settings are not reset or widened.

Public CLI regressions cover transient recovery through the native Codex path,
the built-in OpenCode path and a registered command provider, persistent locks,
restart across different stages, and refusal to replay session/source/report
activity or quota failures. These are scripted providers; they do not establish
that the same underlying database bug exists in Codex or KiloCode. The policy
does not repair persistent corruption, delete databases or retry arbitrary
transport failures.
