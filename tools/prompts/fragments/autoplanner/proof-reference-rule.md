
REGRESSION REFERENCE: regression_reference names the saved repository comparison commit when known.
A same-chat follow-up does not reset this reference. The current workspace may already contain a completed
earlier fix. Passing in the current workspace alone does not make an inherited test: criterion invalid:
its exact existing test can still fail on the runner's original reference and pass with the candidate.
Do not require breaking working code, regenerating that test, changing its protected selector or obtaining
historical-proof reuse merely because it passes now. Check the actual reference before raising that concern.
The runner prepares the effective baseline, including any approved reviewed-change or operator patch; a raw
Git commit is not a claim that no overlay applies. A missing reference remains unknown, never today's code
by assumption. Existing criterion IDs, requested behaviors and proof selectors remain protected. This context
does not accept a historical PASS or skip fresh runner proof, protected-test checks, independent review or
approval of the exact new plan. New behavior still needs its own fail-first proof against the runner's reference.
