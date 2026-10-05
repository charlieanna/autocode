**First PR for #254: hold a rejection that repeats only in per-attempt noise; one explicit fresh attempt is the way out.**

It lands on top of #301 (branch claude/issue-301-permission-hold, head a147108). That branch already adds tools/autocode_failure_retry.py, with:
- stop_cause, target, record, arm and launched;
- REPEATED_FAILURE and UNRECONCILED;
- recovery_limits.RETRY_ADVICE and advice(allow_retry);
- the allow_retry site in record_operational_exhaustion.

Line citations refer to origin/master 02e59cb unless marked #301.

The design is design 1, with these grafts:
- **From design 2:** the run root is derived from the output path; temp names are matched by their exact shapes.
- **From design 3:**
  - a stalled incident's pending repair keeps its evidence up to date but is never queued;
  - a dispatch-side guard;
  - the CLI tests assert the pause_status recorded on the published request.
- **Narrowed from design 1:** path rewriting stops at the scratch tree, so repository file names stay distinct.

---

**What is verified broken on master**

Each Validator replay failure quotes its own receipt directory, <run>/check-replay/<stem>-<uuid hex>/replay.json (autocode_check_replay.py:75-76, 119).

signature() normalizes only this attempt's own output stem and lowercase hex (autocode_failures.py:51-54). So the original, report-repair-01 and report-repair-02 never match, and the run pauses PAUSED_INVALID_OUTPUT at streak 1.

That status publishes no request (autocode_resolver_runtime.py:351-360). A plain --resume-paused therefore archives the repair and launches a fresh Validator plus 2 repairs (autocode_stage_recovery.py:666-709; autocode_run_actions.py:305), with no bound.

If the streak does reach 3:
- the exhausted repair stays pending (autocode.py:271-290);
- the request is published by the pausing invocation itself (autocode.py:1274-1275);
- --retry-failed-stage refuses any pending repair (autocode_stage_recovery.py:874-875);
- after a source edit, a plain resume holds (run_actions.py:222-231). That is the #302 dead end (docs/bugs/2026-10-04-paused-source-edit.md:25-42).

---

**Incident identity**

The key is unchanged: failures.key = digest(stage or original_stage, source revision after the attempt, error status or class) (autocode_failures.py:26-38).

Continuity is failures.signature over normalized text. A new public pure function `normalize(text, *, roots=())` goes in tools/autocode_failures.py. That module still imports only autocode_util, and the fingerprint is already its job. The name autocode_incident is reserved for slice 2's incident packet.

**How roots are found.** `roots` is the parent of the nearest `iterations` ancestor of Path(record['output']), taken both as recorded and resolved (the layout is <run>/iterations/NNN/<slug>-NN, autocode_artifacts.py:56-57), plus state.get('run_dir'). record() passes these to signature(), with the run_dir taken from state.

**Rules, in order:**
1. This attempt's own stem becomes `<attempt>` (existing, :52-53).
2. Each root prefix becomes `<run>`. Then only these run-owned shapes are rewritten:
   - `<run>/iterations/\d+/[^/\s]+` becomes `<run>/iterations/<n>/<attempt>`;
   - `<run>/check-replay/[^/\s]+-[0-9a-f]{32}` becomes `<run>/check-replay/<receipt>`;
   - `/check-\d+/` becomes `/check-<n>/`;
   - `archived-[^/\s]+-[0-9a-f]{6}` (autocode_run_records.py:327) becomes `archived-<attempt>`.

   Everything after `scratch/tree/` is repository-relative and is kept verbatim (autocode_verify.py:735).
3. Temp names, matched by shape only:
   - a path component `tmp[a-z0-9_]{8}` becomes `tmp<rand>`;
   - `pytest-of-[^/\s]+/pytest-\d+` becomes `pytest-of-<user>/pytest-<n>`;
   - `/(private/)?var/folders/[^/]+/[^/]+/T/` becomes `<tmp>/`.
4. Dashed UUIDs, in any case, become `<uuid>`.
5. ISO-8601 timestamps become `<time>`.
6. Elapsed durations become `<duration>`: a decimal number followed by s, ms, sec, secs or seconds, or an integer followed by ms. Examples: 'Ran 3 tests in 0.004s', '1 failed in 0.12s'.
7. The existing hex≥12 and whitespace rules.

**Kept:** commands, exit codes, test ids, line numbers, concern and finding ids, repository paths, non-temp paths outside the run, error class and saved-output kinds.

Update the module docstring (:1-9).

---

**Novelty rule**

An entry is stalled when failures.stalled is true: streak ≥ 3 identical consecutive fingerprints at one source (autocode_failures.py:75-77, 118-123).

**R1, at rejection.** In tools/autocode.py reject_completed_stage (259-302), set `held = bool(failure and failures.stalled(failure))`:
- The block condition at :271 becomes `eligible and repair_limit(state) and (record.get('report_only') if pending else not held)`. A held incident therefore never opens a new round.
- The queue at :284 becomes `if pending['attempts'] < repair_limit(state) and not held`. A round already open stops at the stall, but latest_rejected, pins and error are still updated, so the archived evidence stays paired.
- The run falls through to the existing PAUSED_REPEATED_FAILURE pause (:293-302). Its message is unchanged.

**R1', at dispatch.** In execute_report_repair, after the stale and pin checks (:661-668): if `failures.stalled((state.get('failure_history') or {}).get(original.get('failure_key')) or {})`, raise `support.Paused('PAUSED_REPEATED_FAILURE', ...)` with the same sentence as the stall.

This closes the remaining ways a stalled repair could run again:
- a resume giving the repair its attempts back (reset_report_repair_for_resume, run_actions.py:314). That line is reachable on a --chat resume at a published request: the hold at :222-224 is skipped and the guard at autocode.py:820 returns early because the status is WAITING_FOR_USER;
- a repair left behind by an interrupted Investigator.

The net change to autocode.py is about +6 lines: 1345 + 3 from #301 + 6, against a ceiling of 1491 (tests/test_architecture.py:28).

**R2, without authority nothing launches.** The existing guard (autocode.py:813-840) and the published-request hold (run_actions.py:173-179, 222-231) now apply, because the streak actually reaches 3.

**R3, one authorization buys exactly one fresh non-report attempt.** At an unchanged source:
- authorize_failure_retry archives the incident's spent repair and rotates the role's session;
- the guard consumes the authorization in the same command (autocode.py:829-835);
- #301's failure_retry.launched marks the row when the attempt launches.

Nothing is reset. An identical recurrence continues the streak (3 becomes 4; _interrupted skips investigate_stuck and runner-owned rows, autocode_failures.py:67-71) and holds immediately:
- no repair, by R1;
- no Investigator: the stage:status identity is spent (autocode_stuck_job.py:179-182) and operator_retried is true (:204-209).

**R4, changed source is new evidence.** At a PAUSED_REPEATED_FAILURE stop, whether the status is raw or sits behind a published request, the same flag also works after an operator edit:
- it withdraws the request and archives the repair;
- the next attempt runs on the new source, which gets a new key at streak 1 with its normal repairs;
- relevance is not checked (that is a later slice).

**R5, unstalled entries are unchanged.**

---

**Changes**

**1. tools/autocode_failures.py:** normalize() as above. signature(stage_record, error, probe, roots=()) uses it, and record() passes the roots. About +40 lines.

**2. tools/autocode_failure_retry.py (#301):** add `stalled_target(state, *, cause, revision, published=None)`. It returns (record, entry) or raises ValueError with today's refusal texts. It imports autocode_failures, which imports only util, so no cycle is added. It refuses in these cases:
- cause != HOLD_STATUS, with cause taken from stop_cause(state, published), which tolerates a stale binding. resolver_human.current fails once user_events or the observed source move (resolver_human.py:53-79, 372), and reset_for_resume can append an event (resolver_runtime.py:718). Message: '--retry-failed-stage requires a run paused for repeated failure'.
- Any of UNRECONCILED except pending_report_repair is set (active_stage, uncertain_artifacts, job_failure), or a pending_report_repair exists whose original.failure_key is not the stalled key. Message: 'Reconcile the active attempt or pending report repair before authorizing a retry'.
- pending_questions other than the published request's own (the same check as target()).
- The latest failed stages row with a failure_key is not stalled. Message: 'No unchanged repeated failure to authorize; fix the cause, then resume'.
- The identity is inconsistent, moved from autocode_stage_recovery.py:884-889 but without requiring identity.artifact_hash == current revision. Required: failure_history[key] is entry; key(identity) == key; artifact_hash == record.source_revision; identity.stage == owner == next_stage. Message: 'Retry authorization requires the exact ... stage and failure identity'.

The revision callable is evaluated only to report whether the source changed. Update the docstring's 'Two stops accept it' section to cover a spent repair and an edited source.

**3. tools/autocode_stage_recovery.py:**
- Extract `_archive_spent_repair(state, original, reason)` from prepare_exhausted_execution_report_retry (:696-707) and reuse it in archive_stale_report_repair (:754-760).
- In authorize_failure_retry, after #301's denial branch, replace master's :868-889 with stalled_target. On acceptance:
  1. supersede_operational;
  2. if a pending repair exists, archive it with reason 'Explicit authorized retry of a stalled failure' and rotate the role's session;
  3. failure_retry.record(REPEATED_FAILURE, stopped={attempt_id, events, source_revision: record['source_revision']}, ...), so the row is bound to the failure's revision even when the source changed;
  4. write state.
- A refusal stays read-only.
- prepare_exhausted needs no change: with no pending repair it returns False.

**4. tools/autocode_resolver_runtime.py:** at #301's allow_retry site, allow_retry is also true when `stalled_target(state, cause=error.status, published=state.get(human.PUBLIC), revision=...)` does not raise. The request then carries RETRY_ADVICE and the option 'Authorize one fresh attempt with --resume-paused --retry-failed-stage', instead of INFORM_ADVICE (master :398-413). There is one shared check for advice and acceptance (#288).

**5. tools/autocode_run_actions.py:** the 'AutoResolver retained the human guidance...' hold (:177-178) appends recovery_limits.advice(allow_grant=False, allow_retry=True) only when target or stalled_target accepts.

**6. tools/autocode_recovery_view.py:** _failures (:131-144) adds two fields:
- `streak`: entry.streak, or count for a legacy entry;
- `authorized_retries`: the number of failure_retry_authorizations rows with that key.

Both are additive; nothing is renamed.

**7. tools/fake_codex.py:** the unreproducible-check branch (:273) applies only while `(data.get('recovery_context') or {}).get('human_information')` is absent. Report repairs replay the saved report (:42-48), so they are unaffected.

**8. Docs:**
- docs/workflow.md :162 and :168-171: what the fingerprint ignores; a stalled incident gets no further repair; one fresh attempt per flag, including after an edit.
- docs/cli.md, #301's --retry-failed-stage row: adds 'a rejected report that stalled after its repairs', with or without a source edit.
- docs/bugs/2026-10-04-paused-source-edit.md: the 'Still open' case is closed for stalled failures.
- New note docs/bugs/2026-10-05-replay-rejection-cycle.md: master spends 3 Validator calls on every resume with no bound; after this slice, 0 per resume and 1 per authorization.

---

**State keys**

No new top-level keys.
- **failure_history[k].signature:** now computed over normalized text. Its only reader is record's streak continuation. Old entries restart a streak in progress once, which is the conservative direction.
- **pending_report_repair:** moved to report_repair_archive by the new authorize path.
- **session_rotations, reconciliation_notes:** existing writers.
- **failure_retry_authorizations:** #301 rows of kind repeated_failure, with launched_attempt. Read by the guard, failure_retry.launched, stuck_job.operator_retried and the new view field.
- **recovery_context.human_information:** existing (resolver_human.py:501). It is how corrective information reaches the attempt (stage_context.py:41).

Status view: recovery.failure_groups[].streak and .authorized_retries only.

---

**What the user sees**

1. **First run.** 1 Validator, 2 report repairs and 1 Investigator (spent), then a pause recorded as PAUSED_REPEATED_FAILURE with the request published. The stop reason quotes the exact replay error and #301's RETRY_ADVICE.
2. **Plain resume, chat resume or restart.** Exits 2 with nothing launched. After provide_information, the guidance hold names --retry-failed-stage.
3. **`--resume-paused --retry-failed-stage`.** Prints 'Failure retry authorized for the recorded repeated failure; one fresh attempt proceeds under existing limits.' It archives the spent repair, rotates the Validator session and launches exactly one Validator. An identical failure holds again at count 4 / streak 4 with the same advice; there is no repair and no Investigator.
4. **After a source edit.** The same flag starts one fresh attempt on the edited source.
5. **--status.** The existing 'Retry the failed step once' action (recovery_view.py:196-197) is now accepted.

---

**Tests**

1. **tests/test_failures.py, new NormalizeTests (pure).**
   - Positive: each noise kind collapses. Covered: other attempts' receipt dirs; iterations/NNN; check-NN; archived-<stem>-<hex6>; /tmp/tmpab12cd_9; pytest-of-u/pytest-7; /var/folders/../T; UUIDs in either case; ISO times; 'Ran 3 tests in 0.004s' vs '0.017s'.
   - Negative: still distinct are different commands, test ids, exit codes 1 vs 2, line numbers, P1 vs P2, scratch/tree/test_v2.py vs test_v3.py, and /tmp/a.log vs /tmp/b.log.
2. **LedgerTests.**
   - The real check_replay error text with receipt dirs for validator-01, validator-report-repair-01 and -02 reaches streak 3, and repeated() returns the entry. This fails on master.
   - The same noise with different commands stays at streak 1.
   - The existing DISTINCT tests are unchanged.
3. **PauseTests, through reject_completed_stage on the RetrofitTest fixture.**
   - A stalled incident raises Paused PAUSED_REPEATED_FAILURE, not ReportRepairQueued, with attempts left. That includes mid-round: max_attempts 2 with a streak carried over.
   - The pending repair's latest_rejected and error are updated.
   - Control: a different error afterwards raises ReportRepairQueued.
4. **tests/test_report_repair.py.** A pending repair of a stalled incident whose attempts were given back raises PAUSED_REPEATED_FAILURE from execute_report_repair, and patch.object(runner, 'run_role') is never called.
5. **tests/test_failure_retry.py, FailureRetryTests.**
   - A pending repair bound to the stalled key is archived and the session rotated; the guard consumes the authorization once; a reload holds.
   - The read-only refusals gain job_failure and a pending repair of another key. {'pending_report_repair': {'attempts': 2}} still refuses.
   - :69-71 becomes a new test: a changed source authorizes one attempt bound to the failure's revision.
   - New stale-binding case: after a user event is appended, a published hold still accepts the flag.
6. **CLI negative control (FailureRetryCLITests).** Fake provider with AUTOCODE_FIXTURE_UNREPRODUCIBLE_CHECK, AUTOCODE_FIXTURE_MODE=no-human and AUTOCODE_REGISTRY_LAUNCH_PROBE.
   - Validator-side launches are exactly [sol, sol_report_repair, sol_report_repair, investigate_stuck], and the published request's origin.pause_status is PAUSED_REPEATED_FAILURE.
   - Two plain --resume-paused, one --resume-paused --chat and one bare restart each exit 2 with no launch, and failure_history and stuck_investigations are unchanged.
   - Every flag in the stop reason and in the request is accepted.
   - --retry-failed-stage launches exactly [sol]; failure_groups[0] is {count 4, streak 4, authorized_retries 1}.
   - A second authorization launches exactly one more sol.
7. **CLI sound control.**
   1. From the hold, send --resolver-response provide_information. A plain resume still launches nothing and names the flag.
   2. --retry-failed-stage launches exactly [sol, astra_review] and reaches TASK_COMPLETE.
   3. The fresh validator-NN.prompt.md contains the corrective text.
   4. evidence.check_replay is PASS, and its source_revision equals the validation's.
   5. The earlier FAIL receipts remain under check-replay/, and failure_history is kept at count 3.
   6. Exactly one repeated_failure row exists, with launched_attempt set.
8. **tests/test_check_replay.py:396-405.** Assert the published request's origin.pause_status == PAUSED_REPEATED_FAILURE. The saved status is the published request's status (resolver_human.py:111), so state['status'] cannot be asserted.
9. **tests/test_paused_source_edit.py.** New test_retry_failed_stage_moves_a_stalled_hold_whose_source_was_edited, seeded with a stalled history. The flag gives calls [('sol', False)]; the request is superseded; the repair is archived with its pins; the sol session is rotated; the row has launched_attempt. :176-194 stays unchanged.
10. **tests/test_recovery_advice_conformance.py.** A stalled-rejection checkpoint: every advertised flag is accepted, and --retry-failed-stage moves the run.
11. **tests/test_recovery_view.py.** streak and authorized_retries are present; a legacy entry reports streak = count.
12. **Gate.**
   - `$PY -m unittest tests.test_architecture`: TANGLED is unchanged, there is no cycle, and autocode.py stays ≤ 1491 lines.
   - `$PY tools/run_suite.py --changed --include-slow`, for test_rework_cli and test_build_recovery_blackbox.
   - `$PY scenarios/run.py run --fake`.
   - If test_failure_retry or test_check_replay goes over 10 s, add it to tests/suite_slow.json with a reason.

---

**Explicitly deferred:**
- the incident packet and a hypothesis;
- astra_diagnose erasing history;
- per-incident Investigator identity, and skipping it when the cause is deterministic;
- A,B,A alternation;
- Builder same-edit detection;
- relevance checks;
- setup-vs-defect classification;
- gating the view action by eligibility;
- checkpoint continuation dropping authorization rows;
- time and token metrics.

Size is about 460 LOC, about 300 of it tests.