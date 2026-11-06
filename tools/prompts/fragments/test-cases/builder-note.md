
TESTS NAMED IN THE PLAN: every acceptance criterion of your milestone whose verification_method starts with
"test:" is a concrete example you must write as its own test, using the supported test identifier declared
immediately after the marker and asserting exactly the criterion's example. Preserve an explicitly requested
native name exactly (test: TestFixedReturnsTwo -> func TestFixedReturnsTwo). In Go, a criterion-ID identifier
such as test_ac1_x maps to native TestAc1X by the runner's documented whole-word and numeric-group alias
rules; do not define a lowercase Go test function.
If no name is declared, use that criterion's id (C2 -> test_c2_<what it checks>). Before the Validator runs, the runner
runs these tests itself, with those of milestones already accepted: each must pass with the change and must
not have passed before the run began. A criterion whose verification_method starts with "guard:" is behavior
that already works and must keep working: preserve its declared test name too (otherwise C4 -> test_c4_...);
it must pass before and after the change, so put it where it imports only code that exists before the change.
The runner checks fail-first tests and base guards in a separate checkout. If the approved plan requires
your own fail-first run, write and run the new tests first. Run the named checks against your candidate and
keep the fix applied after you make it. If you must run the approved plan's baseline checks after applying
the fix, use a separate workspace-contained scratch copy. Use the existing fail-first receipt for guards it already
ran. The runner checks the pre-change suite, so run it separately only when required by the plan or needed to
diagnose a failure. Reuse a passing candidate check when its command
and inputs are unchanged. Rerun after a relevant change, failure, or required repetition.
Criteria without "test:" or "guard:" are checked by the Validator as usual.
Keep existing test names and assertions intact. Add a new case test when needed; do not rename or remove an
existing test to make its name match a planned case id. The regression proof rejects removed test names.
