# A first build on a scaffolded project can never prove its regression check

Open; found by the live run of `program-notes-cli` for #22 and #23 on 2026-10-06
(Claude models, run directory `20261006T031926Z-program-notes-cli-claude-tiers-x12_z6gl`).
The cause is not in the program code: any run whose base is a project scaffold meets it.
The program makes it common, because a walking skeleton is the first build on whatever
the project already holds.

The regression proof (`tools/autocode_verify.py`) lets a run introduce a project's first
source and test suite only when `_document_only_base` accepts the base tree: an empty
tree, or one holding nothing but a regular, non-executable root `README.md`. Any other
file, including a `.gitignore` or an empty `tests/__init__.py`, makes the base one whose
existing behavior must be preserved. Its suite then runs zero tests, so preservation
cannot be shown and the verdict stays `UNVERIFIED`. The completion gate requires `PASS`.

In the live run the skeleton workstream (`M1`) planned, was approved, built and
validated: all six criterion tests failed on the base and passed on the candidate, and
the whole suite passed. Its base held `README.md`, `.gitignore` and an empty
`tests/__init__.py`. The Completion Reviewer returned `REWORK`, the Resolver had
already diagnosed the same task, and the run stopped at `PAUSED_INVALID_OUTPUT`. The
Investigator traced it to `_document_only_base` and offered three ways out: accept the
workstream outside the run, widen the allowance, or start from a README-only base. A
user answer that approved a greenfield waiver had no effect, because no code path reads
such a waiver.

The narrow allowance is deliberate (its docstring: "unknown files, executable
documents, links and submodules need preservation"). A `.gitignore` and an empty file
cannot hold existing behavior, though, so accepting them would not weaken the proof.

The `program-notes-cli` seed now holds only `README.md`, and the skeleton owns
`tests/__init__.py`. That gets the scenario past the gate; it does not fix the gate.

## Options

- Accept regular, non-executable `.gitignore` files and empty regular files in
  `_document_only_base`, beside `README.md`.
- Let a recorded user decision waive preservation for a base whose suite runs no tests.
