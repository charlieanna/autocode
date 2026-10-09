# A repair that followed the reviewer's own decision paused as a stale handoff (#185)

**Seen:** live run `1y_2l0cv` of `discuss-then-design-then-build` on Claude models (2026-10-07), in the
design turn. The first Completion Review failed task T1 (all 11 criteria of milestone M1), and the
Resolver's repair kept 6 of them. That repair broke AC8, one of the dropped criteria. The next Completion
Review's sealed REWORK decision named AC8 again, which its prompt allows for any criterion of the
approved milestone, and the Resolver's repair followed it. Builder admission then paused the run with
`PAUSED_STALE_HANDOFF` ("current source, task, settings, contract or scope changed before admission").

**Cause:** since #423, admission lets a repair task only narrow the failed task's criteria
(`_narrows` in `autocode_resolver_recovery.py`). The failed task was the narrowed repair, so the
reviewer's AC8 counted as an addition. Every binding key matched. Nothing retries this pause:
`PAUSED_STALE_HANDOFF` is not one the Investigator takes. #578 did not change this module.

**Fix:** admission also allows the criteria that the packet's sealed decision assigns to its next task,
when that task is in the failed task's milestone and the criteria belong to that milestone in the
approved contract. Both come from the hash-pinned packet, so the Resolver still cannot add a criterion
the reviewed decision did not name, move milestones or change the contract.
