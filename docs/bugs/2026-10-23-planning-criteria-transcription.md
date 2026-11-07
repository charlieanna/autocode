# Repeated planning repairs from copied criterion hashes (#1958)

The Codex/GLM-through-Kilo run `20261023-004044-chat-afb1d2` repeated planning
repair because GLM mistyped hash references for unchanged criteria. Revision 02
took 347.926 seconds and its repair 181.468 seconds; revision 03 took 484.689
seconds and a rejected repair another 199.971 seconds. The archived reports
contain altered digits and a 65-character AC10 hash in revision 02. These are provider-active
durations, not an estimate of guaranteed savings. Bundle manifest SHA256:
`a375d49382cb3ff4834c52b4b078475638ed4011302645474367e2cc6c6bdf0a`.

#1947 gave repairs the missing exact inventory and removed duplicate historical
drafts. The normal author response still needed a long hash per unchanged row.
Repeating opaque identities across a large revision is unnecessary model work.

Discovery and revision generation now also allow this representation inside the
otherwise complete contract:

```json
{
  "acceptance_criteria": {
    "retain_from": "r4:<exact-current-contract-hash>",
    "upsert": [],
    "remove": []
  }
}
```

The runner checks that one exact contract token, retains untouched rows byte for
byte, replaces changed IDs in place and appends new full rows in supplied order.
Removing a row requires its explicit ID. Full arrays and older per-row references
remain accepted. Repairs offer the delta only when their pinned generation schema
allows it; an existing repair's schema is never rewritten.

Expansion occurs before the ordinary canonical report, source, protected-contract
and permission guards. A delta does not excuse a missing source, weakened proof
or unauthorized removal. Stale tokens and ambiguous operations are refused, never
corrected by guessing. Existing report artifacts preserve the provider's delta;
the accepted planning record contains the full expanded criteria.

Focused public-CLI fake-provider regressions check an ordinary revision reaching
independent final review and exact-plan approval with no report repair, an unrelated
format repair retaining the delta, and a stale delta never reaching approval or
Builder. Pure tests check changed/new/removed rows, byte preservation, schema
compatibility and protected proof/removal refusal. These are deterministic protocol
results, not a paid-model speed comparison or proof of reliable completion.

Tester and Completion Reviewer remain separate. This correction removes a source
of avoidable report transcription; substantive review concerns still require
planning work. Broader stage selection and evidence reuse remain under #1867.

## The final review's criteria delta (#1991)

#1858 left the final reviewer no way to change anything: a wrong criterion or a
proof naming the wrong test could only become a blocking question, a stop for the
person and a full Planner revision. In 36 saved runs with both a revision and a
final review, the final review had changed the plan in 15; seven of those touched
criteria, milestones or constraints. The owner chose criteria only (2026-10-24).

The final reference now always carries the same delta, `acceptance_criteria:
{"retain_from", "upsert", "remove", "proofs"}` beside `retain_contract`; empty, it
retains every row. The runner expands it onto the retained contract and runs the
ordinary change-receipt, source-coverage and proof guards as for a Planner
revision. A reference may declare `contract_changes` only when its delta changes a
row, never `conflict_resolutions`, and a corrected criterion set may supply its own
`requirement_trace` where the validated one no longer covers every requirement.
Milestones, the technical approach and permissions remain outside the reference. A
report repair offers the delta only when its pinned reference schema holds it.
