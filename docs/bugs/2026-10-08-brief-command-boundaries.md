# Output rules crossing a CLI invocation (#645)

On master `1a41c89`, a command's clause ended only at a semicolon or newline.
With a period between `todo.py add TEXT` and `todo.py list`, the add command
inherited the list command's output template. Replaying a correct silent add
failed the original-brief gate. Conversely, a single-item add template inherited
"one per line" from a later list command and an add printing the whole list passed.
Both verdicts were reproduced with the runner-owned acceptance command.

Compiler version 2 ends each command's description at the next Python CLI
invocation, including one declaring a nonzero exit. Inline formats and data do
not create a command boundary. A declaration still needs its own explicit
format, "one per line" and successful exit; single-item formats are outside this
bounded compiler. Rejecting a phantom observation does not independently prove
the single-item command's behavior: its ordinary tests and oracle still apply.

## Saved-run compatibility

New bindings use version 2. An existing sealed version-1 manifest pins that run
to the original parser, including subsequent planning reviews and amendments.
Verification recomputes the original inventory, hashes and reviewer proposals;
it does not merely trust a stored regex. No contract or evidence hashes are
silently rewritten, no review provenance is fabricated, and cross-version
replacement within a run is refused. Unknown versions are refused.

This means the boundary defect remains in existing version-1 runs. To adopt the
corrected compiler, start a new run from the current project and review its new
plan. An unsealed draft with no manifest uses version 2. This change does not
automatically migrate already-approved proof or approve a replacement plan.

Regression fixtures retain manifests generated on untouched master, including
both affected briefs and an unaffected semicolon brief. Tests verify exact
version-1 identities and retained independent-review receipts, reject both
version-2 phantom obligations, and drive a correct period-separated to-do CLI
through completion. The to-do catalog brief now uses a period at that boundary
so fake and live qualification exercise the correction.
