"""Acceptance examples exercise the requested domain; they cannot redefine it.

Shared by planning and independent validation so a general testing suggestion
cannot override an approved design, or make one example stand for all inputs.
"""

DOMAIN = """
NUMERIC BOUNDARIES: derive the valid numeric domain from the requested behavior, existing API and any approved
 design. Test 2**63-1, 2**63, and 10**5000 (and large negatives for signed domains) when that contract requires
 unbounded integer semantics. Probe persistence, exact arithmetic and rollback wherever the requested behavior
 uses them. SQLite INTEGER bindings stop at 64 bits and SQL overflow can promote to REAL; use lossless storage
 and application integer arithmetic for unbounded values. Decimal int/str conversion can hit Python's digit limit.
An int annotation alone does not impose arbitrary-precision semantics on a design that explicitly uses floats.
Preserve that float representation and public return type; do not demand 10**5000 support, replace it with
Fraction/Decimal, invent an expanded numeric requirement, or ask the user to revise an approved design just to
satisfy this testing advice. Test relevant boundaries within the specified domain and mixed-type interactions.
If the source contract itself genuinely conflicts, cite both binding source requirements; testing advice is
not a source requirement. Do not invent a bound for an explicitly unbounded integer contract.
"""

COVERAGE = """
REQUIREMENT COVERAGE: concrete acceptance examples are necessary checks, not replacements for the general
rules in the request and approved brief. Preserve those rules in required_behaviors and plan tests for each
relevant input class. A trace to one criterion ID proves only the behavior its examples actually cover; it
does not establish the rest of a broad requirement. Read each source_quote, split combined requirements into
independent obligations, and check them against the proposed examples before accepting coverage. Raise a
blocking plan concern for an omitted required input class. Keep testable variants in executable criteria;
file inspection alone cannot prove input behavior. Do not expand the requested domain or reopen settled choices.
For parsing rules that ignore blank records or separators, cover them before the first meaningful record,
between records, after the last record, and when only ignored records remain. Exercise combinations with
required quoting or multiline behavior; the first logical record need not be the first physical line.
Independent validation checks these classes even when the Builder's named example tests already pass.
"""

DISCRIMINATING = """
DISCRIMINATING CRITERIA: every acceptance example and criterion check must tell the requested behavior apart from
the original code and from a plausible broken version of the mechanism it promises. For each new-behavior
criterion, state what the original code does with the same input, and make its check a check the original code
fails, including on negative paths: a failure check must assert something the baseline's generic failure
(unknown command, usage error, missing function, ImportError) cannot satisfy, such as a reason naming the
rejected field or the unreadable path, not only an exit status and nonempty error output. Make each check fail
when the specific mechanism the criterion promises is removed while everything else stays: an installed entry
point is run as installed, isolated from the source tree, so the checkout cannot supply its modules; normal
discovery is checked by default discovery observed as the default collection, not by naming the file to a
runner; invalid-input examples cross the input boundary the parser applies (for example values the parser
would read as options) so they reach the new diagnostic. Examples that use existing interfaces call existing
APIs as they work on the current code, through the entry point the current code provides. In worked examples,
take each worked-example operand from the record the rule applies to, never from a neighbouring record. When
the approach reuses a helper that accepts more than the public contract, reconcile the public contract with
any broader helper the approach reuses, and give a distinguishing example whose result differs between the
two. Requirements: keep the confirmation summary consistent with the requirement rows; never attribute to a
case a property the rows give only to a different case. Plans: do not prescribe a per-item pass over a whole
collection for a bulk operation when one pass serves, unless the request requires it. When a criterion of your
own fails any of these, correct that criterion yourself; this is guidance for writing and checking criteria,
not a reason to ask the person a new question.
"""

DISCRIMINATING_REVIEW = """
DISCRIMINATING REVIEW: check each acceptance criterion against DISCRIMINATING CRITERIA. When the original code,
or a broken variant that removes the promised mechanism while everything else stays, would still pass a
criterion's check, raise a blocking concern naming the baseline or broken variant that passes it and the
criterion affected. Do not turn this into a question for the person.
"""

EXTERNAL_CONTRACTS = """
EXTERNAL CONTRACTS: when the change reads, receives or sends data whose shape is defined outside the repository
(another service's log documents or API responses, a webhook or alert body, a message or queue format, a table
another system writes), and neither the request, a saved answer nor a file in the repository shows that shape,
the shape is the person's fact, not an engineering choice, even in an empty repository. Requirements asks for it
in the same round as its other questions, offering to take a pasted sample. A plan that still needs it asks the
person with an open blocking question that offers the likely shape as its recommended option; it never writes a
guessed field name, payload or response into the technical approach or a criterion as if it were given. A
criterion checked only against a mock of a guessed shape proves agreement with the guess, not with the person's
system. A decision left for the person to confirm is asked as a question, never left as a note in the technical
approach ("flagged for the user to confirm at approval").
"""

EXTERNAL_CONTRACTS_REVIEW = """
EXTERNAL CONTRACTS REVIEW: raise a blocking concern when a criterion or the technical approach rests on the shape
of external data nobody gave (a field name, a payload, a response format), or leaves a decision for the person
as a note in the technical approach, naming the shape or the decision and the criteria that depend on it.
"""

REQUIREMENTS_LABELS = """
SOURCE LABELS: ignored_statements is a JSON array of strings, never objects. Copy each ignored source
statement exactly into that array. Explain its reason only in the top-level summary string.
If a checklist entry is solely the nonoperative label "Required examples:", use this field shape:
"ignored_statements": ["Required examples:"]
Put no explanation or text/summary fields inside an array entry. Use the exact label from the source;
do not add this example label when it is absent. Preserve every actual example, exact expected result and behavior it introduces
in requirements, required_behaviors and acceptance_tests. Never ignore an operative requirement, constraint,
permission, saved correction or requested literal output merely because it resembles a heading.
"""
