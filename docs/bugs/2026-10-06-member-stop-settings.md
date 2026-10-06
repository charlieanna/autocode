# A settings flag at a parallel Builder member's stop (#542, #543)

Reproduced on master `ddc940f` through the real CLI and worker processes (offline
fixtures). **Fixed** 2026-10-06; regression in `tests/test_member_stop_settings.py`.

## Reproduced behavior

**#542: the member's model question was dropped, its advice kept.** Builder M1 of a
parallel batch stopped on quota, or its provider's content filter refused it, and the
parent asked `route-terra`. Then `autocode --run-dir RUN --resume-paused --sol-model
openai/gpt-6-luna`:

1. `run_setup.load_locked` withdrew the request ("Settings changed without changing the
   exhausted bound").
2. `run_actions` asked again from `Paused(status, stop_reason)`. That error carries no
   `quota_worker` payload, so `worker_quota.stopped` returned None and the new request
   asked no `route-terra` question.
3. Its cause was the saved `stop_reason`, which the withdrawn request had composed from
   its cause and its advice. The new stop reason repeated all of it, including "To
   continue on another model, answer --answer route-terra=MODEL ...", and then the new
   advice, so AutoResolver's text appeared twice.
4. `--answer route-terra=MODEL`, the command it advised, was refused: "route-terra is
   not the model question of the current request".

A budget flag (`--max-iterations N`) did not reproduce it: that path collects the batch
again and keeps the question.

**#543: a settings flag with `--retry-builder` hit an internal guard.** At any member
stop (quota, refusal, or a generic Builder failure, `AUTOCODE_BUILDER_FAIL=M1`),
`--resume-paused --retry-builder M1` with `--max-parallel-builders 3` or `--sol-model
MODEL` exited 2 with `autocode: Role result belongs to another implementation task`.
`state.json` was unchanged.

1. `--retry-builder` is a recovery action that checks the request itself, so
   `load_locked` kept the request. The settings write made it stale: the settings digest
   is part of its binding.
2. On that write, `run_records.normalize_human_boundary` found no current request and
   rebuilt the stale one as a legacy decision. It took the last stage record as the
   decision's source. At a batch checkpoint that record is a member's copied worker
   attempt (`worker_milestone`), not the parent's, so `autopilot.queue_resolution` hit
   `goals.execution_guard`.

## Fix

- **Asking again from a withdrawn request** (`resolver_runtime.record_operational_exhaustion`,
  `_asked_again`). `resolver_human.withdrawn` names the operational request withdrawn
  last: the request issued last (by `issued_at`; the ledger is saved in key order),
  superseded, with no other request published or queued. Asked again for the same pause:
  - A cause that is exactly the stop reason that request composed (`_stop_reason`:
    cause, then advice) is replaced by the request's own cause (`discovered`). Old
    advice, such as a `route-terra` answer, is never repeated into a request that may
    not ask it.
  - `worker_quota.asked_again` restores the member's payload from the withdrawn
    request's origin while that member is still the batch's current stop
    (`worker_quota.current`). The `route-terra` question for that milestone is asked
    again, and the answer it advises is accepted.
- **No legacy decision from a member's record.** `normalize_human_boundary` does not
  rebuild a legacy decision at a batch checkpoint (`worker_quota.at_checkpoint`) while the
  published request is still pending in the ledger (`resolver_human.pending_issued`),
  even when a settings write left it stale. An uncertain serial attempt already
  prevents it the same way. The request stays for its own authority: a retry
  supersedes it, and a route answer with its token re-binds it.
- **A member retry with settings.** `dispatch.member_retry_refusal` holds the checks
  `request_retry` made. `load_locked` now asks it before saving a settings change that
  comes with `--retry-builder`. A refused retry, such as a member its provider's
  content filter refused, saves nothing and says so. An accepted retry runs under the
  new settings: a quota member reruns its model, and a failed member gets its second
  attempt.

The tests cover each member cause through the CLI: quota and refusal for the settings
flag alone, and quota, refusal and a generic failure with `--retry-builder`. A unit test
covers a member that is no longer the batch's current stop: its request is asked again
without the question or its advice.
