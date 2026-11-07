# Prompt prefix and repetition audit (#2136, #2113)

2026-10-30. This note measures one retained fake-provider run and finds where a per-run or per-attempt value breaks the shared leading text of repeated prompts. It also lists static text that reaches stages which cannot act on it. The run did not complete: it stopped at `PAUSED_BUILDER_CLASSIFICATION` because of two fixture gaps that PR #2515 has since fixed. So its Builder and Tester rounds 2 and 3 and its three Investigator prompts measure a defect loop, and that loop is 41.5% of the run's prompt bytes. The largest measured prefix change is moving the planning source-id list, which lengthens the shared prefix of the two discovery prompts by 28,684 bytes. Taking the stage events path out of the provider contract lengthens the Builder, Tester and Investigator-repair prefixes by 4,535 to 6,651 bytes. The original Investigator handoff is indented, and compacting it removes 5,336 bytes. Only bytes and prefix lengths are measured here, plus fake-provider controller timings. Shipped fixes #2211 and #2215 do not cover these findings.

## Evidence and method

### The run

- The run is `20261029-214316-fix-the-greeting-cli-298a66e4`, made with the fake provider at no cost by a test fixture on 2026-10-29. Its `autocode bundle` manifest is `cc523802cfffaac1641fa57d0383603685cdd2a5185b405bfe1032a65e759db2`. Each stage attempt saved its exact prompt as `iterations/NNN/<slug>-NN.prompt.md`. The bundle keeps the stage reports and state but not those prompt files, so the prompt byte figures here come from the run directory itself.
- It holds 15 prompts totalling 735,613 bytes. Requirements gather, revise, plan challenge and plan finalize ran once each. Discovery ran twice. Builder and Tester ran three rounds each. Iteration 003 holds three archived Investigator prompts: one `investigate_stuck` and two `investigate_stuck_report_repair`.
- Its workflow record says `kind=build`, `brief_size=small`, `named_by: requirements_gather`, `source: model`. The run is named for a fix to the greeting CLI, but the bug-fix workflow never ran. The full build-planning chain ran instead: gather, discovery twice, challenge, revise and finalize.
- Containment was off, so every containment effect below is derived from the code.
- Code references were checked against 7dab5115 (merge of #2521, master on 2026-10-30). Line numbers refer to that commit.

### Why the run stopped

`state.json` records status `PAUSED_BUILDER_CLASSIFICATION`. All three Tester reports say `"verdict": "PASS"`. The changed-code gate still refused completion: "Changed product lines still need behavioral evidence or an independently checked reason: packages/greet/greet.py:13". The fake Tester gave no `changed_code_exceptions` reason for that line, so the Builder was sent back twice. The Investigator then took the stage, and its fake report lacked `failure_class`. The original report and both repairs were rejected for that missing field, and the run paused.

AGENTS.md classes a fixture-driven stop at this status as a fixture or mutator gap. PR #2515, merged on 2026-10-30, fixed both gaps on master. It gave the fixture Investigators a `failure_class` and fixture Testers their changed-line reasons, and it widened the mutator's reach. A current run is therefore not expected to repeat this loop; it was not re-run for this note. Every comparison between Builder, Tester or Investigator attempts below involves at least one prompt from that loop. Those comparisons still show how a rework prompt is assembled, but this run's rework count is an artifact. The loop also took 13.7 of the run's 29.5 seconds.

| Prompts | Count | Bytes | Share of run |
| --- | ---: | ---: | ---: |
| Planning, Builder round 1, Tester round 1 | 8 | 430,155 | 58.5% |
| Builder and Tester rounds 2 and 3 (defect loop) | 4 | 182,512 | 24.8% |
| Investigator and two report repairs (defect loop) | 3 | 122,946 | 16.7% |
| All prompts | 15 | 735,613 | 100% |

### Byte convention

Every figure uses one convention.

- Bytes are UTF-8 bytes of the saved prompt file.
- Each prompt splits into four parts that sum to its total. Instructions run from byte 0 to the start of the schema line, including the provider contract and the newline ending the previous line.
- The schema is the one-line JSON schema without its newline. The separator is the 23 bytes after it: that newline, a blank line, and `CURRENT HANDOFF DATA` with its newline. The handoff is every byte after the separator.
- A fragment size is the whole file, trailing newline included. An occurrence means the whole file appears verbatim in a prompt.
- A JSON value size is compact JSON with ASCII escapes, which is how these handoffs are written, unless a row says otherwise.
- A shared prefix is the count of identical leading bytes. A simulated prefix edits a copy of the prompt and counts again. A removed path becomes the one-byte placeholder `E`.

### What is not established

No token counts, provider cache reuse or model latency were measured. The earlier note `docs/bugs/2026-10-27-context-growth-attribution.md` bounds the relevance of this one. On its live small-fix runs, stages read 287,097 and 330,021 bytes of tool output. Within one Builder, later requests read 76% to 99% of their input from cache. It also showed that 14,336 cached Builder tokens were not AutoCode's prompt text. This note measures only the first request's prompt bytes. By that evidence they are a minority of a stage's context on small fixes. This audit carries out that note's candidate 4, measuring prefix stability, and is not a new direction.

## Prompt sizes

| Stage and file | Total | Instructions | Schema | Handoff |
| --- | ---: | ---: | ---: | ---: |
| Requirements gather, 001 | 26,143 | 20,493 | 4,345 | 1,282 |
| Discovery, 001 round 1 | 58,660 | 47,292 | 8,472 | 2,873 |
| Discovery, 001 round 2 | 65,060 | 49,526 | 9,683 | 5,828 |
| Requirements revise, 001 | 72,319 | 52,130 | 12,662 | 7,504 |
| Plan challenge, 001 | 56,286 | 30,330 | 1,901 | 24,032 |
| Plan finalize, 001 | 63,303 | 32,041 | 6,707 | 24,532 |
| Builder, 001 | 33,838 | 17,186 | 1,450 | 15,179 |
| Builder, 002 (loop) | 35,885 | 17,609 | 1,450 | 16,803 |
| Builder, 003 (loop) | 36,157 | 17,609 | 1,450 | 17,075 |
| Tester, 001 | 54,546 | 28,805 | 3,965 | 21,753 |
| Tester, 002 (loop) | 55,092 | 28,805 | 3,965 | 22,299 |
| Tester, 003 (loop) | 55,378 | 28,805 | 3,965 | 22,585 |
| Investigator, 003 (loop) | 34,567 | 9,506 | 965 | 24,073 |
| Investigator repair 01, 003 (loop) | 41,964 | 11,038 | 965 | 29,938 |
| Investigator repair 02, 003 (loop) | 46,415 | 11,038 | 965 | 34,389 |

The Investigator family is 122,946 bytes. One repair prompt outweighs any single Tester prompt.

## Shared prefixes

| Pair | Measured | After a simulated change |
| --- | ---: | --- |
| Discovery round 1 and round 2 | 16,902 | A1: 45,586. A1 with C4: 48,116 |
| Builder 001 and 002 | 11,260 | A5: 11,591. A2 alone: unchanged |
| Builder 002 and 003 | 13,218 | A2: 19,869 |
| Tester, every pair | 24,412 | A2: 28,947. A2 with A3: 32,024 |
| Investigator repair 01 and 02 | 6,658 | A2: 12,128 |
| Investigator and either repair | 0 | Different opening text |
| Different stages | 12 to 37 | Investigator against any other stage: 0 |

The two discovery prompts are not two attempts. They are two clarification rounds with a human answer between them: the controller exited at 21:43:19.74 and a new invocation ran the second discovery. The source list grows by appending the answer, so reuse of their prefix would span a human pause.

Builder rounds agree on 17,792 bytes of instructions and schema, which is everything except the test-style excerpt and the events path. The Tester's 23,883 bytes before its provider contract are identical in all three rounds.

## Ranked candidates

- **Tier A** makes a measured prefix stable. Both checks found it safe under conditions.
- **Tier B** removes text that cannot apply, gated on a deterministic condition.
- **Tier C** relocates or reformats text. Most of it adds shared prefix only after Tier A lands.
- **Tier D** is contested. One check found the change, as proposed, unsafe.

Tier A lists the measured gains first, then A4, whose figures are derived, then the small A5. The other tiers put the largest bytes per prompt first, except that Tier C opens with the changes that pay off on their own. Every change to a `tools/prompts/**/*.md` resource triggers the prompt-evidence check, `tools/check_prompt_evidence.py`. Its PR body then needs a "Live run evidence" block with live-run fields or an Exempt reason. Every change to rendered bytes needs a reviewed update of `tests/prompt_rendering_fixtures.json`, which `test_every_fixed_stage_rendering_matches_its_reviewed_bytes` checks.

### Tier A: makes a measured prefix stable

**A1. Move the per-run source-id list out of the middle of the planning instructions.**
- **Where.** `tools/autocode_source_trace.py:542-553` appends one `  [id] kind=...` line per source. It is called at `tools/units/autoplanner.py:1516-1518` and `tools/autocode_stage_context.py:293`. The list starts at byte 16,881 in both discovery prompts, 16,569 in revise, 11,593 in challenge and 11,055 in finalize.
- **Bytes.** The list is 21 or 52 bytes. Removing it moves the discovery divergence from 16,902 to 45,586 bytes, a gain of 28,684. The handoff `source_inventory` already carries the same sources, built from the same `quotable(...)` call at `autoplanner.py:224-229` and `:1341-1347`.
- **Change.** Prefer the conservative variant. In Python, move the whole `source_trace.rule(records)` block with its header line to the end of the static planning policy, before the transport block. The second option drops the id lines and points at `source_inventory`. It rewords `source-citation-rule.md:4-5` and `:41`, which triggers the prompt-evidence check.
- **Conditions.** For finalize, move the `brief_literals.rule` list at `autoplanner.py:1514-1515` too. Conditional rules after the list can still differ between rounds, so 28,684 is what this run measured, not a guaranteed region. Keep the `if records` guard.
- **Negative controls.** Run `tests.test_source_trace`, `tests.test_brief_obligations` and `tests.test_prompt_rendering`. Rewrite the literal assertions at `tests/test_source_trace.py:949`, `tests/test_prompt_rendering.py:393` and `tests/test_brief_obligations.py:717`. Unknown source ids, uncovered sentences and corrupted quotes are still refused. A control-reply source stays out of both list and inventory, as #1630 requires.

**A2. Take the stage events-log path out of the provider output contract.**
- **Where.** `tools/providers/opencode.py:618-626` builds the contract from the 421-byte `prompt-for-schema-02.md`, the events path, the 4,373-byte `prompt-for-schema.md` and the schema. `tools/providers/claude.py:441-452` does the same in the other order. The path is at byte 12,686 in Builder 001, 13,109 in Builder 002 and 003, 24,303 in every Tester, and 6,512 in both Investigator repairs.
- **Bytes.** The path is 127 bytes for the Builder, 129 for the Tester and 153 for an Investigator repair. After it come 4,373 bytes of contract text and the schema line. With a placeholder, the Builder 002/003 prefix grows by 6,651 to 19,869. The Investigator repairs grow by 5,470 to 12,128, then diverge at `archived_paths`. The Tester grows by 4,535 to 28,947, then stops at the gap_id enum that A3 addresses. Builder 001/002 is unchanged until A5 lands.
- **Change.** Make the sentence static and have it name a handoff key such as `stage_events_log`, keeping the never-write warning. Add the exact path to the handoff the way `autocode_tool_handoff.with_capture_command` adds `capture_command`. Keep compact or indented form and never overwrite a key. Change the Claude adapter in the same PR.
- **Conditions.** The schema stays last before `CURRENT HANDOFF DATA`, because `tools/fake_opencode.py:106-115` refuses trailing text. Leave `review-output.md` alone, since `tests/test_schema_prompt_formatting.py:25,32` pins it byte for byte. Under default containment this gains nothing until A4 lands. If a recorded answer could cite the new key, declare it in `autocode_replay_bindings`.
- **Negative controls.** Update `tests/test_claude_provider.py:441` and the OpenCode prompt tests deliberately. Keep `tests.test_capture_event_contract`, `tests.test_check_command_from_receipt`, `tests.test_report_repair`, `tests.test_provider_launch_containment` and `tests.test_saved_run_replay_cli` passing. Invented event IDs and writes to the events log are still refused. Add a test that two attempts render an identical contract prefix.

**A3. Put attempt-specific schema enums last in the prompt copy of the schema.**
- **Where.** `tools/autocode_changed_code_review.py:76,79` sets `maxItems` and the gap_id enum. The saved schema is written with `sort_keys` at `autocode_util.py:244/257`, so `changed_code_exceptions` comes first. It is rendered at `tools/providers/opencode.py:623` and `claude.py:449`.
- **Bytes.** The first 270 bytes of the 3,965-byte Tester schema line are shared, and the gap_id value starts there. After A2, moving `changed_code_exceptions` last grows the Tester prefix from 28,947 to 32,024, a gain of 3,077. A3 alone gains nothing, because the events path comes first. Planning `retain_from` enums sit 848 to 1,883 bytes into their schemas, but each planning stage ran once, so no planning gain was measured.
- **Change.** In `prompt_for_schema` only, render attempt-specific properties after the static ones. Never reorder `review_generation_schema` or the saved `*.schema.json`, because those bytes are hashed at `autocode_job_report_recovery.py:132` and `autocode_native_image.py:216`.
- **Negative controls.** Add a test that `json.loads` of the prompt schema equals the saved schema, with attempt-specific properties last. Update the exact-suffix assertions in `tests/test_claude_provider.py:444`, `tests/test_schema_prompt_formatting.py:112,117,130,156` and `tests/test_report_repair.py:1186` only where enums appear. `changed_code_review.bind` still refuses an unoffered gap, a stale gap and a `maxItems` mismatch. `tests.test_saved_run_replay_cli` gap rebinding still passes.

**A4. Write a stable placeholder in contained launches, not the per-launch scratch path.**
- **Where.** `tools/autocode_provider_launch.py:477-483` replaces both capture examples with the scratch path `<workspace>/.autocode/tool-containment-<uuid4hex>/scratch`. That directory is created per launch at `tools/autocode_tool_containment.py:1013-1015`. The first example is at byte 16,019 in the Tester and 9,300 in the Builder.
- **Bytes.** These figures are derived, because the run was uncontained. A contained prefix would stop at the uuid, 62 bytes into the path for this run's workspace. The Tester would lose 8,322 of today's 24,412 shared bytes. Builder 002/003 would lose 3,847 of 13,218. A2 and A3 gain nothing under containment until this lands.
- **Change.** When contained, replace both examples with one path-free token that names the handoff field, such as `--output <tool_containment.scratch>/evidence-<unique-name>.json`. The exact path stays in `tool_containment.scratch`, which `containment_prompt` already writes.
- **Conditions.** Today's text is the deliberate fix for the 2026-10-06 live self-build incident, and `tests/test_provider_launch_containment.py:272-280` and `:282-302` pin it. Keep one location in every example, and keep the sentence that scratch replaces any other evidence directory. It changes model input on a path with a real incident, so it needs one bounded live run in which a contained Tester resolves the token.
- **Negative controls.** Rewrite the two containment tests to assert the token, no uuid before the marker, and the scratch value in the handoff. Receipts outside scratch, and citations of another attempt's scratch, are still refused.

**A5. Move the Builder's test-style excerpt to the end of the Builder instructions.**
- **Where.** `tools/autocode_test_examples.py:73-97` reads the excerpt from the working tree and inserts it before the marker. The callers are `tools/units/common.py:86` and `tools/autocode_builder_worker.py:155`. The section header is at byte 10,652.
- **Bytes.** The excerpt is 717 bytes in round 1 and 1,140 bytes in rounds 2 and 3, because round 1 added a test. In this run only the 331-byte `BUILD_OUTPUT_NOTE` follows it. Moving it after that note grows the round 1/2 prefix from 11,260 to 11,591, a gain of 331. The contract and schema still follow the changed excerpt, so they stay out of reach between rounds 1 and 2. The gain grows when the test-case, acceptance-test or bug-job Builder notes are present, which this run did not measure.
- **Change.** Append the section after those notes and `BUILD_OUTPUT_NOTE` in both callers, before the provider contract.
- **Conditions.** Never put it after the schema, because the fake and fixture providers refuse that. Do not move it into the handoff, since a test asserts it is in the instruction part.
- **Negative controls.** `tests.test_test_examples`, `tests.test_native_test_names` and the `autocode_builder_worker` tests.

### Tier B: removes text that cannot apply

| Id | Text removed | Bytes per prompt | Prompts in this run |
| --- | --- | ---: | --- |
| B1 | Progressive policy for the Plan Reviewer challenge | 4,041 | challenge |
| B2 | Milestone advancement rules in the Builder | 2,524 | Builder ×3 |
| B3 | spec_first, follow-up and previous-handoff gather text | 2,770 | gather |
| B4 | Validator-check paragraph of the OpenCode contract | 1,123 | Builder ×3, Investigator ×3 |
| B5 | Obligation policy with no obligations | 1,103 | discovery ×2, revise |
| B6 | Other stages' draft-failure paragraphs in revise | 1,198 | revise |
| B7 | Progressive check-retirement paragraph | 846 | discovery ×2, revise, challenge, finalize |
| B8 | Empty-inventory descriptors | 469 | Builder ×3, Tester ×3 |
| B9 | Wall-clock timing maps in the Tester checkpoint | 322 to 376 | Tester ×3 |
| B10 | Duplicated budget sentence and proof prose | 328 | planning ×6 |

**B1. Gate the Plan Reviewer progressive policy on a proposal or a progressive record.** `tools/units/autoplanner.py:1163-1171` and `:1458-1471` append `progressive-policy.md` for every planning stage. The proposer stages get it after the 770-byte proposal fragment, and challenge gets it after the 586-byte review fragment. The policy appears whole in 5 prompts, 20,205 bytes in this run. For challenge, include it only when `autocode_progressive_plan.declares` holds for the draft or `autocode_progressive_state.view(state)` is non-empty. Keep the sentence saying progressive planning never suits small or coupled tasks. For the four proposer stages, 3,195 bytes per prompt remain after B7, and this audit neither attributes them nor rules them out. Controls: `tests/test_report_repair.py:732-785` and the `tests.test_progressive_*` modules.

**B2. Gate milestone advancement rules on the response schema in the Builder.** The rules come from `fragments/goals/execution-prompt.md:15` and `fragments/milestones/policy.md`, added at `tools/autocode_stage_context.py:270` and `:302`. The Builder schema has no `next_task`, `decision` or advancement field. Emit the decision-only sentences only when the schema declares one, following `execution_prompt.criteria_instruction`. Keep the budget and "edits are not progress" lines. Controls: the `tests.test_milestone_*` modules and `tests.test_planning`. The completion control cannot be this run, because it never completed. Use a fake scenario whose oracle requires completion, such as `bugfix-trivial`, and record its result on the candidate revision.

**B3. Gate spec_first, follow-up and previous-handoff text in requirements gather.** `tools/units/autoplanner.py:1100` sends `tools/prompts/requirements-gather.md` whole. The code already decides each case at `:1289`, `:1410`, `:1412-1414` and `:1586`. Split the file and gate each part, placing per-turn parts last. The spec_first setting is fixed for the run at `autocode_configure.py:373`. Add spec_first, follow-up and previous-handoff gather renders, since none exist today.

**B4. Gate the validator-check paragraph on a top-level `checks` property.** It is line 4 of `fragments/provider-opencode/prompt-for-schema.md`, emitted at `tools/providers/opencode.py:618-626`. The Builder and Investigator schemas have no `checks` property, so 6 prompts carry 6,738 bytes they cannot use. It sits after the events path, so this is a reduction only. The Tester prompt must stay identical. Map the new fragment in `tools/ci_test_selection.py`.

**B5. Replace the obligation policy with one line when `deferred_obligations` is empty.** `tools/units/autoplanner.py:1480` emits it unconditionally. Branch at the call site on the whole list, keep `obligation_policy(stage)` unchanged for `tests/test_planning.py:218-224`, and write the line inline. It must still say to return `remediation_records=[]`. The net saving is about 950 to 1,000 bytes per prompt.

**B6. Keep only the glm_revise paragraph of draft-failure-correction in revise.** `tools/units/autoplanner.py:1454-1455` appends the whole fragment for challenge, revise and finalize. Dropping the finalize paragraph is safe now. The challenge paragraph defines the receipt that revise calls "that exact pair", so rewrite the revise paragraph to stand alone first.

**B7. Gate the progressive check-retirement paragraph on an existing delegation.** It is the first 846 bytes of `progressive-policy.md`, 4,230 bytes in this run. Without a delegation, `autocode_progressive_state.py:483-484` refuses retirement lines anyway. Emit it only when `progressive.view(state).get('delegation')` is set, and pass that flag into `repair_rules`. Combine with B1 for challenge.

**B8. Drop empty-inventory descriptors from the stage packet.** `tools/autocode_verification_plan.py:371-382` always builds `original_brief` and `lifecycle_risks`, added at `tools/autocode_stage_context.py:234`. In the stage packet only, drop the descriptive strings when the inventory is empty and `protected_manifest` is None. Keep both keys. Leave `obligations()` unchanged, because the approval screen and repair handoff render it.

**B9. Drop the timing maps from the Tester's `milestone_checkpoint`.** `tools/autocode_stage_context.py:299` sends the full `checkpoints.summary(state)`. For `sol` only, drop `seconds_by_role` and `hours_by_role` from a copy. Keep `current`, `limits`, `current_evidence_ready` and `blocker`, because the policy names them. The `--status` view stays unchanged.

**B10. Remove the duplicated budget sentence and proof prose from the planning handoff.** The sentence is at `tools/units/autoplanner.py:1311` and the prose is `regression_reference.preparation` at `tools/autocode_regression_reference.py:22`. Drop `preparation` for non-gather stages, because `proof-reference-rule.md:7-8` says the same. Keep the budget number in the handoff, with an explicit "Unlimited" value, never a bare 0.

### Tier C: relocation and formatting

**C1. Compact the Investigator handoff.** `tools/autocode_stuck_job.py:435` writes the packet with `indent=2`. In the original Investigator prompt the handoff is 24,073 bytes, and compact it would be 18,737. The 5,336-byte difference is formatting only. Both repair handoffs are already compact. Containment keeps an indented handoff indented, at `tools/autocode_provider_launch.py:495`, so the gain holds either way. Add a round-trip test and run `tests.test_stuck_job` and `tests.test_builder_failure`.

**C2. Stop the Investigator repairs re-sending the original handoff.** The repair key `investigation_context` is 23,388 bytes in repair 01 and 23,880 in repair 02. It is built at `tools/autocode.py:1272` by `stuck_repair_context.context`.

| Original Investigator key | Bytes | In both repair contexts |
| --- | ---: | --- |
| `builder_failure` | 11,380 | identical |
| `goal_contract` | 2,928 | identical |
| `current_task` | 608 | identical |
| `saved_answers` | 488 | identical |
| Seven smaller keys | 374 | identical |
| `recent_stages`, `workspace_inventory`, `execution_engine` | 2,623 | differ |

Eleven keys, 15,778 bytes, are byte-identical in both repairs. `builder_failure.record` alone is 8,519 bytes. A repair only fixes the report's structure, so most of this context is repeated input. `archived_paths`, built at `tools/autocode.py:1258-1260` and `tools/autocode_run_records.py:546-586`, grows from 3,646 to 6,997 bytes. It maps absolute paths to absolute paths, and the 96-byte run-directory prefix appears 22 and 40 times, 2,112 and 3,840 bytes. Reusing the original context safely needs a binding to the original handoff, such as its hash or attempt identity. No binding is proposed here, so this is a finding, not a ready change. A relative form of `archived_paths` is the bounded part.

**C3. Order the Builder and Tester handoff so keys stable for the run come first.** `tools/autocode_stage_context.py:65-98` sets the order. After the first differing key, 12,814 and 13,178 bytes of unchanged keys follow. This adds nothing alone, because instruction text diverges first. Keep `task` first for `tests/test_coordination_context.py:133,136`, and land it after A2 and A5.

**C4. Place the conditional RETAIN VALIDATED TEXT block after the provider contract.** The block is 2,203 bytes in discovery round 2, from `tools/units/autoplanner.py:1546-1556`. After A1 it moves the discovery prefix from 45,586 to 48,116, a gain of 2,530. Alone it adds nothing. Every provider must accept a stage trailer after the schema-last contract, and report repair must keep the rule. `tests/test_report_repair_context.py:441-449` rejects the "always include" variant.

**C5. Emit fixed policy strings from the Builder and Tester handoff in the instructions instead.** The strings come from `tools/autocode_stage_context.py:79`, `:100-113`, `:134-139`, `:199` and `:234`. The Builder has 4,703 movable bytes and the Tester 1,985 per round. These bytes move; they are not removed. Strip them only in the stage packet, keep the data keys, and rewrite `tests/test_capture_event_contract.py:59-62` and `tests/test_package_writes.py:168-176`.

**C6. Compact the uncontained Builder and Tester handoff.** `tools/autocode_stage_context.py:351` uses spaced separators. The gain is 541, 596 and 603 bytes for the Builder and 794, 804 and 811 for the Tester. Under containment they are already compact, through #2211. Rewrite `tests/test_goals.py:518` to parse the JSON.

**C7. Trim the challenge `user_events` the way finalize already does.** `user_events[0]` repeats `saved_answers.Q1` in full, 481 bytes, in challenge, discovery round 2 and revise. Trimming at `tools/autocode_plan_retention.py:385-392` saves about 345 bytes per answer. Keep `contract_token`, `id`, `answer_id` and `text`.

**C8. Use one placeholder for the capture output directory.** `fragments/support/common.md:5-8` names `<run-directory>/evidence/` and the OpenCode contract names `.autocode/evidence/`. This saves about nothing but removes two names for one receipt. Keep the literal strings that `containment_prompt` replaces, or update them with A4.

### Tier D: contested

**D1. Move `contract_author_rules` to the front of the Plan Reviewer handoff.** The key is identical in challenge and finalize. Its value is 17,575 bytes as written, 17,599 with its key. It is the 21st of 24 keys in challenge and the 20th of 26 in finalize, so moving it first puts it ahead of 20 and 19 keys. Its largest parts are `example_criteria` at 7,535 bytes, `contract_references` 5,157, `revision_conflicts` 2,108 and `milestone_handoff` 1,646. Moving it into the instructions is rejected, as below. A key-order change needs no fragment edit, and it only helps once the instructions before it are shared.

**D2. Put the shared Plan Reviewer block before the stage opener.** Challenge and finalize share a contiguous 9,622-byte block but only a 37-byte prefix. One check rejected it: it puts the role statement behind more than 9 KB of rules, and several of those rules depend on run state. If done, hoist only unconditional constants and compare concern coverage in one bounded live run.

**D3. Use a Tester-specific milestone fragment.** 4,236 of 5,423 bytes are addressed to the Plan Reviewer or the Builder. One check rejected it, because `tests/test_milestone_policy.py:34-44` pins sentences that shape the Tester's blocking judgment. The text is static, so the prefix gains nothing.

**D4. Keep only the fail-closed baseline sentences unconditional.** `BASELINE_POLICY` is 509 bytes in every Builder and Tester prompt. No state records an authorized baseline exception, and `detect_framework` misses Vitest in monorepos. At most, gate the Vitest line on a deterministic Vitest signal, about 250 to 350 bytes.

**D5. Separate the planning wording in the Tester's COVERAGE block.** This is 154 bytes. `tests/test_planning.py:699-700` pins the shared policy on purpose, so reword rather than delete.

**D6. Remove the duplicated capture receipt sentences.** This is 113 bytes per Tester prompt. The fragment is shared, and Builder and Codex Testers rely on different copies. Not recommended.

**D7. Put a shared transport preamble at byte 0.** The same fragments sit at stage-specific offsets.

| Fragment | Bytes | Prompts | Total |
| --- | ---: | ---: | ---: |
| `prompt-for-schema-02.md` and `prompt-for-schema.md` | 4,794 | 9 | 43,146 |
| `support/common.md` | 1,232 | 12 | 14,784 |
| `support/milestone-policy.md` | 1,624 | 9 | 14,616 |
| `autoplanner/source-citation-rule.md` | 2,651 | 5 | 13,255 |
| `provider-opencode/planning-output.md` | 1,597 | 6 | 9,582 |
| `baseline/baseline-policy.md` | 509 | 6 | 3,054 |
| All, over 15 prompts | | | 98,437 |

The Investigator prompts carry only the OpenCode contract pair from this set. `tests/test_prompt_rendering.py:512` pins `startswith(instruction)`, and contracts differ by provider and schema branch. Treat this as a measurement only.

### Static sections with no proposed change

These whole fragments are static and now attributed. No change is proposed for them.

| Fragment | Bytes | Prompts | Total |
| --- | ---: | --- | ---: |
| `autoplanner/progressive-policy.md` | 4,041 | discovery ×2, revise, challenge, finalize | 20,205 |
| `autoplanner/example-criteria-rule.md` | 5,291 | discovery ×2, revise | 15,873 |
| `goals/contract-references.md` | 5,090 | discovery ×2, revise | 15,270 |
| `check-replay/validator-note.md` | 4,139 | Tester ×3 | 12,417 |
| `autoplanner/evidence-facts.md` | 2,107 | discovery ×2, revise, challenge, finalize | 10,535 |
| `autoplanner/question-policy.md` | 1,776 | gather, discovery ×2, revise, finalize | 8,880 |

`goals/contract-references.md` also reaches challenge and finalize again inside `contract_author_rules.contract_references`. `support/milestone-policy.md` is counted in D7.

## Fake-provider controller timings for #2113

These are controller overhead under the fake provider only. They say nothing about model time. Each stage time runs from its `stage_started` event to its `stage_finished` event in `activity.jsonl`.

| Measure | Seconds |
| --- | --- |
| Whole run, first invocation to last controller exit | 29.5 |
| Defect loop, second Builder start to controller exit | 13.7 |
| Model preflight, first check start to last check end, 3 invocations of 4 checks | 0.97, 0.77, 0.99 |
| Planning stages | 0.33 to 0.43 each |
| Builder stages | 0.59, 0.47, 0.58 |
| Regression proof, Builder finish to proof finish | 1.98, 1.68, 1.96 |
| Tester stages | 1.97, 2.81, 2.54 |
| Tester time after the fake provider exited | 1.59, 2.29, 2.14 |
| Investigator and two repairs | 0.47, 0.52, 0.60 |

Most of each Tester stage is controller work after the provider process ends. #2113 reports a tiny follow-up spending minutes in the Tester, so that post-provider step is worth attributing on a live run.

## Rejected

- Moving `contract_author_rules` into the instructions, even labelled as data. `tests/stage_repair_provider.py:395-403` runs author commands it finds before the marker, which revives #1808. A hash-only reference is also rejected, because the reviewer must audit the exact text.
- Removing repeated hashes or a duplicated `contract_token` from the Builder and Tester handoffs. Each copy identifies a separately checked record.
- Reordering the saved or generation schema, which is written with `sort_keys`, read back and hashed.
- Leaving the generic capture examples in contained prompts, which reverses the 2026-10-06 fix.
- Reading the Builder excerpt from the base revision, or placing it after the schema or in the handoff.
- Moving `regression_proof.meaning` or `review_source.instruction` out of the handoff. The first is compared with `sort_keys`, and the second guards the source content.
- Gating the baseline policy on a "recorded baseline exception". No such state exists.

## Not covered

- **Workflows and stages.** The bug-fix workflow and its `investigate_bug` stage never ran, although the brief was a fix. The run took the full build-planning chain for a small fix. Under a fake this shows nothing about real recognizer behaviour, but it is untested for #2113. There are no acceptance-test writer, Completion Reviewer, `astra_checkpoint`, Resolver, Designer or Analyst prompts.
- **Repeated planning.** Gather, revise, challenge and finalize ran once each, so no between-attempt planning prefix is measured.
- **A completed run.** The run stopped in a fixture-caused loop, so its rework counts are not representative.
- **Containment.** All containment effects are derived from the code.
- **Tokens, caching and model latency.** None were measured. Backend system prompts are not in the saved prompts.
- **Model behaviour.** Fakes cannot show that output is unchanged after reordering or removing text. A4, D2 and D3 stay unverified until a bounded live run.

## Suggested order

1. **A1, conservative variant.** A Python-only move with the largest measured gain, 28,684 bytes. It needs the reviewed fixture update for the planning renders, but no prompt-evidence block.
2. **A3 and A5.** Python-only reorders that are small and independent. Each needs the reviewed fixture update. A3 pays off only after A2, so it can land first or with A2.
3. **C1, the Investigator handoff.** Not Tier A, but a Python-only 5,336-byte reduction that holds with or without containment. It needs the `stuck/*` fixture update.
4. **A2.** It rewords `prompt-for-schema-02.md` and the Claude fragment, so it needs the prompt-evidence check and the reviewed fixture update. Land it with its Claude change in one PR.
5. **A4.** A Python change on the containment path, with the reviewed fixture update for `containment/*`. It needs one bounded live contained Tester run, because the path had a real incident.
