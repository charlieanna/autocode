# Draft example corrections and scenario oracle repairs

The remaining concerns after PR #208 were reproduced and independently addressed
at the user's request, without waiting for an external reviewer.

## Numeric examples in unapproved drafts

A model-written CSV criterion listed two data records but expected one. The
Planner and Plan Reviewer could identify the arithmetic error, yet the revision
guard demanded a user answer before correcting the never-approved draft.

A narrow exception now accepts only integer stdout corrections in JSON or
word/count output. A saved blocking reviewer concern must quote the old and new
outputs and identify the criterion. The revision records a structured receipt
in the existing contract history, displayed with the final plan. The final
reviewer must independently recompute the result; the user still approves the
exact corrected plan before any Builder starts.

The runner verifies that the input, command, exit code, stderr, proof, human
review and all other protected contract fields stay unchanged. Previously
approved and user-authored examples remain protected, including user JSON with
different whitespace. Missing or fabricated concerns, malformed output, changed
JSON strings/keys, input changes and proof downgrades are rejected. This is not
a general mechanism for rewriting criteria or proving arbitrary prose arithmetic.

## Design review grading

Issue #197's oracle required an ordering question although `processor.py` already
requires per-domain sequence order. A blocking review that cited that invariant
was therefore graded as false completion for omitting an engineering question.
The oracle now requires the blocking concern to connect the partitioning defect
to the existing processor contract. Questions are optional. All three blocking
gaps, rejection of invented blockers, no source edits and design-only dispatch
remain required. A generic ordering complaint or a question without the actual
blocking finding fails. Historical campaign verdicts are not overwritten.

## CSV quoting and field length

The brief now specifies double-quote placement and escaping explicitly. The
reference enforces it before delegating record parsing to `csv.reader`, retaining
valid escaped quotes, embedded newlines, CRLF and blank lines. Hidden checks
reject quotes inside each unquoted field, stray text after closing quotes and
truncated quoted fields, including after a prior valid record. These fatal
errors must never print a partial JSON result.

A further probe found that valid long zero-padded ages crossed the native
131,072-character field limit and failed as malformed input. The reference now
uses the largest supported field limit and keeps integer conversion bounded.
Hidden checks cover a 140,000-digit valid age and invalid age. Separate broken
controls preserve the old quote acceptance and field limit.
