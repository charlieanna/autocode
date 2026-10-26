# Integrated asynchronous TUI qualification (#1886)

The initial source pin is master
`97380606f5aa841e1c8798b6602c8b587474c602`, runtime tools tree
`767079d14d32167548b4c37065eac83d3f2a442a`. This qualification adds one
combined native-host journey and fixes the long-label display defect #2070.
The PR and #1886 evidence comments record the final tested commit, CI verdict
and host artifact; this document does not turn pending checks into passes.

## Confirmed display defect

An attention row placed the workspace and up to 80 task characters before its
event type and read state. At 80 columns, a long Unicode request hid both the
completion label and `[acknowledged]`. The focused long-title regression fails
on the source pin. Rows now show state and event type first, followed by the
project and task. Storage, core identities and task actions are unchanged.

The real curses regression uses the long title at 80x24, acknowledges the item,
returns to the unchanged Unicode draft/cursor while the acknowledgement child
is deliberately held, and verifies acknowledged state at 120x34. Its local
sample was 14 ms for a key while that child remained held. This is input
responsiveness, not model or task-completion speed.

## Combined native-host journey

`tests.test_worker_host.PersistentHost.test_three_projects_two_viewers_share_attention_without_replaying_a_stale_approval`
runs only on the explicitly prepared disposable Linux service/SSH CI host:

1. Complete project C with the offline provider; leave project B at its exact
   plan approval; hold project A's Builder behind a fixture gate. All use the
   same unchanged provider executable and local acknowledgement identity.
2. Open C, then B in a real TUI. Keep a Unicode draft and cursor on B; acknowledge
   C through F8 without approving B or changing either task's attempts.
3. Start another real viewer. It observes C's saved acknowledgement, then
   explicitly opens and approves B. B completes while A remains held.
4. Submit the former approval in the first viewer. Its fresh status check must
   detect the changed gate and reach the current completion without another
   Builder or task attempt. Release A and require one Builder there too.

The projects have distinct workspaces and shared public discovery. Git clones
reuse the committed fixture seed; a scoped gate is installed before any run
starts, so it cannot invalidate another run's transport. The test uses public
TaskRun commands/status and actual TUI input, not private task-state edits.
Failed host output remains in the CI artifact. Ordinary unit-test skips of the
native-host class are not qualification evidence.

## S01-S15 evidence matrix

This is the coverage map for the remaining integrated audit. A source test name
identifies an obligation; the final recorded result must identify its actual
qualified revision and host. Existing passing slices remain useful without
claiming that the whole matrix has passed.

| Scenarios | Public proof and negative controls | Scope still to reconcile |
| --- | --- | --- |
| S01-S04: approval, external view, slow verification, detach/SSH | `test_worker_host` launches persistent services, exercises quit/Ctrl-D/SIGHUP/SIGKILL/PTY/SSH loss, and checks unchanged attempt and controller identities before completion. `test_chat_polling_cli` keeps input live during held reads. | Final native-host artifact at the recorded qualification head; an owned legacy worker retains its different exit policy. |
| S05: worker and host loss | Native worker-loss cases retain edits/routes/attempts and refuse false completion. `test_worker_cli` separately injects a changed boot identity and unavailable boot inspection; accepted launch receipts never re-arm dispatch. Literal graceful guest reboot passed in run 40247430778, with three public post-reboot checks and exact-child cleanup. | The disposable `durable_local` fixture does not convert the retained legacy `owned` product run or establish abrupt power-loss behavior. |
| S06-S07: competing viewers and three runs | Existing launch-receipt contention plus the new three-project/two-viewer case; a stale approval must not dispatch a second Builder. | The new combined case needs the dedicated host result, not a mocked service or skipped local test. |
| S08: late, failed and reordered reads | `test_chat_polling`, `test_chat_polling_cli`, `test_chat_context` and `test_chat_observation` preserve fresh authority, drafts and last-known availability while reads hang or arrive late. | Report actual samples and configured bounds; no inferred 25-run freshness promise. |
| S09: uncertain provider/report recovery | Retained-attempt worker-loss cases, explicit refusal/token regressions and verification display tests distinguish saved edits from accepted checks. | Retain the exact fault/action family for a final combined recovery verdict; one pause receipt is not universal action idempotency. |
| S10: detached attention and changed gates | `test_attention_flow` restores questions, shares read receipts, refuses stale answers/plans and distinguishes same-chat completion IDs. The new host case joins real acknowledgement, another viewer and an active Builder. | Acknowledgement is read state, never an answer, plan approval or delivery acceptance. |
| S11-S12: cost, routes and stage choices | `test_chat_cost` keeps incomplete accounting unknown and separates launched routes from saved future routes. `test_chat_stage_policy` renders only the core's compact/full/fallback decision and its evidence coverage. | F2 is now implemented by #2038/#2043; its opt-in policy and qualification belong to #1867. Display evidence alone does not establish savings. |
| S13: actual bug and follow-up | The retained adaptive Codex/GLM TUI task has red-on-original/green-on-fix proof, seven pytest tests and 17 independent checks; bundle manifest `a27fe3cd0b1d1ad6cef46df2467a85f45cc6e0eb07a6738a71960adeee318343`. | That older execution is not a fresh live execution of this source pin. Keep any new trial or reuse decision explicit; never relabel a fake or read-only replay as new model delivery. |
| S14-S15: terminals, history and identity | Real curses tests cover 80/120 columns, Unicode, paste, resize and held acknowledgement; context tests cover unavailable, removed and similarly titled projects. #2070 adds long-title state/type visibility. | Correlate terminal results with their actual source pin and retain failed attempts. |

## Timing and test interpretation

The retained live task recorded 967.613408 summed attempt seconds (16m08).
Its raw session was 51m32, including a measured 24m10 engineering/CI/install
hold. Known accounting was $0.529521 with seven unpriced stages. The retained
direct Kilo **CLI** sample was 27.891 seconds wall time. These are different
measurement scopes and are not a controlled extension benchmark or proof that
current AutoCode is faster. Tester and Completion Reviewer remain separate.

Local focused evidence for the display fix: 117 attention/polling/TUI/context
checks passed in 1.117 seconds; two model-free real-terminal tests passed in
1.535 seconds. The first 61-case contract batch encountered two tmux IPC errors
inside the macOS tool sandbox; the terminal checks passed with normal private
terminal access. The red long-title assertion and failed attempt are retained
with the ignored qualification output. No paid model was needed for this
display or host-fixture correction.

#1886 and parent #1877 remain open until the mandatory integrated evidence and
live-delivery obligations are reconciled or explicitly deferred by the owner.

## Retained real follow-up after #2071

Merged runtime `51a4f676d4127d694bb7fd06c495fdd2f6b1830a` was installed in the
existing isolated trial; all 936 shipped files matched that checkout. The saved
chat `20261023-233633-chat-53c9db` initially remained complete at turn 1 with
22 attempts, $0.529521 known and seven unpriced stages. A real TUI follow-up
requested an optional keyword-only `start=0` accumulator, exactly four new
pytest functions and preservation of all seven existing functions. This is a
feature after the already delivered bug fix. The same chat reached turn 2;
GLM-5.3 Low handled planning and Codex Medium handled independent plan review
through Kilo 7.8.3. No MiMo or allowance reset was used.

The continuation exposed distinct causes, which must not be collapsed into
one latency claim:

- The first report omitted `acceptance_tests`, a genuine output-format error.
- Identical task/feedback text doubled six coverage obligations into twelve.
  #2072/#2073 deduplicates the projection while retaining source receipts and
  real coverage refusals.
- Planning omitted protected text and milestone coverage. The guard refused
  those omissions; it is not permission to weaken approved requirements.
- Reviewer concern C2 inferred that already passing workspace tests could not
  retain fail-first proof. Public evidence still names original run commit
  `231f0c1820e82dd6accda4e76954e5032e1c7d9c`, whose function is `sum(values)`.
  The runner does not reset that comparison on follow-up. #2074 supplies that
  reference and its overlay limits in planning/repair context. The same review's
  invalid generator syntax and missing keyword-only assertions were legitimate
  separate concerns.

The retained pre-correction bundle manifest is
`01c16b41d71a769f84e3cce1c9a7d02db0469a6fdaf1facf86af6e487cb7a841`.
At that checkpoint the run was paused before a second Builder, with $1.071969
known and nine unpriced stages. It is not a completed follow-up. The original
17 independent checks passed before starting it; the new accumulator oracle
failed against the unchanged function with the expected missing-argument error.
During live Requirements, `/help` rendered in 19.7 ms at 120x34. That measures
input responsiveness, not delivery speed. Candidate prompt convergence, final
product checks and a complete elapsed-time comparison remain outstanding.

## Later qualification and approved-plan transition (#2096)

The literal graceful reboot qualification passed at source
`1f8f49d4d266921d3b0baac445971e4dc6fa72c3` in Actions run 40247430778.
Artifact 12619417187 has ZIP SHA256
`779deeb1e75373628c8daa7e824f0e001bcf4341e007b19078c685cc1dd729fb`.
The guest boot changed while the outer host boot did not. All three public
samples retained attempts, routes, approvals, edited file hashes and known
completed usage with one dispatch, no false completion and an explicit
process-cleanup recovery hold. Cleanup passed; the coordinator took 670.036
seconds. Both earlier failed qualification attempts remain recorded in #1881.
These are offline-provider lifecycle checks, not live model throughput.

Merged #2085 corrected feedback provenance; #2089 corrected duplicate inherited
review counting. On installed master
`0b39b1681b8bb3658d78970c4bbbed9361d8521d`, ordinary TUI Resume completed
the final review without a new allowance. The owner then approved exact plan
revision 12, exported body hash
`c14147a7fb3edd065138e26e8223a9a5188e86de117116d1f33e2ad5de72fcd7`.
The test writer preserved all seven original pytest function bodies and added
exactly four tests. Independent pre-Builder pytest gave three expected
missing-`start` failures and eight passes in 0.02 seconds.

The next transition exposed #2096: inherited cases that intentionally pass the
previous completed turn were unconditionally relabeled `guard:` and sent back
to planning. This produced unapproved revision 13 and a 3/2 review hold before
Builder. Public bundle manifest:
`9368699ce839cb34e08b96d66fbb4a9b104c30ddd57345973632214092e7453b`.
The correction must preserve the exact approved plan, actual review charges
and fresh original-reference proof. A new follow-up guard additionally needs
fresh results against authenticated previous-delivery source; historical PASS
receipts cannot replace either current check. At this checkpoint, the
follow-up remains incomplete and no faster delivery result is established.
