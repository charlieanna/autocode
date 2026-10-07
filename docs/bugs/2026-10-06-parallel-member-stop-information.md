# A parallel Builder's model stop had no single way forward after information (#541)

A Builder in a parallel batch that stops on quota (`PAUSED_BUDGET`) or on a
content-filter refusal (`PAUSED_CONTENT_FILTER`) asks the parent's `route-terra`
question. The request also advised "send corrective information ..., then
`autocode resume`". After a person did that, no question was open and nothing
agreed on what came next.

Before #486 a plain `--resume-paused` held for good, so a refused member had no
way on short of re-planning. #486 (#581) re-evaluates the information once: it
admits a plain resume, which collects the batch again and asks `route-terra`
again without a launch. Reproduced on master `5071412` (with #486) through the
real CLI and worker processes, using the `tests/test_quota_worker.py` fixture
(M1 stopped, M2 `BUILT`), what was still wrong:

- The request still advised "..., then `autocode resume`".
- After the information `needs.action` named `--resume-paused`, while the card
  offered only `inspect` and `feedback`.
- Refused member: `--resume-paused --retry-builder M1` was refused with "It
  continues on the model a person names in answer to its route-terra question",
  and no such question was open. `--resume-paused --terra-model MODEL` retired
  the information and asked a generic request with no `route-terra` question, so
  no answer could rerun M1. `--answer route-terra=MODEL` was rejected, naming a
  relaunch that publishes nothing.
- Quota member: two commands were accepted. The resume asked `route-terra`
  again, and `--retry-builder M1` reran M1 and completed the run. The card
  offered neither.

## Fix

`tools/autocode_member_stop.py` names the one way forward once a member's
model-stop request was answered without a model (information or leave paused):
`--resume-paused --retry-builder M`.

- For a refused member it collects the member's saved stop again: AutoResolver
  asks its `route-terra` question again, and nothing launches
  (`autocode_worker_quota.refused_retry` returns the stop;
  `autocode_member_stop.ask_again` records a `builder_route_question` user
  event, so the binding is new and the request is asked again rather than held
  as already answered). For a quota member it reruns the member unchanged, as
  before.
- AutoResolver's evaluation of the information holds a plain resume there and
  names that command (`autocode_operational_information.decide`); while the
  review is pending, `information_review.action` already names it. After
  `leave_paused` a plain resume holds as before and names it too.
- The request's advice at a member's stop names it instead of "then `autocode
  resume`" (`autocode_resolver_runtime.record_operational_exhaustion`), and so do
  the response's acknowledgement and stop reason, `needs.action`
  (`autocode_run_view`) and the recovery card (`autocode_recovery_view`: "Retry
  Builder task M1 unchanged", or "Ask which model Builder task M1 continues on").
- While a question is open, `--retry-builder` of a refused member is still
  refused; its message names the open question only when it is that member's,
  and otherwise says the member is asked once the open request is answered.
  With none open, another refused member's retry is refused naming the answered
  member's command, which is asked about first.
- A stale or retired re-ask at a member's stop (for example after
  `--terra-model`) asks that member's question again, reporting the member's own
  stop, not a generic request.
- `--answer route-terra=MODEL` with no question open is refused naming that
  command, not a plain relaunch (which asks nothing at this stop).

## Still open

- `--grant-recovery N` advice can still appear at a member's stop when the run's
  automatic-recovery allowance is spent; a grant then re-collects the batch and
  asks the member's question again.
- After corrective information on another pause (#486), `needs.action` names
  `--resume-paused` while the recovery card offers a resume only for the pauses
  in its own list.
