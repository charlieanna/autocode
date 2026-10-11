
REQUIREMENT TRACE: requirement_trace_rows in the handoff data lists every requirement from the requirements
handoff, and any feedback on a plan the user was shown that no Requirements report has read yet (its
requirement_id is the feedback event ID). requirement_trace must contain exactly one row for each of those
requirement_id values, no more and no fewer; an empty requirement_trace is refused. disposition is covered,
excluded or superseded. For covered, evidence is an acceptance criterion ID of this contract (for example "AC3",
or "AC3 checks this"), or a required_behaviors entry copied exactly; a paraphrase is refused. For excluded,
evidence is a scope_exclusions entry copied exactly and backed by a saved user answer; for superseded, it cites
the saved answer or feedback event ID. While the draft has open_blocking_questions and no criteria yet, a covered
row may say what it waits on (for example "pending Q1"); the next draft, after the answer, must cite criteria.
