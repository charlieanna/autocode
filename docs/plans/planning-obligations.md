# Planning obligations: let the runner do the bookkeeping

The #1630 fixes (#1635, #1638, #1762, #1780) each repaired one failure a small task hit on
its way to a plan. None of them changed why the Planner is slow or why a plan loses a
whole review round to a gap the runner could have named. This plan does. It is written
from the measurements in `docs/bugs/2026-10-19-small-task-requirements-input.md` and the
three live runs of 2026-10-21 recorded there.

## What the measurements say

One discovery prompt for the queue bug fix (98,884 bytes), replayed through Kilo with
read-only tools:

| Route | Seconds | Reasoning tokens | Output tokens |
| --- | ---: | ---: | ---: |
| MiMo Flash, medium | 827 | 34,701 | 6,134 |
| GLM-5.3, low | 98 | 940 | 4,831 |

MiMo generates 30–50 tokens a second, so the stage takes as long as the model reasons. In
the MiMo replay 28–41% of the reasoning text was source-trace bookkeeping (which brief
sentence each criterion quotes, whether the `plan` reply is a sentence) and about a third
was example arithmetic and the `test:`/`guard:` decision for each diagnosis case and
requirement row. The model drafted the whole plan inside its reasoning and then wrote it
out again.

The live run with #1780 (GLM-5.3 low planner, GPT-5.6 Sol reviewer, run
`20261021-024734-…-fab18778`) reached approval in 773 s of model time. 521 s of that
was one review round spent on four blocking concerns: a wrong test selector prefix, a
criterion proving more than its named test, an uncited brief sentence, and a wrong
decision basis. The runner can detect the third mechanically today and does so only at
the Plan Reviewer's seal. The revision then lost two report repairs to a rule conflict:
the reviewer asked for a criterion change and `autocode_contract_revision` refused it as
"without a user-backed contract change".

## The four lists the Planner reconciles by hand

The discovery handoff carries four overlapping obligation lists, and the prompt tells the
model to satisfy each:

1. `source_inventory`: every human source's full text. SOURCE TRACE requires a verbatim
   quote of every sentence (`autocode_source_trace._coverage_errors`, checked at the
   seal via `check_ready`).
2. `requirement_trace_rows`: the Requirements handoff's R1…Rn, each with a
   `source_quote`. REQUIREMENT TRACE requires one trace row each, with a criterion ID as
   evidence (`autocode_goals.check_requirement_trace`, checked when the draft arrives in
   `autopilot._bind_plan`).
3. `bug_diagnosis.test_cases`: the Investigator's T1…Tn. The diagnosis note requires one
   criterion and one named test per case.
4. `requirements_handoff.report.required_behaviors` and `acceptance_tests`: carried into
   the contract and covered by criteria.

The model works out which sentence each R row came from, which T case proves which
sentence, and which handoff behavior still needs a criterion, then writes citations by
copying text. The runner already holds every fact needed for that mapping:
`autocode_requirement_cues.sentences` splits sources, `_covered` matches a quote to a
sentence, and each R row carries its quote.

## Step 1: name uncited sentences when the draft arrives

*Status: implemented in #1787.*

**Today.** `_bind_plan` validates citations (`check_citations`: each quote is verbatim)
and requirement rows, but sentence coverage runs only inside `check_ready`, which runs
only on a sealed body. A draft that leaves a sentence uncited reaches the Plan Reviewer,
who spends a full call finding it; under adaptive planning the seal then fails and #1762
sends the draft back to revise.

**Change.**

1. `units/autoplanner.uncited_sentences(state, contract)`: the sentences of
   `source_trace.quotable(brief_obligations.sources(state))` that no criterion cites
   (`_coverage_errors` with an empty `out_of_scope`), as
   `[{"source_id", "sentence"}]`. Pure function over state.
2. After `_bind_plan` accepts a `glm_draft` or `glm_revise`, save that list in
   `planning.reports[stage].uncited_sentences` and put it in the next stage's handoff as
   `uncited_source_sentences`. The challenge prompt gains one sentence: each listed
   sentence is either a blocking concern naming it or an `out_of_scope` entry with a
   reason. The revise prompt gains one sentence: cite each listed sentence or say in
   `alternatives` why it is out of scope. The status view shows the count
   (`view.planning.uncited_sentences`, added, never removed).
3. A revision (`glm_revise`) that still leaves a sentence uncited which the previous
   review did not place in `out_of_scope` is refused with the sentences listed, before
   any reviewer call. A first draft is not refused: the Planner may be right that a
   sentence is out of scope, and only the reviewer can say so (#595).

**Not changed.** The reviewer's authority over `out_of_scope` and `negation_waivers`; the
seal; the citation format.

**Tests.** `tests/test_autoplanner_uncited.py` (pure): the list for a draft citing all,
some and none of the sentences; the `plan` reply is never listed (#1780); a feedback
turn scopes the list like `_turn_scoped`. In `tests/test_brief_obligations.py`: a
revision that re-leaves a sentence uncited is refused with that sentence in the error;
a revision whose previous review placed the sentence out of scope is accepted.
`tests/test_run_view.py`: the new field.

**Expected effect.** In the fab18778 run, concern C3 (116 s of the review, part of the
97 s revision) becomes a one-line handoff entry. In the first live run of #1630 the
adaptive seal failure becomes a targeted revise instead of a refused approval.

## Step 2: control replies are never sources

**Today.** `autocode_source_log.save_answer` logs every `user_cli` answer with text as a
`user_answer` source. #1780 dropped one of them by id prefix and text in
`source_trace.quotable`. The rewording question (#1691, ids `rewording-…`) has the same
shape: `Accept the new wording` and `Keep the approved wording` are button labels, and each
was a sentence the plan had to quote.

**Change.** `tools/autocode_control_replies.py` holds one registry, keyed by the runner's own
question-id prefix, of the exact replies that only steer that question. `quotable` drops a
record that matches it. The answer stays in the log and the ledger, so decision provenance
can still cite it with `basis=user_answer`, and contracts that already quote it keep
verifying. A correction typed to the same question, and any answer to a model-written
question, stay full sources.

**Why not an `origin` marker on the question.** An earlier draft of this plan marked runner
questions with a field and logged their answers as a new `user_control` kind. A field on
`goals.QUESTION` is reachable by a model, which could then mark its own question's answers
as non-sources and remove them from source tracing. A runner-owned registry has no such
path and changes no log format, so saved runs and inventory hashes are untouched.
Implemented in #1789.

## Step 3: pre-split sentences and pre-mapped rows in the handoff

This is the latency change. It replaces copy-and-match bookkeeping with ids.

**Change.**

1. The handoff gains `source_sentences`: for each quotable source, its sentences in
   order, each `{"id": "task:0#3", "source_id": "task:0", "text": "...",
   "requirement_ids": ["R2", "R3"]}`. `requirement_ids` are the R rows whose
   `source_quote` covers that sentence (`_covered` with spans). The full
   `source_inventory` stays, so nothing a model may quote disappears.
2. A criterion citation may be `{"source_id": "task:0", "sentence": "task:0#3"}` as
   well as the existing `{"source_id", "quote"}`. `check_citations` resolves a sentence
   id to its verbatim text from the immutable source log and then runs the same checks;
   `_coverage_errors`, the literal and number survival checks and the seal see quotes
   only. The sealed body stores what the model sent; resolution is deterministic because
   the log never rewrites a source (`autocode_source_log`).
3. SOURCE TRACE tells the model to cite sentence ids and reserves verbatim quotes for a
   span inside a sentence. REQUIREMENT TRACE tells the model each row's sentences are
   already listed. The diagnosis note is unchanged.
4. The fake providers (`fake_codex`, `fake_command_tool`, `blackbox_build_provider`,
   `live_fixture_provider`, `fake_parallel_builder`) keep producing quotes; no fixture
   changes. The golden renders in `tests/prompt_rendering_fixtures.json` change for the
   planning cases only and are refreshed after a diff review, as #1635 did.

**Measurement, before merging.** Replay the retained abfc35 discovery-01 prompt, source
restored to its `before.json` hashes, three times per route on the rendered prompt
before and after: MiMo Flash medium and GLM-5.3 low, 900 s cap, Kilo read-only agent.
Record seconds, reasoning tokens, schema validity and coverage errors. Then one live
spec-first run of the queue brief on GLM-5.3 low with GPT-5.6 Sol reviewing. The change
ships if the MiMo median reasoning tokens fall by at least a third with no new coverage
error, and the live run reaches approval with no coverage concern. If MiMo does not
improve, the step is kept only if GLM's reasoning does not grow; otherwise it is reverted
and the measurements go in the bug note.

**Tests.** `tests/test_source_trace.py`: a sentence-id citation resolves and is checked
like its quote; an unknown id, a wrong `source_id`, and an id into a `user_control`
record are refused; a mixed draft (ids and quotes) passes coverage. `tests/test_prompt_rendering.py`:
`source_sentences` is present for citation stages, absent elsewhere, and its
`requirement_ids` match the rows' quotes.

## Order and size

| Step | Files | Size | Depends on |
| --- | --- | --- | --- |
| 1 | `units/autoplanner`, `autopilot._bind_plan`, two prompt fragments, `autocode_run_view` | small | nothing |
| 2 | `autocode_control_replies` (new), `autocode_source_trace`, `autocode_requirements_review` | small | nothing; replaces #1780's prefix test |
| 3 | `autocode_source_trace`, `units/autoplanner`, `autocode_stage_context`, two prompt fragments, golden renders | medium, with a measurement gate | 1 and 2 |

Steps 1 and 2 are independent and each is one pull request. Step 3 is one pull request
opened as a draft until its measurement is in the body.

## Found on the way, fixed separately

- **Review-requested changes refused.** `autocode_contract_revision.consume` refuses a
  revision that changes a criterion unless a user answer backs it, even when the
  previous review's blocking concern asked for exactly that change (run fab18778, AC6,
  two repairs). Fix: a change whose declared `concern_id` names an unresolved blocking
  concern of the saved `astra_challenge` report, and whose item is the one that concern
  names, counts as backed by that review. One test each way.
- **Snapshot exclusions missing on Kilo.** `autocode.py` writes the `__pycache__/` and
  `*.pyc` exclusions (`readonly_events.prepare_opencode_snapshots`) only when
  `engine == "opencode" and not configured_tool`, while `assert_unchanged_review` guards
  every read-only stage's report. On a Kilo run the worktree's `info/exclude` is the Git
  default, so an Investigator that runs `unittest` in the workspace trips the guard
  (two runs on 2026-10-21, `PAUSED_STALE_VALIDATION`). Fix: install the exclusions for
  every launch the guard applies to. Test: a Kilo-configured launch writes them.
- **GLM-5.3 low omits `proposed_assumptions[].text`** in every Requirements report
  (one 24 s repair each run). A model slip against a schema it is shown; not a runner
  change. Noted for the routing docs.

## Not in this plan

- Changing the default stage limit or the reviewer effort tiers.
- Giving the Kilo Planner a shell. The read-tools-only agent is a containment decision
  (#413); the example rule's "work each literal result out" stays a planning rule.
- Any change to what the Plan Reviewer may decide (#595).
