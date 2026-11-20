# report-check and admission give one answer (#3865, #3866, #3891)

## Shared cause

`autocode report-check` (`tools/autocode_report_check.py`) reimplemented a subset of the checks final
admission runs on a completed stage report, and Requirements admission raised on its first failing check.
So a Requirements report got "OK" from report-check and was then refused for an ambiguity-audit reference
or a cited line report-check never looked at (#3865); a repair round named only one of the faults already
in the report, and the next fault cost another paid repair (#3866); and report-check never flagged a Tester
criterion citing a command event no listed check is bound to, which the final evidence-binding gate in
`autopilot.apply_review_result` refuses (#3891).

## The fix

The decidable checks are pure functions that return a list of errors, and both admission and report-check
call them:

| Decision | One implementation | Admission caller | report-check caller |
| --- | --- | --- | --- |
| Ambiguity-audit references | `ambiguity_audit.problems` | `ambiguity_audit.validate` | `requirements_faults.faults` |
| Cited file exists, line in bounds | `code_citations.citation_problem` (`_citation`) | `verify_citation` | `requirements_faults.faults` (audit refs) |
| The same for `source_refs`, in `require`'s line syntax | `code_citations.required_citation` (`_citation`) | `require` | `requirements_faults.faults` |
| Which `source_refs` are local files | `requirements_faults.local_source_refs` | `autocode_requirements_handoff._accept` | `requirements_faults.faults` |
| Quote grounding | `requirement_quotes.ground_cited` / `grounded` / `problems` | `goals.check_requirement_handoff` | `requirements_faults.faults` (older specs: `requirement_quotes.problems`) |
| Criterion event binding, message | `check_refs.unbound_refs`, `check_refs.unbound_message` | `apply_review_result` | `check_refs.public_unbound_events` |

- `code_citations.require` names every bad citation in one rejection. `source_refs` go through `require`, whose
  line syntax refuses an explanation spanning lines (`app.py:2 why` and a newline, "has no line number"), so the
  shared fault list asks `require`'s own `required_citation` about them, and names one fault per reference;
  ambiguity-audit references keep `verify_citation`'s syntax, as their admission check does.
- `units.autoplanner.accept_handoff` keeps every gate and its order. When a Requirements report is refused with
  an exception of exact type `ValueError`, the rejection keeps its original message first and appends each
  other fault `requirements_faults.faults` finds in a copy of the report taken before the gates ran, skipping
  any the message already names. Citations are held to what the stage read when it read files, as its
  `source_refs` check is, so an unread cited file is named beside the other faults. `ExecutionEvidenceRequired`
  keeps its type and message, a triage answer's rejection is unchanged, and a failure while listing the faults
  re-raises the original rejection.
- report-check's spec for `requirements_gather` carries `requirements_faults.context(state)`: the source texts,
  the coverage checklist, the audit's source ids, the input refs (task, answer, linked record and user event
  ids), whether the run inherits a contract, the workspace and whether it has inventory files. It carries no
  event log or read paths: whether the stage read a cited file stays only in admission. A pinned `catalog/...`
  audit ref is left to admission, which needs the run's pinned catalog.
- The Validator prompt, the report-repair evidence instruction and the Claude evidence fragment state the
  gate's rule: in criterion evidence an event reference counts only when it is the event the runner attached
  to a listed check, so a criterion cites that check as `check:<n>`, and a bare `event:` there is always refused.

No gate accepts or rejects a different report: only a rejection's message gains faults, and report-check
writes nothing and confers nothing.

## Cases checked beyond the reported ones

The regression fixtures in `tests/test_report_admission_parity.py` vary the fault classes and the callers:
an audit dimension citing a requirement id (`r4`) as code; a `source_refs` line past the end of a file; a
`source_refs` file that does not exist; a `source_refs` line citation whose explanation spans lines; a cited
file the stage never read (admission only); a quote no
source holds; a Planner `code_refs` list through `require`; Tester criteria citing an event no typed check
names, a bare `event:` beside a typed check, and the three citations beside a bare-`event:` check. Valid
Requirements and Tester reports pass report-check and admission (or the evidence-binding gate).

## Rejection rate the changed checks would have caught

**Saved responses.** This workspace holds one run, the run that made this change. `autocode usage report`
(scope `/home/user/ac-g1`, 1 run, 6 launches) records 1 rejected launch, a Planner `revise_plan` answer of
class `other`, which none of these checks reads. Its one Requirements answer
(`requirements-gather-01.response.txt`) was accepted; `requirements_faults.faults` finds 0 faults in it
(0 of 1, 0%). It has no saved Tester answer yet (0 available). No other run's saved responses are in this
workspace, so the rate over earlier live runs (the ones behind the issues) is not measured here.

**Regression fixtures** (`tests/test_report_admission_parity.py`):

| Fixture | Faults admission establishes | report-check before | report-check now | Admission's first rejection before | now |
| --- | --- | --- | --- | --- | --- |
| AC1/AC2 Requirements report | 2 (audit `r4`, `app.py:40`) | 0 ("OK") | 2 | 1 | 2 |
| AC3/AC4 Requirements report | 3 (quote R2, `missing.py`, `app.py:40`) | 1 | 3 | 1 | 3 |
| AC21 Requirements report | 2 (audit `r4`, unread `README.md`) | 0 ("OK") | 1 (reading is admission's) | 1 | 2 |
| Line explanation spanning lines (unnamed test) | 2 (audit `r4`, `app.py:2 why` + newline) | 0 ("OK") | 2 | 1 | 2 |
| AC5 `code_refs` through `require` | 2 (`missing.py`, `app.py:40`) | not a report-check input | not a report-check input | 1 | 2 |
| AC6 Tester report | 2 (AC1 `event:item_9`, AC2 `event:`) | 0 | 2 | 1 (the gate names the first) | 1 |
| AC19 (c) Tester report, bare check | 1 (AC1 `event:`) | 0 | 1 | 1 | 1 |
| AC7 Requirements, AC7 Tester, AC20 bare-check Tester (valid) | 0 | 0 | 0 | accepted | accepted |

- Faulty Requirements reports report-check flags: 1 of 4 before, 4 of 4 now. Their faults it names:
  1 of 9 before, 8 of 9 now; the ninth is the unread file, which only admission can establish.
- Faults Requirements admission names in its first rejection: 4 of 9 before (one per report), 9 of 9 now;
  with AC5's `require`, 5 of 11 before and 11 of 11 now.
- Tester reports the evidence-binding gate refuses that report-check flags: 0 of 2 before, 2 of 2 now
  (AC6 and AC19 (c)). AC19 (b), a concrete event beside a bare-`event:` check, is refused by the gate only
  when the run the runner binds is another one, so report-check does not flag it (below).
- Valid fixtures flagged: 0 of 3 before and now.

## Still open

These admission faults are not in the shared fault list. Each is still named only when its own check raises,
so two of them in one report still take two rounds (a rejection for one of them does gain the shared faults),
and report-check does not report them:

- an empty `source_refs` in a report that builds code ("Report must cite existing source in source_refs");
- a missing outcome, required behaviors or acceptance tests;
- empty or duplicate question ids, and duplicate or malformed requirement rows;
- unverified pinned `catalog/...` `source_refs` citations;
- `workspace_scope` faults (`autocode_workspace_scope.validated`);
- `proposed_assumptions` faults, including a convention assumption whose `convention_ref` is unverified;
- the coverage-checklist check, reached only after every quote is grounded, and the proposed-reframe checks.

None of these is in report-check's Requirements check except the coverage checklist.

One report-check Tester flag is advisory. A concrete `event:<id>` that no listed check names is flagged,
as the request asks, but `verify_checks` (`tools/autocode_support.py`) re-binds a listed check whose own event
ID is missing from the stage's event log to the latest run of its command, and the gate then accepts that ID
when it is the one cited. The flag's advice, cite `check:<n>`, is accepted either way.

Changing the three instruction files changed the rendered prompt bytes pinned in
`tests/prompt_rendering_fixtures.json` (`test_every_fixed_stage_rendering_matches_its_reviewed_bytes`). The
fixture is updated in this change: only the `bytes` and `sha256` of the 24 render cases whose text changed
(9 Tester renderings from `validator.md`, 15 repair renderings from `evidence-instruction.md`); each was checked to return to its old hash when the file's
previous text is put back. A pull request changing `tools/prompts/**` still needs the prompt-evidence
check's "Live run evidence" or Exempt block (`tools/check_prompt_evidence.py`).
