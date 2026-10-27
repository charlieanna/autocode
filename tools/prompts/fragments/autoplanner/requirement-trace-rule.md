
REQUIREMENT TRACE: requirement_trace_rows in the handoff data lists every requirement from the requirements
handoff, and any feedback on a plan the user was shown that no Requirements report has read yet (its
requirement_id is the feedback event ID).
Retained final-plan exception: when contract.retain_contract refers to a plan with a validated Planner trace,
use requirement_trace=[] to inherit that trace. Adding contract.initial_task does not require a new trace.
Do not copy or paraphrase the validated trace, including its evidence strings. Only supply a replacement when
an actual criteria correction requires different coverage, or no validated trace exists. This exception applies
to requirement_trace only; the independent reviewer still supplies source_trace and concern decisions.
Otherwise, requirement_trace must contain exactly one row for each of those requirement_id values, no more
and no fewer; an empty requirement_trace is refused. disposition is covered,
excluded or superseded. For covered, evidence is an acceptance criterion ID of this contract (for example "AC3",
or "AC3 checks this"), or a required_behaviors entry copied exactly; a paraphrase is refused. For excluded,
evidence is a scope_exclusions entry copied exactly and backed by a saved user answer; for superseded, it cites
the saved answer or feedback event ID. While the draft has open_blocking_questions and no criteria yet, a covered
row may say what it waits on (for example "pending Q1"); the next draft, after the answer, must cite criteria.
disposition proxied is for a user-visible requirement whose result is rendered by code outside this checkout,
so its criteria can prove only a stand-in (an internal value the visible result is believed to depend on).
Its evidence cites criteria as for covered, and proxy_reason says what the user sees, what the criteria prove
instead and why nothing here can show the link. Never mark such a row covered. Until a saved answer accepts
the stand-in (cite its ID in proxy_reason), add an open_blocking_questions entry of kind decision that names
the requirement ID and asks the person whether proving the stand-in is enough. Every other row's proxy_reason
is null.
