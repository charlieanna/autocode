# Whitespace overhead in inline output schemas

OpenCode stage prompts embedded a pretty-printed JSON output schema. Compact JSON
serialization preserves every schema value while removing indentation and
separator whitespace. This change does not remove schema fields or constraints.

The regression test parses the schema from the generated prompt, checks equality
with the source schema, and verifies compact formatting and a smaller prompt
schema. It fails against the previous prompt builder and passes after the change.

An offline comparison using the actual old and new prompt builders on 319 saved
schemas removed 862,716 bytes. Every other generated prompt byte was unchanged.
Of those savings, 162,072 bytes came from the 59 scenario review prompts: 4.31%
of their complete original prompt bytes. This measures bytes, not live tokens,
billing, or model output quality.

The affected suite passed 254 tests in 23 modules. Combined local validation completed
54 offline scenarios: 52 PASS, one expected NOT_EXERCISED, and one live-only
SKIPPED. The full suite was not green: the installed-package check needed an
isolated package installation, and CLI and browser tests hit their time limits.
The separate validation-diagnostic change is outside this formatting fix.

## Authorized live comparison

Eight fresh OpenCode sessions ran GLM-5.3 with high reasoning on two recorded
report-repair inputs: the greeting CLI Planner's duplicate `contract_changes`
field and the to-do CLI Plan Reviewer's empty extra `evidence` field. Each case
ran all four combinations of pretty/compact schema and generic/precise error
message, in opposite order between cases. Everything else in each pair was
fixed. The sessions had read-only permissions, a 180-second deadline, and caps
of four completed model steps, eight tool calls, and 100,000 observed tokens.

| Matched formatting comparison | Pretty schema | Compact schema | Reduction |
| --- | ---: | ---: | ---: |
| Input tokens, including cached input once | 62,234 | 59,310 | 4.70% |
| Input plus output/reasoning tokens | 73,943 | 70,146 | 5.14% |
| Schema-valid reports | 4/4 | 4/4 | — |
| Exact preservation of all required report values | 4/4 | 4/4 | — |
| Tool calls | 0 | 0 | — |

Every attempt completed in one model step without hitting a cap. The output
oracle permits only removal of the invalid duplicate or empty field; all other
report values must equal the supplied draft. The duplicate `contract_changes`
value remains at its valid top-level location. No acceptance criteria, findings,
uncertainty, or decision records were lost in these eight outputs.

This is a small repair-prompt comparison: two cases, one attempt per combination,
on a fixed model, not full scenario builds or the original mixed-model deployment.
The total-token difference includes variable output/reasoning behavior; neither
percentage is a dollar-saving estimate or a universal quality guarantee. The
separate diagnostic comparison did not establish fewer repair loops because its
baseline also made zero tool calls and succeeded in one step.

The authorized campaign consumed 144,089 reported tokens in total, including
43,584 cached-input tokens. The protocol, source hashes, prompts, events, reports,
and per-attempt measurements are retained locally under the ignored
`.scenario-runs/savings-audit-20261002/live-comparison/`; they are not committed.
