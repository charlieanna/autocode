BASE AND VERIFICATION
Base: origin/master e2eadf0, which includes PR #404 (9f3b7fb) and PR #388.

Experiment in <session scratchpad, gone>/wf-185/judge, a git-archive export of e2eadf0:
- Master as is: discuss-then-design-then-build --fake ends HONEST_BLOCKER, oracle 14/19, at PAUSED_REPEATED_FAILURE.
- With a 30-line B1 tools patch (follow_up: stage_index plus previous.design.documents from runner-measured changed_files; workflows: previous_design plus one PROMPT rule): PASS 19/19.
  - Turn 3 ran recognize_workflow, check_design, astra_discovery, astra_challenge, terra, sol, astra_review.
  - workflow.design_document and design_constraint.design_document were both docs/design/metadata-cache.md.
- With plan_approved=True added to the oracle's design and build turns: 23/23.
- tests.test_architecture, test_follow_up and test_workflows: 28 OK.

Owner note: PR #404's body says design-review-with-answers was "deferred by owner decision". Ship B1 on its own. Confirm with the owner before starting B2.

THE RULE (goes into docs/cli.md)
"--follow-up continues only a finished run (TASK_COMPLETE). A waiting, paused or blocked run refuses it (exit 2, state unchanged): answer it (--answer/--delegate with --resolver-token), approve or correct it (--approve-goal/--feedback), or resume it. Jobs that report questions without waiting for them (design review, discussion, code review) complete with the questions in their report; you reply to them with --follow-up. After a design review, that reply makes the Architect revise the same review. After a design turn, 'Build it.' builds that design as approved (checked against the code first, no Requirements, plan approval still required)."

Kept unchanged:
- follow_up.accept's status rule (autocode_follow_up.py:49-51).
- --answer's token rule (autocode_run_actions.py:385-398).
- goals.answer's routing.
- The AutoResolver scopes.

Why the issue's option 1 as worded is rejected: a design review never waits on master. Making it wait would need a second source of questions in the clarification guard and a re-routed goals.answer. It would also make design-review-planted (whose reference has Q2) stop and be auto-answered. Option 2 would weaken the finished-only guard.

PR B1: THE RULE, PLUS discuss-then-design-then-build PASSES (about 110 lines of code, 150 of tests, 40 of docs)

tools/autocode_follow_up.py
- The turn record gains:
  - stage_index = len(state.stages) at accept;
  - event_id = the brief_feedback event id (ties the turn to the #388 receipt).
- New turn_changes(state): changed_files of stage records from the finishing turn's stage_index onward (0 for the first request), skipping records with rejected set, de-duplicated, measured by the runner (autocode.py:536).
- previous.wrote: the job's report or note (review.report_path, design_review.report_path or answer.note_path, chosen by workflow kind) plus turn_changes, without .autocode/.
- The rewritten task names what the previous turn wrote, at most 8 paths, then "and N more". Example: "(discuss; it wrote docs/decisions/metadata-cache.json)".
- New carried_design(state, workspace):
  - propose mode: {mode: "propose", documents: .md files in turn_changes, without README.md, that exist};
  - review mode: {mode: "review", report_path, design_under_review, verdict, blocking, advisory, questions: [{id, question}]}, read from the report. An unreadable report raises ValueError, as carried_review does.
  - Stored as previous.design.
- Update the docstring's turns[] schema and list each key's readers.

tools/autocode_workflows.py
- follow_up() adds previous_design = previous.design (lines 134-144).
- PROMPT gets one rule after line 107: "Asking to build or implement what a design turn produced is build, with design_document set to the one document in previous_design.documents." approved_design (221-229) still validates the path.

tools/autocode_run_actions.py
- At line 386, when --answer or --delegate meets a TASK_COMPLETE run with nothing published, raise "This run is finished and waits for no answer; reply to the questions in its report with --follow-up TEXT".
- The follow-up refusal message (follow_up.py:50-51) also names --answer.

tools/autocode_design_job.py and tools/autocode_discuss_job.py
- render() adds "Reply with --follow-up TEXT to answer them in this run", only when there are questions.

scenarios/harness/catalog.py
- TURN_AFTER = ("complete",).
- Lines 129-130 refuse "stop" and every "needs:*" with: after must be "complete": a follow-up continues only a finished run (docs/cli.md).
- Rewrite the comment at lines 32-35.
- No catalog entry uses anything else (git grep).

scenarios/harness/driver.py
- Keep until_stopped(say_at): plan_compare and test_adversarial_lifecycle use it.
- New TurnNotReached(DriveError), raised in the branch at lines 186-188. Under the rule, that branch now means the product stopped before the turn could be said.
- Docstring: turns follow completion only.

scenarios/run.py
- On TurnNotReached, keep verdict.judge's outcome, capped at HONEST_BLOCKER (never PASS), and put "stopped before turn N" in the summary instead of ERROR.

Catalog: discuss-then-design-then-build (already on master since #404; no new files)
- Brief: discuss-cache-choice's question (in-process lru_cache, or a shared file cache with 4 gunicorn workers and a 60/hour upstream limit), writing docs/decisions/metadata-cache.json.
- Turns: "Shared it is; design it." and "Build it.".
- [fake] turn_paths: [["docs/decisions/"], ["docs/design/"], ["app/", "tests/"]].
- Oracle, file-level:
  - decision_note_kept;
  - design_document_written;
  - design_follows_decision (outside the Rejected sections: METADATA_CACHE_DIR, atomic write);
  - build_follows_design (AST check of the modules and signatures the design names);
  - project_tests_pass;
  - hidden_tests_pass (separate worker processes share one cache directory).
- Oracle, run-level:
  - three_turns_in_one_run;
  - discuss turn: run_checks(discuss, no_build, max_questions=3), changes only docs/decisions/;
  - design turn: run_checks(design), changes only docs/design/;
  - build turn: run_checks(build, no_requirements, max_questions=0), changes only app/ and tests/;
  - build_turn_checked_the_design.
- Reference: the note, docs/design/metadata-cache.md, app/shared_cache.py, app/metadata.py and tests.
- Broken: design-ignores-decision, build-ignores-design, build-changes-the-interface.
- Changes in this PR:
  - delete [run] known_failure (scenario.toml:10-11);
  - oracle.py:151-152 add plan_approved=True to the design and build turns (verified 23/23).
- A design turn that also writes code fails only through the per-turn changed_files check (a run-only defect), so it becomes a FakeRunTests case, not a broken/ variant.

B1 docs
- docs/cli.md:
  - row 77 (--answer): only a run whose needs.kind is answer; a finished run's questions take --follow-up;
  - row 79 (--follow-up): finished runs only (exit 2 otherwise); after a design turn, "Build it." builds that design via check_design;
  - a short "Waiting or finished" subsection: finished → --follow-up; waiting → --answer/--delegate/--approve-goal/--feedback; stopped → --resume-paused; plus the refusals and their TaskRun equivalents (TaskRun.answer, TaskRun.follow_up).
- docs/task-run.md:
  - lines 105-114: the rewritten task names what the previous turn wrote, and a build after a design starts at check_design;
  - no status-view field changes.
- docs/workflow.md:
  - lines 27-31: a design review's or a discussion's questions are answered with --follow-up;
  - the build line: a build after a design turn checks that design.
- scenarios/README.md:
  - lines 229-241 and the tree at line 451: after = "complete" only, and why;
  - TurnNotReached gives HONEST_BLOCKER;
  - row 368: no longer a known failure.

B1 tests
- tests/test_follow_up.py:
  - stage_index and event_id are recorded;
  - turn_changes skips rejected records and earlier turns;
  - previous.wrote is the note after a discussion, the report after a review, and the Builder's files after a propose turn;
  - the task names them, capped;
  - carried_design gives documents only for propose mode (README.md excluded) and review fields for review mode; an unreadable report raises ValueError with state unchanged;
  - RUNNING, WAITING_FOR_USER, AWAITING_GOAL_APPROVAL and PAUSED_* are still refused.
- tests/test_workflows.py: follow_up() carries previous_design; PROMPT has the build-after-design rule.
- CLI level: through in-process autocode main or TaskRun on a finished design-review run, --answer Q1=x exits 2 with the --follow-up hint and leaves state.json unchanged, while --follow-up is accepted and view.turn becomes 2.
- tests/test_design_job.py and tests/test_discuss_job.py: the hint appears only when there are questions.
- scenarios/test_harness.py:
  - TurnTests: load refuses after = "stop" and "needs:answer" (extend test_a_turn_must_say_something_after_a_known_state);
  - TurnNotReached on a stopped run gives HONEST_BLOCKER, never PASS;
  - FakeRunTests: discuss-then-design-then-build reference is PASS, with check_design in turns[2].model_stage_names;
  - FakeRunTests: a temporary solution whose design turn also delivers app/ (a temp catalog copy widening turn_paths row 2) is FALSE_COMPLETE on design_turn_changed_only_its_report.
- Run: unittest tests.test_architecture; tools/run_suite.py --changed; scenarios/run.py run --fake (every scenario as on master, except this one now PASS); tools/run_suite.py --scenario-harness.

PR B2: THE ARCHITECT REVISES A FINISHED REVIEW, PLUS design-review-with-answers (about 230 lines of code, 250 of tests, 40 of docs, plus the catalog entry)

tools/autocode_follow_up.py
- carried_design in review mode carries the whole report: concerns (id, area, severity, status, resolution, summary, evidence, example), questions, revision and revisions.
- If state.design_review.report_sha256 is set and differs from the file's sha256, raise ValueError: "review/design-review.json changed since the Architect's review; restore it or start a new run". Exit 2.
- New design_review_to_revise(state): the newest turn's previous.design plus said and event_id, when kind == design and previous.design.mode == review.

tools/autocode_design_job.py
- SCHEMA and PROMPT are unchanged for a first review.
- New schemas: REVISED_CONCERN = CONCERN plus status (open|resolved) and resolution; REVISION_SCHEMA uses it.
- schema_for(state) returns REVISION_SCHEMA when design_review_to_revise(state) is set, otherwise SCHEMA.
- A REVISE block is appended only in revise mode:
  - keep every earlier concern id (open or resolved), and never drop or renumber one;
  - change a severity, or resolve a concern, only for a reason the user's message or the repository gives, and say which in evidence or resolution;
  - add a concern only for a problem the message exposes;
  - drop the questions the message answered, keep the others, and do not re-ask;
  - if the message asks to review a different design, review it fresh.
- REPAIR_RULES repeats these rules for report-only repairs (served by jobs.repair_rules).
- packet() adds previous_review and user_message only in revise mode.
- New same_design(before, after): a shared repository path in design_under_review, otherwise equal normalized text.
- check(value, changed_files, previous=None):
  - the verdict is request_changes exactly when there is an OPEN blocking concern;
  - an example is required for open blocking concerns;
  - when not revising (no previous, or same_design is false), refuse any resolved concern;
  - when revising, refuse missing earlier ids, duplicate ids, a resolved concern without a resolution, and resolving an id that is new in this report.
- apply():
  - revision = previous.revision + 1 when revising, otherwise 1.
  - The written report adds runner fields: revision; status and resolution on every concern (open and "" in a first review); revisions = earlier entries + [{revision, said (null for the first), event_id, verdict, blocking: open ids, advisory: open ids, resolved: ids, questions: [{id, question}]}].
  - state.design_review gains revision, resolved and report_sha256; blocking and advisory become open-only counts.
- render() shows the revision number and the reply hint.
- Docstring: revise mode, the report fields and the state keys.
- New import edge design_job → follow_up. follow_up imports only workflows and contract_identity, so there is no cycle.

Other tools files
- tools/units/autoreview.py:97: `getattr(job, "schema_for", lambda s: job.SCHEMA)(state)`. The repair path reuses the record's schema (autocode.py:693-725).
- tools/autocode_workflows.py: a PROMPT rule: answering or correcting a finished design review (previous_design.mode review) is design, and the Architect revises it.
- tools/autocode_progress_view.py:209-213: an additive `questions` (open count) and `revision` on the design_review report entry. Nothing is renamed.

Harness
- oracle.IGNORED adds ".fake-turns", and project.overlay_paths skips it.
- fake_codex:
  - stray_edits skips .fake-turns;
  - design() serves <solution>/.fake-turns/<turn_number()>/review_design.json when it exists, passing status and resolution through;
  - recognize(): a follow-up with previous_design.mode == review that does not say build or implement is design.
- catalog: the prefix rule (lines 139-145) applies to every scenario with turns, not only those with turn_paths.
- driver: at each turn mark and at the end of drive(), copy the files changed since the previous snapshot to <evidence>/turn-files/<i>/.
- run.py: turns[i].kept_files = that directory.

Catalog: design-review-with-answers (new, category conversation)
- Seed (new; design-review-planted settles per-domain ordering in processor.py:3-6):
  - events/processor.py: RegistryEvent (registry, domain, kind, seq not checked) and one Postgres-table consumer, so order is incidental. The docstring says which consumers rely on order, and at what scope, "has never been written down". A transfer moves a domain to another registry.
  - events/billing.py: dedupes by event_id.
  - tests/test_processor.py and README.md.
  - docs/design/registry-events-kafka.md: topic registry-events, 12 partitions keyed by registry_id. Billing and notifications are at-least-once and dedupe by event_id (idempotency is covered). Throughput is sized. Planted: dual-write migration with no rollback or reconciliation (blocking from the start). Planted: registry-events.dlq has no owner (advisory). Silent on ordering.
- Brief: "Review the design in docs/design/registry-events-kafka.md for moving registry event processing to Kafka; read events/processor.py for what the system does today. Challenge it on correctness, failure modes, operations and migration, and say which goals it meets. Where the design leaves a requirement open that only I can decide, ask me rather than assume. Do not write code or edit the design."
- scenario.toml:
  - [fake] check = python3 -c "import json; json.load(open('review/design-review.json'))";
  - [[turn]] after = "complete", say = "Ordering is per-domain.";
  - [[turn]] after = "complete", say = "Per-registry is fine.".
- Oracle, file-level (also runs in check mode). Let X be the final concern about order, sequence or partition key.
  - report_valid;
  - three_revisions: revision == 3; len(revisions) == 3; revisions[1].said and revisions[2].said equal the turn messages;
  - asked_about_ordering_first: revisions[0].questions has an ordering question;
  - ordering_not_blocking_before_any_answer: X is not in revisions[0].blocking;
  - ordering_blocking_after_first_answer: X is in revisions[1].blocking;
  - ordering_resolved_after_second_answer: X is in revisions[2].resolved, its resolution mentions registry, and no open ordering concern or question remains;
  - revised_not_rewritten: every id in revisions[k] appears in revisions[k+1] (blocking, advisory or resolved);
  - migration_gap_blocking_every_revision: one id, blocking at r1, r2 and r3;
  - dlq_advisory_raised;
  - no_invented_blockers (idempotency or throughput blockers count as invented);
  - still_requests_changes;
  - only_changed_under("review/") against the seed.
- Oracle, run-level:
  - three_turns_in_one_run;
  - for each turn, run_checks(workflow="design", no_build=True, no_requirements=True, max_questions=0);
  - turnN_changed_only_the_review: changed_files == [review/design-review.json];
  - turns 2 and 3: model stages ⊆ {recognize_workflow, review_design, plus its report repair} and include review_design;
  - trail_matches_each_turn's_report: the kept turn-files copy of each turn's report has the same open blocking, open advisory and resolved ids as revisions[i], read from disk and not from the runner's trail.
- Reference:
  - review/design-review.json, the runner-written revision 3, copied from a fake run and stored with indent 2 and a trailing newline;
  - .fake-turns/0..2/review_design.json, the model-shaped reports:
    - r1: F1 migration blocking, F2 DLQ advisory, Q1 "which ordering do consumers need: none, per domain, per registry?";
    - r2: adds O1 blocking (a transfer splits a domain's events across partitions) and asks Q2 "is per-registry order acceptable?";
    - r3: O1 resolved ("per-registry accepted; the registry_id key keeps each registry's events in order"), no questions.
- Broken variants (each has a final report and its own .fake-turns; each fails on files):
  - answer-ignored: r2 keeps ordering advisory. Fake run: FALSE_COMPLETE.
  - still-blocking: r3 keeps O1 blocking. FALSE_COMPLETE.
  - blocking-before-asking: r1 already blocks on ordering. FALSE_COMPLETE.
  - rewritten: r3 renames everything to C1..C3. The runner refuses it, so HONEST_BLOCKER; the file-level check revised_not_rewritten fails.
  - edits-design: changes the design document. The runner refuses the stray write; only_changed_under fails.

B2 docs
- docs/cli.md row 79: after a design review the reply revises it. Same concern ids; a settled concern is kept as resolved; each revision is recorded under revisions in review/design-review.json; an edited report is refused.
- docs/task-run.md: problems.reports gains questions and revision.
- docs/workflow.md 27-29: design → review → --follow-up reply → revision n+1, review/ only.
- scenarios/README.md: .fake-turns/<turn>/<stage>.json; kept_files; a new catalog row.

B2 tests
- tests/test_design_job.py:
  - a first review uses SCHEMA and PROMPT unchanged;
  - revise mode uses REVISION_SCHEMA, and autoreview.job_request carries it;
  - packet has previous_review and user_message only when revising;
  - check refuses (one subTest each): a dropped id, a duplicate id, resolved without a resolution, resolving a new id, resolved in a first review, and a verdict that does not match open blockers;
  - same_design false gives a fresh review;
  - two follow-ups accumulate three revisions entries, revision 3, open-only counts and report_sha256.
- tests/test_follow_up.py: an edited report is refused with state unchanged; design_review_to_revise is None unless the newest turn follows a review and the kind is design.
- tests/test_progress_view.py: the additive fields are present and no field is renamed.
- scenarios/test_harness.py:
  - .fake-turns is never materialized or listed;
  - the prefix rule applies without turn_paths;
  - kept_files is recorded per turn;
  - FakeRunTests: the reference is PASS with turns 2-3 running [recognize_workflow, review_design]; answer-ignored and still-blocking are FALSE_COMPLETE; rewritten is not PASS;
  - OracleControlTests picks up the entry automatically (seed fails, reference passes, every broken variant fails).
- Regression: scenarios/run.py run --fake on every scenario, especially design-review-planted, design-review-sound, review-then-fix and architecture-two-services; run_suite --changed; --scenario-harness.

DEFERRED
- 3 live passes per scenario: separate spend approval and --i-authorize-live-model-spend; never in routine runs.
- A build follow-up that names no design still refreshes the previous job's Requirements handoff and can stop with PAUSED_REPEATED_FAILURE (seen on master), for example "Build it." straight after a discussion. File an issue; it touches the requirement-drop guard (autocode_goals.py:309-340).
- No product guard limits a propose-mode design turn's Builder to documents; only the oracle catches it.
- Turn 3's Planner still sees turn 2's requirement trace rows (a live risk).
- The Analyst's answer text is carried only as the note pointer.
- Analyst questions do not wait.
- The status view lists no questions by id.
- Quote/basis enforcement was dropped as brittle for live runs.
- The Completion Owner send-back question from review-then-fix.

LIVE RISKS
- The recognizer must route the two replies to design, and "Build it." to build with design_document. A misroute shows in the per-turn checks.
- The Architect may block on ordering in turn 1 (the blocking-before-asking failure mode) or word design_under_review without a path. The latter becomes a fresh review and fails three_revisions, loudly.