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

## Initial S01-S15 coverage map

This historical map identifies the original obligations and their boundaries.
The final scoped results below record their qualified revisions and hosts.

| Scenarios | Public proof and negative controls | Initial qualification boundary |
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

#1886 and parent #1877 were held open for the mandatory integrated evidence and
live-delivery obligations. Their final reconciliation is recorded below.

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

## Final scoped result, 2026-10-26 UTC

The paid same-chat continuation completed on master
`45c2997ffad16e1ce3b999daa85d0e5f5d1b8f85` (tools tree
`f76a0eb205a415c64960ae6b9cac7db0df641038`). A clean wheel and all 950
installed source files were checked byte-for-byte. Ordinary Resume restored
the exact owner-approved revision 12; its body equals the exported plan hash
`c14147a7fb3edd065138e26e8223a9a5188e86de117116d1f33e2ad5de72fcd7`.
The three consumed planning reviews and their saved three-call allowance
remain recorded. No further planning review, test generation, approval,
recovery grant or limit increase was needed. Preflight added capability
certificates for the existing Medium/Low choices; routes and limits stayed.

The core recorded `TASK_COMPLETE` with current delivery at
`2026-10-26T01:43:19.634824Z`, source
`72c74b702d066a7b504f1cc5056251c3b8ab8258cbbf6870d474c5c13e2dfc19`.
All eleven criteria were accepted. The test writer changed only
`test_totals.py`; Builder changed only `totals.py`. Seven existing test
functions are unchanged, with exactly four added. Independent verification
used an offline Linux container, a read-only source mount and no credentials:

| Source with the final tests | Result |
| --- | --- |
| Original buggy reference `231f0c1820e82dd6accda4e76954e5032e1c7d9c` | 7 failed, 4 passed |
| Previous completed checkpoint `7b078185c719c0c265954ca0b3185bff78acad03` | 3 failed, 8 passed |
| Accepted candidate | 11 passed; 29 additional independent checks passed |

The fresh core proof also passed against both references. Original restore
cases retain their `test:` obligations; the new preservation guard uses a
fresh check of the authenticated previous delivery. Tester and Completion
Reviewer remain separate. Actual reached models are only Codex
`openai/gpt-6.1-sol` Medium and `zai-coding-plan/glm-5.3` Low through Kilo 7.8.3.
The public bundle manifest is
`2fc5b922ad297fd9cbf5271604e073b45617bbbe780624627b2608b578bc6e25`.

### S01-S15 closeout

Each row retains its qualified source and host evidence. Reused component
results are distinct from the paid run and the final viewer check.

| Scenarios | Scoped result and evidence |
| --- | --- |
| S01-S04 | PASS: [#2081 Ubuntu](https://github.com/charlieanna/autocode/actions/runs/40240625702) and [native service/SSH](https://github.com/charlieanna/autocode/actions/runs/40240625692), head `68d4ce7a090e1dd150319a563c47126dbcd95618`: 508 selected tests and all 8 native cases. Viewer/SSH loss, competing ownership and boundary pause retain the core's current authority. |
| S05 | PASS: literal graceful reboot [40247430778](https://github.com/charlieanna/autocode/actions/runs/40247430778), source `1f8f49d4d266921d3b0baac445971e4dc6fa72c3`, three public post-reboot samples and exact-child cleanup. The earlier failed attempts remain recorded above and in #1881. |
| S06-S08 | PASS: the same #2081 native/Ubuntu qualifications cover three projects/two viewers, stale approval, reordered reads, unavailable contexts and 25 public inventory entries. Actual listing-held samples were 121.3 ms for a key and 127.4 ms for help. Listing and active subscriptions are separate scopes. |
| S09 | PASS: public repair/retry cases in [master run 40140418425](https://github.com/charlieanna/autocode/actions/runs/40140418425/job/121014841573), source `76377546fa7a1e56df6e14b2d7807b1f86bd4cfb`, include wrong-command/wrong-exit report correction, retained implementation and exact interrupted-report retry. #2096 adds focused stale-token/one-time recovery controls and the actual same-chat recovery above. Acknowledgement, pause, approval, retry and refusal keep their distinct authority. |
| S10 | PASS: attention-flow/read-state cases in #2081 plus its native two-viewer journey. Read acknowledgement grants no answer or plan authority. |
| S11-S12 | PASS: cost coverage remains explicitly incomplete, and core policy/fallback rows are rendered by clients. [#2043 CI](https://github.com/charlieanna/autocode/actions/runs/40163897145), head `d07ea7e90bd2ee0ade510fee532c39bd7af1262f`, includes all 8 stage-policy and 14 chat-cost cases. [M763's verification-schedule checks](https://github.com/charlieanna/autocode/actions/runs/40140418425/job/121014841573) passed 33 cases including identity invalidation and public restart reuse. Automatic policy/savings decisions remain the separate #1867 discussion. |
| S13 | PASS: the original bug delivery and the exact-r12 same-chat feature are accepted, with fresh proof, preserved tests, independent checks and immutable source/bundle pins above. |
| S14-S15 | PASS: Unicode, paste, resize, history and stable run/context identity use #2081's real-terminal evidence. #2110/#2111 additionally fixes repeated completion text under distinct public IDs: [302 tests/16 modules](https://github.com/charlieanna/autocode/actions/runs/40257712275/job/121363100207), real terminal regressions and [native service/SSH](https://github.com/charlieanna/autocode/actions/runs/40257712276/job/121363047259), head `7cea2180e7b51ca25f2e101b9a41999f77b4393b`. |

After #2111 merged, master
`6afbf948fd4ea474d17ba7bcc1a5edc9172c3b9c` (tools tree
`cf2dee3e9ae87cef53104b1bb80aa6f29d345893`) was cleanly installed and all
950 files verified. Reopening the actual completed chat visibly showed
"The requested task is complete" beside current verified delivery and 11/11
requirements. All 65 attempts, usage, routes, settings, approvals and delivery
were unchanged; no model call was repeated. The pre-fix screen and pure
two-view replay are retained with #2110's red/green regression evidence.

This qualification covers supported persistent Linux and graceful reboot.
The retained product run remains legacy `owned`; the reboot fixture was
`durable_local`. Abrupt power loss, 25 active workers, a fixed global freshness
SLA and deployment of Jira/channel adapters are outside this accepted scope.

### Actual timing and cost comparison

| Measurement | Observed result |
| --- | --- |
| Resume to recorded core completion | 515.065 s (8m35) |
| Two preflights / Builder / fresh proof | 45.699 s / 95.115 s / 8.9 s |
| Tester / separate Completion Reviewer | 239.338 s / 58.888 s |
| Whole follow-up, including earlier failures and holds | 5h01m30.688s wall; 43 attempt rows, 13 rejected; 36m26.293s known summed execution |
| Retained original AutoCode bug run | 16m08 known summed attempt time; 51m32 raw wall, including 24m10 engineering/CI/install hold |
| Retained direct Kilo CLI original-bug sample | 27.891 s wall; 7 tests and 17 independent checks |

The 8m35 recovery slice establishes successful convergence after the planning
repair. It does not erase the preceding failures or establish an overall
speed advantage over Kilo. The comparison uses different task slices and
verification scopes. The final display repair and its CI happened after core
completion and are outside that 8m35 measurement.

The core records a known estimate of $2.162992; 13 stages remain unpriced and
the cap remains $3. Historical per-attempt estimates use another pricing basis;
their sum is not that subtotal. Complete provider billing remains unknown.
The main remaining performance decision is evidence-aware stage selection
and reduced duplicated model work, recorded with the actual measurements in
[#1867](https://github.com/charlieanna/autocode/issues/1867#issuecomment-6406604380).

Failed CI remains part of the record: #2098 first needed a formatting fix and
then encountered the stale additive-field expectation; #2099 corrected that
expectation but exposed the separate unqualified fake-version fixture.
[#2106](https://github.com/charlieanna/autocode/pull/2106) repaired only that
fixture, with 23 CI tests and required checks green. Product admission and the
shared unsupported-version control were preserved. No paid baseline or broad
local reliability sweep was added for these test/documentation corrections.
