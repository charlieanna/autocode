# Requirement tracing across paragraphs (#919)

A real program banner brief put B1's verification metadata before a blank line
and an inherited-ID instruction. Requirements quoted both real clauses, but the
sentence extractor joined them into an artificial obligation. List boundaries
could also join a substantive heading to its first bullet.

Requirement cues now split at blank paragraphs and new list items before the
existing sentence splitter runs. Wrapped prose and indented list continuations
stay together; substantive headings and text after an unclosed fence remain owed.
Coverage matching and constraint enforcement are unchanged.

Replay of the original native report removes only the fused-span false positive:
its four genuinely uncited constraints still reject. A control explicitly covering
those clauses passes; omitting the inherited-ID instruction still rejects.
The fresh-master live Requirements probe also stopped with genuine trace omissions.
This correction does not certify complete planning, child completion or a program.
