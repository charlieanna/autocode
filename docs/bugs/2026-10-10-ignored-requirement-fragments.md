# Ignored fragments could hide longer requirements

Requirements coverage treated verified source quotes and ignored statements
with the same bidirectional substring match. Ignoring `Required examples:`
therefore also covered a separate requirement to export that exact literal.

A saved real Codex Requirements report preserved both the literal requirement
and the separately ignored heading. A pure negative control removed the
literal requirement from a copy of its requirement rows: the old public
handoff guard still accepted it. This demonstrates a guard defect, not an
observed omission by that live model.

Coverage now distinguishes verified quotes from ignored statements. An ignored
entry must contain the entire normalized requirement sentence; a complete
ignored sentence followed by its explanation remains compatible, as does a
complete heading without the final period synthesized by the scanner. Verified
quotes retain their existing formatting and multi-sentence handling. Source
scanning, schemas and saved run formats do not change. This narrow correction
does not establish general semantic completeness of model reports.
