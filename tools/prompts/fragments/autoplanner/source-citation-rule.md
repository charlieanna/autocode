
SOURCE TRACE. Every acceptance criterion must name what it traces to: add
"sources": [{"source_id": ..., "quote": ...}] to each criterion, where quote is
copied verbatim from that source's text. The run's human sources are listed
below with their ids. Between
them, the criteria must quote every sentence of every source; a sentence the
plan must not cover belongs in the reviewer's out_of_scope with a reason,
never silently dropped or quietly reworded.

Whatever you quote binds you. Every number, backticked literal and negation
inside a cited sentence must survive in the citing criteria (or in the
contract's restated required_behaviors and constraints): a criterion that
changes a quoted limit, drops a quoted negation, or rewrites a quoted literal
is rejected before approval, and a dropped negation stands only when the Plan
Reviewer's source_trace names the span and the reason in negation_waivers.

When this report schema includes source_trace, the final reviewer supplies it:
out_of_scope and negation_waivers, each entry {"source_id", "quote", "reason"}
with quote verbatim from the named source. Before approving, check the draft
against the sources: every criterion cited, every sentence covered or
explicitly out of scope, every quoted number, literal and negation survived.
An uncovered sentence or a corrupted quote is a blocking concern quoting the
sentence and the deviation.
The run's human sources:
