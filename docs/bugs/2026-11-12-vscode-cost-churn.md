# VS Code cost-churn walkthrough, 2026-11-12

The owner requested three separate AutoCode chats for #3287, #3288 and #3290,
using GLM 5.3 and MiMo v2.6 Pro. Requirements and exact plans were reviewed by
the operating agent under the AGENTS.md delegation. The live runtime and
extension started at `e52d677c2`; delivery branches were reconciled with
`cccb6545a`, then rebased after #3323 and #3324 landed at `92d38af469`.
The live workflows did **not** establish completion. Retained
patches and subsequent operating-agent corrections have separate deterministic
test evidence in their pull requests (#3323 for #3288, #3325 for #3287).

## Additional findings

1. **Uncertain model-preflight cleanup has no fresh public recheck.**
   Run `20261112-010212-vscode-3220a0` stopped at `PAUSED_PROCESS_CLEANUP` after
   `SessionScanIncomplete`. Its offered continuation refused because cleanup
   was not verified. The saved uncertainty remained sticky; there was no safe
   public action to obtain a fresh observation. The view nevertheless presented
   a generic person-facing continuation. This blocked the #3290 live workflow;
   it was not cleared or treated as permission to repeat the provider call. [E1]

2. **The bundle omits model-preflight receipts.** The same run's export did
   not include its `model-preflight/` attempt receipts. The original evidence
   remains local; the exported bundle alone cannot establish cleanup. [E1]

3. **An old moved-run path exports obsolete evidence.** For
   `20261112-010138-vscode-08ff80`, status followed the moved-run pointer, while
   a bundle requested through the former root path exported the early checkpoint
   and an obsolete diff base. The canonical worktree run exported the current
   history and the expected `e52d677c2` base. Bundle collection should resolve
   the same public identity as status. [E2]

4. **Proof-only guard normalization restarts planning and approval.** Passing
   checks being classified as guards caused another review cycle in the #3288
   follow-up. Its reviewed revisions 5 and 7 had the same criteria, selectors,
   scope and limits; their difference was the review evidence in `source_trace`.
   This machinery should not create another person gate. Exact-plan binding
   remains required for an actual plan change. Observed on `e52d677c2`; needs
   qualification against newer runtime changes. [E3]

5. **Source-trace number preservation mistakes metadata for requirements.**
   The #3287 plan needed literal `AC5`, `AC6` and `discovery-02` references
   inserted into a criterion to satisfy the gate, although the referenced
   behavior and tests were already represented. This consumed additional plan
   revisions without changing product behavior. Observed on `e52d677c2`. [E3]

6. **New-chat model controls misrepresent explicit project configuration.**
   The extension displayed global stage defaults while the admitted run honored
   its explicit project GLM/MiMo configuration. Execution did not silently switch
   models, but the controls showed the wrong starting choices. Observed on the
   installed `e52d677c2` extension; not re-qualified after rebasing delivery. [E3]

7. **Current-master retained-work CLI baseline failure needs triage.** On
   qualified native Linux, `cccb6545a` and the #3288 candidate both failed
   `tests.test_retained_work_cli.RetainedRepairFlow.test_new_assignment_reviews_retained_work_without_another_source_edit`:
   the fixture observed three Builder launches where it requires two. The
   candidate's changed selection otherwise passed 22 of 23 modules (301 tests
   total). This is a baseline finding, not an introduced #3288 regression;
   its cause has not been established and its assertion was not weakened.

Known upstream fixes encountered on the old runtime, including #3304, #3307,
#3314, #3318, #3319, #3320 and #3322, are excluded from this new-finding list.
The walkthrough's three 3600-second Builder timeouts are additional evidence
for #3290. No fifteen-test deletion was observed in those live attempts; the
#3290 regression uses a separate synthetic fifteen-file deletion fixture.

## Evidence manifests and limits

The initial #3287 candidate and delegated plan review broadened the change to
deliberate ready handoffs. Another reviewer found that VS Code's
`advanceAfterInput` loop depends on their `RUNNING` status. The reviewed #3325
preserves that contract and projects the pause only for a genuinely lost
controller. This was a candidate planning/review error, not another master bug.

- **E1:** run `20261112-010212-vscode-3220a0`, bundle manifest
  `e2853d780d4e26156e811bd5c48339289a314e8a5a54da06e6c8a5fda1c336cb`.
- **E2:** canonical original #3288 run's second-timeout bundle, manifest
  `02eb44101e21770b7967f2087b1cc9f2d112a4a09694709d8d0dcc84ffd311fe`.
- **E3:** supplementary observation bundle, manifest
  `a3d67f97e08694810fa19dfb18fe37737903ee44ff2218030f6a2b739eb1334a`.
- #3287's first Builder-timeout bundle has manifest
  `827d7004980e4a1045ca8f2ce48f6e73129c4b382966893b9abb6aa9d22ff5a3`.

At the recorded pause, public usage views estimated $27.997005 for #3287,
$12.024657 for the original #3288 run, $3.996775 for its separate follow-up,
and $0.594365 for #3290. These are API-equivalent estimates, not verified
subscription invoices; the first two views explicitly have incomplete cost
accounting. Failed attempts and the original #3288 terminal stop were retained.

The computer-use service continued reporting the Mac locked after manual-unlock
replies, so the later work used public CLI actions and independent delivery
checkouts. No claim is made that the final fixes completed a live extension run.
