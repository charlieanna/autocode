# A settings flag at a parallel Builder member's stop (#542, #543)

Reproduced on master `ddc940f`, again on `d0919ad`, `87d8db3` and `0591e76` (2026-10-07),
through the real CLI and worker processes (offline fixtures): all eight tests in
`tests/test_member_stop_settings.py` fail on `0591e76`. **Fixed** 2026-10-06, with the
review finding below fixed 2026-10-07.

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

A generic Builder failure (`AUTOCODE_BUILDER_FAIL=M1`) asks no model question, but step 3
applied to it too: after the settings flag its stop reason gave AutoResolver's advice twice.
A budget flag (`--max-iterations N`) did not reproduce it: that path collects the batch
again and keeps the question.

**#543: a settings flag with `--retry-builder` hit an internal guard.** At any member
stop (quota, refusal, or a generic Builder failure, `AUTOCODE_BUILDER_FAIL=M1`),
`--resume-paused --retry-builder M1` with `--max-parallel-builders 3` or `--sol-model
MODEL` exited 2 with `autocode: Role result belongs to another implementation task`.
`state.json` was unchanged. Naming a member whose Builder had already completed
(`--retry-builder M2 --sol-model MODEL`) hit the same guard instead of its refusal.

1. `--retry-builder` is a recovery action that checks the request itself, so
   `load_locked` kept the request. The settings write made it stale: the settings digest
   is part of its binding.
2. On that write, `run_records.normalize_human_boundary` found no current request and
   rebuilt the stale one as a legacy decision. It took the last stage record as the
   decision's source. At a batch checkpoint that record is a member's copied worker
   attempt (`worker_milestone`), not the parent's, so `autopilot.queue_resolution` hit
   `goals.execution_guard`.

**Review of the first fix (on `47107cf`): a Builder model given with a member retry was
saved but never used.** With the guard gone, `--resume-paused --retry-builder M1
--terra-model MODEL` at a quota member's stop was accepted. It saved the parent's Builder
model and reran M1 on the route its batch started it with, which M1's own run keeps
(`dispatch.prepare` copies the settings into it). The retry neither ran under the new
setting nor refused it. The same held for a failed member, and for a Builder provider or
reasoning effort.

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
  - Both rules sit in `record_operational_exhaustion`, so they apply wherever the pause
    is asked again: `run_actions` after the settings write, or the run setup itself if
    it asks again for every withdrawn pause. A caller that already passes the request's
    own cause leaves the first rule nothing to do; the second still applies.
- **No legacy decision from a member's record.** `normalize_human_boundary` does not
  rebuild a legacy decision at a batch checkpoint (`worker_quota.at_checkpoint`) while the
  published request is still pending in the ledger (`resolver_human.pending_issued`),
  even when a settings write left it stale. An uncertain serial attempt already
  prevents it the same way. The request stays for its own authority: a retry
  supersedes it, and a route answer with its token re-binds it.
- **A member retry with settings.** `dispatch.member_retry_refusal` holds the checks
  `request_retry` made. `load_locked` now asks it before saving a settings change that
  comes with `--retry-builder`. A refused retry, such as a member its provider's
  content filter refused or one that already completed, saves nothing and says so in a
  sentence after the refusal ("... Nothing was saved, including this invocation's
  settings; they are saved by the same command without --retry-builder."). An accepted
  retry is saved with its settings and the run continues under them (the Tester, later
  batches): a quota member reruns its model, and a failed member gets its second attempt.
- **A Builder route change with a member retry is refused** (`worker_quota.retry_route_refusal`,
  asked by `load_locked` after `member_retry_refusal`). A member reruns on the route its
  batch started it with, and only an answer to its `route-terra` question moves that
  (`worker_quota.assign_child`). A changed Builder model, provider or reasoning effort with
  `--retry-builder` names the model the member reruns on, and for a member stopped on its
  model the accepted form, `--answer route-terra=MODEL`; nothing is saved.

The tests cover each member cause through the CLI: quota, refusal and a generic failure,
both for the settings flag alone and with `--retry-builder` (and a completed member's
refusal), and a Builder model given with a quota or failed member's retry. A unit test
covers a member that is no longer the batch's current stop: its request is asked again
without the question or its advice.

## Composition with neighbouring changes

- **Master's #586** (`--resume-paused` with `--answer` dispatches the next stage) acts
  after a committed requirements answer or goal approval. A member's `route-terra`
  answer goes through `run_actions.answer_quota_question`, which saves the route and
  exits before that path, so the two do not meet. A route answer that comes with
  another role's settings flag behaves as before: the settings write withdraws the
  request, the settings are saved, and the answer is refused as out of date, naming the
  `--no-chat` run that publishes a fresh request. That fresh request now asks the
  member's question again, so the answer it advises is accepted. Checked on `47107cf`
  with `--resume-paused --answer route-terra=MODEL --sol-model MODEL` at a quota member's
  stop: the setting was saved, the answer refused as out of date, and the next run asked
  `route-terra` again.
- **Master's #581** (corrective information is re-evaluated at a resume) skips an
  invocation carrying `--retry-builder`. `resolver_human.withdrawn` names only a
  superseded request, never one consumed by a response, so the information path's own
  ask-again (`operational_information.retired`) is unchanged here.
- **`claude/operational-pause-fail-opens`** asks a withdrawn pause again inside
  `load_locked` from the withdrawn request's own cause, and `run_actions` asks from
  `pause_authority.held_cause`, which also strips appended advice. Both reach
  `record_operational_exhaustion`, where the payload rule restores the member's question;
  the cause rule then has nothing to do. With `--retry-builder` that branch also keeps the
  request rather than withdrawing it, and the member checks here run before its settings
  write. Either merge order works: the two merge without conflicts, and on the combination (this branch
  at `8c034e0` with that branch at `8f9eef8`, 2026-10-07) the eight tests here, that
  branch's `tests.test_operational_pause_authority` and `tests.test_architecture` pass
  (40 tests). That branch alone (`8f9eef8`) still drops the member's question and hits
  the guard: seven of the eight tests here fail on it (the guard three times, the question
  dropped twice, route advice repeated once, a Builder model accepted with a member retry
  once). Only the failed member's advice passes, which its `held_cause` already gives once.
- **`claude/issue-541-member-stop-information`** (#541, unmerged) conflicts with this branch
  in `dispatch.request_retry` and `worker_quota` (it moves `current` to
  `autocode_member_stop` and gives `refused_retry` the state and the open request). Whichever
  merges second keeps `member_retry_refusal` as the one member check and calls the new
  `refused_retry` from it; a `Paused` it returns (a member's stop collected again) is not a
  refusal, so the run setup must not turn it into one.
