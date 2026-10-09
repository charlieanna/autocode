# A declared permission replacement was refused, so "Build it." asked the user (#185)

**Seen:** three live runs of `discuss-then-design-then-build` on Claude models (2026-10-06). The design
turn's approved contract allowed only the design document. In the "Build it." turn the Planner replaced
that permission boundary as its prompt asks: one `contract_changes` row, `change: permission_changed`,
the previous boundary as `item`, the new one as `replacement`, backed by the follow-up's `brief_feedback`
receipt. The contract guard refused it with "Planner revision drops or changes '<new boundary>' without
a user-backed contract change". It took the symmetric difference of the two boundary lists and wanted a
row for each side, so the new text needed a row of its own. The repair and the Investigator read the
refusal as "Build it. was not authorization" and asked the user. One run then completed with nothing
built (its own question's proposed default was "No").

**Fix** (`autocode_contract_revision.revision_guard`):
- A removed boundary consumes its user-backed `permission_changed` row; that row's `replacement` must be
  in the new boundaries and covers the boundary it adds.
- An added boundary with no such replacement still needs a user-backed row of its own, and the refusal
  now says a boundary was added.
- A user-backed row for an accepted assumption or delegated decision the revision really dropped is not
  an error, though neither list is protected; a leftover row is named in the refusal.

The fake Planner now bounds each turn of a `turn_paths` conversation to that turn's paths and declares
the change the same way, so the fake run reproduces the live refusal before the fix.

**Later (2026-10-07):** a build of the design the previous turn proposed now plans afresh
(`docs/bugs/2026-10-07-build-after-design-revised-the-design-contract.md`), so this scenario no longer
declares a permission change; `tests/test_contract_revision.py` is the guard's remaining test.

**Not fixed here:** a design turn written by a weak Builder tier may not converge in three rounds
(one live run stopped at `PAUSED_BUILDER_RETRY_LIMIT`); the `claude-tiers` profile has no stronger
Builder to escalate to. The guard is still skipped when a Planner replaces an unapproved draft.
