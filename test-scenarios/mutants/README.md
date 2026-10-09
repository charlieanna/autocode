# Mutation catalogue

Two families of mutants, both against the same cart spec:

| File | Scenario | What is mutated | What a real Validator must notice |
| --- | --- | --- | --- |
| `MUTANTS.conf` | 09 | `cart.py` (implementation) | A spec behavior broke, even though the inherited suite stays green |
| (inline in scenario-11) | 11 | `tests/test_cart.py` only | A requirement stopped being proven, even though the suite stays green |

## Adding a mutant

**Implementation (09).** Add a line to `MUTANTS.conf` and a matching
`add_mutant` call in `scenario-09-mutation-validation.sh` with the mutant body.
The body must keep the *shallow* suite green — if `pytest tests/` fails, the
mutant is not a valid trap and is skipped as INVALID.

**Test-suite (11).** Add an `add_mutant` call in
`scenario-11-test-suite-mutation.sh` whose transformation silently weakens the
strong suite while leaving every test passing.

Domain-specific mutants worth adding before trusting any score: currency
rounding rules, timezone boundaries, authz checks that are `>=` where they
should be `>`, pagination off-by-ones, and the "assert `abs(x-90.9) < 1`"
loosening that still looks rigorous.

## Scoring

Catch rate alone is misleading. Read it with the control (correct code must be
accepted) and the diagnosis column (findings must name the actual defect).
See the tables at the bottom of each scenario script.
