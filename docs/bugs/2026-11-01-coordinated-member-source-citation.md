# 2026-11-01 Coordinated-Member Source Citation Clarification

**Issue**: #2347
**Manifest**: abc6a4bcdd4c162708520bb920894c522a53ea2797ea5d9b26d7f180c3b72396
**Resolution**: Instruction clarification; fresh live qualification pending

## Problem

Coordinated members received ambiguous guidance for citing whole-task sentences. Models created global coordinator-only ACs with coordinator verification_methods, creating circular local completion gates. After 16 attempts, run stopped at PAUSED_INVALID_OUTPUT.

## Root Cause

Instruction said trace obligations to required_behaviors but didn't clarify source provenance vs fulfillment. Missing: cite every whole-task sentence in LOCAL ACs with LOCAL test/guard verification_methods; preserve whole-task text verbatim in required_behaviors/constraints.

## Solution

Added to coordinated_member instruction (autocode_coordination_context.py:163-167):
- Source citations establish provenance, not fulfillment
- Cite whole-task sentences in LOCAL ACs with LOCAL test/guard verification
- Preserve whole-task requirement verbatim in required_behaviors/constraints
- No separate global coordinator-only ACs
- Observable boundary preservation vs internal representation

## Tests

tests/test_coordination_source_citation.py: 4 tests (instruction assertion, working shape, 2 negatives)
All existing coordination/source-trace tests pass (83 tests: 13 coordination tests including augmented bound-context wiring case, plus 66 source-trace tests).
